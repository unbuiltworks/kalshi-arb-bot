#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot, live-capable AWS launcher for the exact frozen Kalshi BTC15m
# Policy-D V1 release. The strategy code itself is never rewritten here: this
# wrapper only pins, tests, preflights, arms, launches, and records it.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/kalshi-policy-d-v1-release}"
RELEASE_BRANCH="release/kalshi-btc15m-policy-d-v1-final"
EXPECTED_COMMIT="e80969196c7f71a7c646c10f9108d34afd7e56bb"
PINNED_COMMIT="${PINNED_COMMIT:-}"
APPROVAL_ID="${ARB_KALSHI_POLICY_D_V1_APPROVAL_ID:-}"
START_NOT_BEFORE_UTC="${START_NOT_BEFORE_UTC:-}"
MAX_WINDOWS="${MAX_WINDOWS:-4}"
MAX_SESSION_SECONDS="${MAX_SESSION_SECONDS:-4200}"
CONTAINER="${CONTAINER:-arb-kalshi-policy-d-v1-q1-canary}"
STATE_ROOT="${STATE_ROOT:-/opt/arb/state/kalshi-policy-d-v1}"

[ "${ARB_KALSHI_POLICY_D_V1_APPROVED:-NO}" = "YES" ] || {
  echo "POLICY_D_AWS_BLOCKED fresh approval flag missing" >&2
  exit 1
}
[ "$PINNED_COMMIT" = "$EXPECTED_COMMIT" ] || {
  echo "POLICY_D_AWS_BLOCKED pinned commit mismatch" >&2
  exit 1
}
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "POLICY_D_AWS_BLOCKED invalid commit" >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo "POLICY_D_AWS_BLOCKED invalid approval id" >&2; exit 1; }
[[ "$MAX_WINDOWS" =~ ^[1-4]$ ]] || { echo "POLICY_D_AWS_BLOCKED max windows outside release bound" >&2; exit 1; }
[[ "$MAX_SESSION_SECONDS" =~ ^[0-9]+$ ]] || { echo "POLICY_D_AWS_BLOCKED invalid session seconds" >&2; exit 1; }
[ "$MAX_SESSION_SECONDS" -ge 900 ] && [ "$MAX_SESSION_SECONDS" -le 7200 ] || {
  echo "POLICY_D_AWS_BLOCKED session seconds outside release bound" >&2
  exit 1
}

for cmd in git docker date grep awk; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "POLICY_D_AWS_BLOCKED missing $cmd" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "POLICY_D_AWS_BLOCKED source repo missing" >&2; exit 1; }

# Find a secure env without printing values. Require the PEM form because it is
# self-contained inside the container; if AWS does not have it, fail closed and
# leave venue state untouched.
ENV_FILE=""
for candidate in \
  /opt/arb/secrets/kalshi.env \
  /opt/arb/secrets/prediction-markets.env \
  /opt/arb/secrets/gemini-short-crypto.env \
  "$SOURCE_REPO/.env"
do
  if [ -f "$candidate" ] \
    && grep -q '^KALSHI_API_KEY_ID=.' "$candidate" \
    && grep -q '^KALSHI_PRIVATE_KEY_PEM=.' "$candidate"; then
    ENV_FILE="$candidate"
    break
  fi
done
[ -n "$ENV_FILE" ] || {
  echo "POLICY_D_AWS_BLOCKED Kalshi credentials not present in an approved AWS env file" >&2
  exit 1
}
echo "POLICY_D_AWS_SECURE_ENV_PRESENT path=$ENV_FILE values_redacted=true"

# Isolated durable state and one-use approval marker.
SAFE_APPROVAL="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
STATE_DIR="$STATE_ROOT/$SAFE_APPROVAL"
APPROVAL_DIR="$STATE_ROOT/approvals"
USED_MARKER="$APPROVAL_DIR/${SAFE_APPROVAL}.used"
mkdir -p "$STATE_DIR" "$APPROVAL_DIR"
chmod 0750 "$STATE_ROOT" "$STATE_DIR" "$APPROVAL_DIR" || true
[ ! -e "$USED_MARKER" ] || { echo "POLICY_D_AWS_BLOCKED approval already consumed" >&2; exit 1; }

# Never overlap another copy of this live canary.
RUNNING="$(docker ps --format '{{.Names}}' | grep -E '^arb-kalshi-policy-d-v1-q1-canary$' || true)"
[ -z "$RUNNING" ] || { echo "POLICY_D_AWS_BLOCKED live Policy-D canary already running" >&2; exit 1; }
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# Fetch and detach the exact tested release without moving the host's primary
# checkout, which may be serving unrelated recorders.
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$RELEASE_BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
test "$(git -C "$SOURCE_REPO" rev-parse "origin/$RELEASE_BRANCH")" = "$PINNED_COMMIT"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "$PINNED_COMMIT"
echo "POLICY_D_AWS_SOURCE_READY commit=$PINNED_COMMIT"

# Build a minimal exact-source image. Avoid the repository's general Dockerfile
# because its default build gate is for unrelated sports services.
DOCKERFILE="/tmp/Dockerfile.kalshi-policy-d-v1"
cat > "$DOCKERFILE" <<'DOCKER'
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY src/ src/
COPY config/ config/
COPY tests/ tests/
DOCKER
IMAGE="arb-kalshi-policy-d-v1:${PINNED_COMMIT:0:12}"
docker build -f "$DOCKERFILE" -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact focused release regressions, with network disabled.
docker run --rm --network none "$IMAGE" python -m py_compile \
  src/kalshi_btc15m_policy_d_live_v1.py \
  src/kalshi_btc15m_policy_d_live_v1_entry.py
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_kalshi_btc15m_policy_d_live_v1.py \
  tests/test_kalshi_btc15m_policy_d_live_v1_entry.py \
  tests/test_sports_kp_execution.py \
  tests/test_sports_kp_venue_hardening.py \
  tests/test_orderbook.py
echo "POLICY_D_AWS_RELEASE_TESTS_OK"

# Honor the predeclared evening start. No venue mutation occurs before this.
if [ -n "$START_NOT_BEFORE_UTC" ]; then
  START_EPOCH="$(date -u -d "$START_NOT_BEFORE_UTC" +%s)"
  while [ "$(date -u +%s)" -lt "$START_EPOCH" ]; do
    REMAIN=$(( START_EPOCH - $(date -u +%s) ))
    if [ "$REMAIN" -gt 30 ]; then SLEEP=30; else SLEEP="$REMAIN"; fi
    echo "POLICY_D_AWS_WAITING_FOR_EVENING_START remaining_s=$REMAIN order_entry=false"
    sleep "$SLEEP"
  done
fi

# Authenticated, mutation-free preflight immediately before arming. The exact
# strategy itself verifies exchange status, balance, KXBTC15M open orders,
# positions, and durable state while LIVE remains disarmed.
PREFLIGHT="$(docker run --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e KALSHI_ENV=prod \
  -e KALSHI_POLICY_D_V1_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.kalshi_btc15m_policy_d_live_v1)"
printf '%s\n' "$PREFLIGHT"
printf '%s\n' "$PREFLIGHT" | grep -q '"status":"DISARMED_READY"' || {
  echo "POLICY_D_AWS_BLOCKED disarmed authenticated readiness did not return DISARMED_READY" >&2
  exit 1
}
printf '%s\n' "$PREFLIGHT" | grep -q '"live_armed":false' || {
  echo "POLICY_D_AWS_BLOCKED preflight unexpectedly armed" >&2
  exit 1
}
echo "POLICY_D_AWS_DISARMED_PREFLIGHT_OK order_entry=false"

# Consume the one-use approval immediately before the process can mutate venue
# state. There is no automatic restart.
printf '%s\n' "approval_id=$SAFE_APPROVAL commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$APPROVAL_DIR"

echo "POLICY_D_AWS_LIVE_STARTING commit=$PINNED_COMMIT series=KXBTC15M q=1 max_windows=$MAX_WINDOWS max_session_seconds=$MAX_SESSION_SECONDS auto_restart=false rebates_counted=false fail_closed_entrypoint=true"
docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e KALSHI_ENV=prod \
  -e KALSHI_POLICY_D_V1_STATE_DIR=/state \
  -e KALSHI_POLICY_D_V1_LIVE=YES \
  -e KALSHI_POLICY_D_V1_ARM_ID="$APPROVAL_ID" \
  -e KALSHI_POLICY_D_V1_ARM="$APPROVAL_ID" \
  -e KALSHI_POLICY_D_V1_MAX_WINDOWS="$MAX_WINDOWS" \
  -e KALSHI_POLICY_D_V1_MAX_SESSION_SECONDS="$MAX_SESSION_SECONDS" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.kalshi_btc15m_policy_d_live_v1_entry >/dev/null
sleep 2
RUNNING_NOW="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)"
[ "$RUNNING_NOW" = "true" ] || {
  echo "POLICY_D_AWS_BLOCKED live container failed to stay running" >&2
  docker logs --tail 120 "$CONTAINER" 2>&1 || true
  exit 1
}
echo "POLICY_D_AWS_LIVE_DETACHED container=$CONTAINER started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker logs --tail 80 "$CONTAINER" 2>&1 || true
