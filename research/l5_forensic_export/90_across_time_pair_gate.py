"""THE VENUE GATE, re-specified: can a complement pair be assembled ACROSS TIME?

The earlier venue rejections (Gemini, Kalshi) were measured on the
SIMULTANEOUS pair: at any single instant, does ask(Up) + ask(Down) fall
below $1.00? On that test both venues failed outright -- 0 of 24,520 and
0 of 2,269 observations.

But L5 never needed the simultaneous pair. The fill reconstruction shows
it buys one leg while that leg is cheap, runs a mean of 3.4 fills on that
side, then switches. The pair is assembled ACROSS TIME. So the
simultaneous test answers a stricter question than the strategy asks, and
a venue can fail it while still supporting the mechanism.

This measures the right thing, three ways, per market:

  1. SIMULTANEOUS      min over t of [ask_Up(t) + ask_Down(t)]
                       -- the old, stricter test.

  2. ACROSS-TIME, PERFECT FORESIGHT
                       min over t1 of ask_Up(t1) + min over t2 of ask_Down(t2)
                       -- an upper bound on what ANY across-time policy
                       could achieve. If this does not clear $1.00, the
                       mechanism is impossible on that market, full stop.
                       That makes it a valid necessary-condition gate.

  3. ACROSS-TIME, CAUSAL
                       for a threshold grid: buy each leg the FIRST time its
                       ask is at or below theta, in forward time order, both
                       legs required inside the same window. No foresight,
                       no lookahead -- an implementable rule.

(2) says whether the venue could ever support it. (3) says whether a dumb
rule actually gets there. The gap between (1) and (2) is the value of
assembling across time rather than at an instant.

Everything is GROSS of fees. Fees are venue-specific and are applied by the
caller via --fee-per-contract; the result reports both gross and net.

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys

THETAS = [0.30, 0.35, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.55, 0.60]


def pct(x, n):
    return round(x / n, 4) if n else None


def q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * p))], 5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", nargs="+", required=True)
    ap.add_argument("--label", required=True,
                    help="venue/dataset name for the report")
    ap.add_argument("--fee-per-contract", type=float, default=0.0,
                    help="all-in per-contract fee, applied to the PAIR as 2x")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print("pandas required", file=sys.stderr)
        return 1

    frames = []
    for p in a.parquet:
        d = pd.read_parquet(p)
        d["__src"] = os.path.basename(p)
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)

    need = {"market_slug", "outcome", "captured_at", "best_ask"}
    missing = need - set(d.columns)
    if missing:
        print(f"missing columns: {missing}", file=sys.stderr)
        return 1
    if "coin" not in d.columns:
        d["coin"] = "unknown"

    before = len(d)
    d = d[d["best_ask"].notna()].copy()
    dropped_null = before - len(d)
    if "crossed" in d.columns:
        n_crossed = int(d["crossed"].sum())
    else:
        n_crossed = None
    d = d.sort_values("captured_at")

    per_market = []
    for (slug, coin), g in d.groupby(["market_slug", "coin"], sort=False):
        legs = {}
        for oc, gg in g.groupby("outcome"):
            legs[oc] = gg[["captured_at", "best_ask"]].values.tolist()
        if len(legs) != 2:
            continue
        (n1, s1), (n2, s2) = list(legs.items())

        # 1. simultaneous: step both series forward on a merged clock
        merged = sorted(
            [(t, 1, p) for t, p in s1] + [(t, 2, p) for t, p in s2])
        a1 = a2 = None
        sim = None
        for t, which, px in merged:
            if which == 1:
                a1 = px
            else:
                a2 = px
            if a1 is not None and a2 is not None:
                v = a1 + a2
                sim = v if sim is None else min(sim, v)

        # 2. across-time, perfect foresight
        m1 = min(p for _, p in s1)
        m2 = min(p for _, p in s2)
        xt = m1 + m2

        # 3. across-time, causal: first touch at or below theta on each leg
        causal = {}
        for th in THETAS:
            f1 = next((p for _, p in s1 if p <= th), None)
            f2 = next((p for _, p in s2 if p <= th), None)
            causal[th] = (f1 + f2) if (f1 is not None and f2 is not None) \
                else None

        per_market.append({
            "market_slug": slug, "coin": coin,
            "snapshots": len(s1) + len(s2),
            "simultaneous_min": sim,
            "across_time_min": xt,
            "leg_min_a": m1, "leg_min_b": m2,
            "causal": causal,
        })

    n = len(per_market)
    if not n:
        print("no markets with both outcomes", file=sys.stderr)
        return 1
    fee2 = 2.0 * a.fee_per_contract

    sim_v = [m["simultaneous_min"] for m in per_market
             if m["simultaneous_min"] is not None]
    xt_v = [m["across_time_min"] for m in per_market]
    leg_v = [m["leg_min_a"] for m in per_market] + \
            [m["leg_min_b"] for m in per_market]

    R = {
        "label": a.label,
        "sources": [os.path.basename(p) for p in a.parquet],
        "fee_per_contract_applied": a.fee_per_contract,
        "coverage": {
            "rows_after_dropping_null_ask": len(d),
            "rows_dropped_null_ask": dropped_null,
            "rows_flagged_crossed": n_crossed,
            "markets_with_both_outcomes": n,
            "coins": sorted(d["coin"].unique().tolist()),
        },
        "THE_OLD_TEST_simultaneous_pair": {
            "_what": "min over t of ask_Up(t)+ask_Down(t), per market",
            "n_markets": len(sim_v),
            "median": q(sim_v, .5), "min": round(min(sim_v), 5),
            "p05": q(sim_v, .05), "p25": q(sim_v, .25),
            "markets_below_1.00_gross":
                pct(sum(1 for v in sim_v if v < 1.0), len(sim_v)),
            "markets_below_1.00_net_of_fees":
                pct(sum(1 for v in sim_v if v + fee2 < 1.0), len(sim_v)),
        },
        "THE_RIGHT_TEST_across_time_perfect_foresight": {
            "_what": "min ask on leg A at any time + min ask on leg B at any "
                     "time, same market. Upper bound on any across-time "
                     "policy, so a necessary-condition gate.",
            "n_markets": n,
            "median": q(xt_v, .5), "min": round(min(xt_v), 5),
            "p05": q(xt_v, .05), "p25": q(xt_v, .25), "p75": q(xt_v, .75),
            "markets_below_1.00_gross":
                pct(sum(1 for v in xt_v if v < 1.0), n),
            "markets_below_1.00_net_of_fees":
                pct(sum(1 for v in xt_v if v + fee2 < 1.0), n),
            "markets_below_0.98_net":
                pct(sum(1 for v in xt_v if v + fee2 < 0.98), n),
        },
        "value_of_assembling_across_time": {
            "_what": "simultaneous minus across-time, per market",
            "median_improvement_cents": None if not sim_v else round(
                100 * statistics.median(
                    [m["simultaneous_min"] - m["across_time_min"]
                     for m in per_market
                     if m["simultaneous_min"] is not None]), 3),
        },
        "per_leg_min_ask": {
            "_what": "the cheapest each leg ever got. Compare with L5's "
                     "MEAN ACCEPTED price of 0.4614.",
            "median": q(leg_v, .5), "p25": q(leg_v, .25),
            "share_below_0.50": pct(sum(1 for v in leg_v if v < 0.50),
                                    len(leg_v)),
            "share_below_0.4614": pct(sum(1 for v in leg_v if v < 0.4614),
                                      len(leg_v)),
        },
        "CAUSAL_policy_first_touch_at_or_below_theta": {
            "_what": "implementable: buy each leg the first time its ask is "
                     "at or below theta, forward in time, both legs required "
                     "inside the window. No foresight.",
            "by_theta": {},
        },
    }
    for th in THETAS:
        got = [m["causal"][th] for m in per_market
               if m["causal"][th] is not None]
        R["CAUSAL_policy_first_touch_at_or_below_theta"]["by_theta"][
            f"{th:.2f}"] = {
            "markets_both_legs_filled": len(got),
            "fill_rate": pct(len(got), n),
            "median_pair_cost_gross": q(got, .5),
            "share_pair_below_1.00_net":
                pct(sum(1 for v in got if v + fee2 < 1.0), len(got))
            if got else None,
            "expected_markets_per_100_with_profitable_pair":
                round(100 * (len(got) / n) *
                      (sum(1 for v in got if v + fee2 < 1.0) / len(got)), 2)
            if got else 0.0,
        }

    bycoin = collections.defaultdict(list)
    for m in per_market:
        bycoin[m["coin"]].append(m)
    R["by_coin"] = {}
    for c, ms in sorted(bycoin.items()):
        xv = [m["across_time_min"] for m in ms]
        sv = [m["simultaneous_min"] for m in ms
              if m["simultaneous_min"] is not None]
        R["by_coin"][c] = {
            "markets": len(ms),
            "across_time_median": q(xv, .5),
            "across_time_below_1.00_net":
                pct(sum(1 for v in xv if v + fee2 < 1.0), len(xv)),
            "simultaneous_median": q(sv, .5) if sv else None,
            "simultaneous_below_1.00_net":
                pct(sum(1 for v in sv if v + fee2 < 1.0), len(sv))
            if sv else None,
        }

    xt_ok = R["THE_RIGHT_TEST_across_time_perfect_foresight"][
        "markets_below_1.00_net_of_fees"]
    sim_ok = R["THE_OLD_TEST_simultaneous_pair"][
        "markets_below_1.00_net_of_fees"]
    R["VERDICT"] = {
        "simultaneous_pair_viable_share": sim_ok,
        "across_time_pair_viable_share": xt_ok,
        "gate":
            "MECHANISM IMPOSSIBLE on this venue -- even with perfect "
            "foresight the across-time pair never clears $1.00 net."
            if (xt_ok or 0) == 0 else
            ("MECHANISM POSSIBLE, and the simultaneous test UNDERSTATED it: "
             f"across-time clears $1.00 net on {xt_ok:.1%} of markets "
             f"versus {sim_ok:.1%} simultaneously."
             if (sim_ok or 0) < (xt_ok or 0) else
             "across-time offers no advantage over simultaneous here."),
        "_caveat":
            "Perfect foresight is an upper bound, not an achievable result. "
            "Use the CAUSAL block for what an implementable rule gets. "
            "Depth is NOT modelled: these are touch prices, so a real order "
            "may not fill the intended size at them.",
    }

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps({k: R[k] for k in
                      ("label", "coverage", "THE_OLD_TEST_simultaneous_pair",
                       "THE_RIGHT_TEST_across_time_perfect_foresight",
                       "value_of_assembling_across_time", "per_leg_min_ask",
                       "VERDICT")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
