#!/bin/bash
# owner 21:3xZ "Keep $10, raise code cap": deploy 743985333, then arm 22:30-23:15Z (4 windows, $3, approved daily 10).
cd "$(dirname "$0")"
export KIT_SHA=7439853331e1d665fb5e26f2cd13a67caa9504d0 KIT_START=2026-11-12T22:30:00+00:00
python3 -u - <<'PY'
import time, datetime as dt
from bal_common import *
c=client()
while True:
    d=c.latest_deployment(); print(dt.datetime.now(dt.timezone.utc).isoformat()[11:19], d["commitHash"][:9], d["status"], flush=True)
    if d["commitHash"]==SHA and d["status"]=="SUCCESS": break
    if d["commitHash"]==SHA and d["status"] in ("FAILED","CRASHED"): raise SystemExit("DEPLOY FAILED")
    time.sleep(20)
PY
echo "== dry $(date -u +%T)"; python3 rec_apply.py /tmp/claude-0/-home-user-kalshi-arb-bot/25810578-579e-58a1-b347-d472915aca64/scratchpad/dry_h10.json --dry | grep -E "PRE|STOP|DRY|runtime" | cut -c1-1500
python3 -c "
import time,datetime as dt
while dt.datetime.now(dt.timezone.utc) < dt.datetime(2026,11,12,22,8,0,tzinfo=dt.timezone.utc): time.sleep(5)"
echo "== arm $(date -u +%T)"; python3 rec_apply.py armed.json | grep -v "^document" | cut -c1-1500
python3 -c "import time;time.sleep(45)"
python3 -c "
from bal_common import *
c=client(); v=c.variables(); hb=c.heartbeat() or {}
print('VARS', {k:v.get(k) for k in ('L5_DAILY_REALIZED_LOSS_HALT_USD','L5_MAX_TOTAL_EXPOSURE_USD','L5_ACC_MAX_ROUNDS','L5_TS_EXPERIMENT_ID','L5_LIVE_AUTH_MAX_WINDOWS')})
ap=hb.get('authorization_preflight') or {}
print('SHA', c.latest_deployment()['commitHash'][:9], 'preflight', ap.get('ok'), ap.get('fail_reasons'), 'awaiting', ap.get('awaiting_authorized_window'))
"
for t in 22:38:00 22:47:00 22:56:00 23:05:00 23:14:00 23:23:00 23:32:00; do python3 bal_mon.py $t; done
