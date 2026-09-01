#!/usr/bin/env bash
set -Eeuo pipefail
: "${AWS_HOT_INSTANCE_ID:?AWS_HOT_INSTANCE_ID is required}"

REMOTE_CMD="sudo -u ssm-user bash -lc 'set -euo pipefail; echo GEMINI_HOST_CENSUS_BEGIN; docker ps --format \"{{.Names}}|{{.Image}}|{{.Status}}\" | grep -Ei \"gemini|q10|v5[0-9]|dynamic|l5\" || true; echo GEMINI_HOST_CENSUS_END'"
PARAMS=$(jq -nc --arg cmd "$REMOTE_CMD" '{commands:[$cmd]}')
CID=$(aws ssm send-command \
  --instance-ids "$AWS_HOT_INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "ARB read-only Gemini host live census" \
  --parameters "$PARAMS" \
  --timeout-seconds 60 \
  --query 'Command.CommandId' \
  --output text)
for _ in $(seq 1 30); do
  STATUS=$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$AWS_HOT_INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break ;; esac
  sleep 2
done
aws ssm get-command-invocation \
  --command-id "$CID" \
  --instance-id "$AWS_HOT_INSTANCE_ID" \
  --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}'
