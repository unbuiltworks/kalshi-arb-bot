#!/usr/bin/env bash
set -euo pipefail

# One-shot LIVE Gemini BTC 5m q=1 maker canary.
# USER AUTHORIZATION REQUIRED BEFORE RUNNING.
# Safety:
# - pinned strategy commit; no floating live code
# - separate worktree/image/container
# - exact q=1 only, BTC 5m only (enforced in Python)
# - fractional maker fills are flattened at exact observed quantity
# - authenticated DISARMED preflight immediately before live run
# - fresh >=3c modeled pair-edge gate rechecked inside live process
# - durable ledger reservation occurs before Gemini order submission
# - one process / one campaign / no restart
# - does not stop or modify sports maker or arb-short-* shadows

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-short-crypto-live-q1}"
BRANCH="feature/gemini-short-crypto-live-q1"
PINNED_COMMIT="8b88aefdc41c3bc4ef099cbc21d39c1accdcd5e2"
IMAGE="arb-gemini-short-btc5m-q1:live-8b88aef"
CONTAINER="arb-gemini-short-btc5m-q1-live"
PREFLIGHT_CONTAINER="arb-gemini-short-btc5m-q1-live-preflight"
ENV_FILE="${ENV_FILE:-/opt/arb/secrets/gemini-short-crypto.env}"

if [ ! -d "$SOURCE_REPO/.git" ]; then
  echo "source repo missing: $SOURCE_REPO" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "secure env missing: $ENV_FILE" >&2
  exit 1
fi

perms="$(stat -c '%a' "$ENV_FILE")"
group_digit="${perms: -2:1}"
other_digit="${perms: -1}"
if (( other_digit != 0 || group_digit == 2 || group_digit == 3 || group_digit == 6 || group_digit == 7 )); then
  echo "secure env permissions too broad: mode=$perms" >&2
  exit 1
fi

for key in GEMINI_API_KEY GEMINI_API_SECRET LEDGER_INGEST_TOKEN; do
  if ! grep -q "^${key}=." "$ENV_FILE"; then
    echo "required env missing or empty: $key" >&2
    exit 1
  fi
done

echo "LIVE_Q1_ENV_OK names=GEMINI_API_KEY,GEMINI_API_SECRET,LEDGER_INGEST_TOKEN mode=$perms"

if docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "live canary container already running; refusing duplicate" >&2
  exit 1
fi
docker rm -f "$CONTAINER" "$PREFLIGHT_CONTAINER" >/dev/null 2>&1 || true

git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"
if ! git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}" 2>/dev/null; then
  echo "pinned commit not available after fetch: $PINNED_COMMIT" >&2
  exit 1
fi

if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT"
actual="$(git -C "$WORKTREE" rev-parse HEAD)"
test "$actual" = "$PINNED_COMMIT"
echo "LIVE_Q1_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE"
echo "LIVE_Q1_IMAGE_READY image=$IMAGE"

# Explicit regression tests inside the exact image used live.
docker run --rm --network none "$IMAGE" \
  python -m pytest -q \
    tests/test_gemini_short_crypto_quantity.py \
    tests/test_gemini_short_crypto_btc5m_canary_v3.py
echo "LIVE_Q1_SAFETY_TESTS_OK"

# Fresh authenticated preflight, still forcibly disarmed. No ledger mutation or order entry.
PREFLIGHT_LOG="$(mktemp)"
trap 'rm -f "$PREFLIGHT_LOG"' EXIT

docker run --name "$PREFLIGHT_CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=NO \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID= \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM= \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1_v3 \
  2>&1 | tee "$PREFLIGHT_LOG"
docker rm "$PREFLIGHT_CONTAINER" >/dev/null 2>&1 || true

python3 - "$PREFLIGHT_LOG" <<'PY'
import json, sys
from decimal import Decimal
p = sys.argv[1]
report = None
prefix = "GEMINI_BTC5M_MAKER_Q1 "
with open(p, encoding="utf-8") as f:
    for line in f:
        if not line.startswith(prefix):
            continue
        try:
            obj = json.loads(line[len(prefix):])
        except Exception:
            continue
        if obj.get("stage") == "PREFLIGHT":
            report = obj
            break
if report is None:
    raise SystemExit("preflight JSON missing")
checks = {
    "terms_ok": report.get("terms_ok") is True,
    "available_usd>=2": Decimal(str(report.get("available_usd") or "0")) >= Decimal("2"),
    "q1_supported": report.get("q1_supported") is True,
    "passes_3c_pair_floor": report.get("passes_3c_pair_floor") is True,
    "armed_false": report.get("armed") is False,
    "order_entry_false": report.get("order_entry") is False,
}
print("LIVE_Q1_PREFLIGHT_CHECKS", checks)
if not all(checks.values()):
    raise SystemExit("live q1 preflight gate failed")
print("LIVE_Q1_PREFLIGHT_OK order_entry=false")
PY

CANARY_ID="aws-btc5m-q1-$(date -u +%Y%m%dT%H%M%SZ)-$$"
echo "LIVE_Q1_STARTING canary_id=$CANARY_ID commit=$PINNED_COMMIT q=1 venue=gemini asset=BTC horizon=5m"

# Detached so an SSM/browser disconnect cannot kill the process while maker orders rest.
docker run -d --name "$CONTAINER" --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_LIVE_Q1=YES \
  -e GEMINI_SHORT_CRYPTO_CANARY_ID="$CANARY_ID" \
  -e GEMINI_SHORT_CRYPTO_CANARY_ARM="$CANARY_ID" \
  "$IMAGE" python -u -m src.gemini_short_crypto_btc5m_maker_canary_q1_v3

set +e
rc="$(docker wait "$CONTAINER")"
wait_rc=$?
set -e

echo "=== LIVE Q1 CANARY LOG ==="
docker logs "$CONTAINER" 2>&1 || true
echo "=== LIVE Q1 CANARY END ==="
echo "LIVE_Q1_CONTAINER_EXIT docker_wait_rc=$wait_rc process_rc=$rc canary_id=$CANARY_ID"

if [ "$wait_rc" -ne 0 ] || [ "$rc" != "0" ]; then
  echo "LIVE_Q1_PROCESS_NONZERO -- inspect logs and reconcile before any further live run" >&2
  exit 1
fi

echo "LIVE_Q1_ONE_SHOT_COMPLETE canary_id=$CANARY_ID -- do not infer realized profit until settlement/reconciliation"
