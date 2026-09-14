"""Chronological out-of-sample validation for Gemini L5 residual exit timing.

READ-ONLY.  This module freezes the canonical L5 behavior and evaluates the
predeclared Gemini execution-shell / terminal-exit candidates on a 50/50
chronological split of the retained BTC-5m tape.  The first half is TRAIN and
may select one shell/timing pair; the second half is HOLDOUT and is reported
once with that frozen selection.

This is deliberately stricter than choosing the best timing on all 400 windows.
No venue orders are sent and all fills remain strict trade-through shadow
assumptions.  P&L is theoretical execution P&L, not realized.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from src import gemini_l5_inventory_cf as invcf
from src import gemini_l5_residual_exit_timing_sweep as ts
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT = ROOT / "analysis/l5/gemini_l5_residual_exit_oos.json"
POLICIES_SHELLS = (
    ("stochastic", "reprice_180s"),
    ("stochastic", "rest_60s"),
    ("stochastic", "touch_cancel_30s"),
)
EXIT_TTE_SECONDS = ts.EXIT_TTE_SECONDS


def _d(v, default="0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def _score(rows: list[dict], bbo: dict[str, list[dict]], tte: int) -> dict:
    pair = sum((_d(r.get("pair_pnl_theoretical")) for r in rows), D("0"))
    residual_rows = [r for r in rows
                     if _d((r.get("final_inventory") or {}).get("residual")) != 0]
    exit_total = D("0")
    executable = 0
    for r in residual_rows:
        symbol = str(r.get("symbol"))
        x = ts._exit(r, bbo[symbol], tte)
        if x is not None and x.get("executable"):
            executable += 1
            exit_total += _d(x.get("exit_pnl"))
    all_exec = executable == len(residual_rows)
    net = pair + exit_total if all_exec else None
    return {
        "windows": len(rows),
        "windows_with_fills": sum(1 for r in rows if int(r.get("fills") or 0) > 0),
        "matched_qty_total": str(sum((_d((r.get("final_inventory") or {}).get("matched")) for r in rows), D("0"))),
        "pair_pnl_theoretical_total": str(pair),
        "residual_windows": len(residual_rows),
        "residual_exit_executable_windows": executable,
        "residual_exit_coverage_pct": (round(100.0 * executable / len(residual_rows), 2)
                                       if residual_rows else 100.0),
        "residual_exit_pnl_theoretical_total": str(exit_total),
        "shadow_net_with_forced_exit_total": str(net) if net is not None else None,
    }


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    cut = len(symbols) // 2
    train_symbols, holdout_symbols = symbols[:cut], symbols[cut:]
    old_policy, old_shell = invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL
    candidates = []
    try:
        for policy, shell in POLICIES_SHELLS:
            invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = policy, shell
            train_rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                          for s in train_symbols]
            holdout_rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                            for s in holdout_symbols]
            for tte in EXIT_TTE_SECONDS:
                train = _score(train_rows, bbo, tte)
                holdout = _score(holdout_rows, bbo, tte)
                candidates.append({
                    "policy": policy,
                    "shell": shell,
                    "exit_tte_s": tte,
                    "train": train,
                    "holdout": holdout,
                })
    finally:
        invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = old_policy, old_shell

    eligible = [c for c in candidates
                if c["train"]["shadow_net_with_forced_exit_total"] is not None]
    selected = (max(eligible,
                    key=lambda c: _d(c["train"]["shadow_net_with_forced_exit_total"]))
                if eligible else None)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_CHRONOLOGICAL_OOS",
        "classification": "VENUE-SPECIFIC EXECUTION/RISK CONTROL with frozen SHARED L5 BEHAVIOR",
        "labels": "READ-ONLY; strict shadow fills; theoretical P&L; no orders",
        "split": {
            "method": "50/50 chronological by BTC05M symbol expiry",
            "train_windows": len(train_symbols),
            "holdout_windows": len(holdout_symbols),
            "train_first": train_symbols[0] if train_symbols else None,
            "train_last": train_symbols[-1] if train_symbols else None,
            "holdout_first": holdout_symbols[0] if holdout_symbols else None,
            "holdout_last": holdout_symbols[-1] if holdout_symbols else None,
        },
        "predeclared_shells": [s for _, s in POLICIES_SHELLS],
        "predeclared_exit_tte_seconds": list(EXIT_TTE_SECONDS),
        "selection_rule": "maximize TRAIN theoretical net among candidates with complete residual-exit coverage; evaluate frozen winner once on HOLDOUT",
        "selected_on_train": selected,
        "holdout_gate": {
            "nonnegative": bool(selected and selected["holdout"]["shadow_net_with_forced_exit_total"] is not None
                                and _d(selected["holdout"]["shadow_net_with_forced_exit_total"]) >= 0),
            "requires_complete_exit_coverage": bool(selected and selected["holdout"]["residual_exit_coverage_pct"] == 100.0),
        },
        "all_candidates": candidates,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_RESIDUAL_EXIT_OOS " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
