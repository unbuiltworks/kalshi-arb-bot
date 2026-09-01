#!/usr/bin/env bash
set -euo pipefail

# One-use, bounded AWS launcher for the user-approved Gemini L5 Crypto+Sports q1
# mechanics canary. The GitHub workflow waits for the Kalshi Policy-D container
# to finish before invoking this script. This launcher independently re-checks
# overlap and clean account state before any Gemini mutation.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-l5-crypto-sports-q1-live}"
STATE_DIR="${STATE_DIR:-/home/ssm-user/gemini-short-crypto-state}"
BRANCH="feature/gemini-l5-crypto-sports-live-q1"
PINNED_COMMIT="${PINNED_COMMIT:-}"
APPROVAL_ID="${ARB_GEMINI_L5_CRYPTO_SPORTS_Q1_APPROVAL_ID:-}"
APPROVED="${ARB_GEMINI_L5_CRYPTO_SPORTS_Q1_APPROVED:-NO}"
KALSHI_CONTAINER="arb-kalshi-policy-d-v1-q1-canary"
CRYPTO_CONTAINER="arb-gemini-l5-crypto-q1"
SPORTS_CONTAINER="arb-gemini-l5-sports-q1"

[ "$APPROVED" = "YES" ] || { echo "fresh combined q1 approval flag missing" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid PINNED_COMMIT" >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo "invalid approval id" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"

if [ -n "${ENV_FILE:-}" ]; then :
elif [ -f /opt/arb/secrets/gemini-short-crypto.env ]; then ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
elif [ -f "$SOURCE_REPO/.env" ]; then ENV_FILE="$SOURCE_REPO/.env"
else echo "no Gemini secure env file found" >&2; exit 1
fi

for cmd in git docker; do command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }; done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "secure env missing" >&2; exit 1; }
for key in GEMINI_API_KEY GEMINI_API_SECRET; do
  grep -q "^${key}=." "$ENV_FILE" || { echo "required Gemini env missing: $key" >&2; exit 1; }
done
mkdir -p "$STATE_DIR"; chmod 0750 "$STATE_DIR" || true

# Recheck that the latency-sensitive Kalshi canary is no longer running.
if docker inspect "$KALSHI_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$KALSHI_CONTAINER")" = "true" ]; then
  echo "blocked: Kalshi Policy-D canary still running" >&2; exit 1
fi
RUNNING_GEMINI="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-.*(live|q1|canary|hour|threshold|incentive)' || true)"
if [ -n "$RUNNING_GEMINI" ]; then
  echo "blocked: another Gemini live process is running" >&2; printf '%s\n' "$RUNNING_GEMINI" >&2; exit 1
fi

docker rm -f "$CRYPTO_CONTAINER" "$SPORTS_CONTAINER" >/dev/null 2>&1 || true
USED_MARKER="$STATE_DIR/l5_crypto_sports_q1_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "approval already consumed" >&2; exit 1; }

git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true; fi
rm -rf "$WORKTREE"; git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "$PINNED_COMMIT"
SHORT_SHA="${PINNED_COMMIT:0:12}"
IMAGE="arb-gemini-l5-crypto-sports:q1-${SHORT_SHA}"
echo "L5_CRYPTO_SPORTS_Q1_SOURCE_READY commit=$PINNED_COMMIT"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact-image tests, network disabled.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_sports_l5_live_probe_q1.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_incentive_mm_live_probe_q1.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_dual_horizon_settlement_reconcile_v2.py \
  tests/test_gemini_short_crypto_local_budget.py

echo "L5_CRYPTO_SPORTS_Q1_SAFETY_TESTS_OK"

clean_account() {
  docker run -i --rm --network host \
    --env-file "$ENV_FILE" \
    -e PYTHONUNBUFFERED=1 \
    -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
    -v "$STATE_DIR:/state" \
    "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_inventory_local_budget as b
from src import gemini_short_crypto_local_budget as local
async def main():
    if local.local_open_state() is not None:
        raise RuntimeError("local open Gemini reservation present")
    snap = await b.assert_clean_account()
    print(json.dumps({"type":"L5_CRYPTO_SPORTS_Q1_CLEAN_ACCOUNT","clean":True,**snap,"order_entry":False,"mutation":False}, sort_keys=True), flush=True)
asyncio.run(main())
PY
}

clean_account
echo "L5_CRYPTO_SPORTS_Q1_PREFLIGHT_OK"

# Public Sports readiness from the exact image immediately before consuming approval.
docker run --rm --network host "$IMAGE" python -u - <<'PY'
import asyncio, json
from src.gemini_sports_l5_live_probe_q1 import discover_sports_moneyline
x = asyncio.run(discover_sports_moneyline())
if x is None:
    raise SystemExit("no qualifying Sports q1 candidate at pre-arm time")
print(json.dumps({"type":"L5_SPORTS_Q1_PREARM_CANDIDATE","event_ticker":x.ticker,"symbol":x.symbol,"seconds_to_expiry":round(x.seconds_to_expiry,1),"order_entry":False}, sort_keys=True))
PY

echo "L5_SPORTS_Q1_PREARM_READY"

# One-use approval is consumed only after all disarmed gates pass.
printf '%s\n' "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"; sync "$USED_MARKER.tmp"; mv "$USED_MARKER.tmp" "$USED_MARKER"; sync "$STATE_DIR"

# ---- Campaign 1: one proven short-crypto q1 mechanics campaign. ----
CRYPTO_ID="l5cs-${SAFE_APPROVAL_ID:0:8}-crypto-$(date -u +%H%M%S)"
echo "L5_CRYPTO_Q1_LIVE_START id=$CRYPTO_ID q=1_per_outcome max_campaigns=1 auto_restart=false"
docker run --name "$CRYPTO_CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_INCENTIVE_MM_LIVE_PROBE_Q1=YES \
  -e GEMINI_INCENTIVE_MM_PROBE_ID="$CRYPTO_ID" \
  -e GEMINI_INCENTIVE_MM_PROBE_ARM="$CRYPTO_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_incentive_mm_live_probe_q1
CRYPTO_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$CRYPTO_CONTAINER")"
echo "L5_CRYPTO_Q1_TERMINAL exit=$CRYPTO_EXIT"
docker logs --tail 600 "$CRYPTO_CONTAINER" 2>&1 || true
test "$CRYPTO_EXIT" = "0"

# The crypto wrapper waits for settlement if inventory exists. No Sports mutation
# is permitted unless the shared ledger/account is clean again.
clean_account
echo "L5_BETWEEN_CAMPAIGNS_CLEAN_OK"

# Recheck Policy-D didn't unexpectedly restart and no other Gemini live process appeared.
if docker inspect "$KALSHI_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$KALSHI_CONTAINER")" = "true" ]; then
  echo "blocked: Kalshi unexpectedly running before Sports leg" >&2; exit 1
fi
OTHER="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-' | grep -v "^${CRYPTO_CONTAINER}$" || true)"
if [ -n "$OTHER" ]; then echo "blocked: another Gemini container before Sports" >&2; printf '%s\n' "$OTHER" >&2; exit 1; fi

# ---- Campaign 2: one Sports q1 campaign. ----
SPORTS_ID="l5cs-${SAFE_APPROVAL_ID:0:8}-sports-$(date -u +%H%M%S)"
echo "L5_SPORTS_Q1_LIVE_START id=$SPORTS_ID q=1_per_outcome max_campaigns=1 auto_restart=false"
docker run --name "$SPORTS_CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_SPORTS_L5_LIVE_Q1=YES \
  -e GEMINI_SPORTS_L5_PROBE_ID="$SPORTS_ID" \
  -e GEMINI_SPORTS_L5_PROBE_ARM="$SPORTS_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_sports_l5_live_probe_q1
SPORTS_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$SPORTS_CONTAINER")"
echo "L5_SPORTS_Q1_TERMINAL exit=$SPORTS_EXIT"
docker logs --tail 600 "$SPORTS_CONTAINER" 2>&1 || true
test "$SPORTS_EXIT" = "0"

# If Sports filled, its reservation intentionally remains until authoritative
# settlement reconciliation. Do not call it realized here.
docker run --rm --network none \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import json
from src import gemini_short_crypto_local_budget as local
row = local.local_open_state()
print(json.dumps({"type":"L5_CRYPTO_SPORTS_Q1_FINAL_LOCAL_STATE","open_reservation":row,"realized":False if row else None,"order_entry":False}, default=str, sort_keys=True))
PY

echo "L5_CRYPTO_SPORTS_Q1_SEQUENCE_COMPLETE order_entry=false rewards_counted_realized=false auto_restart=false"
