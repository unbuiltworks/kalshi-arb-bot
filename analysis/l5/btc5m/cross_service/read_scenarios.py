"""Run in the BTC5m code line. Feeds each published record to the REAL
`account_risk` with the lane's own settled results and prints the combined
UTC-day and LIFETIME figures against the truth."""
import datetime as dt
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_btc5m_runner as RUN           # noqa: E402

NOW = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
A = "cpc-btc-updown-5m-2026-11-20-1420z"
B = "cpc-btc-updown-5m-2026-11-20-1705z"
P = "cpc-btc-updown-5m-2026-11-19-1200z"
R = "cpc-btc-updown-5m-2026-11-19-2355z"
OLD = "cpc-btc-updown-5m-2026-11-15-1200z"
TODAY = NOW - dt.timedelta(minutes=5)
WHEN = {A: TODAY, B: TODAY,
        P: dt.datetime(2026, 11, 19, 12, 6, tzinfo=dt.timezone.utc),
        OLD: dt.datetime(2026, 11, 15, 12, 6, tzinfo=dt.timezone.utc)}
ANCHOR_TOTAL, OTHER = D("-39.468"), D("-1.00")
CASH_READ = dt.datetime(2026, 11, 16, 0, 11, 52, tzinfo=dt.timezone.utc)
#: the lane's OWN settled results (from its own fills): {market: (when, pnl)}
OWN = {"S1": {B: "-0.05"}, "S2": {B: "-0.0151"}, "S3": {B: "-0.05"},
       "S4": {A: "-0.0114"}, "S5": {A: "-0.03", B: "0.05"},
       "S6": {A: "-0.0114", B: "-0.0151"}, "S8": {B: "-0.05"},
       "S9": {B: "-0.05"}, "S10": {B: "-0.05"},
       "L1": {P: "-0.30"}, "L2": {P: "-0.30"}, "L3": {OLD: "-5.00"},
       "L4": {R: "-0.20"}, "L5": {R: "-0.20"}, "L6": {P: "-0.30"},
       "L7": {P: "-0.30"}, "L8": {P: "-0.30"}}
ROLL = {"L4": dt.datetime(2026, 11, 19, 23, 59, 40, tzinfo=dt.timezone.utc),
        "L5": dt.datetime(2026, 11, 20, 0, 0, 10, tzinfo=dt.timezone.utc)}
pub = json.load(open(sys.argv[1]))
new_lane = hasattr(RUN, "_own_not_in_lifetime")


def verdict(got, truth):
    if got == truth:
        return "EXACT"
    return ("CONSERVATIVE by %s" % (truth - got) if got < truth
            else "UNDERSTATES LOSS by %s" % (got - truth))


rows = {}
for name, risk in pub["scenarios"].items():
    key = name.split()[0]
    hist = {m: {"at": ROLL.get(key, WHEN.get(m)), "pnl": D(v)}
            for m, v in OWN[key].items()}
    today = {m: r["pnl"] for m, r in hist.items()
             if r["at"].date() == NOW.date()}
    total = sum(today.values(), D(0))
    own = RUN.OwnSettled.of(today, hist) if new_lane \
        else min(D(0), total)
    for label, age, boom in (("fresh", 3, False), ("stale 31 s", 31, False),
                             ("unreachable", 3, True)):
        if label != "fresh" and key not in ("S2", "L1"):
            continue
        doc = {"as_of_utc": (NOW - dt.timedelta(seconds=age)).isoformat(),
               "account_risk": risk}

        def f(_u, doc=doc, boom=boom):
            if boom:
                raise TimeoutError()
            return doc
        r = RUN.account_risk(url="http://x", now=NOW, own_unaccounted=own,
                             fetch=f)
        t_day = OTHER + total
        t_life = ANCHOR_TOTAL + OTHER + sum(
            (x["pnl"] for x in hist.values() if x["at"] > CASH_READ), D(0))
        if r.get("ok") is not True and "utc_day_realized_usd" not in r:
            rows["%s | %s" % (name, label)] = {
                "day": "REFUSES (%s)" % r.get("reason"), "life": "REFUSES"}
            continue
        rows["%s | %s" % (name, label)] = {
            "day": "%s %s" % (r["utc_day_realized_usd"], verdict(
                D(r["utc_day_realized_usd"]), t_day)),
            "life": "%s %s" % (r["lifetime_realized_usd"], verdict(
                D(r["lifetime_realized_usd"]), t_life))}
print(json.dumps({"new_lane": new_lane, "patched": pub["patched"],
                  "rows": rows}))
