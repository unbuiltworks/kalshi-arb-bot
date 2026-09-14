"""Paired execution-shell sweep for the canonical L5 behavior on Gemini BTC5m.

Behavior/model/accounting stay fixed.  Only the venue execution shell changes:
rest/cancel/reprice discipline.  This is therefore VENUE-SPECIFIC EXECUTION
research, not a new strategy.  Every fill is strict historical trade-through
shadow evidence, never a venue fill or realized P&L.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src import gemini_l5_inventory_cf as invcf
from src import gemini_l5_shadow_v2 as gs
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT = ROOT / "analysis/l5/gemini_l5_execution_shell_sweep.json"
SHELLS = ("touch_cancel_30s", "rest_60s", "rest_120s", "rest_180s", "reprice_180s")


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    original = invcf.PRIMARY_SHELL
    arms = {}
    try:
        for shell in SHELLS:
            invcf.PRIMARY_SHELL = shell
            rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art) for s in symbols]
            summary = invcf.summarize(rows)
            g = summary["overall"]
            arms[shell] = {
                "windows": summary.get("windows"),
                "windows_with_fills": summary.get("windows_with_fills"),
                "fills": g.get("fills"),
                "fills_per_action": g.get("fills_per_action"),
                "extend_orders": g.get("extend_orders"),
                "extend_fills": g.get("extend_fills"),
                "extend_conversion_pct": g.get("extend_conversion_pct"),
                "complete_orders": g.get("complete_orders"),
                "complete_fills": g.get("complete_fills"),
                "complete_conversion_pct": g.get("complete_conversion_pct"),
                "matched_fraction_p50": g.get("matched_fraction_p50"),
                "terminal_residual_abs_p50": g.get("terminal_residual_abs_p50"),
                "pair_cost_after_known_fees_p50": g.get("pair_cost_after_known_fees_p50"),
                "pair_pnl_theoretical_total": g.get("pair_pnl_theoretical_total"),
                "residual_settlement_pnl_theoretical_total": g.get("residual_settlement_pnl_theoretical_total"),
                "net_theoretical_total": g.get("net_theoretical_total"),
                "gross_spend_theoretical_total": g.get("gross_spend_theoretical_total"),
                "net_per_gross_spend_theoretical": g.get("net_per_gross_spend_theoretical"),
                "switches_per_window": g.get("switches_per_window"),
                "runs_per_window": g.get("runs_per_window"),
                "flat_boundary_replays": summary.get("flat_boundary_replays"),
            }
    finally:
        invcf.PRIMARY_SHELL = original
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_EXECUTION_SWEEP",
        "classification": "VENUE-SPECIFIC EXECUTION",
        "behavior_fixed": "canonical L5 model bbb55bf90638d2bd; modal action policy; q=1 research scale",
        "semantics": "strict trade-through shadow evidence only; all P&L theoretical; no venue orders",
        "symbols": len(symbols),
        "arms": arms,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_EXECUTION_SWEEP " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
