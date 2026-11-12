"""Print state changes until <HH:MM:SS> UTC today (max ~9 minutes per call). Read-only.
usage: bal_mon.py HH:MM:SS"""
import time, json, datetime as dt, sys
from bal_common import *
c = client()
hh, mm, ss = [int(x) for x in sys.argv[1].split(":")]
now = dt.datetime.now(dt.timezone.utc); END = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
prev = None; seen = set()
while dt.datetime.now(dt.timezone.utc) < END:
    try:
        hb = c.heartbeat() or {}
        sg = hb.get("submission_gate") or {}; op = hb.get("order_progress") or {}; cam = hb.get("campaign") or {}; dr = hb.get("day_realized") or {}
        ae = hb.get("account_exposure") or {}
        key = (sg.get("reason"), sg.get("armed"), op.get("live_orders"), op.get("parked_orders"), cam.get("windows_spent"), cam.get("submissions_spent"), hb.get("market"),
               dr.get("all_time_realized_usd"), str(ae.get("account_total_worst_case_usd")), str(hb.get("pending_terminal")), str(hb.get("quarantined")))
        if key != prev:
            print("%s mkt=%s armed=%s reason=%-20s live=%s parked=%s pending=%s quar=%s win=%s subs=%s exposure=%s realized=%s" % (
                str(hb.get("utc"))[11:19], str(hb.get("market"))[-6:], sg.get("armed"), sg.get("reason"), op.get("live_orders"), op.get("parked_orders"),
                len(hb.get("pending_terminal") or []), len(hb.get("quarantined") or []), cam.get("windows_spent"), cam.get("submissions_spent"),
                ae.get("account_total_worst_case_usd"), dr.get("all_time_realized_usd")), flush=True)
            prev = key
        for lab in ("L5_LIVE_ORDER", "L5_LIVE_ALARM", "L5_PORTFOLIO_WITHDRAW", "L5_LIVE_SETTLED", "L5_LIVE_HALT", "L5_LIVE_UNCERTAIN"):
            r = c.newest_labelled(lab)
            if r:
                s = json.dumps(r.get("payload"), default=str); h = (lab, s[:300])
                if h not in seen:
                    seen.add(h); print("   %-24s %s %s" % (lab, str(r.get("timestamp"))[11:19], s[:520]), flush=True)
    except Exception as e:
        print("   poll error %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
    time.sleep(8)
print("MON END", dt.datetime.now(dt.timezone.utc).isoformat()[11:19], flush=True)
