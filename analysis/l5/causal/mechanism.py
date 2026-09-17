"""L5_CAUSAL_MECHANISM_V1 -- which process actually generates the BTC15m tape?

OWNER 2026-09-17. We have enough description. This discriminates between four
mechanisms that all fit the descriptive picture equally well, which is exactly
why the descriptive picture cannot choose between them.

    H1 ACTION_SELECTOR        individual fills are state-dependent discrete
                              decisions.
    H2 INVENTORY_TARGET       a continuously moving desired inventory; fills
                              move him around that target.
    H3 PASSIVE_QUOTE_SURFACE  two-sided / multi-level resting liquidity; market
                              flow largely chooses the observed side and run
                              sequence.
    H4 VOLATILITY_HARVESTER   oscillation is tolerated because price movement
                              generates later complementary inventory and the
                              matched economics that follow.

NO WINNER IS FORCED IN SAMPLE. `PREDICTIONS` below is declared before any
scoring code exists in this file and states, for each test, what each
hypothesis expects. Each test is then measured on a CHRONOLOGICAL out-of-sample
split and scored against those declarations. A hypothesis can end the run with
no support, with mixed support, or contradicted -- and a test that separates
nothing is reported as separating nothing rather than being dropped.

THE DATA IS THE SAME ONE-SIDED PUBLIC TAPE. 693,916 BTC15m BUY fills, zero
sells, no book at any instant, no unfilled orders, no queue. Every price here
is the price of one of L5's own prints. A test that would need the book is not
attempted and is named in UNTESTABLE_HERE.
"""
from __future__ import annotations

import collections
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

# ---------------------------------------------------------------------------
# DECLARED BEFORE SCORING. Each entry: what the test measures, and what each
# hypothesis predicts. "-" means the hypothesis makes no distinct prediction.

PREDICTIONS: Dict[str, Dict[str, str]] = {
    "P1_same_instant_clustering": {
        "measures": "share of fills sharing (timestamp, side, price) with "
                    "another fill -- one aggressor consuming one resting level",
        "H1": "LOW: one decision produces one order",
        "H2": "LOW",
        "H3": "HIGH: a resting level is swept in pieces",
        "H4": "-",
    },
    "P2_price_walk_within_a_run": {
        "measures": "share of consecutive same-side fills at an IDENTICAL price",
        "H1": "LOW: a decision re-prices",
        "H2": "LOW",
        "H3": "HIGH: the same resting level keeps trading",
        "H4": "-",
    },
    "P3_bought_the_falling_side": {
        "measures": "P(the side bought is the side whose price FELL over the "
                    "preceding 60s), fills at flat price excluded",
        "H1": "~0.5 unless a directional rule exists, and none is supported",
        "H2": "-",
        "H3": "ABOVE 0.5: a resting bid is hit by sellers pushing that side down",
        "H4": "-",
    },
    "P4_run_continuation_hazard": {
        "measures": "P(next fill is the same side | current run length k), OOS",
        "H1": "state-dependent, not monotone in k alone",
        "H2": "FALLS with k: the target pulls back",
        "H3": "RISES with k: order flow is persistent",
        "H4": "-",
    },
    "P5_error_correction_to_a_target": {
        "measures": "mean change in residual over the next 5 fills, by current "
                    "residual bucket",
        "H1": "-",
        "H2": "STRONG NEGATIVE SLOPE and stable OOS: deviation is corrected",
        "H3": "WEAK: a passive surface has no target to correct toward",
        "H4": "-",
    },
    "P6_state_beats_persistence": {
        "measures": "OOS log-loss of P(next side) from a state-conditional "
                    "table vs a previous-side-only baseline",
        "H1": "IMPROVES materially: state carries the decision",
        "H2": "improves via residual only",
        "H3": "little improvement beyond persistence",
        "H4": "-",
    },
    "P7_matched_yield_rises_with_volatility": {
        "measures": "fraction of shares acquired in a |dp| bin that end the "
                    "window MATCHED",
        "H1": "-",
        "H2": "-",
        "H3": "-",
        "H4": "RISES with |dp|: movement brings the complement",
    },
    "P8_timing_depends_on_inventory_or_on_flow": {
        "measures": "spread of median inter-fill seconds across OUR residual "
                    "buckets vs across market |dp| buckets",
        "H1": "inventory matters: a decider waits differently by position",
        "H2": "inventory matters",
        "H3": "FLOW matters and INVENTORY DOES NOT: a resting quote does not "
              "know our position",
        "H4": "-",
    },
}

UNTESTABLE_HERE = (
    "whether L5 was quoting both sides at once -- unfilled orders leave no "
    "trace, so a two-sided surface and a one-sided one are indistinguishable "
    "except through their consequences, which is what P1/P2/P3/P8 measure",
    "queue position, depth, spread, or any book quantity",
    "whether a fill was maker or taker (only fee-bearing vs fee-free)",
)

MIN_BIN = 500


def _med(xs):
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


# ---------------------------------------------------------------------------
# features

def build(cohort: str = "BTC15m") -> List[dict]:
    """One row per fill, with only what the eight tests need."""
    windows, capture_end = F.load_windows(cohort)
    rows: List[dict] = []
    for cid, w in windows.items():
        fills = w["fills"]
        if len(fills) < 5:
            continue
        _a, horizon, end = w["meta"]
        up_q = sum(x[3] for x in fills if x[2] == "Up")
        dn_q = sum(x[3] for x in fills if x[2] != "Up")
        if up_q <= 0 or dn_q <= 0:
            continue
        matched = min(up_q, dn_q)
        mf = {"Up": matched / up_q, "Down": matched / dn_q}
        # same-instant groups
        g_tsp = collections.Counter((t, oc, px) for t, px, oc, _s, _u in fills)
        g_ts = collections.Counter((t, oc) for t, _px, oc, _s, _u in fills)

        up = dn = 0.0
        run_side, run_len = None, 0
        p_hist: List[Tuple[int, float]] = []
        prev_t = None
        prev_same_px = None
        for i, (t, px, oc, sz, usd) in enumerate(fills):
            p_up = px if oc == "Up" else 1.0 - px
            dp = None
            for tt, pp in reversed(p_hist):
                if t - tt >= 60:
                    dp = p_up - pp
                    break
            res = up - dn
            run_here = (run_len + 1) if run_side == oc else 1
            same_px = (prev_same_px is not None and run_side == oc
                       and abs(px - prev_same_px) < 1e-12)
            rows.append({
                "cid": cid, "t": t, "i": i, "side": oc, "px": px, "sz": sz,
                "p_up": p_up, "dp_60s": dp,
                "abs_dp_60s": None if dp is None else abs(dp),
                "residual_before": res,
                "run_len_before": run_len if run_side == oc else 0,
                "run_here": run_here,
                "same_price_as_prev_same_side": same_px if run_here > 1 else None,
                "dt_s": None if prev_t is None else t - prev_t,
                "clustered_tsp": g_tsp[(t, oc, px)] > 1,
                "clustered_ts": g_ts[(t, oc)] > 1,
                "cluster_size_tsp": g_tsp[(t, oc, px)],
                "matched_frac_side": mf[oc],
                "tte_s": end - t,
            })
            if oc == "Up":
                up += sz
            else:
                dn += sz
            prev_same_px = px if run_side == oc or run_side is None else px
            if run_side != oc:
                prev_same_px = px
            run_side, run_len = oc, run_here
            prev_t = t
            p_hist.append((t, p_up))
    rows.sort(key=lambda r: (r["t"], r["cid"], r["i"]))
    # the NEXT fill's side, within the same window
    by_w: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by_w[r["cid"]].append(r)
    for v in by_w.values():
        for a, b in zip(v, v[1:]):
            a["next_side"] = b["side"]
            a["next_residual_5"] = None
        for k, r in enumerate(v):
            j = min(k + 5, len(v) - 1)
            r["residual_after_5"] = v[j]["residual_before"]
        v[-1]["next_side"] = None
    return rows


def split(rows: Sequence[dict], frac: float = 0.7):
    c = int(len(rows) * frac)
    return rows[:c], rows[c:]
