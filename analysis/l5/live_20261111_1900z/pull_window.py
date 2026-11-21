"""Read-only: every order-related line the armed process logged for the 19:00Z window."""
import sys, json, re, datetime as dt, collections, time
sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/l5/live_20261111_1900z")
from bal_common import *
c = client()
DEP = "66ccd7ce-46f8-4005-889b-70333a3df879"
W0 = dt.datetime(2026, 11, 11, 19, 0, 0, tzinfo=dt.timezone.utc)
Q = ("query($d:String!,$f:String,$n:Int,$s:DateTime,$e:DateTime){deploymentLogs(deploymentId:$d,filter:$f,limit:$n,startDate:$s,endDate:$e){timestamp message}}")
def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
def pull(f, a=-60, b=760, step=120):
    out, seen = [], set()
    t = a
    while t < b:
        for k in range(3):
            try:
                rows = c._gql(Q, {"d": DEP, "f": f, "n": 500, "s": (W0 + dt.timedelta(seconds=t)).isoformat(), "e": (W0 + dt.timedelta(seconds=min(t + step, b))).isoformat()}).get("deploymentLogs") or []
                break
            except Exception: time.sleep(2); rows = []
        for r in rows:
            key = (r["timestamp"], r["message"][:200])
            if key not in seen: seen.add(key); out.append(r)
        t += step
    return sorted(out, key=lambda r: r["timestamp"])
res = {}
for f in ("L5_LIVE_LIFECYCLE", "L5_LIVE_DECISION", "L5_TS_REQUOTE", "L5_MISSED_PAIR", "L5_BOOK_COMPLETION_RESULT", "L5_TS_COMPLETION", "L5_OPENING_DECISION", "HELD_SIDE_ADD", "BUDGET_EXHAUSTED", "L5_LIVE_ALARM", "Traceback", "L5_LIVE_FILL"):
    rows = pull(f)
    res[f] = [{"t": round((ts(r["timestamp"]) - W0).total_seconds(), 1), "m": r["message"]} for r in rows]
    print(f, len(rows), flush=True)
json.dump(res, open("/home/user/kalshi-arb-bot/data/l5/recent/live_20261111_1900z/window_lines.json", "w"))
