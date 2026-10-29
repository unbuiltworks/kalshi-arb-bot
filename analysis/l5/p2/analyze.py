"""P2 analysis: segmentation, economics decomposition, and the SEQUENCE test.

Run:  python3 analysis/l5/p2/analyze.py [out.json]
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reconstruct as R                                     # noqa: E402

UNATTRIBUTABLE = ("account_level_not_attributable_to_market: the venue emits "
                  "MAKER_REBATE/TAKER_REBATE/REWARD with an EMPTY conditionId")


def _st(xs: list[float]) -> dict:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    def q(p): return xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 6),
            "p10": round(q(.10), 6), "p25": round(q(.25), 6),
            "p50": round(q(.50), 6), "p75": round(q(.75), 6),
            "p90": round(q(.90), 6), "min": round(xs[0], 6), "max": round(xs[-1], 6)}


def lane(w: R.Window) -> str:
    return f"{(w.asset or '?').upper()}_{w.window_minutes}m"


def window_record(w: R.Window) -> dict:
    """Everything P2 asks for about one window, as a sequence not a trade count."""
    st = R.trajectory(w)
    e = R.economics(w, st)
    if not st:
        return {}
    last = st[-1]
    switches = sum(1 for s in st if s.is_switch)
    extends = sum(1 for s in st if s.is_extend)
    # run-length structure: how long does he stay on one side before flipping?
    runs, cur = [], 1
    for s in st[1:]:
        if s.is_switch:
            runs.append(cur); cur = 1
        else:
            cur += 1
    runs.append(cur)
    # PAIR-COST COMPRESSION: the owner's "gradually compress matched cost".
    pcs = [s.pair_cost for s in st if s.pair_cost is not None]
    first_pc, last_pc = (pcs[0], pcs[-1]) if pcs else (None, None)
    imb = [abs(s.residual) for s in st]
    peak_two_sided = max((min(s.qty_up, s.qty_dn) for s in st), default=0.0)
    return {
        "window_id": w.window_id, "slug": w.slug, "source": w.source,
        "lane": lane(w), "asset": w.asset, "window_minutes": w.window_minutes,
        "window_start_ts": w.window_start_ts,
        "n_fills": len(st),
        "n_side_switches": switches,
        "n_extends": extends,
        "run_len_mean": round(statistics.fmean(runs), 4) if runs else None,
        "run_len_max": max(runs) if runs else None,
        "two_sided": peak_two_sided > 1e-9,
        "first_side": st[0].outcome,
        "first_price": st[0].price,
        "first_shares": st[0].shares,
        "qty_up": round(last.qty_up, 6), "qty_dn": round(last.qty_dn, 6),
        "matched_qty": round(e.matched_qty, 6),
        "max_imbalance": round(max(imb), 6) if imb else 0.0,
        "terminal_residual": round(e.residual_qty, 6),
        "residual_side": e.residual_side,
        "pair_cost_first": round(first_pc, 6) if first_pc is not None else None,
        "pair_cost_final": round(last_pc, 6) if last_pc is not None else None,
        "pair_cost_compressed": (None if (first_pc is None or last_pc is None)
                                 else bool(last_pc < first_pc - 1e-9)),
        "matched_pnl": round(e.matched_pnl, 6) if e.matched_pnl is not None else None,
        "residual_pnl": round(e.residual_pnl, 6) if e.residual_pnl is not None else None,
        "total_spend": round(e.total_spend, 6),
        "total_pnl": round(e.total_pnl, 6) if e.total_pnl is not None else None,
        "winning_outcome": w.winning_outcome,
        "reconciles": e.reconciles,
        "fill_prices": [s.price for s in st],
        "fill_shares": [s.shares for s in st],
        "fill_tte": [s.tte for s in st],
    }


def summarise(recs: list[dict], label: str) -> dict:
    if not recs:
        return {"label": label, "windows": 0}
    settled = [r for r in recs if r.get("total_pnl") is not None]
    matched_pnl = sum(r["matched_pnl"] or 0.0 for r in settled)
    resid_pnl = sum(r["residual_pnl"] or 0.0 for r in settled)
    total = sum(r["total_pnl"] for r in settled)
    spend = sum(r["total_spend"] for r in settled)
    two = [r for r in recs if r["two_sided"]]
    comp = [r["pair_cost_compressed"] for r in recs
            if r["pair_cost_compressed"] is not None]
    prices = [p for r in recs for p in r["fill_prices"]]
    shares = [s for r in recs for s in r["fill_shares"]]
    term = [abs(r["terminal_residual"]) for r in recs]
    return {
        "label": label,
        "windows": len(recs),
        "fills": sum(r["n_fills"] for r in recs),
        "settled_windows": len(settled),
        "reconciled": sum(1 for r in recs if r.get("reconciles") is True),
        "reconcile_checked": sum(1 for r in recs if r.get("reconciles") is not None),
        # ---- ECONOMICS, decomposed as required -------------------------------
        "MATCHED_PAIR_usd": round(matched_pnl, 4),
        "RESIDUAL_TIMING_usd": round(resid_pnl, 4),
        "FEES_usd": R.UNKNOWN,
        "NET_TRADING_usd": round(total, 4),
        "capital_deployed_usd": round(spend, 4),
        "net_per_dollar_deployed": round(total / spend, 6) if spend else None,
        "matched_share_of_net": (round(matched_pnl / total, 4)
                                 if abs(total) > 1e-9 else None),
        # ---- SEQUENCE --------------------------------------------------------
        "share_two_sided": round(len(two) / len(recs), 4),
        "fills_per_window": _st([r["n_fills"] for r in recs]),
        "side_switches_per_window": _st([r["n_side_switches"] for r in recs]),
        "extends_per_window": _st([r["n_extends"] for r in recs]),
        "run_len_mean": _st([r["run_len_mean"] for r in recs]),
        "max_imbalance": _st([r["max_imbalance"] for r in recs]),
        "terminal_abs_residual": _st(term),
        "share_terminal_flat": round(
            sum(1 for t in term if t <= 1e-6) / len(term), 4),
        "pair_cost_first": _st([r["pair_cost_first"] for r in recs]),
        "pair_cost_final": _st([r["pair_cost_final"] for r in recs]),
        "share_pair_cost_compressed": (round(sum(comp) / len(comp), 4)
                                       if comp else None),
        "share_final_pair_le_1_00": _share([r["pair_cost_final"] for r in recs],
                                           lambda v: v <= 1.00),
        "share_final_pair_le_0_99": _share([r["pair_cost_final"] for r in recs],
                                           lambda v: v <= 0.99),
        # ---- CLIP SIZE / PRICE, for the screenshot question -------------------
        "fill_price": _st(prices),
        "fill_shares": _st(shares),
        "share_fills_le_2c": _share(prices, lambda p: p <= 0.02),
        "share_fills_le_5c": _share(prices, lambda p: p <= 0.05),
        "share_fills_ge_98c": _share(prices, lambda p: p >= 0.98),
        "maker_or_taker": R.UNKNOWN,
    }


def _share(xs: list, pred) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(1 for x in xs if pred(x)) / len(xs), 4) if xs else None
