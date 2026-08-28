#!/usr/bin/env bash
set -euo pipefail

# LIVE-CAPABLE but disarmed-by-default one-shot AWS launcher for the bounded
# Gemini ETH5 q=1 dynamic-complement maker probe.
#
# A control workflow must provide BOTH a fresh approval flag and a unique one-use
# approval ID. These gates are checked BEFORE secure-env discovery. When
# QUALIFY_WAIT_SECONDS > 0, the launcher may then poll the exact tested image in
# authenticated READ-ONLY mode for a qualifying ETH5 window; the one-use approval
# marker is consumed only AFTER all entry gates pass.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-dynamic-complement-q1}"
BRANCH="feature/gemini-incentive-mm-dynamic-complement-q1"
PINNED_COMMIT="${PINNED_COMMIT:-}"
CONTAINER="arb-gemini-dynamic-complement-live-probe-q1"
QUALIFY_WAIT_SECONDS="${QUALIFY_WAIT_SECONDS:-0}"
QUALIFY_POLL_SECONDS="${QUALIFY_POLL_SECONDS:-10}"

[ -n "$PINNED_COMMIT" ] || { echo "PINNED_COMMIT is required" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid PINNED_COMMIT" >&2; exit 1; }
[[ "$QUALIFY_WAIT_SECONDS" =~ ^[0-9]+$ ]] || { echo "invalid QUALIFY_WAIT_SECONDS" >&2; exit 1; }
[[ "$QUALIFY_POLL_SECONDS" =~ ^[0-9]+$ ]] || { echo "invalid QUALIFY_POLL_SECONDS" >&2; exit 1; }
[ "$QUALIFY_POLL_SECONDS" -ge 2 ] || { echo "QUALIFY_POLL_SECONDS must be >=2" >&2; exit 1; }

# Fresh authorization is mandatory and is intentionally checked before ENV_FILE.
[ "${ARB_GEMINI_DYNAMIC_COMPLEMENT_Q1_APPROVED:-NO}" = "YES" ] || {
  echo "fresh dynamic-complement q1 approval flag missing" >&2
  exit 1
}
APPROVAL_ID="${ARB_GEMINI_DYNAMIC_COMPLEMENT_Q1_APPROVAL_ID:-}"
[ -n "$APPROVAL_ID" ] || { echo "fresh dynamic-complement q1 approval ID missing" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
[ -n "$SAFE_APPROVAL_ID" ] || { echo "invalid dynamic-complement q1 approval ID" >&2; exit 1; }

if [ -n "${ENV_FILE:-}" ]; then
  :
elif [ -f /opt/arb/secrets/gemini-short-crypto.env ]; then
  ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
elif [ -f "$SOURCE_REPO/.env" ]; then
  ENV_FILE="$SOURCE_REPO/.env"
else
  echo "no Gemini secure env file found" >&2
  exit 1
fi

if [ -n "${STATE_DIR:-}" ]; then
  :
elif [ -d /home/ssm-user/gemini-short-crypto-state ]; then
  STATE_DIR=/home/ssm-user/gemini-short-crypto-state
else
  STATE_DIR=/opt/arb/gemini-short-crypto-state
fi

for cmd in git docker; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "$cmd is required" >&2; exit 1; }
done
[ -d "$SOURCE_REPO/.git" ] || { echo "source repo missing: $SOURCE_REPO" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "secure env missing: $ENV_FILE" >&2; exit 1; }
for key in GEMINI_API_KEY GEMINI_API_SECRET; do
  grep -q "^${key}=." "$ENV_FILE" || { echo "required env missing or empty: $key" >&2; exit 1; }
done
mkdir -p "$STATE_DIR"
chmod 0750 "$STATE_DIR" || true

# Never overlap any known Gemini short-crypto live campaign. Read-only shadows are
# intentionally not matched here.
RUNNING_LIVE="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-(btc-dual-hour|btc5m-inventory-hour|l5-threshold|incentive-mm-live-probe|dynamic-complement-live-probe|.*live-canary)' || true)"
if [ -n "$RUNNING_LIVE" ]; then
  echo "blocked: another Gemini short-crypto live container is running" >&2
  printf '%s\n' "$RUNNING_LIVE" >&2
  exit 1
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
USED_MARKER="$STATE_DIR/dynamic_complement_q1_approval_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "approval ID already consumed: $SAFE_APPROVAL_ID" >&2; exit 1; }

# Fetch as the repository owner on AWS. The host's ssm-user has the proven private
# repo authentication path; callers should invoke this script under that user.
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH"
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT"
actual="$(git -C "$WORKTREE" rev-parse HEAD)"
test "$actual" = "$PINNED_COMMIT"
SHORT_SHA="${PINNED_COMMIT:0:12}"
IMAGE="arb-gemini-dynamic-complement:q1-${SHORT_SHA}"
echo "DYNAMIC_COMPLEMENT_Q1_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact-image focused safety regressions with network disabled.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_incentive_mm_dynamic_complement.py \
  tests/test_gemini_short_crypto_dynamic_complement_q1.py \
  tests/test_gemini_short_crypto_dynamic_complement_q1_v2.py \
  tests/test_gemini_short_crypto_dual_horizon_settlement_reconcile_v2.py \
  tests/test_gemini_incentive_mm_dynamic_live_probe_q1.py

echo "DYNAMIC_COMPLEMENT_Q1_SAFETY_TESTS_OK"

# Exact-image clean-account preflight. Authenticated/read-only and only after the
# fresh user approval has passed the launcher gate.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_short_crypto_local_budget as local
from src import gemini_short_crypto_dynamic_complement_q1_v2 as dyn
async def main():
    if local.local_open_state() is not None:
        raise RuntimeError("local open reservation present")
    snap = await budget.assert_clean_account()
    print(json.dumps({
        "type":"DYNAMIC_COMPLEMENT_Q1_CLEAN_START",
        "clean":True,
        "entry_pair_edge_floor":str(dyn.ENTRY_PAIR_EDGE),
        "final_pair_edge_floor":str(dyn.FINAL_PAIR_EDGE),
        "initial_wait_seconds":dyn.INITIAL_WAIT_SECONDS,
        "rescue_seconds":dyn.RESCUE_SECONDS,
        "max_reprices":dyn.MAX_REPRICES,
        "taker_rescue_enabled":False,
        "order_entry":False,
        "mutation":False,
        **snap,
    }, sort_keys=True), flush=True)
asyncio.run(main())
PY

echo "DYNAMIC_COMPLEMENT_Q1_CLEAN_START_OK"

# Optional read-only qualifier wait. This uses the exact same discovery, fresh-book,
# band, fee, q1-size, and time-to-expiry rules as the live child. Crucially, it runs
# BEFORE the one-use approval marker is consumed and cannot submit orders.
if [ "$QUALIFY_WAIT_SECONDS" -gt 0 ]; then
  echo "DYNAMIC_COMPLEMENT_Q1_QUALIFIER_WAIT_START max_seconds=$QUALIFY_WAIT_SECONDS poll_seconds=$QUALIFY_POLL_SECONDS order_entry=false"
  QUALIFY_DEADLINE=$(( $(date +%s) + QUALIFY_WAIT_SECONDS ))
  QUALIFIED=0
  while [ "$(date +%s)" -lt "$QUALIFY_DEADLINE" ]; do
    set +e
    QUALIFY_OUT="$(docker run -i --rm --network host \
      --env-file "$ENV_FILE" \
      -e PYTHONUNBUFFERED=1 \
      "$IMAGE" python -u - <<'PY'
import asyncio, json, sys
from decimal import Decimal as D
from src import gemini_short_crypto_dynamic_complement_q1 as core
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m

async def main():
    original = core.base.discover_btc5m
    core.base.discover_btc5m = discover_eth5m
    try:
        event, report = await core.preflight()
        qualifies = False
        payload = {
            "qualifies": False,
            "order_entry": False,
            "mutation": False,
            "market": None if event is None else event.ticker,
            "symbol": None if event is None else event.symbol,
            "seconds_to_expiry": None if event is None else event.seconds_to_expiry,
        }
        if event is not None and report.get("terms_ok") and report.get("q1_supported"):
            if D(str(report.get("available_usd") or "0")) >= D("2") and event.seconds_to_expiry >= core.MIN_SECONDS_TO_EXPIRY:
                bid, bid_size, ask, ask_size = await core.base.fresh_top(event.symbol)
                model = core.raw_pair_model(bid, ask)
                qualifies = bool(core.entry_eligible(model) and event.seconds_to_expiry >= core.MIN_SECONDS_TO_EXPIRY)
                payload.update({
                    "qualifies": qualifies,
                    "yes_quote": str(model["yes_quote"]),
                    "no_quote": str(model["no_quote"]),
                    "modeled_edge": str(model["modeled_edge"]),
                    "yes_bid_size": str(bid_size),
                    "yes_ask_size": str(ask_size),
                })
        print(json.dumps(payload, sort_keys=True), flush=True)
        raise SystemExit(0 if qualifies else 3)
    finally:
        core.base.discover_btc5m = original

asyncio.run(main())
PY
    )"
    QUALIFY_RC=$?
    set -e
    echo "DYNAMIC_COMPLEMENT_Q1_QUALIFIER $QUALIFY_OUT"
    if [ "$QUALIFY_RC" -eq 0 ]; then
      QUALIFIED=1
      break
    fi
    [ "$QUALIFY_RC" -eq 3 ] || { echo "qualifier failed unexpectedly rc=$QUALIFY_RC" >&2; exit "$QUALIFY_RC"; }
    sleep "$QUALIFY_POLL_SECONDS"
  done
  if [ "$QUALIFIED" -ne 1 ]; then
    echo "DYNAMIC_COMPLEMENT_Q1_NO_QUALIFYING_WINDOW order_entry=false approval_consumed=false"
    exit 3
  fi
  echo "DYNAMIC_COMPLEMENT_Q1_QUALIFYING_WINDOW_FOUND order_entry=false approval_consumed=false"
fi

# Consume the one-use approval immediately before the live process can mutate venue
# state. No automatic restart is permitted.
printf '%s\n' \
  "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$STATE_DIR"

PROBE_ID="dcq1-${SAFE_APPROVAL_ID:0:12}-$(date -u +%H%M%S)"
echo "DYNAMIC_COMPLEMENT_Q1_LIVE_STARTING probe_id=$PROBE_ID approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT q=1_per_outcome asset=ETH horizon=5m entry_edge_floor=0.05 final_edge_floor=0.02 initial_wait_s=60 rescue_s=30 max_reprices=6 taker_rescue=false auto_restart=false"

docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_DYNAMIC_COMPLEMENT_LIVE_PROBE_Q1=YES \
  -e GEMINI_DYNAMIC_COMPLEMENT_PROBE_ID="$PROBE_ID" \
  -e GEMINI_DYNAMIC_COMPLEMENT_PROBE_ARM="$PROBE_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_incentive_mm_dynamic_live_probe_q1

echo "DYNAMIC_COMPLEMENT_Q1_LIVE_DETACHED container=$CONTAINER probe_id=$PROBE_ID"
sleep 2
docker ps -a --filter "name=^/${CONTAINER}$" --format 'DYNAMIC_COMPLEMENT_Q1_CONTAINER {{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 60 "$CONTAINER" 2>&1 || true

echo "Emergency stop: docker stop $CONTAINER"
