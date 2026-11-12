#!/bin/bash
# owner 20:3xZ "Halt $5 in arm write": arm 21:00-21:45Z (4 windows, $3) after the 19:30Z approval expires (20:45Z).
cd "$(dirname "$0")"
python3 -c "
import time,datetime as dt
while dt.datetime.now(dt.timezone.utc) < dt.datetime(2026,11,12,20,46,0,tzinfo=dt.timezone.utc): time.sleep(5)"
export KIT_START=2026-11-12T21:00:00+00:00
echo "== arm $(date -u +%T)"; python3 rec_apply.py armed.json | grep -v "^document" | cut -c1-1200
python3 -c "import time;time.sleep(45)"
python3 -c "
from bal_common import *
c=client(); v=c.variables(); hb=c.heartbeat() or {}
print('VARS', {k:v.get(k) for k in ('L5_DAILY_REALIZED_LOSS_HALT_USD','L5_MAX_TOTAL_EXPOSURE_USD','L5_ACC_MAX_ROUNDS','L5_TS_EXPERIMENT_ID','L5_LIVE_AUTH_MAX_WINDOWS')})
print('SHA', c.latest_deployment()['commitHash'][:9], 'preflight', (hb.get('authorization_preflight') or {}).get('ok'), (hb.get('authorization_preflight') or {}).get('fail_reasons'), 'awaiting', (hb.get('authorization_preflight') or {}).get('awaiting_authorized_window'))
"
for t in 21:08:00 21:17:00 21:26:00 21:35:00 21:44:00 21:53:00 22:02:00; do python3 bal_mon.py $t; done
