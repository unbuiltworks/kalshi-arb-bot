"""Is P6 reproducing L5, or just selecting profitable-looking pairs?

THE QUESTION THAT MATTERS. P6 reaches a 0.94 median pair cost by completing
only 21% of its entries. If L5's own behaviour looks like that -- hold most
markets one-sided, complete rarely and only when cheap -- then P6 is a
faithful policy. If L5 completes most of his markets and still gets cheap
pairs, then P6 is not L5: it is a filter that keeps the cheap completions
and leaves the rest unhedged, which is a different strategy with a
different risk profile wearing L5's name.

Computed from the same 760,817-fill corpus, EARLY REGIME ONLY (pre
2026-06-23), which is the regime P6's model was fit on.
"""
from __future__ import annotations
import collections, datetime as dt, json, sys
import numpy as np

sys.path.insert(0, "/home/user/kalshi-arb-bot")
from analysis.l5.clone.extract import load_fills, window_of

BREAK = int(dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp())


def pct(xs, q):
    return round(float(np.percentile(xs, q)), 4) if len(xs) else None


def main() -> int:
    fills = load_fills()
    by_mkt = collections.defaultdict(list)
    for r in fills:
        by_mkt[r.get("conditionId")].append(r)

    stats = {"markets": 0, "two_sided": 0, "fills": [], "matched_frac": [],
             "pair_cost": [], "residual": [], "span_s": [],
             "residual_frac_of_total": []}
    for cid, rows in by_mkt.items():
        rows.sort(key=lambda r: int(r["timestamp"]))
        if int(rows[0]["timestamp"]) >= BREAK:
            continue                      # early regime only
        dur, ws = window_of(rows[0].get("slug") or rows[0].get("eventSlug"))
        if not dur:
            continue
        q = [0.0, 0.0]
        c = [0.0, 0.0]
        for r in rows:
            i = int(r["outcomeIndex"])
            s = float(r.get("size") or 0)
            q[i] += s
            c[i] += float(r["price"]) * s
        tot = q[0] + q[1]
        if tot <= 0:
            continue
        stats["markets"] += 1
        stats["fills"].append(len(rows))
        matched = min(q[0], q[1])
        stats["matched_frac"].append(2 * matched / tot)
        stats["residual"].append(abs(q[0] - q[1]))
        stats["residual_frac_of_total"].append(abs(q[0] - q[1]) / tot)
        stats["span_s"].append(int(rows[-1]["timestamp"])
                               - int(rows[0]["timestamp"]))
        if q[0] > 0 and q[1] > 0:
            stats["two_sided"] += 1
            stats["pair_cost"].append(c[0] / q[0] + c[1] / q[1])

    n = stats["markets"]
    pc = stats["pair_cost"]
    out = {
        "regime": "early (pre 2026-06-23)",
        "markets": n,
        "L5_BEHAVIOUR": {
            "markets_two_sided": stats["two_sided"],
            "two_sided_rate": round(stats["two_sided"] / n, 4) if n else None,
            "fills_per_market": {"median": pct(stats["fills"], 50),
                                 "p90": pct(stats["fills"], 90)},
            "matched_fraction_of_shares": {
                "median": pct(stats["matched_frac"], 50),
                "p25": pct(stats["matched_frac"], 25),
                "p75": pct(stats["matched_frac"], 75)},
            "residual_shares_at_end": {"median": pct(stats["residual"], 50),
                                       "p90": pct(stats["residual"], 90)},
            "residual_as_frac_of_shares": {
                "median": pct(stats["residual_frac_of_total"], 50),
                "p90": pct(stats["residual_frac_of_total"], 90)},
            "first_to_last_fill_s": {"median": pct(stats["span_s"], 50),
                                     "p90": pct(stats["span_s"], 90)},
            "pair_cost_when_two_sided": {
                "n": len(pc), "median": pct(pc, 50), "p25": pct(pc, 25),
                "p75": pct(pc, 75),
                "frac_under_one": (round(float(np.mean(
                    [x < 1.0 for x in pc])), 4) if pc else None),
                "frac_at_or_below_0_96": (round(float(np.mean(
                    [x <= 0.96 for x in pc])), 4) if pc else None)},
        },
        "P6_SHADOW_PROFILE_FOR_COMPARISON": {
            "completion_rate": 0.2105,
            "median_pair_cost": 0.94,
            "frac_at_or_below_0_96": 1.0,
            "residual_left_open_frac": 0.7895,
            "time_at_risk_s_median": 613.5,
            "source": "analysis/l5/clone/PAIR_POLICY_SENSITIVITY.md, in-sample",
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
