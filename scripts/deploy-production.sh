#!/usr/bin/env bash
# GitHub Actions에서 main만 전용 운영 리소스에 배포한다.
set -euo pipefail

fail() { echo "::error::$*" >&2; exit 1; }
[[ "${GITHUB_EVENT_NAME:-}" == push && "${GITHUB_REF:-}" == refs/heads/main ]] || fail 'Production deployment requires a push to main.'
for key in AWS_REGION AWS_ACCOUNT_ID ECR_REPOSITORY INSTANCE_ID SSM_DOCUMENT AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY GH_TOKEN GITHUB_SHA GITHUB_REPOSITORY; do
  [[ -n "${!key:-}" ]] || fail "Missing production configuration: $key"
done
[[ "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || fail 'Invalid AWS account ID.'
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] || fail 'Invalid commit SHA.'
[[ "$INSTANCE_ID" =~ ^i-[0-9a-f]+$ ]] || fail 'Invalid instance ID.'
[[ "$AWS_REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || fail 'Invalid AWS region.'
[[ "$ECR_REPOSITORY" =~ ^[a-z0-9][a-z0-9._/-]*$ ]] || fail 'Invalid ECR repository.'
[[ "$SSM_DOCUMENT" =~ ^[a-zA-Z0-9_.-]+$ ]] || fail 'Invalid SSM document.'
# 잘못된 변수 복사로 현재 개발 리소스를 재사용하지 못하게 한다.
[[ "$INSTANCE_ID" != i-07799fedd264604b9 ]] || fail 'Development EC2 cannot be a production target.'
[[ "$ECR_REPOSITORY" != yeongeopjung-dev-api ]] || fail 'Use a dedicated production ECR repository.'
[[ "$SSM_DOCUMENT" != yeongeopjung-dev-deploy ]] || fail 'Use a dedicated production SSM document.'
export AWS_DEFAULT_REGION="$AWS_REGION" AWS_PAGER=''

is_current_main() {
  local latest
  latest=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')
  if [[ "$latest" != "$GITHUB_SHA" ]]; then
    echo "Skipped stale commit $GITHUB_SHA; main is now $latest."
    return 1
  fi
}
is_current_main || exit 0
account=$(aws sts get-caller-identity --query Account --output text)
[[ "$account" == "$AWS_ACCOUNT_ID" ]] || fail 'AWS credentials belong to a different account.'
host=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --output json)
jq -e '.Reservations[].Instances[] | select(.State.Name == "running") | select(any(.Tags[]?; .Key == "Environment" and .Value == "production")) | select(any(.Tags[]?; .Key == "Project" and .Value == "yeongeopjung"))' <<< "$host" >/dev/null || fail 'Target must be a running yeongeopjung production instance.'

work=$(mktemp -d)
export DOCKER_CONFIG="$work/docker"
trap 'rm -rf -- "$work"' EXIT
# 실행할 SSM 문서의 기본 버전을 숫자로 고정하고 저장소 문서와 대조한다.
version=$(aws ssm describe-document --name "$SSM_DOCUMENT" --query Document.DefaultVersion --output text)
[[ "$version" =~ ^[0-9]+$ ]] || fail 'Could not resolve SSM document version.'
aws ssm get-document --name "$SSM_DOCUMENT" --document-version "$version" --query Content --output text | jq -S . > "$work/actual.json"
jq -S . infra/ssm/deploy-production.json > "$work/expected.json"
cmp -s "$work/actual.json" "$work/expected.json" || fail 'Production SSM document differs from the reviewed repository version.'

registry="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
image="$registry/$ECR_REPOSITORY:$GITHUB_SHA"
aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" --output json | jq -e '.repositories[0].imageTagMutability == "IMMUTABLE"' >/dev/null || fail 'Production ECR tags must be immutable.'
digest=$(aws ecr batch-get-image --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$GITHUB_SHA" --query 'images[0].imageId.imageDigest' --output text)
if [[ "$digest" == None ]]; then
  docker build --platform linux/amd64 --tag "$image" .
  aws ecr get-login-password | docker login --username AWS --password-stdin "$registry"
  docker push "$image"
  digest=$(aws ecr batch-get-image --repository-name "$ECR_REPOSITORY" --image-ids "imageTag=$GITHUB_SHA" --query 'images[0].imageId.imageDigest' --output text)
fi
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'Could not verify the published image digest.'
is_current_main || exit 0
parameters=$(jq -nc --arg sha "$GITHUB_SHA" --arg account "$AWS_ACCOUNT_ID" --arg region "$AWS_REGION" --arg repository "$ECR_REPOSITORY" '{ImageTag:[$sha],AwsAccountId:[$account],AwsRegion:[$region],EcrRepository:[$repository]}')
command_id=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name "$SSM_DOCUMENT" --document-version "$version" --parameters "$parameters" --timeout-seconds 60 --query Command.CommandId --output text)
echo "Production SSM command: $command_id; image: $image; digest: $digest"
for attempt in {1..110}; do
  if status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$INSTANCE_ID" --query Status --output text 2>&1); then
    case "$status" in
      Success)
        echo "Production deployed and passed API/database health: $GITHUB_SHA"
        if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
          printf 'Production commit: `%s`\n\nImage: `%s`\n\nDigest: `%s`\n\nSSM command: `%s` (document version `%s`)\n' "$GITHUB_SHA" "$image" "$digest" "$command_id" "$version" >> "$GITHUB_STEP_SUMMARY"
        fi
        exit 0
        ;;
      Pending|InProgress|Delayed|Cancelling) ;;
      *) fail "SSM command $command_id finished with $status; inspect its server-side output." ;;
    esac
  elif [[ "$status" != *InvocationDoesNotExist* ]]; then
    fail "Could not read SSM command $command_id."
  fi
  sleep 5
done
fail "Timed out waiting for SSM command $command_id; it may still be running. Inspect SSM before retrying."
