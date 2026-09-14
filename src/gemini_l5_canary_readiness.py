"""Fail-closed readiness report for a bounded Gemini q=1 live probe.

This module NEVER arms or submits anything.  It turns the already-declared
research requirements into an auditable report so live-probe pressure cannot
quietly lower the bar after seeing outcomes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "analysis/l5/gemini_l5_execution_shell_sweep.json"
RESIDUAL = ROOT / "analysis/l5/gemini_l5_residual_concentration.json"
SETTLEMENT = ROOT / "analysis/l5/gemini_l5_public_settlement_backfill.json"
OUT = ROOT / "analysis/l5/gemini_l5_canary_readiness.json"

# Research gate declared before this report existed.
MIN_INDEPENDENT_COMPLETE_WINDOWS = 3
MIN_TWO_PLUS_CYCLE_WINDOWS = 1
MIN_PAIR_EDGE_USD = D("0.01")
MIN_NET_THEORETICAL = D("0")
MIN_RESIDUAL_SETTLEMENT_COVERAGE_PCT = D("100")
ARM = "stochastic/reprice_180s"


def _d(v) -> D:
    try:
        return D(str(v))
    except Exception:
        return D("0")


def run() -> dict:
    sweep = json.loads(SWEEP.read_text(encoding="utf-8"))
    residual = json.loads(RESIDUAL.read_text(encoding="utf-8"))
    settlement = json.loads(SETTLEMENT.read_text(encoding="utf-8"))
    arm = ((sweep.get("by_policy") or {}).get("stochastic") or {}).get("reprice_180s") or {}

    independent = int(arm.get("independent_complete_fill_windows") or 0)
    two_cycle = int(arm.get("windows_with_2plus_completed_cycles") or 0)
    pair_total = _d(arm.get("pair_pnl_theoretical_total"))
    matched_qty = _d(arm.get("matched_qty_total"))
    pair_edge = (pair_total / matched_qty) if matched_qty > 0 else D("0")
    coverage = _d(settlement.get("residual_resolution_coverage_pct"))
    complete_net = bool(settlement.get("shadow_net_is_complete"))
    net = _d(settlement.get("shadow_net_with_public_settlement_known_total"))

    checks = {
        "independent_two_sided_windows": {
            "value": independent, "threshold": MIN_INDEPENDENT_COMPLETE_WINDOWS,
            "ok": independent >= MIN_INDEPENDENT_COMPLETE_WINDOWS,
        },
        "same_window_2plus_completed_cycles": {
            "value": two_cycle, "threshold": MIN_TWO_PLUS_CYCLE_WINDOWS,
            "ok": two_cycle >= MIN_TWO_PLUS_CYCLE_WINDOWS,
        },
        "matched_pair_edge_after_known_fees_usd_per_unit": {
            "value": str(pair_edge), "threshold": str(MIN_PAIR_EDGE_USD),
            "ok": pair_edge >= MIN_PAIR_EDGE_USD,
        },
        "public_residual_settlement_coverage_pct": {
            "value": str(coverage), "threshold": str(MIN_RESIDUAL_SETTLEMENT_COVERAGE_PCT),
            "ok": coverage >= MIN_RESIDUAL_SETTLEMENT_COVERAGE_PCT and complete_net,
        },
        "aggregate_shadow_net_nonnegative_public_settlement": {
            "value": (str(net) if complete_net else None),
            "known_components_value": str(net),
            "threshold": str(MIN_NET_THEORETICAL),
            "ok": complete_net and net >= MIN_NET_THEORETICAL,
        },
        "residual_economics_separately_accounted": {
            "mid_proxy_total": residual.get("residual_pnl_theoretical_total"),
            "public_settlement_total": settlement.get("residual_pnl_public_settlement_total"),
            "threshold": "present",
            "ok": settlement.get("residual_pnl_public_settlement_total") is not None,
        },
    }
    ready = all(bool(x.get("ok")) for x in checks.values())
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "READY_FOR_EXPLICIT_Q1_ARM_APPROVAL" if ready else "NOT_READY",
        "arm_evaluated": ARM,
        "checks": checks,
        "ready": ready,
        "execution_envelope_if_later_approved": {
            "qty": 1,
            "maker_only": True,
            "one_market_at_a_time": True,
            "max_residual_usd": 2.0,
            "daily_realized_loss_stop_usd": 2.0,
            "automatic_arm": False,
        },
        "classification": {
            "behavior": "SHARED L5 BEHAVIOR",
            "fill_evidence": "VENUE-SPECIFIC EXECUTION",
            "profit_gate": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
        },
        "labels": "READ-ONLY readiness only; never arms; strict trade-through and P&L remain theoretical; residual outcomes use public Gemini resolution",
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_CANARY_READINESS " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
