#!/usr/bin/env bash
set -euo pipefail
SOURCE_REPO=${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}
WORKTREE=${WORKTREE:-/opt/arb/gemini-l5-breadth-q1-live}
STATE_DIR=${STATE_DIR:-/opt/arb/state/gemini-l5-breadth-q1}
BRANCH=feature/gemini-l5-breadth-q1
PINNED_COMMIT=${PINNED_COMMIT:-}
APPROVAL_ID=${ARB_GEMINI_L5_BREADTH_Q1_APPROVAL_ID:-}
APPROVED=${ARB_GEMINI_L5_BREADTH_Q1_APPROVED:-NO}
CONTAINER=arb-gemini-l5-breadth-q1

[ "$APPROVED" = YES ] || { echo missing_approval >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo bad_pinned_commit >&2; exit 1; }
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || { echo bad_approval_id >&2; exit 1; }

ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
[ -f "$ENV_FILE" ] || ENV_FILE=$SOURCE_REPO/.env
[ -f "$ENV_FILE" ] || { echo missing_env >&2; exit 1; }
mkdir -p "$STATE_DIR"; chmod 0750 "$STATE_DIR" || true

# Serialize live prediction-market experiments. Never overlap Kalshi or another Gemini canary.
if docker ps --format '{{.Names}}' | grep -Eq '^arb-(kalshi-policy-d|gemini-.*(live|q1|canary|hour|threshold|incentive|ladder|breadth))'; then
  echo blocked_other_live_process >&2
  docker ps --format '{{.Names}}' >&2
  exit 1
fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

SAFE=$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')
USED=$STATE_DIR/${SAFE}.used
[ ! -e "$USED" ] || { echo approval_already_used >&2; exit 1; }

git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then
  git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true
fi
rm -rf "$WORKTREE"
git -C "$SOURCE_REPO" worktree prune
git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
IMAGE=arb-gemini-l5-breadth:q1-${PINNED_COMMIT:0:12}
docker build -t "$IMAGE" "$WORKTREE" >/dev/null

# Exact-image focused safety suite.
docker run --rm --network none "$IMAGE" python -m pytest -q \
  tests/test_gemini_l5_breadth_q1.py \
  tests/test_gemini_short_crypto_inventory_rest_v3.py \
  tests/test_gemini_short_crypto_inventory_fresh_top_retry.py
echo L5_BREADTH_SAFETY_TESTS_OK

# Critical property: disarmed execution makes no venue calls.
docker run --rm --network none "$IMAGE" python -m src.gemini_l5_breadth_q1 | grep -q '"stage":"DISARMED"'
echo L5_BREADTH_DISARMED_OK

# Account must be completely clean before breadth mutation. No quarantined carry is allowed.
docker run --rm --network host --env-file "$ENV_FILE" "$IMAGE" python - <<'PY'
import asyncio
from decimal import Decimal as D
from src.gemini_prediction_auth import private_rest_request
async def main():
    a=await private_rest_request('POST','/v1/prediction-markets/orders/active',params={'limit':1000,'offset':0},timeout=8.0)
    p=await private_rest_request('POST','/v1/prediction-markets/positions',params={'limit':1000,'offset':0,'sort':'+expiryDate'},timeout=8.0)
    if a.status_code!=200 or p.status_code!=200: raise RuntimeError('account read failed')
    orders=a.json().get('orders',[])
    positions=[]
    for row in p.json().get('positions',[]):
        if not isinstance(row,dict): continue
        q=D(str(row.get('totalQuantity') or '0'))
        if q>0: positions.append((str(row.get('symbol') or ''),str(q),str(row.get('eventOutcome') or '')))
    if orders: raise RuntimeError(f'active prediction orders={len(orders)}')
    if positions: raise RuntimeError(f'open prediction positions={positions}')
    print({'active_orders':0,'open_positions':0,'mutation':False})
asyncio.run(main())
PY
echo L5_BREADTH_ACCOUNT_PREFLIGHT_OK

# Public readiness: require at least two independent safe 5m markets right now.
docker run --rm --network host "$IMAGE" python - <<'PY'
import asyncio
from src import gemini_l5_breadth_q1 as m
async def main():
    events=await m.discover_5m(); ready=[]
    for asset,event in events:
        bid,bs,ask,asz=await m.fresh_top_retry(event.symbol)
        model=m.quote_pair(bid,ask)
        if m.safe_pair(model):
            ready.append({'asset':asset,'event':event.ticker,'symbol':event.symbol,'yes':str(model['yes']),'no':str(model['no']),'edge':str(model['edge']),'yes_queue':str(bs),'no_queue':str(asz)})
    if len(ready)<2: raise SystemExit(f'only {len(ready)} safe breadth markets')
    print({'safe_markets':ready,'n':len(ready),'order_entry':False})
asyncio.run(main())
PY
echo L5_BREADTH_PUBLIC_READY

# Approval is consumed immediately before live mutation.
printf '%s\n' "approval=$SAFE commit=$PINNED_COMMIT consumed=$(date -u +%FT%TZ)" > "$USED"
SID=brq1-${SAFE:0:10}-$(date -u +%H%M%S)
STATE_FILE=$STATE_DIR/${SAFE}.json
echo "L5_BREADTH_LIVE_START session=$SID q=1 max_markets=4 max_residual_per_market=1 auto_restart=false"
docker run --name "$CONTAINER" --network host --restart no --env-file "$ENV_FILE" \
  -e PYTHONUNBUFFERED=1 \
  -e GEMINI_L5_BREADTH_LIVE_Q1=YES \
  -e GEMINI_L5_BREADTH_SESSION_ID="$SID" \
  -e GEMINI_L5_BREADTH_ARM="$SID" \
  -e GEMINI_L5_BREADTH_STATE_FILE=/state/session.json \
  -v "$STATE_DIR:/state" \
  "$IMAGE" python -u -m src.gemini_l5_breadth_q1
EXIT=$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")
docker logs --tail 1200 "$CONTAINER" 2>&1 || true
echo "L5_BREADTH_TERMINAL exit=$EXIT"
test "$EXIT" = 0

# Normal exit must leave zero active prediction orders. Residual positions may remain for settlement.
docker run --rm --network host --env-file "$ENV_FILE" "$IMAGE" python - <<'PY'
import asyncio, json
from decimal import Decimal as D
from src.gemini_prediction_auth import private_rest_request
async def main():
    a=await private_rest_request('POST','/v1/prediction-markets/orders/active',params={'limit':1000,'offset':0},timeout=8.0)
    p=await private_rest_request('POST','/v1/prediction-markets/positions',params={'limit':1000,'offset':0,'sort':'+expiryDate'},timeout=8.0)
    if a.status_code!=200 or p.status_code!=200: raise RuntimeError('final account read failed')
    orders=a.json().get('orders',[])
    if orders: raise RuntimeError(f'active prediction orders remain={len(orders)}')
    positions=[]
    for row in p.json().get('positions',[]):
        if not isinstance(row,dict): continue
        q=D(str(row.get('totalQuantity') or '0'))
        if q>0: positions.append({'symbol':str(row.get('symbol') or ''),'qty':str(q),'outcome':str(row.get('eventOutcome') or '')})
    print(json.dumps({'active_prediction_orders':0,'open_positions':positions,'mutation':False},sort_keys=True))
asyncio.run(main())
PY
echo L5_BREADTH_SEQUENCE_COMPLETE
