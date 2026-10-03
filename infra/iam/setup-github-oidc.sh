#!/usr/bin/env bash
# GitHub Actions가 장기 키 없이 OIDC로 맡을 IAM 역할과 ECR 저장소, EKS 접근 항목을 만든다.
# 클러스터(infra/eksctl/cluster.yaml)를 만든 뒤 관리자 자격 증명으로 한 번 실행한다.
# 계정 ID는 실행 시점에 조회하며 저장소에 남기지 않는다.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
CLUSTER="${EKS_CLUSTER_NAME:-cpu-sllm}"
ECR_REPO="${ECR_REPOSITORY:-cpu-sllm-k8s}"
GITHUB_REPO="${GITHUB_REPO:-parksungyun0411/cpu-sllm-k8s}"
ROLE_NAME="${ROLE_NAME:-cpu-sllm-k8s-github-deploy}"
NAMESPACE="${NAMESPACE:-llm}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
OIDC_HOST="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"

if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "https://${OIDC_HOST}" \
    --client-id-list sts.amazonaws.com
fi

# main 브랜치에서 돈 워크플로만 역할을 맡을 수 있다.
TRUST="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "${OIDC_HOST}:aud": "sts.amazonaws.com",
        "${OIDC_HOST}:sub": "repo:${GITHUB_REPO}:ref:refs/heads/main"
      }
    }
  }]
}
JSON
)"

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
else
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" >/dev/null
fi

aws ecr describe-repositories --region "$REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --region "$REGION" --repository-name "$ECR_REPO" \
       --image-scanning-configuration scanOnPush=true >/dev/null

ECR_ARN="arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/${ECR_REPO}"
CLUSTER_ARN="arn:aws:eks:${REGION}:${ACCOUNT_ID}:cluster/${CLUSTER}"

# 이미지 푸시와 kubeconfig 생성에 필요한 권한만 준다.
POLICY="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Effect": "Allow",
     "Action": ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
                "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
                "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"],
     "Resource": "${ECR_ARN}"},
    {"Effect": "Allow", "Action": "eks:DescribeCluster", "Resource": "${CLUSTER_ARN}"}
  ]
}
JSON
)"
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name deploy --policy-document "$POLICY"

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# 쿠버네티스 권한은 측정용 네임스페이스의 edit로 제한한다. 네임스페이스는 관리자 권한으로 미리 만든다.
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
aws eks create-access-entry --region "$REGION" --cluster-name "$CLUSTER" \
  --principal-arn "$ROLE_ARN" >/dev/null 2>&1 || true
aws eks associate-access-policy --region "$REGION" --cluster-name "$CLUSTER" \
  --principal-arn "$ROLE_ARN" \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy \
  --access-scope "type=namespace,namespaces=${NAMESPACE}" >/dev/null

echo "역할을 만들었다. 다음 값을 GitHub 저장소 설정에 넣는다."
echo "  secret   AWS_ROLE_ARN = (아래 명령으로 확인)"
echo "           aws iam get-role --role-name ${ROLE_NAME} --query Role.Arn --output text"
echo "  variable AWS_REGION = ${REGION}"
echo "  variable EKS_CLUSTER_NAME = ${CLUSTER}"
echo "  variable ECR_REPOSITORY = ${ECR_REPO}"
