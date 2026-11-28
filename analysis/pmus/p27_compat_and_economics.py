"""P27 -- L5 behavioural compatibility and q=1 economics for PMUS BTC15m.

Two questions, kept apart because they fail for different reasons.

BEHAVIOURAL COMPATIBILITY (P4) asks whether the frozen controller can even
express itself here: does it OPEN, does it EXTEND repeatedly, does opposite
inventory become obtainable, can inventory cross zero, how long do repair
opportunities persist, how often does the $0.01 tick change the desired
action, and how often do the existing bounds bind. Each is compared against
the L5 BTC15m historical distribution it has to be judged against.

ECONOMICS (P5) is computed at q=1 only, on SIX SEPARATE LINES that are never
merged:

  1. matched-pair economics   -- pairs actually held, at their cost
  2. directional residual     -- the unmatched leg, marked at nothing
  3. maker rebate             -- published rule, ACCRUAL, never netted
  4. liquidity reward         -- $0.00, always, until eligibility is proven
  5. taker fees               -- $0.00, because nothing crosses
  6. modelled net             -- the sum, labelled MODELLED

Capacity is NOT derived here. Deriving it needs a fill assumption with
evidence behind it, and the only fill assumption available is Track B's, which
has none.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from decimal import Decimal as D
from typing import Any, Dict, List

from src import pmus_fees as fees

#: L5's own BTC15m corpus, for the comparison column. Sources:
#: L5_OPERATING_MODEL.md §2a and analysis/l5/residual_exit/.
L5_BTC15M = {
    "windows": 5546,
    "fills": 693916,
    "fills_per_window": round(693916 / 5546, 1),
    "sells": 0,
    "p_next_is_extend": 0.66,
    "residual_episodes": 38180,
    "episodes_per_window": round(38180 / 5546, 1),
    "episodes_ending_FLIPPED_through_zero": 0.843,
    "episodes_ending_COMPLETED_FLAT": 0.012,
    "windows_ending_flat": "1 of 5546",
    "median_residual_into_settlement_shares": 141.8,
    "median_residual_pct_of_window_shares": 0.077,
    "median_pair_cost_flip": "0.980-0.986",
    "median_pair_cost_flat_completion": 0.9950,
}


def load(path: str) -> List[dict]:
    d = json.load(open(path))
    return d["windows"]


def compat(windows: List[dict]) -> Dict[str, Any]:
    rels = collections.Counter()
    holds = collections.Counter()
    opens = extends = completes = 0
    crossings = 0
    quoted_windows = 0
    residual_paths: List[List[int]] = []
    extend_runs: List[int] = []
    tick_binding = 0
    band_binding = 0
    total_decisions = 0
    near_touch = 0

    for w in windows:
        ds = w.get("decisions") or []
        total_decisions += len(ds)
        path = []
        run = 0
        saw_quote = False
        for d in ds:
            if d.get("action") == "HOLD":
                holds[d.get("reason", "?")] += 1
                if d.get("reason") == "entry_band":
                    band_binding += 1
                if d.get("reason") == "would_cross":
                    tick_binding += 1
                continue
            saw_quote = True
            r = d.get("relation")
            rels[r] += 1
            if r == "open":
                opens += 1
            elif r == "extend":
                extends += 1
                run += 1
            elif r == "complete":
                completes += 1
            if r != "extend" and run:
                extend_runs.append(run)
                run = 0
            if d.get("near_touch"):
                near_touch += 1
            inv = d.get("inventory_after") or {}
            if "residual" in inv:
                path.append(inv["residual"])
        if run:
            extend_runs.append(run)
        if saw_quote:
            quoted_windows += 1
        if path:
            residual_paths.append(path)

    crossed = 0
    max_abs = []
    for p in residual_paths:
        signs = [1 if x > 0 else (-1 if x < 0 else 0) for x in p]
        nz = [s for s in signs if s != 0]
        if any(a != b for a, b in zip(nz, nz[1:])):
            crossed += 1
        max_abs.append(max((abs(x) for x in p), default=0))

    quotes = sum(rels.values())
    return {
        "windows_replayed": len(windows),
        "windows_with_any_quote": quoted_windows,
        "decision_points": total_decisions,
        "quotes": quotes,
        "does_it_OPEN": opens > 0,
        "opens": opens,
        "does_it_EXTEND_repeatedly": bool(
            [r for r in extend_runs if r >= 2]),
        "extends": extends,
        "extend_run_lengths": sorted(extend_runs, reverse=True)[:10],
        "longest_extend_run": max(extend_runs) if extend_runs else 0,
        "opposite_inventory_obtainable": completes > 0,
        "completes": completes,
        "relations": dict(rels),
        "p_next_is_extend_observed": (round(extends / quotes, 3)
                                      if quotes else None),
        "residual_crossed_zero_in_n_windows": crossed,
        "max_abs_residual_per_window": max_abs,
        "hold_reasons": dict(holds),
        "entry_band_binding_decisions": band_binding,
        "entry_band_binding_frac": (round(band_binding / total_decisions, 3)
                                    if total_decisions else None),
        "tick_forced_standdown": tick_binding,
        "tick_forced_standdown_frac": (round(tick_binding / total_decisions, 3)
                                       if total_decisions else None),
        "desired_price_at_touch_frac": (round(near_touch / quotes, 3)
                                        if quotes else None),
        "l5_reference": L5_BTC15M,
    }


def economics_q1(windows: List[dict]) -> Dict[str, Any]:
    """Six separate lines. Nothing here is realized."""
    matched_pairs = 0
    pair_cost_total = D("0")
    residual_units = 0
    residual_cost = D("0")
    rebate_accrual = D("0")
    assumed_fills = 0

    for w in windows:
        inv_up = inv_dn = 0
        up_cost = dn_cost = D("0")
        for d in w.get("decisions") or []:
            if not d.get("assumed_filled"):
                continue
            assumed_fills += 1
            px = D(str(d["desired_price"]))
            rebate_accrual += fees.maker_rebate(px)
            if d["side"] == "UP":
                inv_up += 1
                up_cost += px
            else:
                inv_dn += 1
                dn_cost += px
        m = min(inv_up, inv_dn)
        matched_pairs += m
        if m:
            avg_up = up_cost / inv_up if inv_up else D(0)
            avg_dn = dn_cost / inv_dn if inv_dn else D(0)
            pair_cost_total += (avg_up + avg_dn) * m
        r = inv_up - inv_dn
        residual_units += abs(r)
        if r > 0 and inv_up:
            residual_cost += (up_cost / inv_up) * abs(r)
        elif r < 0 and inv_dn:
            residual_cost += (dn_cost / inv_dn) * abs(r)

    pair_payout = D(matched_pairs)              # each matched pair pays $1.00
    pair_pnl = pair_payout - pair_cost_total
    return {
        "basis": "q=1, one contract per action, TRACK B assumed fills only",
        "assumed_fills": assumed_fills,
        "1_matched_pair_economics": {
            "pairs": matched_pairs,
            "cost_usd": str(pair_cost_total.quantize(D("0.0001"))),
            "payout_usd": str(pair_payout.quantize(D("0.0001"))),
            "pnl_usd": str(pair_pnl.quantize(D("0.0001"))),
            "avg_pair_cost": (str((pair_cost_total / matched_pairs)
                                  .quantize(D("0.0001")))
                              if matched_pairs else None),
            "label": "SHADOW. These pairs were never bought.",
        },
        "2_directional_residual": {
            "units": residual_units,
            "cost_basis_usd": str(residual_cost.quantize(D("0.0001"))),
            "marked_pnl_usd": None,
            "label": ("NOT marked. The residual's value is a settlement "
                      "outcome, and L5's own corpus measures residual P&L as "
                      "NEGATIVE (-$144,065 over 5,546 windows). Marking it at "
                      "cost would flatter this line."),
        },
        "3_maker_rebate": {
            "accrual_usd": str(rebate_accrual.quantize(D("0.0001"))),
            "rule": "0.0125 * C * p * (1 - p), credited at the point of trade",
            "status": "ACCRUAL ONLY -- excluded from pre-rebate P&L and from "
                      "strategy selection; income only once credited",
        },
        "4_liquidity_reward": {
            "usd": "0.00",
            "why": ("program crypto_up_down_20260922 posts a $30 pool per "
                    "daily_event across ~120 markets, but neither our "
                    "eligibility nor our share of score is established, so "
                    "it is counted at zero"),
        },
        "5_taker_fees": {
            "usd": "0.00",
            "why": "every action is a maker join at the touch; nothing crosses",
        },
        "6_modelled_net": {
            "pre_rebate_usd": str(pair_pnl.quantize(D("0.0001"))),
            "with_rebate_accrual_usd": str(
                (pair_pnl + rebate_accrual).quantize(D("0.0001"))),
            "label": "MODELLED. Not shadow-realized, not realized. The "
                     "residual line is deliberately unmarked, so neither "
                     "figure is a complete P&L.",
        },
        "capacity": {
            "derived": False,
            "why": ("capacity needs a fill assumption with evidence behind "
                    "it. The only fill assumption available is Track B's "
                    "volume-at-touch rule, which has none. No market-share "
                    "figure is assumed."),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shadow", default="analysis/pmus/p25_shadow_trackB.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    ws = load(a.shadow)
    rep = {"label": "PMUS_BTC15M_COMPAT_AND_ECONOMICS_V1",
           "compatibility": compat(ws), "economics_q1": economics_q1(ws)}
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1, default=str)
    c = rep["compatibility"]
    print("P4 -- BEHAVIOURAL COMPATIBILITY  (%d windows, %d decision points)"
          % (c["windows_replayed"], c["decision_points"]))
    print("  does it OPEN?                 %-6s (%d opens)"
          % (c["does_it_OPEN"], c["opens"]))
    print("  does it EXTEND repeatedly?    %-6s (%d extends, longest run %d)"
          % (c["does_it_EXTEND_repeatedly"], c["extends"],
             c["longest_extend_run"]))
    print("  opposite inventory obtainable %-6s (%d completes)"
          % (c["opposite_inventory_obtainable"], c["completes"]))
    print("  residual crossed zero in      %d of %d windows"
          % (c["residual_crossed_zero_in_n_windows"], c["windows_replayed"]))
    print("  P(extend) observed %-7s vs L5 historical %s"
          % (c["p_next_is_extend_observed"],
             L5_BTC15M["p_next_is_extend"]))
    print("  desired price at the touch    %s of quotes"
          % c["desired_price_at_touch_frac"])
    print("  $0.01 tick forced a stand-down %s (%s of decisions)"
          % (c["tick_forced_standdown"], c["tick_forced_standdown_frac"]))
    print("  ENTRY BAND bound              %s (%s of decisions)"
          % (c["entry_band_binding_decisions"], c["entry_band_binding_frac"]))
    print("  hold reasons %s" % c["hold_reasons"])
    e = rep["economics_q1"]
    print("\nP5 -- ECONOMICS at q=1 (%d assumed fills)" % e["assumed_fills"])
    for k in ("1_matched_pair_economics", "2_directional_residual",
              "3_maker_rebate", "4_liquidity_reward", "5_taker_fees",
              "6_modelled_net"):
        print("  %s: %s" % (k, json.dumps(e[k])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
