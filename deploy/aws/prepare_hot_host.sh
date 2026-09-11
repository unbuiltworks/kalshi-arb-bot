#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ssm-user/kalshi-arb-bot"
BRANCH="aws-migration-foundation"
TMP_BOOTSTRAP="/tmp/arb-bootstrap-hot-host.sh"
TMP_RUNNER="/tmp/arb-run-sports-maker-shadow.sh"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -n bash "$0" "$@"
fi

if ! id ssm-user >/dev/null 2>&1; then
  echo "ERROR: expected ssm-user account is missing" >&2
  exit 1
fi
if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ERROR: expected repo is missing at $REPO_DIR" >&2
  exit 1
fi

# Never operate on this repository as root. SSM Run Command is root by default,
# while the checkout belongs to ssm-user.
sudo -u ssm-user -H git -C "$REPO_DIR" fetch origin "$BRANCH" feature/sports-kp-live-executor-impl

sudo -u ssm-user -H bash -lc \
  "git -C '$REPO_DIR' show origin/$BRANCH:deploy/aws/bootstrap_hot_host.sh > '$TMP_BOOTSTRAP'"
chmod 0755 "$TMP_BOOTSTRAP"
bash "$TMP_BOOTSTRAP"

sudo -u ssm-user -H bash -lc \
  "git -C '$REPO_DIR' show origin/$BRANCH:deploy/aws/run_sports_maker_shadow.sh > '$TMP_RUNNER'"
chmod 0755 "$TMP_RUNNER"
chown ssm-user:ssm-user "$TMP_RUNNER"

# Build and parity-check only. No venue credentials are required and no process
# capable of order entry is started.
sudo -u ssm-user -H env PREPARE_ONLY=1 "$TMP_RUNNER"

echo "AWS_HOT_HOST_PREPARED"
echo "NEXT_GATE=secure_env"
