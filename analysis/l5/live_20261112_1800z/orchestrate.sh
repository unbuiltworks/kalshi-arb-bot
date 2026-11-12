#!/bin/bash
# 17:30Z restore -> exact SHA -> arm 18:00Z if lead >= 420 s, else arm 18:15Z -> exact SHA -> verify.
set -e
cd "$(dirname "$0")/../live_20261112_1730z"
python3 -c "
import time,datetime as dt
t=dt.datetime(2026,11,12,17,45,20,tzinfo=dt.timezone.utc).timestamp()
while time.time()<t: time.sleep(5)"
echo "== restore 17:30Z"; python3 bal_restore.py --write || echo "restore deploy-wait error (vars written)"
cd ../live_20261112_1800z
redeploy() { python3 - <<'PY'
from bal_common import *
c=client(); b=c.latest_deployment()
if b["commitHash"]!=SHA or b["status"]!="SUCCESS":
    c._gql("mutation($s:String!,$e:String!,$c:String){serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c)}",{"s":SVC,"e":E,"c":SHA})
    d=client().wait_for_deployment(expect_sha=SHA, timeout_s=600, poll_s=8, not_before=b["createdAt"]); print("ON SHA", d["id"][:8], d["commitHash"][:9])
else: print("already on SHA")
import time; time.sleep(30)
PY
}
echo "== exact SHA after restore"; redeploy
LEAD=$(python3 -c "import time,datetime as dt;print(int(dt.datetime(2026,11,12,18,0,tzinfo=dt.timezone.utc).timestamp()-time.time()))")
if [ "$LEAD" -lt 450 ]; then echo "18:00Z lead $LEAD too short -> 18:15Z"; cd ../live_20261112_1815z
  python3 -c "
import time,datetime as dt
t=dt.datetime(2026,11,12,17,55,0,tzinfo=dt.timezone.utc).timestamp()
while time.time()<t: time.sleep(5)"; fi
echo "== arm $(pwd | grep -o '[0-9]*z$')"; python3 rec_apply.py armed.json | grep -v "^document"
echo "== exact SHA after arm"; redeploy
python3 bal_verify.py armed.json | head -12 || true
