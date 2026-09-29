"""How much capital does L5 actually commit inside one BTC-15m window?

WHY THE ANSWER IS SIMPLE HERE, AND IT IS THE OWNER'S CONSTRAINT THAT MAKES IT
SO. The observed sequence is BUY-only: 63,569 BUY fills, zero SELL, and
`cum_realized_pnl_trading` is exactly 0.0 on every one of those rows. No
intra-window sale and no redemption is recorded anywhere, so nothing offsets a
purchase before settlement. CUMULATIVE PURCHASE DOLLARS THEREFORE *ARE*
COMMITTED CAPITAL, and peak committed capital is the final cumulative spend.

This corrects, in the conservative direction, an earlier caveat of mine that
gross turnover "is not peak capital because matched pairs recycle at
settlement". They recycle AT settlement -- which is after the window. Inside
the window there is no recycling, so the requirement is the full turnover.

A matched pair still has a lower ECONOMIC risk than two unmatched legs, and
that stays true. It does not reduce the cash you must have on hand to buy the
second leg.

THE COUNTERFACTUAL. Keep L5's observed EPISODE TIMING exactly -- every
decision at the moment L5 made it -- and replace each episode's size with our
q10 clip at the price L5 actually paid. That isolates cadence from size: it
asks what OUR economics would cost if we simply acted as often as L5 does.

Read-only. Nothing fitted, no parameter read from any strategy module.
"""
from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional, Sequence

import scripts.l5_completion_behavior as B
import scripts.l5_fill_episode_normalization as N

CLIP = 10.0          # our q10, in shares
EPISODE_GAP_S = 5.0  # the most conservative (most aggressive) collapse
PCTS = (50, 75, 90, 95, 100)


def _pct(v: Sequence[float], p: int) -> Optional[float]:
    if not v:
        return None
    s = sorted(v)
    if p >= 100:
        return s[-1]
    i = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[i]


def window_capital(rows: Sequence[dict]) -> Optional[Dict[str, Any]]:
    fills = N.fills_of(rows)
    if len(fills) < 5:
        return None

    cum = 0.0
    cum_up = cum_dn = 0.0
    q_up = q_dn = 0.0
    traj: List[Dict[str, float]] = []
    for f in fills:
        cum += f["notional"]
        if f["side"] == "Up":
            cum_up += f["notional"]
            q_up += f["shares"]
        else:
            cum_dn += f["notional"]
            q_dn += f["shares"]
        matched = min(q_up, q_dn)
        traj.append({
            "t": f["t"], "cum": cum, "cum_up": cum_up, "cum_dn": cum_dn,
            "q_up": q_up, "q_dn": q_dn,
            "matched": matched, "residual": abs(q_up - q_dn),
            # cost of the matched book so far, per pair
            "pair_unit_cost": ((cum_up / q_up + cum_dn / q_dn)
                               if (q_up > 0 and q_dn > 0) else None),
        })

    eps = N.episodes(fills, EPISODE_GAP_S)
    # COUNTERFACTUAL: L5's timing, our clip, at L5's own prices.
    cf_cum = 0.0
    cf_traj = []
    for e in eps:
        cf_cum += CLIP * e["px"]
        cf_traj.append(cf_cum)

    pair_costs = [x["pair_unit_cost"] for x in traj
                  if x["pair_unit_cost"] is not None]
    return {
        # cumulative purchase dollars == committed capital (BUY-only)
        "peak_capital": cum,
        "peak_capital_up": cum_up,
        "peak_capital_dn": cum_dn,
        "final_q_up": q_up, "final_q_dn": q_dn,
        "final_matched": min(q_up, q_dn),
        "final_residual": abs(q_up - q_dn),
        "completion": (2.0 * min(q_up, q_dn) / (q_up + q_dn)
                       if (q_up + q_dn) else None),
        "pair_unit_cost_first": pair_costs[0] if pair_costs else None,
        "pair_unit_cost_final": pair_costs[-1] if pair_costs else None,
        "pair_unit_cost_median": (statistics.median(pair_costs)
                                  if pair_costs else None),
        "capital_at_half_window": next(
            (x["cum"] for x in traj
             if x["t"] - traj[0]["t"] >= 450.0), cum),
        "n_fills": len(fills),
        "n_episodes": len(eps),
        "episodes_per_min": len(eps) * 60.0 / 900.0,
        "notional_per_episode_median": statistics.median(
            e["notional"] for e in eps),
        "shares_per_episode_median": statistics.median(
            e["shares"] for e in eps),
        # counterfactual: our clip at L5's cadence and prices
        "cf_q10_peak_capital": cf_cum,
        "cf_q10_shares": CLIP * len(eps),
        "cf_ratio_vs_l5": (cf_cum / cum) if cum else None,
        "labels": ("BUY-only tape, cum_realized_pnl_trading == 0 on every "
                   "row: no intra-window sale or redemption, so cumulative "
                   "spend IS committed capital. No recycling assumed."),
    }


def run() -> Dict[str, Any]:
    by = B.windows()
    recs = []
    for wid, rows in by.items():
        w = window_capital(rows)
        if w is None or w["completion"] is None:
            continue
        recs.append({"window_id": wid, "first_t": rows[0]["datetime_utc"], **w})

    comp = sorted(r["completion"] for r in recs)
    hi, lo = comp[2 * len(comp) // 3], comp[len(comp) // 3]
    cohorts = {
        "ALL": recs,
        "HIGH_COMPLETION": [r for r in recs if r["completion"] >= hi],
        "LOW_COMPLETION": [r for r in recs if r["completion"] <= lo],
    }

    keys = ("peak_capital", "peak_capital_up", "peak_capital_dn",
            "capital_at_half_window", "final_matched", "final_residual",
            "pair_unit_cost_final", "n_episodes", "episodes_per_min",
            "notional_per_episode_median", "shares_per_episode_median",
            "cf_q10_peak_capital", "cf_q10_shares", "cf_ratio_vs_l5")
    out: Dict[str, Any] = {
        "windows": len(recs), "episode_gap_s": EPISODE_GAP_S,
        "clip_shares": CLIP,
        "tercile_cuts": {"high": round(hi, 4), "low": round(lo, 4)},
        "no_recycling_evidence": (
            "63,569 BUY fills, 0 SELL, cum_realized_pnl_trading == 0.0 on "
            "every BTC-15m row: nothing offsets a purchase before settlement"),
        "cohorts": {},
    }
    for name, rows in cohorts.items():
        d: Dict[str, Any] = {"n": len(rows)}
        for k in keys:
            v = [r[k] for r in rows if r.get(k) is not None]
            d[k] = {("p%d" % p): (None if _pct(v, p) is None
                                  else round(_pct(v, p), 4)) for p in PCTS}
        out["cohorts"][name] = d
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
