"""Paired policy x execution-shell sweep for canonical L5 on Gemini BTC5m.

This is not a new strategy.  It holds the frozen canonical L5 model,
q=1 research scale, fill semantics and accounting fixed, then asks two
separate questions:

* SHARED L5 BEHAVIOR: does the canonical seeded stochastic action draw
  (which PMUS already logs) materially change EXTEND/COMPLETE sequencing
  versus the modal action?
* VENUE-SPECIFIC EXECUTION: for either canonical policy track, how much do
  Gemini maker rest/cancel/reprice mechanics change strict trade-through
  conversion?

Every fill is historical strict trade-through shadow evidence only.  No venue
orders are sent and all P&L is theoretical.
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
POLICIES = ("modal", "stochastic")


def _metrics(summary: dict) -> dict:
    g = summary["overall"]
    return {
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


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    original_shell = invcf.PRIMARY_SHELL
    original_policy = invcf.PRIMARY_POLICY
    by_policy = {}
    try:
        for policy in POLICIES:
            invcf.PRIMARY_POLICY = policy
            arms = {}
            for shell in SHELLS:
                invcf.PRIMARY_SHELL = shell
                rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                        for s in symbols]
                arms[shell] = _metrics(invcf.summarize(rows))
            by_policy[policy] = arms
    finally:
        invcf.PRIMARY_SHELL = original_shell
        invcf.PRIMARY_POLICY = original_policy

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_POLICY_EXECUTION_SWEEP",
        "classification": {
            "policy": "SHARED L5 BEHAVIOR",
            "shell": "VENUE-SPECIFIC EXECUTION",
            "pnl": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "behavior_fixed": "canonical L5 model bbb55bf90638d2bd; q=1 research scale; seeded stochastic draw is the existing canonical shadow draw, not a new rule",
        "semantics": "strict trade-through shadow evidence only; all P&L theoretical; no venue orders",
        "symbols": len(symbols),
        "by_policy": by_policy,
        # Preserve the old top-level modal shape for downstream readers.
        "arms": by_policy.get("modal", {}),
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_EXECUTION_SWEEP " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
