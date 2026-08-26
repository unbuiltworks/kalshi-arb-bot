#!/usr/bin/env bash
set -euo pipefail

# Run once in AWS CloudShell as an AWS principal allowed to manage IAM.
# Creates a GitHub Actions OIDC provider if absent and a narrowly scoped role
# that can send/read SSM commands only for the ARB hot EC2 instance.

REGION="${AWS_REGION:-us-east-1}"
INSTANCE_ID="${AWS_ARB_HOT_INSTANCE_ID:-i-0b90f0a309153f47e}"
GITHUB_REPO="${GITHUB_REPO:-unbuiltworks/kalshi-arb-bot}"
GITHUB_BRANCH="${GITHUB_BRANCH:-aws-migration-foundation}"
ROLE_NAME="${ROLE_NAME:-arb-github-ssm-deployer}"
OIDC_URL="https://token.actions.githubusercontent.com"
OIDC_HOST="token.actions.githubusercontent.com"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
INSTANCE_ARN="arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

if ! aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}" \
  >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "$OIDC_URL" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
    >/dev/null
fi

TRUST_FILE="$(mktemp)"
POLICY_FILE="$(mktemp)"
trap 'rm -f "$TRUST_FILE" "$POLICY_FILE"' EXIT

cat > "$TRUST_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_HOST}:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "${OIDC_HOST}:sub": "repo:${GITHUB_REPO}:ref:refs/heads/${GITHUB_BRANCH}"
        }
      }
    }
  ]
}
EOF

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-document "file://${TRUST_FILE}"
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://${TRUST_FILE}" \
    --description "GitHub Actions SSM deployer for ARB AWS hot host" \
    >/dev/null
fi

cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SendRunShellScriptToArbHotHost",
      "Effect": "Allow",
      "Action": ["ssm:SendCommand"],
      "Resource": [
        "${INSTANCE_ARN}",
        "arn:aws:ssm:${REGION}::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "ReadCommandResults",
      "Effect": "Allow",
      "Action": [
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name arb-hot-host-ssm \
  --policy-document "file://${POLICY_FILE}"

printf '\nAWS_GITHUB_OIDC_READY\n'
printf 'AWS_ARB_DEPLOY_ROLE_ARN=%s\n' "$ROLE_ARN"
printf 'AWS_ARB_REGION=%s\n' "$REGION"
printf 'AWS_ARB_HOT_INSTANCE_ID=%s\n' "$INSTANCE_ID"
printf '\nAdd those three values as GitHub Actions repository variables (not secrets).\n'
