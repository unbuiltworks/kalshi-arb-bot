#!/usr/bin/env python3
"""Paired-only vs probability-aware inventory management. Research only.

No network, no orders, no credentials, no exposure change, no live wiring.
Every number is a REPLAY number against L5's own executed prints: modelled
fills, never confirmed fills, never realized profit.

WHAT THE LIVE LEDGER SAYS THE PROBLEM IS. analysis/l5/btc15m_campaign_reconcile
decomposes 148 attested venue-confirmed settled windows:

    profit pairs (cost <= 0.96)   138.54 pairs   +$0.1032 each
    rescue pairs (cost >  0.96)   110.03 pairs   +$0.0175 each
    residual (unmatched)          210.27 units   -$0.2685 each
                                                 ------------
                                  settled net    -$40.2353

Residual UNITS outnumber PROFIT PAIRS 210 to 139, and each residual costs 2.6x
what a profit pair earns. So breakeven needs roughly 2.6 profit pairs per
residual unit and we run 0.66. The lever is not more trades: it is fewer
unmatched legs at settlement.

AND THE REFUSAL COUNTERS SAY WHY LEGS GO UNMATCHED. From the executor at
2026-11-05T23:59:04Z, cumulative:

    PAST_ENTRY_CUTOFF_NOT_A_COMPLETION   4756
    PAST_COMPLETE_CUTOFF                 4677
    RESIDUAL_ABOVE_CAP                   2373
    COMPLEMENT_PAIR_ABOVE_CAP            2146
    MAX_LIVE_ORDERS                      1635
    ACCUMULATION_EXHAUSTED                 57

Time cutoffs are ~69% of all refusals and inventory exhaustion is negligible.
The engine stops acting 480 s and 600 s into a 900 s window.

THE POLICIES. All share the deployed clip, cash cap, exposure cap, residual
cap, entry cutoff and pair ceiling. None adds directional exposure; the
selective-underdog lane stays OFF here because its own held-out result was
NO-PROMOTION (analysis/l5/btc15m_signal/VERDICT.md).

  A  paired-only, exactly as deployed (completion cutoff 600 s)
  B  A + an UNBOUNDED completion rescue (any complement under $1.00). This
     is an UPPER BOUND, not a deployable policy: it assumes a fillable
     complement is always available. Reported to bound the prize.
  C  A + completion cutoff extended to 840 s, RISK-REDUCING LEGS ONLY
  D  B + C, also an upper bound
  E  C + a rescue GATED by the owner's
     analysis/l5/btc15m_portfolio_decisions.evaluate against the calibrated
     as-of probability -- rescue only when completing lifts the guaranteed
     floor. This is the deployable probability-aware policy.

C and E would need owner authorization to deploy because they change a
cutoff; nothing here changes any deployed value.

Run: PYTHONPATH=. python3 analysis/l5/btc15m_inventory_policy.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.btc15m_calibration_report import (apply_logistic, clustered_mean,
                                                   fit_logistic, load_candles,
                                                   load_sources)
from analysis.l5.btc15m_portfolio_decisions import Candidate, Inventory, evaluate
from analysis.l5.btc15m_signal_replay import (SignalBook, _simulate,
                                              max_drawdown, paired_delta)
from analysis.l5.favorite_accumulation import (CASH_CAP, COMPLETE_CUTOFF_S,
                                               PAIR_CEILING, load_outcomes,
                                               load_trades)

#: Risk-reducing completions only; entry stays at the deployed 480 s.
EXTENDED_COMPLETE_CUTOFF_S = 840.0
#: Fee sensitivity. 0.0 is the deployed maker fee; 0.01 is the taker fee.
FEES = (0.0, 0.005, 0.01)


def probability_gated_rescue(signal: Optional[SignalBook]):
    """Return a predicate the simulator can use to allow a rescue.

    Uses the owner's portfolio evaluator rather than a second implementation.
    `calibrated=True` is passed because the probability handed in has been
    through chronological Platt scaling fitted on TRAIN windows only -- and
    the action is risk-REDUCING, which that module refuses to block anyway;
    the directional refusal path is never reached from here.
    """
    def allow(*, slug: str, t_abs: float, px: float, up: float, dn: float,
              spend: float, off: float, side: str) -> bool:
        if signal is None:
            return False
        p_up = signal.p_up(slug, t_abs)
        if p_up is None:
            return False
        held_up, held_dn = int(round(up)), int(round(dn))
        side = "UP" if held_dn > held_up else "DOWN"
        try:
            r = evaluate(Inventory(up_qty=held_up, down_qty=held_dn,
                                   sunk_cost=spend,
                                   cash_available=max(0.0, CASH_CAP - spend)),
                         Candidate(action="BUY", side=side, price=px, qty=1),
                         p_up, True, residual_cap=1,
                         max_gross_spend=CASH_CAP)
        except ValueError:
            return False
        if not r.get("eligible"):
            return False
        # Completing is worth it when it lifts the guaranteed floor, which is
        # exactly what carrying an unmatched unit costs us.
        return bool(r.get("worst_case_improvement_if_filled", 0) > 0)
    return allow


def empirical_rescue_threshold(byw: Dict[str, List[Dict]],
                               slugs: Sequence[str]) -> float:
    """1 - P(an unmatched unit wins), measured on the given windows only.

    THE ARITHMETIC THAT DECIDES EVERY RESCUE. Leg one is already paid, so it
    is sunk either way. Completing at `px` turns the position into $1.00 for
    an extra `px`. Carrying it returns $1.00 only when the held side wins.
    So completing beats carrying exactly when

        1 - px  >  P(held side wins)        i.e.   px < 1 - P(win)

    Measured on the replay, unmatched units win 21.8% of the time, so the
    break-even complement price is about $0.78 -- NOT $1.00. A rescue that
    pays anything under a dollar is destroying value on every completion
    between $0.78 and $1.00, and that is exactly what policy B does.

    Estimated on TRAIN windows only and applied to held-out windows, so the
    threshold carries no lookahead.
    """
    won = carried = 0
    for slug in slugs:
        r = _simulate(byw[slug], signal=None, participation=1.0)
        if r["residual"] <= 0:
            continue
        carried += 1
        if r["residual_won"] > 0:
            won += 1
    if not carried:
        return 1.0
    return 1.0 - (won / carried)


def fixed_threshold_rescue(threshold: float):
    """Rescue only below a price threshold. No probability, no lookahead."""
    def allow(*, px: float, **_: Any) -> bool:
        return px < threshold
    return allow


def probability_threshold_rescue(signal: Optional[SignalBook]):
    """Rescue when the calibrated as-of probability says escaping is cheap.

    Uses the probability where it actually belongs in an inventory engine:
    not to bet on a direction, but to price the alternative to completing.
    Complete iff `px < 1 - P(the side we already hold wins)`.
    """
    def allow(*, slug: str, t_abs: float, px: float, up: float, dn: float,
              side: str, **_: Any) -> bool:
        if signal is None:
            return False
        p_up = signal.p_up(slug, t_abs)
        if p_up is None:
            return False
        held = "Up" if up > dn else "Down"      # `side` completes the other
        p_held = p_up if held == "Up" else 1.0 - p_up
        return px < 1.0 - p_held
    return allow


def summarize(per_window: List[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any]:
    if not per_window:
        return {"windows": 0}
    per_window = sorted(per_window, key=lambda kv: kv[0])
    nets = [r["realized"] for _, r in per_window]
    mu, ci, n = clustered_mean({str(ws): [r["realized"]] for ws, r in per_window})
    resid = sum(r["residual"] for _, r in per_window)
    lost = sum(r["residual_lost"] for _, r in per_window)
    traded = [r for _, r in per_window if r["actions"] > 0]
    with_pair = [r for r in traded if r["pairs"] > 0]
    return {
        "windows": n, "net_total": sum(nets),
        "net_per_window": mu, "net_per_window_ci": ci,
        "max_drawdown": max_drawdown(nets),
        "worst_window": min(nets), "losing_windows": sum(1 for v in nets if v < 0),
        "actions": sum(r["actions"] for _, r in per_window),
        "pairs": sum(r["pairs"] for _, r in per_window),
        "rescued": sum(r.get("rescued", 0) for _, r in per_window),
        "fees": sum(r["fees"] for _, r in per_window),
        "residual_units": resid, "residual_lost_units": lost,
        "residual_lose_rate": (lost / resid) if resid else None,
        "traded_windows": len(traded),
        "paired_fill_rate": (len(with_pair) / len(traded)) if traded else None,
    }


POLICIES: Dict[str, Dict[str, Any]] = {
    "A_paired_only": {"rescue": False, "cutoff": COMPLETE_CUTOFF_S, "prob": False},
    "B_rescue": {"rescue": True, "cutoff": COMPLETE_CUTOFF_S, "prob": False},
    "C_late_completion": {"rescue": False, "cutoff": EXTENDED_COMPLETE_CUTOFF_S,
                          "prob": False},
    "D_rescue_late": {"rescue": True, "cutoff": EXTENDED_COMPLETE_CUTOFF_S,
                      "prob": False},
    "E_prob_floor_rescue_late": {"rescue": True,
                                 "cutoff": EXTENDED_COMPLETE_CUTOFF_S,
                                 "prob": "floor"},
    "F_empirical_threshold_rescue": {"rescue": True,
                                     "cutoff": EXTENDED_COMPLETE_CUTOFF_S,
                                     "prob": "empirical"},
    "G_prob_priced_rescue": {"rescue": True,
                             "cutoff": EXTENDED_COMPLETE_CUTOFF_S,
                             "prob": "priced"},
}


def run(split: float = 0.60, participations: Sequence[float] = (1.0, 0.5),
        fees: Sequence[float] = FEES) -> Dict[str, Any]:
    series = load_candles()
    outcomes = load_outcomes()
    sources = load_sources()
    trades = load_trades(outcomes)
    byw: Dict[str, List[Dict]] = collections.defaultdict(list)
    for t in trades:
        byw[t["slug"]].append(t)
    slugs = sorted(byw, key=lambda s: float(s.rsplit("-", 1)[1]))
    probe = SignalBook.build(series, slugs, sources)
    universe = [s for s in slugs
                if probe.p_up(s, float(s.rsplit("-", 1)[1]) + 780.0) is not None]
    cut = int(len(universe) * split)
    train_slugs, test_slugs = universe[:cut], universe[cut:]
    fit_rows: List[Tuple[float, int]] = []
    for slug in train_slugs:
        y = 1 if outcomes[slug] == "Up" else 0
        fit_rows.extend((p, y) for _, p in probe._g.get(slug, ()))
    a, b = fit_logistic(fit_rows)
    book = SignalBook.build(series, universe, sources,
                            calibrate=lambda p: apply_logistic(p, a, b))

    emp_threshold = empirical_rescue_threshold(byw, train_slugs)
    out: Dict[str, Any] = {
        "empirical_rescue_threshold_from_train": emp_threshold,
        "provenance": {
            "price_basis": "L5 executed prints, chronological",
            "fills": "MODELLED, not confirmed fills",
            "candles": "data/btc15m/coinbase_1m.jsonl.gz (Coinbase PROXY)",
            "entry_cutoff_s": 480.0, "pair_ceiling": PAIR_CEILING,
            "cash_cap": CASH_CAP,
            "directional_underdog_lane": "OFF (held-out NO-PROMOTION)",
        },
        "windows": {"with_prints": len(byw), "universe": len(universe),
                    "train": len(train_slugs), "test": len(test_slugs)},
        "platt_on_train": {"a": a, "b": b},
        "results": {}, "delta_vs_A": {},
    }
    for fee in fees:
        for part in participations:
            for name, cfg in POLICIES.items():
                for scope, slugset in (("train", train_slugs), ("test", test_slugs)):
                    mode = cfg["prob"]
                    if mode == "floor":
                        allow = probability_gated_rescue(book)
                    elif mode == "empirical":
                        allow = fixed_threshold_rescue(emp_threshold)
                    elif mode == "priced":
                        allow = probability_threshold_rescue(book)
                    else:
                        allow = None
                    rows = [(float(s.rsplit("-", 1)[1]),
                             _simulate(byw[s], signal=None,
                                       participation=part,
                                       complete_cutoff_s=cfg["cutoff"],
                                       fee_per_contract=fee,
                                       rescue=cfg["rescue"],
                                       rescue_allow=allow))
                            for s in slugset]
                    key = "%s|%s|part=%s|fee=%s" % (name, scope, part, fee)
                    out["results"][key] = summarize(rows)
                    if name != "A_paired_only":
                        base = [(float(s.rsplit("-", 1)[1]),
                                 _simulate(byw[s], signal=None,
                                           participation=part,
                                           complete_cutoff_s=COMPLETE_CUTOFF_S,
                                           fee_per_contract=fee, rescue=False))
                                for s in slugset]
                        out["delta_vs_A"][key] = paired_delta(base, rows)
    return out


def main() -> int:
    out = run()
    os.makedirs("analysis/l5/btc15m_signal", exist_ok=True)
    with open("analysis/l5/btc15m_signal/inventory_policy.json", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("windows: %s" % out["windows"])
    print("empirical rescue threshold 1-P(residual wins), TRAIN only: %.4f"
          % out["empirical_rescue_threshold_from_train"])
    h = "%-46s %4s %9s %17s %8s %6s %6s %6s %7s %8s %7s"
    print(h % ("policy", "win", "net", "net/win +/- 95%", "maxDD", "acts",
               "pairs", "resc", "residU", "residLose", "pairFil"))
    for k in sorted(out["results"]):
        s = out["results"][k]
        if not s.get("windows"):
            continue
        print(h % (k, s["windows"], "%+.4f" % s["net_total"],
                   "%+.5f +/- %.5f" % (s["net_per_window"], s["net_per_window_ci"]),
                   "%+.3f" % s["max_drawdown"], s["actions"], s["pairs"],
                   s["rescued"], "%.0f" % s["residual_units"],
                   "n/a" if s["residual_lose_rate"] is None
                   else "%.3f" % s["residual_lose_rate"],
                   "n/a" if s["paired_fill_rate"] is None
                   else "%.2f" % s["paired_fill_rate"]))
    print()
    print("PAIRED per-window delta vs A on the SAME windows (positive = better)")
    dh = "%-46s %4s %18s %9s %6s %6s %6s"
    print(dh % ("variant", "win", "mean/win +/- 95%", "total", "bett", "wors", "same"))
    for k in sorted(out["delta_vs_A"]):
        d = out["delta_vs_A"][k]
        print(dh % (k, d["windows"], "%+.5f +/- %.5f" % (d["mean"], d["ci"]),
                    "%+.4f" % d["total"], d["better"], d["worse"], d["unchanged"]))
    print()
    print("wrote analysis/l5/btc15m_signal/inventory_policy.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
