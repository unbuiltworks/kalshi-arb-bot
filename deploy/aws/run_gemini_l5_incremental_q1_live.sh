#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_REPO="${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}"
WORKTREE="${WORKTREE:-/opt/arb/gemini-l5-incremental-q1-live}"
BRANCH="feature/gemini-l5-incremental-live-q1"
PINNED_COMMIT="${PINNED_COMMIT:-}"
APPROVAL_ID="${ARB_GEMINI_L5_INCREMENTAL_Q1_APPROVAL_ID:-}"
APPROVED="${ARB_GEMINI_L5_INCREMENTAL_Q1_APPROVED:-NO}"
KALSHI_CONTAINER="arb-kalshi-policy-d-v1-q1-canary"
CONTAINER="arb-gemini-l5-incremental-q1"
STATE_ROOT="${STATE_ROOT:-/opt/arb/state/gemini-l5-incremental-q1}"
QUARANTINE="${GEMINI_L5_QUARANTINE_EVENT_TICKERS:-TENNIS-ATPUSO-20260830-SHA2-TRU}"

[ "$APPROVED" = "YES" ] || { echo "L5_INC_AWS_BLOCKED fresh approval missing" >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "L5_INC_AWS_BLOCKED invalid commit" >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo "L5_INC_AWS_BLOCKED invalid approval id" >&2; exit 1; }
SAFE="$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')"

if [ -n "${ENV_FILE:-}" ]; then :
elif [ -f /opt/arb/secrets/gemini-short-crypto.env ]; then ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
elif [ -f "$SOURCE_REPO/.env" ]; then ENV_FILE="$SOURCE_REPO/.env"
else echo "L5_INC_AWS_BLOCKED Gemini env missing" >&2; exit 1
fi
for key in GEMINI_API_KEY GEMINI_API_SECRET; do grep -q "^${key}=." "$ENV_FILE" || { echo "L5_INC_AWS_BLOCKED required Gemini env missing" >&2; exit 1; }; done
for cmd in git docker; do command -v "$cmd" >/dev/null || { echo "L5_INC_AWS_BLOCKED missing $cmd" >&2; exit 1; }; done

# No overlap with the latency-sensitive Kalshi canary or another Gemini live process.
if docker inspect "$KALSHI_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$KALSHI_CONTAINER")" = "true" ]; then
  echo "L5_INC_AWS_BLOCKED Kalshi Policy-D running" >&2; exit 1
fi
OTHER="$(docker ps --format '{{.Names}}' | grep -E '^arb-gemini-.*(live|q1|canary|hour|threshold|incentive)' || true)"
[ -z "$OTHER" ] || { echo "L5_INC_AWS_BLOCKED another Gemini live process running" >&2; printf '%s\n' "$OTHER" >&2; exit 1; }
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

STATE_DIR="$STATE_ROOT/$SAFE"
APPROVAL_DIR="$STATE_ROOT/approvals"
USED="$APPROVAL_DIR/$SAFE.used"
mkdir -p "$STATE_DIR" "$APPROVAL_DIR"; chmod 0750 "$STATE_ROOT" "$STATE_DIR" "$APPROVAL_DIR" || true
[ ! -e "$USED" ] || { echo "L5_INC_AWS_BLOCKED approval already consumed" >&2; exit 1; }

# Pin exact source without moving the host checkout.
git -C "$SOURCE_REPO" config --global --add safe.directory "$SOURCE_REPO" || true
git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true; fi
rm -rf "$WORKTREE"; git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "$PINNED_COMMIT"
IMAGE="arb-gemini-l5-incremental:q1-${PINNED_COMMIT:0:12}"
docker build -t "$IMAGE" "$WORKTREE" >/dev/null
echo "L5_INC_AWS_SOURCE_READY commit=$PINNED_COMMIT"

# Exact-image focused safety tests, network disabled.
docker run --rm --network none "$IMAGE" python -m py_compile src/gemini_l5_incremental_live_q1.py src/gemini_l5_incremental_live_q1_bounded.py
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_l5_incremental_live_q1.py \
  tests/test_gemini_sports_l5_live_probe_q1.py \
  tests/test_gemini_short_crypto_inventory_maker_q1.py \
  tests/test_gemini_short_crypto_local_budget.py
echo "L5_INC_AWS_SAFETY_TESTS_OK"

# Disarmed exact entrypoint must make no venue calls.
DISARMED="$(docker run --rm --network none "$IMAGE" python -u -m src.gemini_l5_incremental_live_q1_bounded)"
printf '%s\n' "$DISARMED"
grep -q '"stage":"DISARMED"' <<<"$DISARMED"
grep -q '"order_entry":false' <<<"$DISARMED"
echo "L5_INC_AWS_DISARMED_OK"

# Authenticated read-only preflight. We deliberately permit the known quarantined
# settled-pending tennis position, but require ZERO active Gemini prediction orders.
docker run -i --rm --network host --env-file "$ENV_FILE" "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_prediction_auth import private_rest_request
async def main():
    terms = await base._gemini_terms_ok(); usd = await base._gemini_available_usd()
    r = await private_rest_request("POST", "/v1/prediction-markets/orders/active", params={"limit":100,"offset":0}, timeout=5.0)
    if r.status_code != 200: raise RuntimeError(f"active order preflight http={r.status_code}")
    body = r.json(); orders = body.get("orders", []) if isinstance(body, dict) else []
    if orders: raise RuntimeError(f"active Gemini prediction orders present count={len(orders)}")
    if not terms or usd < 5: raise RuntimeError("Gemini terms/funds preflight failed")
    print(json.dumps({"type":"L5_INC_AUTH_PREFLIGHT","terms":True,"available_usd":str(usd),"active_prediction_orders":0,"known_prior_residual_quarantined":True,"mutation":False,"order_entry":False}, sort_keys=True))
asyncio.run(main())
PY
echo "L5_INC_AWS_AUTH_PREFLIGHT_OK"

# Public candidate readiness from the exact image. At least one family must be actionable.
docker run --rm --network host -e GEMINI_L5_QUARANTINE_EVENT_TICKERS="$QUARANTINE" "$IMAGE" python -u - <<'PY'
import asyncio, json
from src import gemini_l5_incremental_live_q1 as m
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
async def main():
    c = await discover_eth5m(); c_ok=False
    if c is not None and c.ticker not in m._quarantine():
        try:
            b,_,a,_ = await m.base.fresh_top(c.symbol, timeout=2.0); c_ok=m.acceptable_model(m.base.pair_model(b,a)) and m.MIN_PRICE <= m.base.pair_model(b,a)["yes_quote"] <= m.MAX_PRICE and m.MIN_PRICE <= m.base.pair_model(b,a)["no_quote"] <= m.MAX_PRICE
        except Exception: c_ok=False
    s = await m.discover_sports(m._quarantine()); s_ok=s is not None
    print(json.dumps({"type":"L5_INC_PUBLIC_READINESS","crypto":c_ok,"sports":s_ok,"crypto_ticker":None if c is None else c.ticker,"sports_ticker":None if s is None else s.ticker,"order_entry":False}, sort_keys=True))
    if not (c_ok or s_ok): raise SystemExit(2)
asyncio.run(main())
PY
echo "L5_INC_AWS_PUBLIC_READY"

# Consume the one-use approval only after all safety gates pass.
printf '%s\n' "approval_id=$SAFE commit=$PINNED_COMMIT consumed_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$USED.tmp"
chmod 0640 "$USED.tmp"; sync "$USED.tmp"; mv "$USED.tmp" "$USED"; sync "$APPROVAL_DIR"

SESSION="incq1-${SAFE:0:10}-$(date -u +%H%M%S)"
echo "L5_INC_AWS_LIVE_START session=$SESSION q=1 max_cycles_per_family=2 max_new_residual_notional=1.40 auto_restart=false quarantine=$QUARANTINE"
docker run --name "$CONTAINER" --network host --restart no \
  --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_L5_INCREMENTAL_LIVE_Q1=YES \
  -e GEMINI_L5_INCREMENTAL_SESSION_ID="$SESSION" \
  -e GEMINI_L5_INCREMENTAL_ARM="$SESSION" \
  -e GEMINI_L5_INCREMENTAL_STATE_FILE=/state/session.json \
  -e GEMINI_L5_QUARANTINE_EVENT_TICKERS="$QUARANTINE" \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_l5_incremental_live_q1_bounded
EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")"
echo "L5_INC_AWS_TERMINAL exit=$EXIT"
docker logs --tail 1200 "$CONTAINER" 2>&1 || true
test "$EXIT" = "0"

# Final authenticated read-only assertion: no active prediction orders remain.
docker run -i --rm --network host --env-file "$ENV_FILE" "$IMAGE" python -u - <<'PY'
import asyncio, json
from src.gemini_prediction_auth import private_rest_request
async def main():
    r = await private_rest_request("POST", "/v1/prediction-markets/orders/active", params={"limit":100,"offset":0}, timeout=5.0)
    if r.status_code != 200: raise RuntimeError(f"final active order check http={r.status_code}")
    body=r.json(); orders=body.get("orders",[]) if isinstance(body,dict) else []
    print(json.dumps({"type":"L5_INC_FINAL_ACTIVE_ORDERS","count":len(orders),"order_entry":False,"mutation":False}, sort_keys=True))
    if orders: raise RuntimeError(f"active Gemini prediction orders remain count={len(orders)}")
asyncio.run(main())
PY
cat "$STATE_DIR/session.json"
echo "L5_INC_AWS_SEQUENCE_COMPLETE realized=false rewards_counted=false auto_restart=false"
