#!/usr/bin/env bash
# 실행 환경을 명령 출력에서 읽어 Markdown으로 찍는다. 손으로 옮겨 적지 않으려고 둔 스크립트다.
# 실험 스크립트가 results/<run-id>/conditions.md로 저장하고, sllmbench-report --conditions 가 REPORT.md에 그대로 넣는다.
#   scripts/run_conditions.sh "THREADS=1 2 4 8" "LIMITS=1 2 4" > results/<run-id>/conditions.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAMESPACE="${NAMESPACE:-llm}"
RELEASE="${RELEASE:-llm}"
DEPLOY="${RELEASE}-llama-server"
CONTEXT="$(kubectl config current-context)"

echo "## 실행 조건"
echo
echo "- 기록 시각: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for kv in "$@"; do echo "- 입력: \`${kv}\`"; done
echo "- kubectl 컨텍스트: \`${CONTEXT}\`"
echo "- Kubernetes: $(kubectl version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.nodeInfo.architecture}{"\t"}{.status.nodeInfo.kernelVersion}{"\t"}{.status.nodeInfo.containerRuntimeVersion}{"\t"}{.status.allocatable.cpu}{"\t"}{.status.allocatable.memory}{"\n"}{end}' \
  | while IFS=$'\t' read -r name arch kernel runtime cpu mem; do
      policy="$(kubectl get --raw "/api/v1/nodes/${name}/proxy/configz" \
        | python3 -c 'import json,sys; c=json.load(sys.stdin)["kubeletconfig"]; print(c.get("cpuManagerPolicy"), c.get("cpuCFSQuotaPeriod"))')"
      echo "- 노드 \`${name}\`: ${arch}, 커널 ${kernel}, ${runtime}, allocatable cpu ${cpu} · memory ${mem}, kubelet cpuManagerPolicy·CFS 주기 ${policy}"
    done
echo "- 파드 이미지: \`$(kubectl -n "$NAMESPACE" get deploy "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[0].image}')\`, 노드의 imageID \`$(kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/instance=${RELEASE}" -o jsonpath='{.items[0].status.containerStatuses[0].imageID}')\`"
echo "- llama-server: $(kubectl -n "$NAMESPACE" exec "deploy/${DEPLOY}" -- /app/llama-server --version 2>&1 | tr '\n' ' ')"
echo "- 베이스 이미지(Dockerfile): \`$(sed -n 's/^ARG LLAMA_IMAGE=//p' "$ROOT/Dockerfile")\`"
echo "- 모델(Dockerfile): $(sed -n 's/^ARG MODEL_REPO=//p' "$ROOT/Dockerfile" | head -1) \`$(sed -n 's/^ARG MODEL_FILE=//p' "$ROOT/Dockerfile" | head -1)\`, 커밋 \`$(sed -n 's/^ARG MODEL_REVISION=//p' "$ROOT/Dockerfile" | head -1)\`, sha256 \`$(sed -n 's/^ARG MODEL_SHA256=//p' "$ROOT/Dockerfile")\`"
# 커밋하지 않은 변경이 측정 코드인지 문서인지 구분할 수 있게 경로를 함께 남긴다.
dirty="$(git -C "$ROOT" status --porcelain | awk '{print $2}' | head -10 | paste -sd ',' - | sed 's/,/, /g')"
echo "- 저장소 커밋: \`$(git -C "$ROOT" rev-parse --short HEAD)\`$(if [[ -n "$dirty" ]]; then echo " (커밋하지 않은 변경: ${dirty})"; fi)"
echo "- 부하 발생 호스트: $(uname -sm), k6 $(k6 version | awk '{print $2}')"
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "- 호스트 CPU: $(sysctl -n machdep.cpu.brand_string), 성능 코어 $(sysctl -n hw.perflevel0.physicalcpu) + 효율 코어 $(sysctl -n hw.perflevel1.physicalcpu), 메모리 $(( $(sysctl -n hw.memsize) / 1073741824 ))GiB, macOS $(sw_vers -productVersion)"
fi
if [[ "$CONTEXT" == kind-* ]]; then
  node="${CONTEXT#kind-}-control-plane"
  echo "- kind: $(kind version | awk '{print $2}'), 노드 이미지 \`$(docker inspect -f '{{.Config.Image}}' "$node")\`"
  image="$(kubectl -n "$NAMESPACE" get deploy "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "- 로컬 이미지 ID(kind load 원본): \`$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || echo '확인 불가')\`"
  echo "- docker 엔진: $(docker version --format '{{.Server.Version}}'), CPU $(docker info --format '{{.NCPU}}')개, 메모리 $(( $(docker info --format '{{.MemTotal}}') / 1048576 ))MiB"
  if command -v colima >/dev/null && colima status >/dev/null 2>&1; then
    echo "- colima: $(colima version | awk 'NR==1 {print $3}'), $(colima status --json | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["driver"], "vCPU", s["cpu"], "메모리", s["memory"] // 2**30, "GiB", "디스크", s["disk"] // 2**30, "GiB")')"
  fi
fi
echo
echo "Helm 릴리스:"
echo
echo '```text'
helm list -A --time-format '2006-01-02T15:04Z07:00' | awk -F'\t' '{print $1 "\t" $2 "\t" $6 "\t" $7}' | sed 's/[[:space:]]*$//'
echo '```'
echo
echo "\`helm get values ${RELEASE}\` (기록 시점의 배포 값. 격자 실험에서는 첫 칸의 값이며 threads와 CPU는 칸마다 바뀐다):"
echo
echo '```yaml'
helm get values "$RELEASE" -n "$NAMESPACE" | sed '1d'
echo '```'
