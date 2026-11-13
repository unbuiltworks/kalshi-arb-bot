#!/bin/bash
# owner 2026-11-13 "Halt + cap $40, go today": deploy 5fa0e14b3, then arm 4 windows ($3, fidelity, 48 slots, halt 40, lifetime 75).
cd "$(dirname "$0")"
export KIT_SRC=/home/user/wt-cap KIT_SHA=${KIT_SHA:?set KIT_SHA}
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
START=$(python3 -c "
import datetime as dt
n=dt.datetime.now(dt.timezone.utc); q=n.replace(minute=(n.minute//15)*15,second=0,microsecond=0)
while (q-n).total_seconds()<540: q+=dt.timedelta(minutes=15)
print(q.isoformat())")
export KIT_START=$START; echo "== start $START"
echo "== dry $(date -u +%T)"; python3 rec_apply.py /tmp/claude-0/-home-user-kalshi-arb-bot/25810578-579e-58a1-b347-d472915aca64/scratchpad/dry40.json --dry | grep -E "^PRE|STOP|DRY" | cut -c1-1800
python3 rec_apply.py /tmp/claude-0/-home-user-kalshi-arb-bot/25810578-579e-58a1-b347-d472915aca64/scratchpad/dry40.json --dry | grep -q "DRY RUN: every precondition met" || { echo "ABORT dry run failed"; exit 4; }
echo "== arm $(date -u +%T)"; python3 rec_apply.py armed.json | grep -E "^PRE|STOP|DEPLOY|written|L5_" | cut -c1-600
python3 -c "import time;time.sleep(45)"
python3 -c "
from bal_common import *
c=client(); v=c.variables(); hb=c.heartbeat() or {}
print('VARS', {k:v.get(k) for k in ('L5_DAILY_REALIZED_LOSS_HALT_USD','L5_MAX_CUMULATIVE_LOSS_USD','L5_FIDELITY_MODE','L5_TS_ENTRY_CUTOFF_S','L5_TS_COMPLETE_CUTOFF_S','L5_ACC_MAX_ROUNDS','L5_TS_EXPERIMENT_ID')})
ap=hb.get('authorization_preflight') or {}
print('SHA', c.latest_deployment()['commitHash'][:9], 'preflight', ap.get('ok'), ap.get('fail_reasons'), 'awaiting', ap.get('awaiting_authorized_window'))
"
END=$(python3 -c "import datetime as dt,os;print((dt.datetime.fromisoformat(os.environ['KIT_START'])+dt.timedelta(minutes=62)).strftime('%H:%M:%S'))")
while [ "$(date -u +%H:%M:%S)" \< "$END" ]; do python3 bal_mon.py $(python3 -c "import datetime as dt;print((dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=8)).strftime('%H:%M:%S'))"); done
echo BATCH DONE
