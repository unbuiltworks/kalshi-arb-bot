#!/bin/bash
# owner 14:3xZ "3 contracts, $9, after this batch": arm 4 windows from 16:00Z once the 14:45-15:30Z batch is done and flat.
cd "$(dirname "$0")"
export KIT_SRC=/home/user/wt-cap KIT_SHA=5fa0e14b3d6b48dd98598051a2e4b738e62a05a0
python3 -u - <<'PY'
import time, datetime as dt
from bal_common import *
c=client()
while dt.datetime.now(dt.timezone.utc) < dt.datetime(2026,11,13,15,46,0,tzinfo=dt.timezone.utc): time.sleep(10)
for _ in range(40):
    hb=c.heartbeat() or {}
    e=str((hb.get("account_exposure") or {}).get("account_total_worst_case_usd")); d=(hb.get("day_realized") or {}).get("utc_day_realized_usd")
    print(dt.datetime.now(dt.timezone.utc).isoformat()[11:19],"exposure",e,"day",d,flush=True)
    if e=="0" and d is not None: break
    time.sleep(15)
PY
./orchestrate40.sh
