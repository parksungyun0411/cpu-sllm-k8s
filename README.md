# CPU 전용 쿠버네티스 소형 LLM 서빙

[![CI](https://github.com/parksungyun0411/cpu-sllm-k8s/actions/workflows/ci.yml/badge.svg)](https://github.com/parksungyun0411/cpu-sllm-k8s/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white) ![Kubernetes](https://img.shields.io/badge/kubernetes-1.35-326CE5?logo=kubernetes&logoColor=white) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

GPU가 없는 쿠버네티스 노드에서 4bit 양자화 소형 LLM을 llama.cpp 서버로 서빙하고, CPU limit·스레드 수·CFS 스로틀링이 생성 처리량을 얼마나 줄이는지, HPA가 계단식 부하에 어떻게 반응하는지 측정하기 위한 프로젝트입니다.

> **진행 중입니다.** 이미지, Helm 차트, 배포 파이프라인, 부하 스크립트, 수집기와 리포트 생성기까지 구현했습니다. EKS 배포와 측정은 아직 실행하지 않았으며, 아래 평가 표는 모두 비어 있습니다.

CPU 추론은 GPU 서빙과 쿠버네티스 설정이 맞물리는 방식이 다릅니다. 추론이 CPU에 묶여 있으므로 HPA의 기본 지표인 CPU 사용률이 실제 부하를 따라가는지 확인할 수 있고, CPU limit을 스레드 수보다 낮게 주면 CFS 쿼터를 소진한 스레드가 주기 끝까지 멈추기 때문에 requests·limits 설정이 처리량 차이로 드러납니다. 모델 적재에도 시간이 걸려 startup·readiness 프로브와 롤링 업데이트 설정이 실제 동작에 영향을 줍니다. 이 세 가지를 같은 이미지와 같은 부하 조건에서 수치로 비교하는 것이 목표입니다.

## 구현 범위

- 멀티스테이지 Dockerfile: 1단계에서 GGUF 모델을 Hugging Face 고정 커밋에서 받아 sha256을 검증하고, 2단계에서 llama.cpp 공식 CPU 서버 이미지(digest 고정)에 넣어 외부 네트워크 없이 기동되는 이미지 구성
- Helm 차트 `charts/llama-server`: Deployment, Service, ConfigMap(`LLAMA_ARG_*`), requests·limits, 모델 적재 시간을 고려한 startup·readiness·liveness 프로브, `autoscaling/v2` HPA, 선택형 ServiceMonitor
- eksctl 클러스터 설정(관리형 노드 그룹 1개, NAT 게이트웨이 없음, metrics-server 애드온)과 정리 스크립트, 과금 항목 메모
- GitHub OIDC 역할·ECR 저장소·EKS 접근 항목 생성 스크립트(계정 ID는 실행 시 조회, 저장소에 기록하지 않음)
- GitHub Actions: push마다 도는 CI(pytest, ruff, `helm lint`, `helm template | kubeconform`, hadolint, shellcheck, actionlint, `k6 inspect`)와 수동 실행 전용 배포 워크플로(빌드 → ECR 푸시 → Helm 배포)
- k6 부하 스크립트: 격자 실험용 고정 동시성(`constant.js`), HPA 실험용 계단식 도착률(`stepped.js`)
- 측정 수집기 `sllmbench-collect`: kubectl 레플리카 상태와 Prometheus 질의(CFS 스로틀 비율, CPU 사용량, 서버 측 생성 토큰 처리량, 대기 요청 수)를 주기적으로 기록하고 k6 결과와 시간축으로 병합해 CSV 생성
- 리포트 생성기 `sllmbench-report`: CSV를 히트맵·시계열 그림과 Markdown 표로 변환해 `reports/<run-id>/`에 저장
- 스레드 × CPU limit 격자 실험과 HPA 계단 부하 실험 실행 스크립트

## 아키텍처

<img src="docs/diagrams/architecture.png" alt="cpu-sllm-k8s 빌드·배포·측정 흐름" width="900">

다이어그램 원본은 [docs/diagrams/architecture.html](docs/diagrams/architecture.html)입니다.

| 단계 | 구성 | 근거 파일 |
|---|---|---|
| 이미지 | Qwen2.5-1.5B-Instruct Q4_K_M + `ghcr.io/ggml-org/llama.cpp:server-b11347` | [Dockerfile](Dockerfile) |
| 배포 | Helm 차트, 릴리스 이름 `llm`, 네임스페이스 `llm` | [charts/llama-server/](charts/llama-server/) |
| 클러스터 | EKS 1.35, 서울 리전, `c7i.2xlarge` 관리형 노드 그룹(최대 2대) | [infra/eksctl/cluster.yaml](infra/eksctl/cluster.yaml) |
| 부하 | k6 고정 동시성(port-forward) · 계단식 도착률(클러스터 내부 파드 → Service) | [load/k6/](load/k6/) |
| 수집 | cAdvisor `container_cpu_cfs_*`, llama-server `llamacpp:*`, kubectl 레플리카 | [src/sllmbench/prom.py](src/sllmbench/prom.py) |

## 기술 스택

| 영역 | 기술 |
|---|---|
| 추론 런타임 | ![llama.cpp](https://img.shields.io/badge/llama.cpp-server-5B6770) ![GGUF](https://img.shields.io/badge/GGUF-Q4__K__M-5B6770) |
| 컨테이너·오케스트레이션 | ![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white) ![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white) ![Helm](https://img.shields.io/badge/Helm-0F1689?logo=helm&logoColor=white) |
| 클라우드 | ![Amazon EKS](https://img.shields.io/badge/Amazon_EKS-FF9900?logo=amazoneks&logoColor=white) ![Amazon ECR](https://img.shields.io/badge/Amazon_ECR-FF9900?logo=amazonaws&logoColor=white) ![eksctl](https://img.shields.io/badge/eksctl-5B6770) |
| CI/CD | ![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white) |
| 부하·관측 | ![k6](https://img.shields.io/badge/k6-7D64FF?logo=k6&logoColor=white) ![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?logo=prometheus&logoColor=white) |
| 분석·리포트 | ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![pandas](https://img.shields.io/badge/pandas-150458?logo=pandas&logoColor=white) ![Matplotlib](https://img.shields.io/badge/Matplotlib-11557C) |
| 코드 품질 | ![pytest](https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white) ![Ruff](https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black) ![kubeconform](https://img.shields.io/badge/kubeconform-5B6770) ![hadolint](https://img.shields.io/badge/hadolint-5B6770) |

## 실행 방법

### 로컬 검증

클러스터 없이 확인할 수 있는 범위입니다. 필요한 도구는 uv, helm, kubeconform, hadolint, k6입니다.

```bash
uv sync
uv run pytest -q
helm lint charts/llama-server --strict
helm template llm charts/llama-server | kubeconform -strict -summary -kubernetes-version 1.35.0
hadolint Dockerfile
k6 inspect load/k6/constant.js
```

테스트는 `tests/fixtures/`의 합성 CSV로 수집기와 리포트 생성기의 계산을 검증합니다. 합성 데이터는 손으로 검산할 수 있게 만든 값이며 측정 결과가 아닙니다.

### EKS 측정

필요한 것은 AWS 계정과 관리자 자격 증명, aws CLI v2, eksctl, kubectl, helm, k6, uv입니다. 클러스터를 만들기 전에 [infra/COST.md](infra/COST.md)의 단가 칸을 현재 공식 요금표로 채우고 예산 알림을 걸어 둡니다.

```bash
# 1. 클러스터 생성 (metrics-server 애드온 포함)
eksctl create cluster -f infra/eksctl/cluster.yaml

# 2. GitHub OIDC 역할, ECR 저장소, 네임스페이스, EKS 접근 항목 생성
infra/iam/setup-github-oidc.sh
#    출력 안내에 따라 저장소 secret AWS_ROLE_ARN 등록

# 3. 이미지 빌드·ECR 푸시·배포 (수동 실행)
gh workflow run deploy.yml
gh run watch

# 4. Prometheus 설치와 포트 포워딩
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  -f infra/monitoring/kube-prometheus-stack-values.yaml
kubectl -n monitoring port-forward svc/prometheus-operated 9090:9090 &

# 5. 실험 (IMAGE_REPO는 ECR 저장소 URI, IMAGE_TAG는 배포한 커밋 SHA)
export IMAGE_REPO=<ecr-repo-uri> IMAGE_TAG=<commit-sha>
scripts/run_grid.sh                    # threads 1 2 4 8 × CPU limit 1 2 4, Guaranteed
QOS=Burstable scripts/run_grid.sh      # requests 500m, 같은 격자
scripts/run_hpa.sh                     # experiments/values-hpa.yaml, 계단 부하

# 6. 리포트 생성
uv run sllmbench-report --grid results/<grid-run>/grid.csv --out reports/<grid-run>
uv run sllmbench-report --timeline results/<hpa-run>/timeline.csv --out reports/<hpa-run>

# 7. 당일 정리
infra/teardown.sh          # 클러스터 삭제
infra/teardown.sh --all    # ECR 저장소와 배포 역할까지 삭제
```

격자 범위와 부하 조건은 환경변수로 바꿉니다.

| 실험 | 환경변수 |
|---|---|
| 격자 | `THREADS`, `LIMITS`, `QOS`, `REQUEST_CPU`, `PARALLEL`, `VUS`, `DURATION` |
| HPA | `STEPS`(분당 요청 수), `STEP_DURATION` |

스크립트의 기본값은 부하 모양을 정하는 입력이며, 처리 가능량을 예상한 값이 아닙니다.

## 평가

**측정 전입니다.** 측정을 마치면 각 값의 출처가 되는 `reports/<run-id>/REPORT.md`를 표 아래에 함께 적습니다.

스레드 × CPU limit 격자(Guaranteed, 고정 동시성), 칸마다 생성 tokens/s:

| threads \ CPU limit | 1 | 2 | 4 |
|---|---:|---:|---:|
| 1 | | | |
| 2 | | | |
| 4 | | | |
| 8 | | | |

같은 격자에서 CFS 스로틀 비율과 p95 지연도 기록하며, Burstable(requests 500m)도 같은 형식으로 정리합니다.

HPA 계단 부하:

| 지표 | 값 |
|---|---:|
| 계단별 Ready 레플리카 수 | |
| 스케일아웃 소요 시간(HPA 목표 변경 → 파드 Ready) | |
| 계단별 p95 지연 | |
| 계단별 생성 tokens/s | |
| 오류율 | |

### 산출물 규칙

측정 한 번이 `reports/<run-id>/` 폴더 하나가 됩니다. 원자료는 `results/<run-id>/`에 남고 커밋하지 않습니다. 자세한 규칙은 [reports/README.md](reports/README.md)에 있습니다.

| 파일 | 내용 |
|---|---|
| `results/<run-id>/*-k6.csv` | k6 `--out csv` 원자료 |
| `results/<run-id>/*-cluster.csv` | 수집기 샘플: 레플리카, HPA 목표, CPU 코어, 스로틀 비율, 서버 tokens/s, 대기 요청 |
| `results/<run-id>/grid.csv` | 격자 한 칸당 한 줄: threads, cpu_limit, qos, tokens/s, 스로틀 비율, p95, 오류율 |
| `results/<run-id>/timeline.csv` | 15초 구간별: Ready 레플리카, HPA 목표, req/s, p95, tokens/s, 스로틀 비율 |
| `reports/<run-id>/REPORT.md` | 위 CSV로 만든 표와 그림 |

스로틀 비율은 `container_cpu_cfs_throttled_periods_total`과 `container_cpu_cfs_periods_total`의 1분 rate 비율이고, 서버 측 처리량은 `llamacpp:tokens_predicted_total`의 rate입니다. 질의 원문은 [src/sllmbench/prom.py](src/sllmbench/prom.py)에 있습니다.

## 프로젝트 구조

```text
cpu-sllm-k8s/
├── Dockerfile              모델 내장 멀티스테이지 이미지
├── charts/llama-server/    Helm 차트
├── experiments/            실험별 values 덮어쓰기
├── infra/
│   ├── eksctl/             클러스터 설정
│   ├── iam/                GitHub OIDC 역할·ECR·접근 항목 생성
│   ├── monitoring/         kube-prometheus-stack 값
│   ├── teardown.sh         정리 스크립트
│   └── COST.md             과금 항목과 확인할 요금표
├── load/                   k6 스크립트와 부하용 질문
├── scripts/                격자·HPA 실험 실행
├── src/sllmbench/          수집기와 리포트 생성기
├── tests/                  단위 테스트와 합성 픽스처
├── reports/                측정 리포트(아직 없음)
├── docs/diagrams/          아키텍처 다이어그램(HTML 원본·PNG)
└── .github/workflows/      ci.yml, deploy.yml
```

## 구현하면서 신경 쓴 점

- 모델은 런타임 다운로드 대신 이미지에 넣었습니다. 망이 분리된 환경에서는 파드 기동 시 외부에서 모델을 받을 수 없기 때문입니다. 이미지 크기가 커지는 비용을 감수하고, 같은 파일이 들어가도록 Hugging Face 커밋과 sha256을 고정했습니다.
- llama-server 설정은 ConfigMap의 `LLAMA_ARG_*` 환경변수로 넘깁니다. 격자 실험에서 스레드 수만 바꿔 재배포할 수 있고, ConfigMap 체크섬을 파드 어노테이션에 넣어 값이 바뀌면 파드가 새로 뜹니다. 환경변수 이름은 고정한 빌드(`b11347`)의 `common/arg.cpp`에서 확인했으며, `--threads-batch`는 대응하는 환경변수가 없어 컨테이너 인자로 넘깁니다.
- 프로브는 역할을 나눴습니다. llama-server의 `/health`는 모델 적재 중 503, 준비 후 200을 반환합니다. startupProbe가 `values.yaml` 기준 최대 300초(5초 × 60회)까지 기다린 뒤 liveness가 넘겨받고, 생성 중 HTTP 응답이 늦어 파드가 재시작되지 않도록 liveness의 타임아웃과 실패 허용 횟수를 readiness보다 넉넉하게 잡았습니다.
- 롤링 업데이트는 `maxSurge: 1`, `maxUnavailable: 0`으로 두어 새 파드가 Ready가 되기 전에 기존 파드를 내리지 않습니다.
- HPA 실험의 기본 설정은 requests와 limits를 같게 둔 Guaranteed입니다. HPA 사용률은 requests 대비 비율이라 requests를 낮추면 같은 부하에서도 사용률이 높게 계산되므로, Burstable 비교는 별도 실행으로 분리했습니다.
- 배포 워크플로는 `workflow_dispatch`로만 실행됩니다. 클러스터가 떠 있는 시간에만 비용이 생기기 때문입니다. 장기 액세스 키 대신 GitHub OIDC로 IAM 역할을 맡고, 신뢰 정책은 이 저장소의 `main` 브랜치로, 쿠버네티스 권한은 `llm` 네임스페이스의 edit으로 제한했습니다. secret이 없으면 첫 job에서 이유를 출력하고 중단합니다.
- 서비스는 ClusterIP로 두고 LoadBalancer를 만들지 않았습니다. port-forward는 파드 하나에만 연결되므로, 레플리카가 늘어나는 HPA 실험은 클러스터 안에서 k6 파드를 띄워 Service로 요청을 보냅니다.

## 한계

- EKS 배포, 이미지 빌드, IAM 스크립트, 실험 스크립트는 한 번도 실행하지 않았습니다. 로컬에서 확인한 범위는 lint, 매니페스트 스키마 검증, 단위 테스트, k6 스크립트 파싱까지입니다. 작업 환경에 Docker가 없어 이미지도 로컬에서 빌드해 보지 않았습니다.
- 측정 결과가 없으므로 이 저장소는 CPU limit이나 스레드 수가 처리량에 미치는 영향에 대해 아직 아무것도 보여 주지 않습니다.
- HPA는 CPU 사용률 기준 한 종류만 구현했습니다. 대기 요청 수(`llamacpp:requests_deferred`) 기준 HPA는 Prometheus Adapter나 KEDA가 필요해 포함하지 않았습니다.
- 노드 수는 최대 2대로 고정했고 Cluster Autoscaler나 Karpenter를 쓰지 않습니다. 노드 자원이 부족하면 HPA가 늘린 파드는 Pending으로 남으므로, 측정 곡선은 이 노드 범위 안에서만 해석할 수 있습니다.
- `c7i.2xlarge`의 서울 리전 제공 여부와 요금은 확인하지 않았습니다.
- 부하용 질문은 직접 작성한 12개이며 공공 FAQ 원문이 아닙니다. 응답 품질은 평가하지 않습니다.
- 롤링 업데이트 중 실패 요청 수, 메모리·OOM 경계, EC2·ECS와의 비교는 계획에만 있고 구현하지 않았습니다.
- 수집기는 15초 간격 샘플이라 그보다 짧은 변화는 기록하지 못합니다.

## 라이선스

코드는 [MIT](LICENSE) 라이선스를 따릅니다. 이미지 빌드 시 받는 모델 [Qwen/Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)는 Apache-2.0이며 저장소에는 포함하지 않습니다. 런타임 llama.cpp는 MIT 라이선스입니다.
