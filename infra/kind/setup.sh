#!/usr/bin/env bash
# 로컬 kind 클러스터에 측정 환경을 만든다: 클러스터 → 이미지 빌드·반입 → metrics-server → Prometheus.
# 전제: docker 엔진(macOS는 colima 등), kind, kubectl, helm.
#   infra/kind/setup.sh
# 끝나면 Prometheus가 http://localhost:9090 으로 열린다. 정리는 kind delete cluster --name cpu-sllm.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLUSTER="${CLUSTER:-cpu-sllm}"
IMAGE_REPO="${IMAGE_REPO:-cpu-sllm-k8s}"
IMAGE_TAG="${IMAGE_TAG:-kind}"
KPS_VERSION="${KPS_VERSION:-92.3.1}"
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-3.14.0}"
ARCH="$(docker info --format '{{.Architecture}}' | sed 's/aarch64/arm64/; s/x86_64/amd64/')"

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config "$ROOT/infra/kind/cluster.yaml" --wait 120s
fi

# 레지스트리 없이 노드에 직접 넣는다. 차트는 experiments/values-kind.yaml의 pullPolicy: Never로 설치한다.
docker build --platform "linux/$ARCH" --provenance=false -t "${IMAGE_REPO}:${IMAGE_TAG}" "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
docker save --platform "linux/$ARCH" -o "$TMP/image.tar" "${IMAGE_REPO}:${IMAGE_TAG}"
kind load image-archive --name "$CLUSTER" "$TMP/image.tar"

# kind 노드의 kubelet 인증서에는 노드 IP SAN이 없어 metrics-server가 검증에 실패한다.
helm upgrade --install metrics-server metrics-server \
  --repo https://kubernetes-sigs.github.io/metrics-server/ --version "$METRICS_SERVER_VERSION" \
  -n kube-system --set 'args={--kubelet-insecure-tls}' --wait --timeout 5m

helm upgrade --install kps kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version "$KPS_VERSION" \
  -n monitoring --create-namespace \
  -f "$ROOT/infra/monitoring/kube-prometheus-stack-values.yaml" \
  -f "$ROOT/infra/monitoring/kube-prometheus-stack-values-kind.yaml" --wait --timeout 10m
kubectl -n monitoring rollout status statefulset/prometheus-kps-kube-prometheus-stack-prometheus --timeout 5m

echo "준비 완료. 실험 실행:"
echo "  export IMAGE_REPO=${IMAGE_REPO} IMAGE_TAG=${IMAGE_TAG} EXTRA_VALUES=experiments/values-kind.yaml"
echo "  scripts/run_grid.sh"
