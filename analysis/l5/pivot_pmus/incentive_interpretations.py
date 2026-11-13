#!/usr/bin/env python3
"""Offline. Re-states the PUBLISHED pools and the SNAPSHOT share estimates
under three readings of what `rewardPool` is paid over, because the public
API does not say and the two venue texts in the repo point different ways:

  PER_MARKET  : each market's period pays the full rewardPool  (upper bound)
  PER_EVENT   : rewardPool shared by the program's markets in the same event
                (doc: "Daily (per event): a per-day pool on an event";
                 "Time Period: a window during the event lifecycle ... with
                 its own reward pool")
  PER_PROGRAM : rewardPool shared by every market in the program
                (repo quote of polymarket.us/rewards, Sept 2026: "shared
                 across the program's markets -- never summed per market")

M_event / M_program are counts of OPEN markets in this census carrying that
program, so the shared readings are upper bounds within their reading.

    python3 analysis/l5/pivot_pmus/incentive_interpretations.py
"""
import gzip
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = ("daily", "daily_event")


def main():
    cen = json.load(gzip.open(os.path.join(HERE, "raw", "incentives_census.json.gz")))
    ev = json.load(gzip.open(os.path.join(HERE, "raw", "all_events_slim.json.gz")))
    ev_of = {m["slug"]: e["id"] for e in ev["events"] for m in e["markets"]}
    rows = cen["rows"]
    m_prog = Counter(r["tp"]["programId"] for r in rows)
    m_ev = Counter((r["tp"]["programId"], ev_of.get(r["marketSlug"])) for r in rows)
    tot = defaultdict(lambda: defaultdict(float))
    seen_ev, seen_prog = set(), set()
    for r in rows:
        tp = r["tp"]; c = r["event_category"]; kind = "daily_usd_per_day" if tp["period"] in DAILY else "game_period_usd"
        pool = float(tp["rewardPool"])
        tot[c][kind + "|PER_MARKET"] += pool
        k_ev = (tp["programId"], tp.get("start"), ev_of.get(r["marketSlug"]))
        if k_ev not in seen_ev:
            seen_ev.add(k_ev); tot[c][kind + "|PER_EVENT"] += pool
        k_p = (tp["programId"], tp.get("start"))
        if k_p not in seen_prog:
            seen_prog.add(k_p); tot[c][kind + "|PER_PROGRAM"] += pool
    totals = {c: {k: round(v, 0) for k, v in sorted(d.items())} for c, d in tot.items()}
    books = json.load(open(os.path.join(HERE, "raw", "incentive_share_books.json")))
    est = []
    for c in books["markets"]:
        me = m_ev[(c["programId"], ev_of.get(c["slug"]))] or 1
        row = {"slug": c["slug"], "category": c["category"], "programId": c["programId"], "period": c["period"],
               "pool": c["pool"], "M_event": me, "M_program": c["M"], "df": c["df"], "target": c["target"],
               "maxSpread": c["maxSpread"], "spreads": c.get("spreads")}
        for q in ("10", "50"):
            s = c["summary"][q]
            pm = s["mean_usd_per_period_PER_MARKET"]
            row["Q%s" % q] = {"shares_bid_ask_by_round": s["shares_bid_ask_by_round"],
                              "PER_MARKET": round(pm, 2), "PER_EVENT": round(pm / me, 2),
                              "PER_PROGRAM": round(pm / c["M"], 3),
                              "min_round_PER_MARKET": s["min_usd_per_period_PER_MARKET"],
                              "capital_usd": s["capital_both_sides_usd"]}
        est.append(row)
    out = {"label": "PUBLISHED pools x SNAPSHOT books; three pool readings; NOT credited, NOT realized",
           "pool_totals_by_category": totals, "estimates": est}
    json.dump(out, open(os.path.join(HERE, "incentive_estimates.json"), "w"), indent=1)
    print("TOTALS (USD):")
    for c, d in sorted(totals.items(), key=lambda x: -x[1].get("daily_usd_per_day|PER_MARKET", 0)):
        print(" %-12s %s" % (c, d))
    print("\n%-44s %-11s %5s %3s %4s | Q10 $/period PM / EV / PROG | Q50 PM / EV / PROG | cap10 cap50" % (
        "slug", "period", "pool", "Mev", "Mpr"))
    for r in est:
        a, b = r["Q10"], r["Q50"]
        print("%-44s %-11s %5.0f %3d %4d | %7.2f %7.2f %7.3f | %7.2f %7.2f %7.3f | %5.1f %5.1f" % (
            r["slug"][:44], r["period"], r["pool"], r["M_event"], r["M_program"], a["PER_MARKET"], a["PER_EVENT"],
            a["PER_PROGRAM"], b["PER_MARKET"], b["PER_EVENT"], b["PER_PROGRAM"], a["capital_usd"] or 0, b["capital_usd"] or 0))


if __name__ == "__main__":
    main()
