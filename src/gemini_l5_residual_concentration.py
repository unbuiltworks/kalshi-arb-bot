"""Per-window loss decomposition for canonical stochastic L5 on Gemini BTC5m.

This is a diagnostic of the existing canonical seeded stochastic action track
under the existing reprice_180s Gemini execution adapter.  It does not add a
filter, alter the model, or submit orders.  The purpose is to distinguish
matched-pair economics from directional residual settlement and determine
whether the negative aggregate is broad or concentrated in a few windows.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from src import gemini_l5_inventory_cf as invcf
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT = ROOT / "analysis/l5/gemini_l5_residual_concentration.json"


def _d(v) -> D:
    try:
        return D(str(v or 0))
    except Exception:
        return D("0")


def _p50(xs):
    vals = [float(x) for x in xs]
    return round(statistics.median(vals), 4) if vals else None


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    old_policy, old_shell = invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL
    try:
        invcf.PRIMARY_POLICY = "stochastic"
        invcf.PRIMARY_SHELL = "reprice_180s"
        rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                for s in symbols]
    finally:
        invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = old_policy, old_shell

    active = [r for r in rows if int(r.get("fills") or 0) > 0]
    matched = [r for r in active if int(r.get("matched_increments") or 0) > 0]
    residual = [r for r in active
                if abs(_d((r.get("final_inventory") or {}).get("residual"))) > 0]
    residual_winners = [r for r in residual if _d(r.get("residual_settlement_pnl_theoretical")) > 0]
    residual_losers = [r for r in residual if _d(r.get("residual_settlement_pnl_theoretical")) < 0]
    flat = [r for r in active
            if abs(_d((r.get("final_inventory") or {}).get("residual"))) == 0]

    total_pair = sum((_d(r.get("pair_pnl_theoretical")) for r in rows), D("0"))
    total_resid = sum((_d(r.get("residual_settlement_pnl_theoretical")) for r in rows), D("0"))
    total_net = total_pair + total_resid
    loser_abs = sorted((abs(_d(r.get("residual_settlement_pnl_theoretical")))
                        for r in residual_losers), reverse=True)
    total_loss_abs = sum(loser_abs, D("0"))
    top1_share = ((loser_abs[0] / total_loss_abs) if loser_abs and total_loss_abs else None)
    top3_share = ((sum(loser_abs[:3], D("0")) / total_loss_abs)
                  if loser_abs and total_loss_abs else None)

    detail = []
    for r in active:
        inv = r.get("final_inventory") or {}
        path = r.get("inventory_path") or []
        detail.append({
            "symbol": r.get("symbol"),
            "fills": r.get("fills"),
            "matched_increments": r.get("matched_increments"),
            "completed_cycles": r.get("completed_cycles"),
            "switches": r.get("switches"),
            "runs": r.get("runs"),
            "final_matched": inv.get("matched"),
            "final_residual": inv.get("residual"),
            "pair_pnl_theoretical": r.get("pair_pnl_theoretical"),
            "residual_pnl_theoretical": r.get("residual_settlement_pnl_theoretical"),
            "net_pnl_theoretical": r.get("net_pnl_theoretical"),
            "gross_spend_theoretical": r.get("gross_spend_theoretical"),
            "side_sequence": [x.get("side") for x in path],
            "relation_sequence": [x.get("relation") for x in path],
            "terminal_pair_cost_pre_fee": (path[-1].get("pair_cost_pre_fee") if path else None),
        })
    detail.sort(key=lambda x: float(_d(x.get("net_pnl_theoretical"))))

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_RESIDUAL_DIAGNOSTIC",
        "classification": {
            "action_track": "SHARED L5 BEHAVIOR",
            "fill_path": "VENUE-SPECIFIC EXECUTION",
            "pair_vs_residual_pnl": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "arm": "canonical stochastic / Gemini reprice_180s / q=1 / strict trade-through",
        "labels": "READ-ONLY; no filters learned from outcome; all fills and P&L theoretical",
        "windows": len(rows),
        "windows_with_fills": len(active),
        "windows_with_matched_increment": len(matched),
        "flat_terminal_windows": len(flat),
        "residual_terminal_windows": len(residual),
        "residual_winner_windows": len(residual_winners),
        "residual_loser_windows": len(residual_losers),
        "pair_pnl_theoretical_total": str(total_pair),
        "residual_pnl_theoretical_total": str(total_resid),
        "net_pnl_theoretical_total": str(total_net),
        "residual_loss_concentration": {
            "total_loser_abs_usd": str(total_loss_abs),
            "top1_share_pct": (round(float(top1_share) * 100, 2) if top1_share is not None else None),
            "top3_share_pct": (round(float(top3_share) * 100, 2) if top3_share is not None else None),
            "loser_abs_p50_usd": _p50(loser_abs),
        },
        "active_windows": detail,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_RESIDUAL_CONCENTRATION " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
