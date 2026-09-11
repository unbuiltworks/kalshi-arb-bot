#!/usr/bin/env bash
set -Eeuo pipefail
: "${AWS_HOT_INSTANCE_ID:?AWS_HOT_INSTANCE_ID is required}"

# AWS-side health reads first. These are diagnostic-only and deliberately tolerate
# missing describe permissions so we can still attempt the read-only SSM census.
echo AWS_CONTROL_PLANE_DIAGNOSTICS_BEGIN
aws ec2 describe-instances \
  --instance-ids "$AWS_HOT_INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].{InstanceId:InstanceId,State:State.Name,PrivateIp:PrivateIpAddress,LaunchTime:LaunchTime,IamProfile:IamInstanceProfile.Arn}' \
  --output json 2>&1 || true
aws ec2 describe-instance-status \
  --instance-ids "$AWS_HOT_INSTANCE_ID" --include-all-instances \
  --query 'InstanceStatuses[0].{InstanceState:InstanceState.Name,SystemStatus:SystemStatus.Status,InstanceStatus:InstanceStatus.Status}' \
  --output json 2>&1 || true
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$AWS_HOT_INSTANCE_ID" \
  --query 'InstanceInformationList[0].{PingStatus:PingStatus,LastPingDateTime:LastPingDateTime,AgentVersion:AgentVersion,PlatformName:PlatformName,PlatformVersion:PlatformVersion,IPAddress:IPAddress,AssociationStatus:AssociationStatus}' \
  --output json 2>&1 || true
echo AWS_CONTROL_PLANE_DIAGNOSTICS_END

REMOTE=$(cat <<'EOF'
set -u
echo AWS_HOST_CENSUS_BEGIN
echo "UTC=$(date -u +%FT%TZ)"
echo "WHOAMI=$(whoami)"
id || true
uname -a || true
uptime || true

echo SSM_USER_CHECK_BEGIN
id ssm-user 2>&1 || true
command -v sudo 2>&1 || true
command -v runuser 2>&1 || true
echo SSM_USER_CHECK_END

echo DOCKER_CHECK_BEGIN
command -v docker 2>&1 || true
docker version --format 'DOCKER_VERSION={{.Server.Version}}' 2>&1 || true
docker ps --format 'CONTAINER name={{.Names}} image={{.Image}} status={{.Status}}' 2>&1 || true
echo DOCKER_CHECK_END

echo PROTECTED_SERVICE_CHECK_BEGIN
printf 'kalshi-btc25-continuous active='; systemctl is-active kalshi-btc25-continuous 2>&1 || true
printf 'kalshi-btc25-continuous enabled='; systemctl is-enabled kalshi-btc25-continuous 2>&1 || true
echo PROTECTED_SERVICE_CHECK_END

echo REPO_CHECK_BEGIN
for repo in /opt/kalshi-arb-bot /home/ssm-user/kalshi-arb-bot /home/ssm-user/l5-pmus-q1-stage; do
  if [ -d "$repo" ]; then
    echo "REPO path=$repo present=true"
    stat -c 'REPO_STAT owner=%U group=%G mode=%a' "$repo" 2>&1 || true
    if [ -d "$repo/.git" ]; then
      git -C "$repo" rev-parse HEAD 2>&1 | sed 's/^/REPO_HEAD=/' || true
      git -C "$repo" branch --show-current 2>&1 | sed 's/^/REPO_BRANCH=/' || true
      git -C "$repo" status --short 2>&1 | sed 's/^/REPO_STATUS=/' || true
    fi
  else
    echo "REPO path=$repo present=false"
  fi
done
echo REPO_CHECK_END

echo CREDENTIAL_FILE_METADATA_BEGIN
for f in /home/ssm-user/l5-pmus-q1-credentials.env /opt/kalshi-arb-bot/.env.aws-live /opt/arb/secrets/gemini-short-crypto.env; do
  if [ -f "$f" ]; then
    stat -c 'CRED_FILE path=%n present=true owner=%U group=%G mode=%a bytes=%s' "$f" 2>&1 || true
  else
    echo "CRED_FILE path=$f present=false"
  fi
done
echo CREDENTIAL_FILE_METADATA_END

echo AWS_HOST_CENSUS_END
EOF
)
ENC=$(printf '%s' "$REMOTE" | base64 -w0)
PARAMS=$(jq -nc --arg cmd "echo '$ENC' | base64 -d | bash" '{commands:[$cmd]}')
CID=$(aws ssm send-command \
  --instance-ids "$AWS_HOT_INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "ARB read-only AWS host census" \
  --parameters "$PARAMS" \
  --timeout-seconds 60 \
  --query 'Command.CommandId' \
  --output text)
for _ in $(seq 1 30); do
  STATUS=$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$AWS_HOT_INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break ;; esac
  sleep 2
done
RESULT=$(aws ssm get-command-invocation \
  --command-id "$CID" \
  --instance-id "$AWS_HOT_INSTANCE_ID" \
  --query '{Status:Status,StatusDetails:StatusDetails,ResponseCode:ResponseCode,PluginName:PluginName,ExecutionStart:ExecutionStartDateTime,ExecutionElapsed:ExecutionElapsedTime,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
  --output json)
echo "$RESULT"
test "$(jq -r '.Status' <<<"$RESULT")" = Success
