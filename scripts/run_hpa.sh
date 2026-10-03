#!/usr/bin/env bash
# HPA 계단 부하. experiments/values-hpa.yaml로 배포하고 k6 계단 부하를 거는 동안 레플리카·스로틀·처리량을 기록한다.
#   IMAGE_REPO=<ecr-repo-uri> IMAGE_TAG=<tag> scripts/run_hpa.sh
set -euo pipefail

: "${IMAGE_REPO:?ECR 저장소 URI를 IMAGE_REPO로 지정한다}"
: "${IMAGE_TAG:?이미지 태그를 IMAGE_TAG로 지정한다}"
NAMESPACE="${NAMESPACE:-llm}"
RELEASE="${RELEASE:-llm}"
DEPLOY="${RELEASE}-llama-server"
PROM_URL="${PROM_URL:-http://localhost:9090}"
STEPS="${STEPS:-6,12,18,24}"
STEP_DURATION="${STEP_DURATION:-4m}"
RUN_ID="${RUN_ID:-hpa-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="results/${RUN_ID}"
mkdir -p "$OUT"

helm upgrade --install "$RELEASE" charts/llama-server -n "$NAMESPACE" --create-namespace \
  -f experiments/values-hpa.yaml \
  --set image.repository="$IMAGE_REPO" --set image.tag="$IMAGE_TAG" --wait --timeout 10m

# 서비스로 보내야 새로 Ready가 된 파드에도 요청이 나뉜다.
# port-forward는 파드 하나에 붙으므로 클러스터 안에서 k6를 돌린다.
kubectl -n "$NAMESPACE" create configmap k6-scripts \
  --from-file=load/k6/lib.js --from-file=load/k6/stepped.js --from-file=load/prompts.json \
  --dry-run=client -o yaml | kubectl apply -f -

uv run sllmbench-collect poll --namespace "$NAMESPACE" --deployment "$DEPLOY" \
  --prom-url "$PROM_URL" --out "${OUT}/cluster.csv" &
POLL_PID=$!
trap 'kill "$POLL_PID" 2>/dev/null || true' EXIT

kubectl -n "$NAMESPACE" delete pod k6 --ignore-not-found
kubectl -n "$NAMESPACE" run k6 --restart=Never --image=grafana/k6:2.3.0 \
  --overrides "$(cat <<JSON
{"spec":{"containers":[{"name":"k6","image":"grafana/k6:2.3.0",
  "workingDir":"/work/k6",
  "command":["sh","-c","mkdir -p /work/k6 && cp /scripts/lib.js /scripts/stepped.js /work/k6/ && cp /scripts/prompts.json /work/ && k6 run -q -e BASE_URL=http://${DEPLOY}:8080 -e STEPS=${STEPS} -e STEP_DURATION=${STEP_DURATION} -e SUMMARY_OUT=/work/summary.json --out csv=/work/k6.csv stepped.js; sleep 600"],
  "volumeMounts":[{"name":"scripts","mountPath":"/scripts"},{"name":"work","mountPath":"/work"}]}],
 "volumes":[{"name":"scripts","configMap":{"name":"k6-scripts"}},{"name":"work","emptyDir":{}}]}}
JSON
)"

# k6가 끝날 때까지 기다린 뒤(컨테이너는 결과를 꺼낼 수 있게 sleep으로 남겨 둔다) 결과를 복사한다.
kubectl -n "$NAMESPACE" wait --for=condition=Ready pod/k6 --timeout=5m
until kubectl -n "$NAMESPACE" exec k6 -- test -f /work/summary.json 2>/dev/null; do sleep 30; done
kubectl -n "$NAMESPACE" cp k6:/work/k6.csv "${OUT}/k6.csv"
kubectl -n "$NAMESPACE" cp k6:/work/summary.json "${OUT}/k6-summary.json"
kubectl -n "$NAMESPACE" delete pod k6

uv run sllmbench-collect timeline --cluster "${OUT}/cluster.csv" --k6 "${OUT}/k6.csv" \
  --out "${OUT}/timeline.csv"
echo "리포트: uv run sllmbench-report --timeline ${OUT}/timeline.csv --out reports/${RUN_ID}"
