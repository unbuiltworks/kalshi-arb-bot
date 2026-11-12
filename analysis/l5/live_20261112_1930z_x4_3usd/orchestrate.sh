#!/bin/bash
# $3 batch: wait for zero exposure (19:00Z residual settles ~19:15) -> arm FOUR windows from the first start with lead >= 480 s.
# Service source is pinned to 64223094a, so variable writes deploy our SHA (no extra exact-SHA redeploys).
set -e
S=/tmp/claude-0/-home-user-kalshi-arb-bot/25810578-579e-58a1-b347-d472915aca64/scratchpad
cd "$(dirname "$0")"
START=$(python3 -u - <<'PY'
import time, datetime as dt, sys
from bal_common import *
c=client()
while True:
    hb=c.heartbeat() or {}; d=c.latest_deployment()
    exp=str((hb.get("account_exposure") or {}).get("account_total_worst_case_usd"))
    now=dt.datetime.now(dt.timezone.utc)
    q=now.replace(minute=(now.minute//15)*15, second=0, microsecond=0)
    while (q-now).total_seconds() < 480: q += dt.timedelta(minutes=15)
    print(now.isoformat()[11:19], "sha", d["commitHash"][:9], d["status"], "exposure", exp, "next start", q.isoformat()[11:16], file=sys.stderr, flush=True)
    if exp == "0" and d["commitHash"] == SHA and d["status"] == "SUCCESS" and (q-now).total_seconds() <= 1500:
        print(q.isoformat()); break
    if now > dt.datetime(2026,11,12,19,30,tzinfo=dt.timezone.utc): print("GIVEUP"); break
    time.sleep(30)
PY
)
echo "START=$START"; [ "$START" = "GIVEUP" ] && exit 5
export KIT_START="$START"
echo "== arm 4 windows from $START"; python3 rec_apply.py armed.json | grep -v "^document"
python3 - <<'PY'
from bal_common import *
c=client(); b=c.latest_deployment()
if b["commitHash"]!=SHA:
    c._gql("mutation($s:String!,$e:String!,$c:String){serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c)}",{"s":SVC,"e":E,"c":SHA})
    print("ON SHA", client().wait_for_deployment(expect_sha=SHA, timeout_s=600, poll_s=8, not_before=b["createdAt"])["commitHash"][:9])
else: print("on SHA", b["status"])
PY
python3 -c "import time;time.sleep(60)"; python3 bal_verify.py armed.json | head -12 || true
