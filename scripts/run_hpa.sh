#!/usr/bin/env bash
# HPA 계단 부하. experiments/values-hpa.yaml로 배포하고 k6 계단 부하를 거는 동안 레플리카·스로틀·처리량을 기록한다.
#   IMAGE_REPO=<ecr-repo-uri> IMAGE_TAG=<tag> scripts/run_hpa.sh
#   IMAGE_REPO=cpu-sllm-k8s IMAGE_TAG=kind EXTRA_VALUES=experiments/values-kind.yaml scripts/run_hpa.sh
set -euo pipefail

: "${IMAGE_REPO:?이미지 저장소(ECR URI 또는 kind에 넣은 이미지 이름)를 IMAGE_REPO로 지정한다}"
: "${IMAGE_TAG:?이미지 태그를 IMAGE_TAG로 지정한다}"
NAMESPACE="${NAMESPACE:-llm}"
RELEASE="${RELEASE:-llm}"
DEPLOY="${RELEASE}-llama-server"
PROM_URL="${PROM_URL:-http://localhost:9090}"
STEPS="${STEPS:-6,12,18,24}"
STEP_DURATION="${STEP_DURATION:-4m}"
RAMP="${RAMP:-10s}"
COOLDOWN="${COOLDOWN:-6m}"
# values-hpa.yaml 위에 덧붙일 values 파일(공백 구분). kind에서는 experiments/values-kind.yaml을 준다.
EXTRA_VALUES="${EXTRA_VALUES:-}"
RUN_ID="${RUN_ID:-hpa-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="results/${RUN_ID}"
mkdir -p "$OUT"

values=(-f experiments/values-hpa.yaml)
for f in $EXTRA_VALUES; do values+=(-f "$f"); done

helm upgrade --install "$RELEASE" charts/llama-server -n "$NAMESPACE" --create-namespace \
  "${values[@]}" \
  --set image.repository="$IMAGE_REPO" --set image.tag="$IMAGE_TAG" --wait --timeout 10m
kubectl -n "$NAMESPACE" rollout status "deploy/${DEPLOY}" --timeout 10m
scripts/run_conditions.sh "STEPS=${STEPS} (분당 요청 수)" "STEP_DURATION=${STEP_DURATION}" "RAMP=${RAMP}" \
  "COOLDOWN=${COOLDOWN}" "EXTRA_VALUES=${EXTRA_VALUES}" > "${OUT}/conditions.md"

# 서비스로 보내야 새로 Ready가 된 파드에도 요청이 나뉜다.
# port-forward는 파드 하나에 붙으므로 클러스터 안에서 k6를 돌린다.
kubectl -n "$NAMESPACE" create configmap k6-scripts \
  --from-file=load/k6/lib.js --from-file=load/k6/stepped.js --from-file=load/prompts.json \
  --dry-run=client -o yaml | kubectl apply -f -

uv run sllmbench-collect poll --namespace "$NAMESPACE" --deployment "$DEPLOY" \
  --prom-url "$PROM_URL" --out "${OUT}/cluster.csv" &
PIDS=($!)
# 15초 샘플로는 파드가 Ready가 되는 시각을 잡을 수 없어, HPA와 파드의 상태 변화를 watch로 따로 남긴다.
kubectl -n "$NAMESPACE" get hpa "$DEPLOY" --watch --no-headers \
  -o custom-columns='SCALED:.status.lastScaleTime,DESIRED:.status.desiredReplicas' \
  > "${OUT}/hpa-watch.txt" &
PIDS+=($!)
kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/instance=${RELEASE}" --watch --no-headers \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp,READY:.status.conditions[?(@.type=="Ready")].status,SINCE:.status.conditions[?(@.type=="Ready")].lastTransitionTime' \
  > "${OUT}/pods-watch.txt" &
PIDS+=($!)
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT

kubectl -n "$NAMESPACE" delete pod k6 --ignore-not-found
kubectl -n "$NAMESPACE" run k6 --restart=Never --image=grafana/k6:2.3.0 \
  --overrides "$(cat <<JSON
{"spec":{"containers":[{"name":"k6","image":"grafana/k6:2.3.0",
  "command":["sh","-c","mkdir -p /work/k6 && cp /scripts/lib.js /scripts/stepped.js /work/k6/ && cp /scripts/prompts.json /work/ && cd /work/k6 && k6 run -q -e BASE_URL=http://${DEPLOY}:8080 -e STEPS=${STEPS} -e STEP_DURATION=${STEP_DURATION} -e RAMP=${RAMP} -e COOLDOWN=${COOLDOWN} -e SUMMARY_OUT=/work/summary.json --out csv=/work/k6.csv stepped.js; echo \$? > /work/exit-code; sleep 600"],
  "volumeMounts":[{"name":"scripts","mountPath":"/scripts"},{"name":"work","mountPath":"/work"}]}],
 "volumes":[{"name":"scripts","configMap":{"name":"k6-scripts"}},{"name":"work","emptyDir":{}}]}}
JSON
)"

# k6가 끝날 때까지 기다린 뒤(컨테이너는 결과를 꺼낼 수 있게 sleep으로 남겨 둔다) 결과를 복사한다.
# workingDir를 emptyDir 아래로 잡으면 런타임이 root 소유로 만들어 k6 사용자가 쓰지 못하므로 cd로 옮긴다.
kubectl -n "$NAMESPACE" wait --for=condition=Ready pod/k6 --timeout=5m
until kubectl -n "$NAMESPACE" exec k6 -- test -f /work/exit-code 2>/dev/null; do
  if [[ "$(kubectl -n "$NAMESPACE" get pod k6 -o jsonpath='{.status.phase}')" != Running ]]; then
    kubectl -n "$NAMESPACE" logs k6 --tail=20 >&2
    echo "k6 파드가 결과를 남기지 못하고 끝났다." >&2
    exit 1
  fi
  sleep 30
done
echo "k6 종료 코드: $(kubectl -n "$NAMESPACE" exec k6 -- cat /work/exit-code)"
kubectl -n "$NAMESPACE" cp k6:/work/k6.csv "${OUT}/k6.csv"
kubectl -n "$NAMESPACE" cp k6:/work/summary.json "${OUT}/k6-summary.json"
kubectl -n "$NAMESPACE" delete pod k6
kill "${PIDS[@]}" 2>/dev/null || true
kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp > "${OUT}/events.txt"

uv run sllmbench-collect timeline --cluster "${OUT}/cluster.csv" --k6 "${OUT}/k6.csv" \
  --out "${OUT}/timeline.csv"
uv run sllmbench-collect steps --cluster "${OUT}/cluster.csv" --k6 "${OUT}/k6.csv" \
  --steps "$STEPS" --step-duration "$STEP_DURATION" --ramp "$RAMP" --cooldown "$COOLDOWN" \
  --out "${OUT}/steps.csv"
uv run sllmbench-collect scale --hpa-watch "${OUT}/hpa-watch.txt" --pods-watch "${OUT}/pods-watch.txt" \
  --k6 "${OUT}/k6.csv" --out "${OUT}/scale.csv"
echo "리포트: uv run sllmbench-report --timeline ${OUT}/timeline.csv --steps ${OUT}/steps.csv --scale ${OUT}/scale.csv --conditions ${OUT}/conditions.md --out reports/${RUN_ID}"
