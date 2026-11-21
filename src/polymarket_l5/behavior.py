"""Behavioral reconstruction of L5 from the canonical sequence (Phase 3).

Answers the audit's twelve questions quantitatively, from source-accounted
fills only. Every number here is computed from
analysis/l5/l5_canonical_sequence.csv.gz (see canonical_sequence.py for
schema and maker/taker honesty rules); P&L attribution is cited from the
ledger (analysis/l5/l5_ledger.json), which is reconciled against
Polymarket's own closed-position rows.

    python3 -m src.polymarket_l5.behavior
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

IN_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
LEDGER = config.OUTPUT_DIR / "l5_ledger.json"
OUT = config.OUTPUT_DIR / "l5_behavior.json"

PRICE_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.80)
TTE_BUCKETS = ((900, 1e9), (600, 900), (300, 600), (120, 300), (60, 120),
               (30, 60), (0, 30))
SHARP_MOVE = 0.10   # |price change| between consecutive same-side fills


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def _pctile(xs: List[float], q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return xs[int(q * (len(xs) - 1))] if xs else None


def main() -> int:
    windows: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(IN_CSV, "rt", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            windows[row["window_id"]].append(row)

    n_windows = len(windows)
    both_sides = 0
    pair_costs: List[float] = []
    residual_final: List[dict] = []
    imb_evolution = defaultdict(list)      # window-progress decile -> |imb share|
    size_by_price = defaultdict(list)      # price band -> qty
    after_threshold = {t: defaultdict(float) for t in PRICE_THRESHOLDS}
    sharp_moves = defaultdict(float)
    tte_stats = {b: defaultdict(float) for b in TTE_BUCKETS}
    max_imb_cost: List[float] = []
    mt_counts = defaultdict(int)
    by_split = defaultdict(lambda: defaultdict(list))   # (asset,dur) -> metric

    for wid, rows in windows.items():
        rows.sort(key=lambda r: (int(r["trade_seq"]),))
        dur_s = int(rows[0]["duration_minutes"]) * 60
        asset = rows[0]["asset"]
        dur = rows[0]["duration_minutes"]
        split = f"{asset}-{dur}m"

        last = rows[-1]
        up_q = _f(last["cum_up_qty"]) or 0.0
        dn_q = _f(last["cum_down_qty"]) or 0.0
        if up_q > 1e-9 and dn_q > 1e-9:
            both_sides += 1
            by_split[split]["both"].append(1.0)
        else:
            by_split[split]["both"].append(0.0)
        pc = _f(last["matched_pair_unit_cost"])
        if pc is not None and (_f(last["matched_qty"]) or 0) > 1e-9:
            pair_costs.append(pc)
            by_split[split]["pair_cost"].append(pc)

        res = _f(last["residual_qty_signed"]) or 0.0
        winner = last["settlement_outcome"]
        if abs(res) > 1e-9:
            res_side = "Up" if res > 0 else "Down"
            last_trade_tte = _f(last["seconds_to_settlement"]) or 0.0
            residual_final.append({
                "abs_qty": abs(res),
                "on_winner": res_side == winner,
                "last_trade_tte": last_trade_tte,
                "kept_trading_into_final_60s": last_trade_tte <= 60,
            })

        window_max_imb = 0.0
        prev_price_by_side: Dict[str, float] = {}
        for i, r in enumerate(rows):
            price = _f(r["price"]) or 0.0
            qty = _f(r["quantity"]) or 0.0
            side = r["outcome"]
            tte = _f(r["seconds_to_settlement"]) or 0.0
            sfs = _f(r["seconds_from_window_start"]) or 0.0
            mt_counts[r["maker_taker"]] += 1

            if r["side"] == "BUY":
                band = min(9, int(price * 10))
                size_by_price[band].append(qty)

            up_s = _f(r["cum_up_spend"]) or 0.0
            dn_s = _f(r["cum_down_spend"]) or 0.0
            imb_cost = abs(up_s - dn_s)
            window_max_imb = max(window_max_imb, imb_cost)
            progress = min(0.999, max(0.0, sfs / dur_s)) if dur_s else 0.0
            tot = up_s + dn_s
            if tot > 0:
                imb_evolution[int(progress * 10)].append(imb_cost / tot)

            for lo, hi in TTE_BUCKETS:
                if lo <= tte < hi:
                    b = tte_stats[(lo, hi)]
                    b["fills"] += 1
                    b["dollars"] += _f(r["dollars"]) or 0.0
                    if r["maker_taker"] == "taker":
                        b["taker_fills"] += 1
                    break

            if r["side"] == "BUY" and i + 1 < len(rows):
                nxt = rows[i + 1]
                nxt_side = nxt["outcome"]
                for t in PRICE_THRESHOLDS:
                    prev = prev_price_by_side.get(side)
                    if prev is not None and prev < t <= price:
                        a = after_threshold[t]
                        a["events"] += 1
                        if nxt_side == side:
                            a["next_same_side"] += 1
                        else:
                            a["next_complement"] += 1
                        a["next_dollars"] += _f(nxt["dollars"]) or 0.0
                prev = prev_price_by_side.get(side)
                if prev is not None and abs(price - prev) >= SHARP_MOVE:
                    rising = price > prev
                    nxt_price = _f(nxt["price"]) or 0.0
                    sharp_moves["events"] += 1
                    if nxt["outcome"] == side and rising:
                        sharp_moves["chase_winner"] += 1
                    elif nxt["outcome"] != side and rising:
                        sharp_moves["buy_cheap_complement"] += 1
                    elif nxt["outcome"] == side and not rising:
                        sharp_moves["average_down_same"] += 1
                    else:
                        sharp_moves["buy_recovering_complement"] += 1
                    sharp_moves["next_price_sum"] += nxt_price
                prev_price_by_side[side] = price

        max_imb_cost.append(window_max_imb)
        by_split[split]["max_imb"].append(window_max_imb)

    n_res = len(residual_final)
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    trading = ledger.get("trading", {})
    income = ledger.get("income", {})

    report: Dict[str, Any] = {
        "source": str(IN_CSV),
        "n_windows": n_windows,
        "q1_both_sides_pct": round(100 * both_sides / n_windows, 1),
        "q2_pair_cost_distribution": {
            "n": len(pair_costs),
            "p10": round(_pctile(pair_costs, 0.10), 4),
            "p25": round(_pctile(pair_costs, 0.25), 4),
            "median": round(_median(pair_costs), 4),
            "p75": round(_pctile(pair_costs, 0.75), 4),
            "p90": round(_pctile(pair_costs, 0.90), 4),
            "pct_above_1": round(100 * sum(1 for p in pair_costs if p > 1.0)
                                 / len(pair_costs), 1),
        },
        "q3_residual_windows": {
            "n_with_residual": n_res,
            "pct_of_windows": round(100 * n_res / n_windows, 1),
            "median_abs_qty": round(_median([r["abs_qty"] for r in residual_final]) or 0, 2),
            "residual_on_winner_pct": round(
                100 * sum(r["on_winner"] for r in residual_final) / n_res, 1) if n_res else None,
            "still_trading_final_60s_pct": round(
                100 * sum(r["kept_trading_into_final_60s"] for r in residual_final) / n_res, 1) if n_res else None,
            "median_last_trade_tte_s": round(_median(
                [r["last_trade_tte"] for r in residual_final]) or 0, 1),
        },
        "q4_imbalance_evolution_by_decile": {
            str(d): round(_median(v) or 0, 4)
            for d, v in sorted(imb_evolution.items())},
        "q5_fill_size_by_price_band": {
            f"{b/10:.1f}-{(b+1)/10:.1f}": {
                "n": len(v), "median_qty": round(_median(v) or 0, 2),
                "p90_qty": round(_pctile(v, 0.90) or 0, 2)}
            for b, v in sorted(size_by_price.items())},
        "q6_after_price_crosses_threshold": {
            f"{t:.2f}": {
                "events": int(a["events"]),
                "next_same_side_pct": round(100 * a["next_same_side"] / a["events"], 1) if a["events"] else None,
                "next_complement_pct": round(100 * a["next_complement"] / a["events"], 1) if a["events"] else None,
            } for t, a in after_threshold.items()},
        "q7_after_sharp_move": {
            "events": int(sharp_moves["events"]),
            "chase_winner_pct": round(100 * sharp_moves["chase_winner"] / sharp_moves["events"], 1) if sharp_moves["events"] else None,
            "buy_cheap_complement_pct": round(100 * sharp_moves["buy_cheap_complement"] / sharp_moves["events"], 1) if sharp_moves["events"] else None,
            "average_down_same_pct": round(100 * sharp_moves["average_down_same"] / sharp_moves["events"], 1) if sharp_moves["events"] else None,
            "buy_recovering_complement_pct": round(100 * sharp_moves["buy_recovering_complement"] / sharp_moves["events"], 1) if sharp_moves["events"] else None,
        },
        "q8_tte_buckets": {
            f"{lo}-{int(hi) if hi < 1e9 else 'start'}s": {
                "fills": int(b["fills"]),
                "dollars": round(b["dollars"], 0),
                "taker_share_pct": round(100 * b["taker_fills"] / b["fills"], 1) if b["fills"] else None,
            } for (lo, hi), b in tte_stats.items()},
        "q9_max_imbalance_cost": {
            "median": round(_median(max_imb_cost) or 0, 2),
            "p90": round(_pctile(max_imb_cost, 0.90) or 0, 2),
            "p99": round(_pctile(max_imb_cost, 0.99) or 0, 2),
            "max": round(max(max_imb_cost), 2),
        },
        "q10_maker_taker": dict(mt_counts),
        "q11_pnl_attribution_from_ledger": {
            "pair_locked_usd": trading.get("pair_locked"),
            "directional_residual_usd": trading.get("residual_and_timing"),
            "maker_rebates_usd": income.get("MAKER_REBATE"),
            "taker_rebates_usd": income.get("TAKER_REBATE"),
            "liquidity_rewards_usd": income.get("REWARD"),
            "net_trading_cash_usd": trading.get("net_trading_cash"),
            "note": "source-accounted cashflows; see l5_ledger.json reconciliation",
        },
        "q12_by_asset_duration": {
            split: {
                "n_windows": len(v["both"]),
                "both_sides_pct": round(100 * sum(v["both"]) / len(v["both"]), 1),
                "median_pair_cost": round(_median(v["pair_cost"]) or 0, 4),
                "median_max_imbalance_usd": round(_median(v["max_imb"]) or 0, 2),
            } for split, v in sorted(by_split.items())},
    }
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
