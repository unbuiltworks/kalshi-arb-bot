#!/bin/bash
# owner 13:4xZ "TTL 120 only": after the 13:30-14:15Z batch, arm the next 4 windows with TTL 120. Waits for zero exposure.
cd "$(dirname "$0")"
export KIT_SRC=/home/user/wt-cap KIT_SHA=5fa0e14b3d6b48dd98598051a2e4b738e62a05a0
python3 -u - <<'PY'
import time, datetime as dt
from bal_common import *
c=client()
while dt.datetime.now(dt.timezone.utc) < dt.datetime(2026,11,13,14,31,0,tzinfo=dt.timezone.utc): time.sleep(10)
for _ in range(60):
    hb=c.heartbeat() or {}
    e=str((hb.get("account_exposure") or {}).get("account_total_worst_case_usd")); d=(hb.get("day_realized") or {}).get("utc_day_realized_usd")
    print(dt.datetime.now(dt.timezone.utc).isoformat()[11:19],"exposure",e,"day",d,flush=True)
    if e=="0" and d is not None: break
    time.sleep(15)
PY
./orchestrate40.sh
