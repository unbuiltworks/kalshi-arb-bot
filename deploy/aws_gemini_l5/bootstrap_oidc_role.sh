#!/usr/bin/env bash
set -euo pipefail

ACCOUNT_ID="318017571498"
REGION="us-east-1"
INSTANCE_ID="i-0b90f0a309153f47e"
BUCKET="kalshi-arb-bot-deploy-318017571498"
ROLE_NAME="arb-github-gemini-l5-deploy"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
REPO="unbuiltworks/kalshi-arb-bot"
BRANCH="feature/l5-gemini-queue-shadow"

ACTUAL_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
[[ "$ACTUAL_ACCOUNT" == "$ACCOUNT_ID" ]] || { echo "Refusing: expected AWS account $ACCOUNT_ID, got $ACTUAL_ACCOUNT" >&2; exit 1; }
aws configure set region "$REGION"

# Dedicated private Gemini deployment/state bucket. The account-ID suffix keeps
# the globally unique S3 name deterministic. Create it only if it does not yet
# exist in this account.
if ! aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "Creating private Gemini bucket $BUCKET..."
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled

if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  echo "Missing GitHub OIDC provider $OIDC_ARN; refusing to create a second provider automatically." >&2
  exit 1
fi

cat >/tmp/gemini-l5-trust.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "$OIDC_ARN"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": [
        "repo:${REPO}:ref:refs/heads/${BRANCH}",
        "repo:${REPO}:environment:gemini-l5-aws"
      ]}
    }
  }]
}
JSON

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document file:///tmp/gemini-l5-trust.json
else
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document file:///tmp/gemini-l5-trust.json >/dev/null
fi

cat >/tmp/gemini-l5-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GeminiDeployArtifacts",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::${BUCKET}",
        "arn:aws:s3:::${BUCKET}/gemini-l5/*"
      ]
    },
    {
      "Sid": "GeminiDeployViaSsm",
      "Effect": "Allow",
      "Action": ["ssm:SendCommand"],
      "Resource": [
        "arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}",
        "arn:aws:ssm:${REGION}::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "GeminiReadSsmCommandResults",
      "Effect": "Allow",
      "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"],
      "Resource": "*"
    }
  ]
}
JSON
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "gemini-l5-deploy" --policy-document file:///tmp/gemini-l5-policy.json

echo "Checking SSM registration for ${INSTANCE_ID}..."
PING="$(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$INSTANCE_ID" --query 'InstanceInformationList[0].PingStatus' --output text)"
[[ "$PING" == "Online" ]] || { echo "Instance $INSTANCE_ID is not SSM Online (status=$PING)" >&2; exit 1; }

echo "Bootstrap complete"
echo "ROLE_ARN=$ROLE_ARN"
echo "INSTANCE_ID=$INSTANCE_ID"
echo "REGION=$REGION"
echo "DEPLOY_BUCKET=$BUCKET"
echo "STATE_URI=s3://${BUCKET}/gemini-l5/state"
