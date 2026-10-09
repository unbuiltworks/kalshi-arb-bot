"""THE VENUE GATE, v2 -- corrected after two errors in v1.

v1 reported "mechanism possible on 92.5% of markets". That was wrong, in
two ways that both inflated the result. Both are fixed here and both are
worth stating, because they are the same class of error this project has
hit repeatedly.

ERROR 1 -- STALE AND CROSSED QUOTES. v1 took the minimum over an entire
window. 3.4% of snapshots are flagged `crossed` (bid above ask), i.e.
stale or erroneous. A minimum is exactly the statistic a bad quote
captures. Excluding crossed rows, the simultaneous pair at the touch has
median 1.0200 and clears $1.00 on 0.01% of snapshots (17 of 169,254) --
not 86.6% of markets. The corrected number agrees with the earlier
Gemini and Kalshi findings instead of contradicting them.

ERROR 2 -- SURVIVORSHIP IN THE CAUSAL POLICY. v1 reported the median pair
cost among markets where BOTH legs filled, and ignored the markets where
only one leg filled. Those are not neutral: a single unpaired leg is a
directional bet that settles at $1 or $0. Reporting the winners'
pair cost and discarding the one-sided cases measures the strategy's good
branch only.

v2 therefore settles every market. The policy walks forward in time and
buys one unit of a leg the first time its ask is at or below theta. At
expiry, matched pairs pay exactly $1.00; an unmatched leg pays $1.00 if it
is the winning outcome and $0.00 otherwise, using the dataset's own
`is_winning_outcome`. The reported quantity is total P&L per market across
ALL markets, filled and half-filled alike.

That is the number that decides whether the mechanism is viable on a
venue, and it is the number v1 failed to compute.

DEPTH IS STILL NOT MODELLED. These are touch prices; a real order may not
fill its intended size there. Every result is therefore an upper bound on
an implementable strategy even after these fixes.

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import statistics
import sys

THETAS = [0.30, 0.35, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55]
SEED = 20261009


def q(v, p):
    if not v:
        return None
    s = sorted(v)
    return round(s[min(len(s) - 1, int(len(s) * p))], 5)


def ci(vals, rng, reps=2000, cap=20000):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), cap)
    out = sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)])
                 for _ in range(reps))
    return out[int(reps * .025)], out[int(reps * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", nargs="+", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--fee-per-contract", type=float, default=0.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)
    import pandas as pd

    d = pd.concat([pd.read_parquet(p) for p in a.parquet], ignore_index=True)
    n_raw = len(d)
    d = d[d["best_ask"].notna()]
    n_null = n_raw - len(d)
    n_crossed = int(d["crossed"].sum()) if "crossed" in d.columns else 0
    if "crossed" in d.columns:
        d = d[~d["crossed"]]                    # ERROR 1 fix
    d = d.sort_values("captured_at")
    if "coin" not in d.columns:
        d["coin"] = "unknown"

    # --- the honest simultaneous baseline, at the touch, not a minimum ---
    piv = d.pivot_table(index=["market_slug", "captured_at"],
                        columns="outcome", values="best_ask", aggfunc="min")
    cols = list(piv.columns)
    sim_share = sim_med = None
    if len(cols) == 2:
        piv = piv.dropna()
        pair = (piv[cols[0]] + piv[cols[1]])
        fee2 = 2 * a.fee_per_contract
        sim_med = round(float(pair.median()), 5)
        sim_share = round(float(((pair + fee2) < 1.0).mean()), 6)
        sim_n = int(len(pair))
    else:
        sim_n = 0

    # --- causal policy, every market settled --------------------------
    res = {th: [] for th in THETAS}
    per_coin = {th: collections.defaultdict(list) for th in THETAS}
    outcome_of = {}
    n_markets = 0
    fee = a.fee_per_contract

    for slug, g in d.groupby("market_slug", sort=False):
        legs = {}
        win = {}
        for oc, gg in g.groupby("outcome"):
            legs[oc] = gg["best_ask"].tolist()
            iw = gg["is_winning_outcome"]
            win[oc] = bool(iw.iloc[0]) if len(iw) else None
        if len(legs) != 2 or any(v is None for v in win.values()):
            continue
        n_markets += 1
        coin = g["coin"].iloc[0]
        outcome_of[slug] = win
        names = list(legs)
        for th in THETAS:
            fills = {}
            for nm in names:
                px = next((p for p in legs[nm] if p <= th), None)
                if px is not None:
                    fills[nm] = px
            if not fills:
                pnl = 0.0                       # never engaged: no position
            else:
                cost = sum(fills.values()) + fee * len(fills)
                if len(fills) == 2:
                    proceeds = 1.0              # matched pair, pays $1
                else:
                    nm = next(iter(fills))
                    proceeds = 1.0 if win[nm] else 0.0
                pnl = proceeds - cost
            res[th].append(pnl)
            per_coin[th][coin].append(pnl)

    R = {
        "label": a.label,
        "sources": [os.path.basename(p) for p in a.parquet],
        "fee_per_contract_applied": fee,
        "corrections_vs_v1": [
            "crossed/stale snapshots excluded (a minimum over a window is "
            "exactly what a bad quote captures)",
            "every market settled, including those where only ONE leg "
            "filled -- v1 reported the both-legs-filled subset only, which "
            "is survivorship",
        ],
        "coverage": {
            "rows_raw": n_raw, "rows_dropped_null_ask": n_null,
            "rows_dropped_crossed": n_crossed,
            "rows_used": int(len(d)),
            "markets_settled": n_markets,
            "coins": sorted(d["coin"].unique().tolist()),
        },
        "simultaneous_pair_at_the_touch": {
            "_what": "ask_A + ask_B at the same snapshot, crossed rows "
                     "excluded. NOT a minimum over the window.",
            "snapshots": sim_n, "median": sim_med,
            "share_below_1.00_net": sim_share,
        },
        "causal_policy_every_market_settled": {},
    }

    best = None
    for th in THETAS:
        v = res[th]
        if not v:
            continue
        lo, hi = ci(v, rng)
        engaged = sum(1 for x in v if x != 0.0)
        row = {
            "markets": len(v),
            "mean_pnl_per_market": round(statistics.fmean(v), 5),
            "ci95": [None if lo is None else round(lo, 5),
                     None if hi is None else round(hi, 5)],
            "median_pnl": q(v, .5),
            "share_markets_profitable": round(
                sum(1 for x in v if x > 0) / len(v), 4),
            "markets_engaged": engaged,
            "total_pnl": round(sum(v), 3),
        }
        R["causal_policy_every_market_settled"][f"{th:.2f}"] = row
        if lo is not None and (best is None or row["mean_pnl_per_market"] >
                               best[1]["mean_pnl_per_market"]):
            best = (th, row)

    R["by_coin_at_best_theta"] = {}
    if best:
        th = best[0]
        for c, v in sorted(per_coin[th].items()):
            lo, hi = ci(v, rng)
            R["by_coin_at_best_theta"][c] = {
                "theta": th, "markets": len(v),
                "mean_pnl": round(statistics.fmean(v), 5),
                "ci95": [None if lo is None else round(lo, 5),
                         None if hi is None else round(hi, 5)],
            }

    if best:
        th, row = best
        lo = row["ci95"][0]
        R["VERDICT"] = {
            "best_theta": th,
            "mean_pnl_per_market": row["mean_pnl_per_market"],
            "ci95": row["ci95"],
            "gate": ("VIABLE at the touch: the best threshold has a mean "
                     "P&L per market whose whole 95% CI is above zero."
                     if lo is not None and lo > 0 else
                     ("NOT VIABLE: no threshold has a mean P&L per market "
                      "significantly above zero once one-sided markets are "
                      "settled."
                      if lo is not None and row["ci95"][1] <= 0 else
                      "INDETERMINATE: the best threshold's CI spans zero.")),
            "_caveat": "Depth is not modelled -- touch prices only -- so "
                       "this remains an UPPER bound on an implementable "
                       "strategy.",
        }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
