#!/usr/bin/env bash
# 스레드 × CPU limit 격자. 칸마다 helm upgrade → 준비 대기 → 고정 동시성 부하 → grid.csv 한 줄.
# 전제: kubectl 컨텍스트가 측정 클러스터, kube-prometheus-stack 설치, 이미지가 ECR에 있음.
#   IMAGE_REPO=<ecr-repo-uri> IMAGE_TAG=<tag> scripts/run_grid.sh
set -euo pipefail

: "${IMAGE_REPO:?ECR 저장소 URI를 IMAGE_REPO로 지정한다}"
: "${IMAGE_TAG:?이미지 태그를 IMAGE_TAG로 지정한다}"
NAMESPACE="${NAMESPACE:-llm}"
RELEASE="${RELEASE:-llm}"
DEPLOY="${RELEASE}-llama-server"
THREADS="${THREADS:-1 2 4 8}"
LIMITS="${LIMITS:-1 2 4}"
QOS="${QOS:-Guaranteed}"          # Guaranteed: requests=limits, Burstable: requests=REQUEST_CPU
REQUEST_CPU="${REQUEST_CPU:-500m}"
PARALLEL="${PARALLEL:-2}"
VUS="${VUS:-$PARALLEL}"
DURATION="${DURATION:-3m}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
RUN_ID="${RUN_ID:-grid-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="results/${RUN_ID}"
mkdir -p "$OUT"

cleanup() {
  if [[ -n "${PF_PID:-}" ]]; then
    kill "$PF_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

for t in $THREADS; do
  for l in $LIMITS; do
    req="$l"
    [[ "$QOS" == "Burstable" ]] && req="$REQUEST_CPU"
    cell="t${t}-l${l}"
    echo "== ${cell} (${QOS})"
    helm upgrade --install "$RELEASE" charts/llama-server -n "$NAMESPACE" --create-namespace \
      --set image.repository="$IMAGE_REPO" --set image.tag="$IMAGE_TAG" \
      --set llama.threads="$t" --set llama.parallel="$PARALLEL" \
      --set resources.requests.cpu="$req" --set resources.limits.cpu="$l" \
      --set serviceMonitor.enabled=true --wait --timeout 10m

    cleanup
    kubectl -n "$NAMESPACE" port-forward "svc/${DEPLOY}" 8080:8080 >/dev/null 2>&1 &
    PF_PID=$!
    sleep 3

    uv run sllmbench-collect poll --namespace "$NAMESPACE" --deployment "$DEPLOY" \
      --prom-url "$PROM_URL" --out "${OUT}/${cell}-cluster.csv" &
    POLL_PID=$!
    k6 run -q -e BASE_URL=http://localhost:8080 -e VUS="$VUS" -e DURATION="$DURATION" \
      -e SUMMARY_OUT="${OUT}/${cell}-k6-summary.json" \
      --out csv="${OUT}/${cell}-k6.csv" load/k6/constant.js
    kill "$POLL_PID" 2>/dev/null || true

    uv run sllmbench-collect summarize --threads "$t" --cpu-limit "$l" --qos "$QOS" \
      --cluster "${OUT}/${cell}-cluster.csv" --k6 "${OUT}/${cell}-k6.csv" --out "${OUT}/grid.csv"
  done
done

echo "결과: ${OUT}/grid.csv"
echo "리포트: uv run sllmbench-report --grid ${OUT}/grid.csv --out reports/${RUN_ID}"
