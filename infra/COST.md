# 비용 메모

이 저장소는 아직 AWS에서 실행한 적이 없다. 아래 금액 칸은 일부러 비워 두었다. 요금은 리전과 시점에 따라 바뀌므로, 클러스터를 만들기 전에 공식 요금표에서 직접 확인해 채운다.

| 과금 항목 | 이 구성에서 생기는 것 | 확인할 곳 | 시간당 단가 |
|---|---|---|---|
| EKS 컨트롤 플레인 | 클러스터 1개 (표준 지원 버전 1.35) | https://aws.amazon.com/eks/pricing/ | 확인 전 |
| EC2 노드 | `c7i.2xlarge` × 2 (온디맨드) | https://aws.amazon.com/ec2/pricing/on-demand/ | 확인 전 |
| EBS | 노드 루트 볼륨 30GiB × 2 (gp3 기본값) | https://aws.amazon.com/ebs/pricing/ | 확인 전 |
| ECR | 모델 내장 이미지 저장 용량 | https://aws.amazon.com/ecr/pricing/ | 확인 전 |
| 퍼블릭 IPv4 | 노드마다 퍼블릭 IP 1개 | https://aws.amazon.com/vpc/pricing/ | 확인 전 |
| 데이터 전송 | 이미지 풀, Hugging Face에서 모델 받기(빌드 러너 쪽) | https://aws.amazon.com/ec2/pricing/on-demand/ | 확인 전 |

일부러 만들지 않는 것: NAT 게이트웨이(`vpc.nat.gateway: Disable`), LoadBalancer 서비스(서비스는 `ClusterIP`, 접근은 `kubectl port-forward`), CloudWatch 컨트롤 플레인 로그.

## 쓰는 방식

1. 측정 계획을 세우고 예상 시간을 정한다.
2. 위 표의 단가를 채워 `(컨트롤 플레인 + 노드 수 × 노드 단가 + …) × 시간`으로 상한을 잡는다.
3. AWS Budgets에 그 금액으로 알림을 걸어 둔다.
4. 측정이 끝난 날 `infra/teardown.sh`를 실행하고, 스크립트 끝의 확인 출력이 비어 있는지 본다.
