#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO=${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}
WORKTREE=${WORKTREE:-/opt/arb/gemini-l5-dynamic-q1-live}
STATE_DIR=${STATE_DIR:-/opt/arb/state/gemini-l5-dynamic-q1}
BRANCH=feature/gemini-l5-dynamic-q1
PINNED_COMMIT=${PINNED_COMMIT:-}
APPROVAL_ID=${ARB_GEMINI_L5_DYNAMIC_Q1_APPROVAL_ID:-}
APPROVED=${ARB_GEMINI_L5_DYNAMIC_Q1_APPROVED:-NO}
CONTAINER=arb-gemini-l5-dynamic-q1

[ "$APPROVED" = YES ] || { echo missing_approval >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo bad_pinned_commit >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo bad_approval_id >&2; exit 1; }

ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
[ -f "$ENV_FILE" ] || ENV_FILE=$SOURCE_REPO/.env
[ -f "$ENV_FILE" ] || { echo missing_env >&2; exit 1; }
mkdir -p "$STATE_DIR"; chmod 0750 "$STATE_DIR" || true

# Exactly one live prediction-market owner. Distinguish q1 from q10 explicitly.
RUNNING=$(docker ps --format '{{.Names}}|{{.Image}}')
LIVEISH=$(printf '%s\n' "$RUNNING" \
  | awk -F'|' '$1 ~ /^arb-kalshi-policy-d$/ || $1 ~ /^arb-gemini-/ || $2 ~ /^arb-gemini-/ {print}' \
  | grep -E '(^|[-_:])q1([^0-9]|$)|live|canary|hour|threshold|incentive|ladder|breadth|dynamic' \
  || true)
if [ -n "$LIVEISH" ]; then
  echo blocked_other_live_process >&2; printf '%s\n' "$LIVEISH" >&2; exit 1
fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

SAFE=$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')
USED=$STATE_DIR/${SAFE}.used
[ ! -e "$USED" ] || { echo approval_already_used >&2; exit 1; }

git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true; fi
rm -rf "$WORKTREE"; git -C "$SOURCE_REPO" worktree prune; git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null

IMAGE=arb-gemini-l5-dynamic:q1-${PINNED_COMMIT:0:12}
docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact-image execution and upgrade-safety tests.  Completion economics and the
# cancel->inactive-verify->repost sequence are deployment blockers.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_l5_dynamic_q1.py \
  tests/test_gemini_l5_pair_q1.py \
  tests/test_gemini_l5_completion_policy.py \
  tests/test_gemini_l5_dynamic_completion_rescue.py \
  tests/test_gemini_l5_dynamic_exact_stop.py \
  tests/test_gemini_l5_ladder_q1.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py
echo L5_DYNAMIC_SAFETY_TESTS_OK

docker run --rm --network none "$IMAGE" python -m src.gemini_l5_dynamic_q1 | grep -q '"stage":"DISARMED"'
docker run --rm --network none "$IMAGE" python -m src.gemini_l5_pair_q1 | grep -q '"stage":"DISARMED"'
echo L5_DYNAMIC_DISARMED_OK

# Venue account must be truly flat immediately before a fresh owner starts.
docker run --rm --network host --env-file "$ENV_FILE" "$IMAGE" python - <<'PY'
import asyncio
from decimal import Decimal as D
from src.gemini_prediction_auth import private_rest_request
async def main():
    a,p=await asyncio.gather(
        private_rest_request('POST','/v1/prediction-markets/orders/active',params={'limit':1000,'offset':0},timeout=8.0),
        private_rest_request('POST','/v1/prediction-markets/positions',params={'limit':1000,'offset':0,'sort':'+expiryDate'},timeout=8.0),
    )
    if a.status_code!=200 or p.status_code!=200: raise RuntimeError(f'account read failed active={a.status_code} positions={p.status_code}')
    orders=a.json().get('orders',[]); positions=[]
    for row in p.json().get('positions',[]):
        if not isinstance(row,dict): continue
        q=D(str(row.get('totalQuantity') or '0'))
        if q>0: positions.append((str(row.get('symbol') or ''),str(q),str(row.get('eventOutcome') or '')))
    if orders: raise RuntimeError(f'active prediction orders={len(orders)}')
    if positions: raise RuntimeError(f'open prediction positions={positions}')
    print({'active_orders':0,'open_positions':0,'mutation':False})
asyncio.run(main())
PY
echo L5_DYNAMIC_ACCOUNT_PREFLIGHT_OK

# Public-only completion-reserve economics read. No credentials and no order entry.
docker run --rm --network host "$IMAGE" python - <<'PY'
import asyncio
from decimal import Decimal as D
from src import gemini_l5_pair_q1 as p
from src import gemini_l5_ladder_q1 as l
async def main():
    e=await p.discover_eth5m()
    if e is None or e.seconds_to_expiry<105:
        print({'ready':False,'reason':'NO_ETH5_WITH_ENOUGH_TTE','order_entry':False}); return
    bid,bs,ask,asz=await l.fresh_top_retry(e.symbol)
    model=p.choose_pair(bid,ask,min_edge=D('0.01'),yes_queue=bs,no_queue=asz,tie_seed='public-preflight')
    print({'ready':model is not None,'event':e.ticker,'symbol':e.symbol,'bid':str(bid),'ask':str(ask),'model':{k:str(v) for k,v in model.items()} if model else None,'yes_queue':str(bs),'no_queue':str(asz),'completion_reserve_ticks':1,'max_complement_reprices':6,'max_new_residual_qty':1,'order_entry':False})
asyncio.run(main())
PY
echo L5_DYNAMIC_PUBLIC_READ_OK

printf '%s\n' "approval=$SAFE commit=$PINNED_COMMIT consumed=$(date -u +%FT%TZ)" > "$USED"
SID=dynq1-${SAFE:0:10}-$(date -u +%H%M%S)
rm -f "$STATE_DIR/session.json"

echo "L5_DYNAMIC_LIVE_START session=$SID q=1 one_pair_at_a_time=true max_new_residual_qty=1 completion_reserve_ticks=1 dynamic_complement_reprices=6 cancel_before_repost=true exact_inactive_gate=true backoff_per_side=0,1,2c first_wait=45 complement_wait=60 max_matched_q=12 max_campaigns=48 max_session_seconds=21600 min_final_pair_edge=0.01 auto_restart=false"
docker run -d --name "$CONTAINER" --network host --restart no --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_L5_DYNAMIC_LIVE_Q1=YES \
  -e GEMINI_L5_DYNAMIC_SESSION_ID="$SID" \
  -e GEMINI_L5_DYNAMIC_ARM="$SID" \
  -e GEMINI_L5_DYNAMIC_STATE_FILE=/state/session.json \
  -e GEMINI_L5_DYNAMIC_MAX_SESSION_SECONDS=21600 \
  -e GEMINI_L5_DYNAMIC_MAX_CAMPAIGNS=48 \
  -e GEMINI_L5_DYNAMIC_MAX_MATCHED_Q=12 \
  -e GEMINI_L5_DYNAMIC_MIN_TOP_PAIR_EDGE=0.01 \
  -e GEMINI_L5_DYNAMIC_FIRST_WAIT_SECONDS=45 \
  -e GEMINI_L5_DYNAMIC_COMPLEMENT_WAIT_SECONDS=60 \
  -e GEMINI_L5_DYNAMIC_MIN_TTE_SECONDS=105 \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_l5_dynamic_q1 >/dev/null

for i in $(seq 1 15); do
  RUNNING_NOW=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)
  LOGS=$(docker logs --tail 180 "$CONTAINER" 2>&1 || true)
  if grep -q '"stage":"LIVE_START"' <<<"$LOGS"; then break; fi
  if [ "$RUNNING_NOW" != true ]; then echo dynamic_owner_exited_during_startup >&2; echo "$LOGS" >&2; exit 1; fi
  sleep 1
done
LOGS=$(docker logs --tail 240 "$CONTAINER" 2>&1 || true)
grep -q '"stage":"LIVE_START"' <<<"$LOGS"
test "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = true
echo "$LOGS"
echo "L5_DYNAMIC_OWNER_RUNNING container=$CONTAINER session=$SID restart=no max_new_residual_qty=1 completion_reserve_ticks=1 dynamic_complement_reprices=6"