#!/bin/bash
# 15:15Z restore -> exact SHA -> 15:45Z arm -> exact SHA. Each step stops the chain on failure.
set -e
cd "$(dirname "$0")/../live_20261112_1515z"
python3 -c "
import time,datetime as dt
t=dt.datetime(2026,11,12,15,30,20,tzinfo=dt.timezone.utc).timestamp()
while time.time()<t: time.sleep(5)"
echo "== restore 15:15Z"; python3 bal_restore.py --write
cd ../live_20261112_1545z
redeploy() { python3 - <<'PY'
from bal_common import *
c=client(); b=c.latest_deployment()
if b["commitHash"]!=SHA or b["status"]!="SUCCESS":
    c._gql("mutation($s:String!,$e:String!,$c:String){serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c)}",{"s":SVC,"e":E,"c":SHA})
    d=client().wait_for_deployment(expect_sha=SHA, timeout_s=600, poll_s=8, not_before=b["createdAt"]); print("ON SHA", d["id"][:8], d["commitHash"][:9])
else: print("already on SHA")
import time; time.sleep(50)
PY
}
echo "== exact SHA after restore"; redeploy
echo "== arm 15:45Z"; python3 rec_apply.py armed.json | grep -v "^document"
echo "== exact SHA after arm"; redeploy
python3 bal_verify.py armed.json | head -12
