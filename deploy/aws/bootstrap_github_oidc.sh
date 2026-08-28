#!/usr/bin/env bash
set -euo pipefail

# Run in AWS CloudShell (or another AWS principal allowed to manage IAM).
# Creates OR repairs the GitHub Actions OIDC provider and the narrowly scoped
# SSM deployment role for the ARB hot host.
#
# The OIDC certificate chain can rotate. Do not pin a historical GitHub
# thumbprint forever: derive the current top intermediate CA from the live TLS
# chain, compare it with IAM, and update IAM when needed.

REGION="${AWS_REGION:-us-east-1}"
INSTANCE_ID="${AWS_ARB_HOT_INSTANCE_ID:-i-0b90f0a309153f47e}"
GITHUB_REPO="${GITHUB_REPO:-unbuiltworks/kalshi-arb-bot}"
GITHUB_BRANCH="${GITHUB_BRANCH:-aws-migration-foundation}"
ROLE_NAME="${ROLE_NAME:-arb-github-ssm-deployer}"
OIDC_URL="https://token.actions.githubusercontent.com"
OIDC_HOST="token.actions.githubusercontent.com"
OIDC_AUDIENCE="sts.amazonaws.com"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
INSTANCE_ARN="arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"

current_top_intermediate_thumbprint() {
  local tmp cert_count last_cert fingerprint
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  openssl s_client \
    -servername "$OIDC_HOST" \
    -showcerts \
    -connect "${OIDC_HOST}:443" \
    </dev/null 2>/dev/null \
    | awk -v dir="$tmp" '/BEGIN CERTIFICATE/{n++} {if(n) print > (dir "/cert" n ".pem")}'
  cert_count="$(find "$tmp" -maxdepth 1 -name 'cert*.pem' -type f | wc -l | tr -d ' ')"
  [ "$cert_count" -ge 2 ] || {
    echo "could not obtain a usable GitHub OIDC TLS certificate chain" >&2
    return 1
  }
  last_cert="$(find "$tmp" -maxdepth 1 -name 'cert*.pem' -type f | sort -V | tail -n1)"
  fingerprint="$(openssl x509 -in "$last_cert" -noout -fingerprint -sha1 \
    | sed -E 's/^.*=//' | tr -d ':' | tr '[:upper:]' '[:lower:]')"
  [[ "$fingerprint" =~ ^[0-9a-f]{40}$ ]] || {
    echo "invalid derived GitHub OIDC fingerprint" >&2
    return 1
  }
  printf '%s\n' "$fingerprint"
}

LIVE_THUMBPRINT="$(current_top_intermediate_thumbprint)"
echo "OIDC_LIVE_TOP_INTERMEDIATE_SHA1=$LIVE_THUMBPRINT"

if ! aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "$PROVIDER_ARN" \
  >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "$OIDC_URL" \
    --client-id-list "$OIDC_AUDIENCE" \
    --thumbprint-list "$LIVE_THUMBPRINT" \
    >/dev/null
  echo "OIDC_PROVIDER_CREATED"
else
  PROVIDER_JSON="$(aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "$PROVIDER_ARN" \
    --output json)"
  PROVIDER_URL="$(printf '%s' "$PROVIDER_JSON" | jq -r '.Url // ""')"
  [ "$PROVIDER_URL" = "$OIDC_HOST" ] || {
    echo "unexpected OIDC provider URL: $PROVIDER_URL" >&2
    exit 1
  }

  if ! printf '%s' "$PROVIDER_JSON" | jq -e --arg aud "$OIDC_AUDIENCE" '.ClientIDList | index($aud) != null' >/dev/null; then
    aws iam add-client-id-to-open-id-connect-provider \
      --open-id-connect-provider-arn "$PROVIDER_ARN" \
      --client-id "$OIDC_AUDIENCE"
    echo "OIDC_AUDIENCE_ADDED=$OIDC_AUDIENCE"
  fi

  if ! printf '%s' "$PROVIDER_JSON" | jq -e --arg fp "$LIVE_THUMBPRINT" '.ThumbprintList | map(ascii_downcase) | index($fp) != null' >/dev/null; then
    # Replace stale thumbprints with the currently observed top intermediate.
    # AWS permits up to five, but keeping obsolete historical fingerprints adds
    # no availability once the live chain has rotated away from them.
    aws iam update-open-id-connect-provider-thumbprint \
      --open-id-connect-provider-arn "$PROVIDER_ARN" \
      --thumbprint-list "$LIVE_THUMBPRINT"
    echo "OIDC_THUMBPRINT_REFRESHED=$LIVE_THUMBPRINT"
  else
    echo "OIDC_THUMBPRINT_CURRENT=$LIVE_THUMBPRINT"
  fi
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
        "Federated": "${PROVIDER_ARN}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_HOST}:aud": "${OIDC_AUDIENCE}"
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
  echo "OIDC_ROLE_TRUST_REFRESHED"
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://${TRUST_FILE}" \
    --description "GitHub Actions SSM deployer for ARB AWS hot host" \
    >/dev/null
  echo "OIDC_ROLE_CREATED"
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

FINAL_PROVIDER="$(aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "$PROVIDER_ARN" \
  --output json)"
FINAL_TRUST="$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.AssumeRolePolicyDocument' --output json)"

printf '\nAWS_GITHUB_OIDC_READY\n'
printf 'AWS_ARB_DEPLOY_ROLE_ARN=%s\n' "$ROLE_ARN"
printf 'AWS_ARB_REGION=%s\n' "$REGION"
printf 'AWS_ARB_HOT_INSTANCE_ID=%s\n' "$INSTANCE_ID"
printf 'OIDC_PROVIDER_URL=%s\n' "$(printf '%s' "$FINAL_PROVIDER" | jq -r .Url)"
printf 'OIDC_PROVIDER_AUDIENCE_OK=%s\n' "$(printf '%s' "$FINAL_PROVIDER" | jq -r --arg aud "$OIDC_AUDIENCE" '.ClientIDList | index($aud) != null')"
printf 'OIDC_PROVIDER_LIVE_THUMBPRINT_OK=%s\n' "$(printf '%s' "$FINAL_PROVIDER" | jq -r --arg fp "$LIVE_THUMBPRINT" '.ThumbprintList | map(ascii_downcase) | index($fp) != null')"
printf 'OIDC_ROLE_SUBJECT=%s\n' "repo:${GITHUB_REPO}:ref:refs/heads/${GITHUB_BRANCH}"
printf 'OIDC_REPAIR_COMPLETE=true\n'
