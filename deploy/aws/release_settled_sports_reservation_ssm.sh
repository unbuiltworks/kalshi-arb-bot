#!/usr/bin/env bash
set -Eeuo pipefail

: "${AWS_HOT_INSTANCE_ID:?AWS_HOT_INSTANCE_ID is required}"

REMOTE_CMD="sudo -u ssm-user bash -lc 'set -euo pipefail; cd /home/ssm-user/kalshi-arb-bot; git fetch origin aws-migration-foundation >/dev/null; git show origin/aws-migration-foundation:deploy/aws/release_settled_sports_reservation.py > /tmp/release_settled_sports_reservation.py; ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env; [ -f \"\$ENV_FILE\" ] || ENV_FILE=/home/ssm-user/kalshi-arb-bot/.env; STATE_DIR=/home/ssm-user/gemini-short-crypto-state; IMAGE=arb-gemini-l5-crypto-sports:q1-10ea65d31512; docker image inspect \"\$IMAGE\" >/dev/null; docker run --rm --network host --env-file \"\$ENV_FILE\" -e PYTHONUNBUFFERED=1 -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state -v \"\$STATE_DIR:/state\" -v /tmp/release_settled_sports_reservation.py:/app/release_settled_sports_reservation.py:ro \"\$IMAGE\" python -u /app/release_settled_sports_reservation.py'"
PARAMS=$(jq -nc --arg cmd "$REMOTE_CMD" '{commands:[$cmd]}')
CID=$(aws ssm send-command \
  --instance-ids "$AWS_HOT_INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "ARB exact settled Sports local reservation release" \
  --parameters "$PARAMS" \
  --timeout-seconds 300 \
  --query 'Command.CommandId' \
  --output text)

for _ in $(seq 1 80); do
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$CID" \
    --instance-id "$AWS_HOT_INSTANCE_ID" \
    --query Status \
    --output text 2>/dev/null || echo Pending)
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 3
done

mkdir -p runtime
aws ssm get-command-invocation \
  --command-id "$CID" \
  --instance-id "$AWS_HOT_INSTANCE_ID" \
  --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
  > runtime/sports-settled-reservation-release.json
cat runtime/sports-settled-reservation-release.json

test "$(jq -r '.Status' runtime/sports-settled-reservation-release.json)" = "Success"
STDOUT=$(jq -r '.Stdout // ""' runtime/sports-settled-reservation-release.json)
grep -q 'SPORTS_SETTLED_RESERVATION_RELEASE' <<<"$STDOUT"
grep -q '"released": true' <<<"$STDOUT"
