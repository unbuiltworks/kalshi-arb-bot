#!/usr/bin/env bash
set -euo pipefail
SOURCE_REPO=${SOURCE_REPO:-/home/ssm-user/kalshi-arb-bot}
WORKTREE=${WORKTREE:-/opt/arb/gemini-l5-wide-book-q1-live}
STATE_DIR=${STATE_DIR:-/opt/arb/state/gemini-l5-wide-book-q1}
BRANCH=feature/gemini-l5-wide-book-q1
PINNED_COMMIT=${PINNED_COMMIT:-}
APPROVAL_ID=${ARB_GEMINI_L5_WIDE_BOOK_Q1_APPROVAL_ID:-}
APPROVED=${ARB_GEMINI_L5_WIDE_BOOK_Q1_APPROVED:-NO}
CONTAINER=arb-gemini-l5-wide-book-q1
QUARANTINE=GEMI-TENNIS-ATPUSO-20260830-SHA2-TRU-SHA2
[ "$APPROVED" = YES ] || { echo missing_approval >&2; exit 1; }
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$APPROVAL_ID" =~ ^[A-Za-z0-9_-]{6,80}$ ]] || exit 1
ENV_FILE=/opt/arb/secrets/gemini-short-crypto.env
[ -f "$ENV_FILE" ] || ENV_FILE=$SOURCE_REPO/.env
[ -f "$ENV_FILE" ] || exit 1
mkdir -p "$STATE_DIR"; chmod 0750 "$STATE_DIR" || true
if docker ps --format '{{.Names}}' | grep -Eq '^arb-(kalshi-policy-d|gemini-.*(live|q1|canary|hour|threshold|incentive))'; then echo blocked_other_live_process >&2; docker ps --format '{{.Names}}' >&2; exit 1; fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
SAFE=$(printf '%s' "$APPROVAL_ID" | tr -cd 'A-Za-z0-9_-')
USED=$STATE_DIR/${SAFE}.used
[ ! -e "$USED" ] || { echo approval_already_used >&2; exit 1; }
git -C "$SOURCE_REPO" fetch origin "$BRANCH" >/dev/null
git -C "$SOURCE_REPO" cat-file -e "${PINNED_COMMIT}^{commit}"
git -C "$SOURCE_REPO" merge-base --is-ancestor "$PINNED_COMMIT" "origin/$BRANCH"
if [ -e "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ]; then git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE" || true; fi
rm -rf "$WORKTREE"; git -C "$SOURCE_REPO" worktree prune; git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" "$PINNED_COMMIT" >/dev/null
IMAGE=arb-gemini-l5-wide-book:q1-${PINNED_COMMIT:0:12}
docker build -t "$IMAGE" "$WORKTREE" >/dev/null
docker run --rm --network none "$IMAGE" python -m pytest -q tests/test_gemini_l5_wide_book_q1.py tests/test_gemini_l5_ladder_q1.py tests/test_gemini_short_crypto_inventory_rest_v3.py tests/test_gemini_short_crypto_inventory_fresh_top_retry.py
echo L5_WIDE_BOOK_SAFETY_TESTS_OK
docker run --rm --network none "$IMAGE" python -m src.gemini_l5_wide_book_q1 | grep -q '"stage":"DISARMED"'
echo L5_WIDE_BOOK_DISARMED_OK
# Read-only account gate. No active orders; only the already-quarantined tennis q1 may coexist.
docker run --rm --network host --env-file "$ENV_FILE" -e ALLOWED="$QUARANTINE" "$IMAGE" python - <<'PY'
import asyncio, os
from decimal import Decimal as D
from src.gemini_prediction_auth import private_rest_request
async def main():
    a=await private_rest_request('POST','/v1/prediction-markets/orders/active',params={'limit':1000,'offset':0},timeout=8.0)
    p=await private_rest_request('POST','/v1/prediction-markets/positions',params={'limit':1000,'offset':0,'sort':'+expiryDate'},timeout=8.0)
    if a.status_code!=200 or p.status_code!=200: raise RuntimeError('account read failed')
    orders=a.json().get('orders',[]); pos=p.json().get('positions',[])
    if orders: raise RuntimeError(f'active prediction orders={len(orders)}')
    allowed=os.environ['ALLOWED'].upper(); bad=[]; seen=[]
    for r in pos:
        if not isinstance(r,dict): continue
        q=D(str(r.get('totalQuantity') or '0'))
        if q<=0: continue
        s=str(r.get('symbol') or '').upper(); seen.append((s,str(q)))
        if s!=allowed: bad.append((s,str(q)))
    if bad: raise RuntimeError(f'unexpected positions={bad}')
    print({'active_orders':0,'allowed_existing_positions':seen,'mutation':False})
asyncio.run(main())
PY
echo L5_WIDE_BOOK_ACCOUNT_PREFLIGHT_OK
# Fresh public edge gate immediately before consuming approval.
docker run --rm --network host "$IMAGE" python - <<'PY'
import asyncio
from decimal import Decimal as D
from src import gemini_l5_wide_book_q1 as m
async def main():
    e=await m.discover_eth5m()
    if e is None or e.seconds_to_expiry<m.MIN_TTE: raise SystemExit('no ready ETH5 event')
    bid,bs,ask,asz=await m.ladder.fresh_top_retry(e.symbol)
    y=D(str(bid)); n=(D('1')-D(str(ask))).quantize(D('0.01')); edge=m.edge(y,n)
    if not m.safe(y,n): raise SystemExit(f'wide-book edge gate failed yes={y} no={n} edge={edge}')
    print({'event':e.ticker,'symbol':e.symbol,'yes':str(y),'no':str(n),'edge':str(edge),'yes_queue':str(bs),'no_queue':str(asz),'order_entry':False})
asyncio.run(main())
PY
echo L5_WIDE_BOOK_PUBLIC_READY
printf '%s\n' "approval=$SAFE commit=$PINNED_COMMIT consumed=$(date -u +%FT%TZ)" > "$USED"
SID=wideq1-${SAFE:0:10}-$(date -u +%H%M%S)
echo "L5_WIDE_BOOK_LIVE_START session=$SID q=1 rungs=1_per_side auto_restart=false"
docker run --name "$CONTAINER" --network host --restart no --env-file "$ENV_FILE" -e PYTHONUNBUFFERED=1 -e GEMINI_L5_WIDE_BOOK_LIVE_Q1=YES -e GEMINI_L5_WIDE_BOOK_SESSION_ID="$SID" -e GEMINI_L5_WIDE_BOOK_ARM="$SID" -e GEMINI_L5_WIDE_BOOK_STATE_FILE=/state/session.json -v "$STATE_DIR:/state" "$IMAGE" python -u -m src.gemini_l5_wide_book_q1
EXIT=$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")
docker logs --tail 800 "$CONTAINER" 2>&1 || true
echo "L5_WIDE_BOOK_TERMINAL exit=$EXIT"
test "$EXIT" = 0
# Exact post-run active-order clean gate.
docker run --rm --network host --env-file "$ENV_FILE" "$IMAGE" python - <<'PY'
import asyncio
from src.gemini_prediction_auth import private_rest_request
async def main():
    r=await private_rest_request('POST','/v1/prediction-markets/orders/active',params={'limit':1000,'offset':0},timeout=8.0)
    if r.status_code!=200: raise RuntimeError('final active-order read failed')
    rows=r.json().get('orders',[])
    if rows: raise RuntimeError(f'active prediction orders remain={len(rows)}')
    print({'active_prediction_orders':0,'mutation':False})
asyncio.run(main())
PY
echo L5_WIDE_BOOK_SEQUENCE_COMPLETE
