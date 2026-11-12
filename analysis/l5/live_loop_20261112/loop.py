"""Owner 2026-11-12 14:35Z: "just keep going. keep opening windows and keep adjusting".
Rolling one-window-at-a-time BTC15m campaign. For each target window T (every 30 min):
  at T-30m+20s  restore the previous window's locks (refuses while orders are at the venue)
                -> exact SHA -> build T's kit from the template -> arm T (all preconditions) -> exact SHA.
Stops on any failure, on the daily halt or lifetime room (rec_apply preconditions), or at the last target.
Settings for each window come from TEMPLATE_DIR/bal_common.py at build time, so an adjustment made
to the template between windows applies to the next one."""
import os, sys, shutil, subprocess, time, datetime as dt
UTC = dt.timezone.utc
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(BASE, "live_20261112_1445z")
def kit(ws):
    d = os.path.join(BASE, "live_20261112_%sz" % ws.strftime("%H%M"))
    if os.path.exists(d): shutil.rmtree(d)
    shutil.copytree(TEMPLATE, d, ignore=shutil.ignore_patterns("armed.json", "__pycache__", "orchestrate.sh"))
    hm = ws.strftime("%H%M"); tag = ws.strftime("%Y-%m-%dT%H:%M:00Z")
    for f in ("bal_common.py", "rec_apply.py"):
        p = os.path.join(d, f); s = open(p).read()
        s = s.replace("cpc-btc-updown-15m-2026-11-12-1445z", "cpc-btc-updown-15m-2026-11-12-%sz" % hm)
        s = s.replace("A = dt.datetime(2026, 11, 12, 14, 45, 0, tzinfo=dt.timezone.utc)",
                      "A = dt.datetime(2026, 11, 12, %d, %d, 0, tzinfo=dt.timezone.utc)" % (ws.hour, ws.minute))
        s = s.replace("owner-20261112-1445z-afbaf751-recycle", "owner-20261112-%sz-afbaf751-loop" % hm)
        s = s.replace('"2026-11-12T14:45:00Z"', '"%s"' % tag)
        open(p, "w").write(s)
    return d
REDEPLOY = r'''
from bal_common import *
import time
c=client(); b=c.latest_deployment()
if b["commitHash"]!=SHA or b["status"]!="SUCCESS":
    c._gql("mutation($s:String!,$e:String!,$c:String){serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c)}",{"s":SVC,"e":E,"c":SHA})
    d=client().wait_for_deployment(expect_sha=SHA, timeout_s=600, poll_s=8, not_before=b["createdAt"]); print("ON SHA", d["id"][:8], flush=True)
time.sleep(50)
'''
def run(cmd, cwd):
    print("$", " ".join(cmd), "@", os.path.basename(cwd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    out = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not l.startswith("document"))
    print(out[-2500:], flush=True)
    return r.returncode
def main():
    prev = dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=UTC)   # window currently armed
    last = dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=UTC)   # last window to arm
    prev_dir = os.path.join(BASE, "live_20261112_%sz" % prev.strftime("%H%M"))
    T = prev + dt.timedelta(minutes=30)
    while T <= last:
        when = (prev + dt.timedelta(minutes=15, seconds=20)).timestamp()
        while time.time() < when: time.sleep(5)
        print("==== window", prev.strftime("%H%M"), "-> restore; next", T.strftime("%H%M"), dt.datetime.now(UTC).isoformat()[11:19], flush=True)
        if run([sys.executable, "bal_restore.py", "--write"], prev_dir) != 0: print("STOP: restore failed"); return 2
        d = kit(T)
        run([sys.executable, "-c", REDEPLOY], d)
        if run([sys.executable, "rec_apply.py", "armed.json"], d) != 0: print("STOP: arming refused"); run([sys.executable, "-c", REDEPLOY], d); return 3
        run([sys.executable, "-c", REDEPLOY], d)
        run([sys.executable, "bal_verify.py", "armed.json"], d)
        prev, prev_dir, T = T, d, T + dt.timedelta(minutes=30)
    when = (prev + dt.timedelta(minutes=15, seconds=20)).timestamp()
    while time.time() < when: time.sleep(5)
    print("==== final restore", prev.strftime("%H%M"), flush=True)
    return run([sys.executable, "bal_restore.py", "--write"], prev_dir)
if __name__ == "__main__":
    sys.exit(main())
