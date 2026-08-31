#!/usr/bin/env bash
set -euo pipefail

# One-shot AWS launcher for the bounded Gemini BTC5m paired-maker live canary.
# Fresh user approval + unique one-use ID are mandatory. The approval marker is
# consumed only after exact-image tests and authenticated clean-account preflight.

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-paired-maker-live10}"
BRANCH="feature/gemini-paired-maker-live-10-canary"
PINNED_COMMIT="${PINNED_COMMIT:-}"
CONTAINER="arb-gemini-paired-maker-live10"

[ -n "$PINNED_COMMIT" ] || { echo "PINNED_COMMIT is required" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid PINNED_COMMIT" >&2; exit 1; }

[ "${ARB_GEMINI_PAIRED_MAKER_LIVE10_APPROVED:-NO}" = "YES" ] || {
  echo "fresh paired-maker live10 approval flag missing" >&2
  exit 1
}
APPROVAL_ID="${ARB_GEMINI_PAIRED_MAKER_LIVE10_APPROVAL_ID:-}"
[ -n "$APPROVAL_ID" ] || { echo "fresh paired-maker live10 approval ID missing" >&2; exit 1; }
SAFE_APPROVAL_ID="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"
[ -n "$SAFE_APPROVAL_ID" ] || { echo "invalid paired-maker live10 approval ID" >&2; exit 1; }

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

# Never overlap any short-crypto live execution campaign. Read-only collectors are
# intentionally excluded.
RUNNING_LIVE="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-(btc-dual-hour|btc5m-inventory-hour|l5-threshold|incentive-mm-live-probe|dynamic-complement-live-probe|paired-maker-live10|.*live-canary)' || true)"
if [ -n "$RUNNING_LIVE" ]; then
  echo "blocked: another Gemini short-crypto live container is running" >&2
  printf '%s\n' "$RUNNING_LIVE" >&2
  exit 1
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
USED_MARKER="$STATE_DIR/paired_maker_live10_approval_${SAFE_APPROVAL_ID}.used"
[ ! -e "$USED_MARKER" ] || { echo "approval ID already consumed: $SAFE_APPROVAL_ID" >&2; exit 1; }

# Exact source pin.
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
IMAGE="arb-gemini-paired-maker:live10-${SHORT_SHA}"
echo "PAIRED_MAKER_LIVE10_SOURCE_READY commit=$actual"

docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Focused exact-image regressions, network disabled.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_maker_live_10_canary.py \
  tests/test_gemini_maker_live_10_canary_v2.py \
  tests/test_gemini_incentive_mm_dynamic_complement.py \
  tests/test_gemini_short_crypto_dynamic_complement_q1_v2.py

echo "PAIRED_MAKER_LIVE10_SAFETY_TESTS_OK"

# Authenticated/read-only clean-account preflight on the exact image. No order entry.
docker run -i --rm --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u - <<'PY'
import asyncio, json
from decimal import Decimal as D
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_short_crypto_local_budget as local
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_maker import live_10_canary as live

async def main():
    if local.local_open_state() is not None:
        raise RuntimeError("local open short-crypto reservation present")
    snap = await budget.assert_clean_account()
    terms = await base._gemini_terms_ok()
    available = D(str(await base._gemini_available_usd()))
    if not terms:
        raise RuntimeError("Gemini Prediction Markets terms not accepted")
    if available < live.MIN_AVAILABLE_USD:
        raise RuntimeError(f"available USD {available} below live10 reserve {live.MIN_AVAILABLE_USD}")
    print(json.dumps({
        "type":"PAIRED_MAKER_LIVE10_CLEAN_START",
        "clean":True,
        "terms_ok":True,
        "available_usd":str(available),
        "max_window_spend":str(live.MAX_WINDOW_SPEND),
        "q_per_pair":1,
        "one_pair_outstanding":True,
        "maker_fee_gate":True,
        "rewards_counted_as_realized":False,
        "order_entry":False,
        "mutation":False,
        **snap,
    }, sort_keys=True), flush=True)

asyncio.run(main())
PY

echo "PAIRED_MAKER_LIVE10_CLEAN_START_OK"

# Consume one-use approval immediately before venue mutation becomes possible.
printf '%s\n' \
  "approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$USED_MARKER.tmp"
chmod 0640 "$USED_MARKER.tmp"
sync "$USED_MARKER.tmp"
mv "$USED_MARKER.tmp" "$USED_MARKER"
sync "$STATE_DIR"

PROBE_ID="pm10-${SAFE_APPROVAL_ID:0:10}-$(date -u +%H%M%S)"
echo "PAIRED_MAKER_LIVE10_STARTING probe_id=$PROBE_ID approval_id=$SAFE_APPROVAL_ID commit=$PINNED_COMMIT asset=BTC horizon=5m q=1 one_pair_outstanding=true max_window_spend=10 max_rescue_loss=2 taker_normal=false auto_restart=false"

docker run -d --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_SHORT_CRYPTO_STATE_DIR=/state \
  -e GEMINI_PAIRED_MAKER_LIVE_10=YES \
  -e GEMINI_PAIRED_MAKER_LIVE_10_ID="$PROBE_ID" \
  -e GEMINI_PAIRED_MAKER_LIVE_10_ARM="$PROBE_ID" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_maker.live_10_canary_v2

echo "PAIRED_MAKER_LIVE10_DETACHED container=$CONTAINER probe_id=$PROBE_ID"
sleep 2
docker ps -a --filter "name=^/${CONTAINER}$" --format 'PAIRED_MAKER_LIVE10_CONTAINER {{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 80 "$CONTAINER" 2>&1 || true

echo "Emergency stop: docker stop $CONTAINER"
