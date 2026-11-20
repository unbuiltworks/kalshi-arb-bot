"""Run in the BTC5m code line. Feeds each published record to the REAL
`account_risk` with the lane's own settled results and prints what the
combined UTC-day figure becomes, against the truth."""
import datetime as dt
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_btc5m_runner as RUN           # noqa: E402

NOW = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
A, B = "cpc-btc-updown-5m-2026-11-20-1420z", "cpc-btc-updown-5m-2026-11-20-1705z"
OTHER = D("-1.00")
#: the lane's OWN settled results today, per scenario (from its own fills)
OWN = {"S1": {B: "-0.05"}, "S2": {B: "-0.0151"}, "S3": {B: "-0.05"},
       "S4": {A: "-0.0114"}, "S5": {A: "-0.03", B: "0.05"},
       "S6": {A: "-0.0114", B: "-0.0151"}, "S7": {B: "-0.05"},
       "S8": {B: "-0.05"}, "S9": {B: "-0.05"}, "S10": {B: "-0.05"}}
pub = json.load(open(sys.argv[1]))
new_lane = hasattr(RUN, "OwnSettled")
rows = {}
for name, risk in pub["scenarios"].items():
    key = name.split()[0]
    o = {m: D(v) for m, v in OWN[key].items()}
    total = sum(o.values(), D(0))
    own = RUN.OwnSettled.of(o) if new_lane else min(D(0), total)
    for label, age, fetch in (
            ("fresh", 3, None), ("stale 31 s", 31, None),
            ("unreachable", 3, "boom")):
        if label != "fresh" and key != "S2":
            continue
        doc = {"as_of_utc": (NOW - dt.timedelta(seconds=age)).isoformat(),
               "account_risk": risk}

        def f(_u, doc=doc, fetch=fetch):
            if fetch:
                raise TimeoutError()
            return doc
        r = RUN.account_risk(url="http://x", now=NOW, own_unaccounted=own,
                             fetch=f)
        truth = OTHER + total
        if r.get("ok") is not True:
            verdict, day = "REFUSES (%s)" % r.get("reason"), None
        else:
            day = D(r["utc_day_realized_usd"])
            verdict = ("EXACT" if day == truth else
                       "CONSERVATIVE by %s" % (truth - day) if day < truth
                       else "UNDERSTATES LOSS by %s" % (day - truth))
        rows["%s | %s" % (name, label)] = {
            "combined_day": None if day is None else str(day),
            "truth": str(truth), "verdict": verdict}
print(json.dumps({"new_lane": new_lane,
                  "publishes_results": pub["publishes_results"],
                  "rows": rows}))
