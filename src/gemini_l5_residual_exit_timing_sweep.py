"""READ-ONLY timing sweep for Gemini terminal residual flattening.

Keeps the frozen L5 behavioral policy and existing Gemini execution shells
unchanged.  For each historical residual, asks what an immediately executable
SELL at retained top-of-book would have done at fixed, predeclared times before
settlement.  This is venue-specific risk-control research, not a new entry
strategy and never sends orders.

The sweep reports every timing arm; it does not select a winner or mutate the
live canary gate.  Taker fee uses the published Gemini prediction-market
formula already audited elsewhere: 7% * C * P * (1-P), rounded up to cents.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

from src import gemini_l5_inventory_cf as invcf
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
SETTLEMENT = ROOT / "analysis/l5/gemini_l5_public_settlement_shell_sweep.json"
OUT = ROOT / "analysis/l5/gemini_l5_residual_exit_timing_sweep.json"
ARMS = (("stochastic", "reprice_180s"),
        ("stochastic", "rest_60s"),
        ("stochastic", "touch_cancel_30s"))
EXIT_TTE_SECONDS = (180, 120, 90, 60, 45, 25)
TAKER_RATE = D("0.07")
ONE = D("1")
TICK = D("0.01")


def _d(v, default="0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def taker_fee(price: D, qty: D) -> D:
    raw = TAKER_RATE * qty * price * (ONE - price)
    return raw.quantize(TICK, rounding=ROUND_CEILING)


def _last_two_sided_before(rows: list[dict], t: float) -> dict | None:
    last = None
    for r in rows:
        if float(r.get("t") or 0) > t:
            break
        if r.get("bid") is not None and r.get("ask") is not None:
            last = r
    return last


def _exit(row: dict, bbo_rows: list[dict], exit_tte_s: int) -> dict | None:
    inv = row.get("final_inventory") or {}
    residual = _d(inv.get("residual"))
    if residual == 0:
        return {"flat": True, "executable": True, "exit_pnl": D("0")}
    symbol = str(row.get("symbol"))
    expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(symbol))
    if expiry is None:
        return None
    exit_t = expiry - float(exit_tte_s)
    book = _last_two_sided_before(bbo_rows, exit_t)
    if book is None:
        return {"flat": False, "executable": False, "reason": "no_two_sided_bbo_at_exit"}

    qty = abs(residual)
    if residual > 0:
        cost = _d(inv.get("avg_up"))
        exit_px = _d(book.get("bid"))
        depth = _d(book.get("bid_sz"))
        outcome = "YES"
    else:
        cost = _d(inv.get("avg_dn"))
        exit_px = ONE - _d(book.get("ask"))
        depth = _d(book.get("ask_sz"))
        outcome = "NO"
    if depth < qty:
        return {"flat": False, "executable": False,
                "reason": "insufficient_top_depth", "outcome": outcome,
                "qty": str(qty), "top_depth": str(depth), "exit_px": str(exit_px)}

    entry_fee = tb.maker_fee(cost, qty)
    exit_fee = taker_fee(exit_px, qty)
    pnl = (exit_px - cost) * qty - entry_fee - exit_fee
    return {"flat": False, "executable": True, "outcome": outcome,
            "qty": str(qty), "entry_avg": str(cost), "exit_px": str(exit_px),
            "top_depth": str(depth), "entry_maker_fee": str(entry_fee),
            "exit_taker_fee": str(exit_fee), "exit_pnl": pnl,
            "exit_tte_s": exit_tte_s, "exit_t": exit_t}


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    settled = json.loads(SETTLEMENT.read_text(encoding="utf-8"))
    old_policy, old_shell = invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL
    report_arms = {}
    try:
        for policy, shell in ARMS:
            invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = policy, shell
            rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                    for s in symbols]
            residual_rows = [r for r in rows
                             if _d((r.get("final_inventory") or {}).get("residual")) != 0]
            pair_total = sum((_d(r.get("pair_pnl_theoretical")) for r in rows), D("0"))
            settle_arm = (((settled.get("arms") or {}).get(policy) or {}).get(shell) or {})
            hold_net = settle_arm.get("shadow_net_public_settlement_total")
            timing = {}
            for tte in EXIT_TTE_SECONDS:
                exits = []
                exit_total = D("0")
                executable = 0
                for r in residual_rows:
                    x = _exit(r, bbo[str(r.get("symbol"))], tte)
                    if x is not None and x.get("executable"):
                        executable += 1
                        exit_total += _d(x.get("exit_pnl"))
                    exits.append({"symbol": r.get("symbol"), **(x or {"executable": False})})
                all_exec = executable == len(residual_rows)
                net = pair_total + exit_total if all_exec else None
                timing[str(tte)] = {
                    "exit_tte_s": tte,
                    "residual_windows": len(residual_rows),
                    "executable_windows": executable,
                    "coverage_pct": (round(100.0 * executable / len(residual_rows), 2)
                                     if residual_rows else 100.0),
                    "all_top_depth_executable": all_exec,
                    "pair_pnl_theoretical_total": str(pair_total),
                    "residual_exit_pnl_theoretical_total": str(exit_total),
                    "shadow_net_with_forced_exit_total": (str(net) if net is not None else None),
                    "public_settlement_hold_net_total": hold_net,
                    "forced_exit_minus_hold_delta": (str(net - _d(hold_net))
                                                       if net is not None and hold_net is not None else None),
                    "details": exits,
                }
            complete = [(int(k), _d(v["shadow_net_with_forced_exit_total"]))
                        for k, v in timing.items()
                        if v.get("shadow_net_with_forced_exit_total") is not None]
            best = max(complete, key=lambda x: x[1]) if complete else None
            report_arms[f"{policy}:{shell}"] = {
                "windows": len(rows),
                "residual_windows": len(residual_rows),
                "public_settlement_hold_net_total": hold_net,
                "timing": timing,
                "best_in_sample_tte_s_diagnostic_only": (best[0] if best else None),
                "best_in_sample_shadow_net_diagnostic_only": (str(best[1]) if best else None),
                "selection_warning": "Do not promote the in-sample best timing without out-of-sample validation.",
            }
    finally:
        invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = old_policy, old_shell

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_TERMINAL_RESIDUAL_EXIT_TIMING_SWEEP",
        "classification": "VENUE-SPECIFIC EXECUTION/RISK CONTROL",
        "exit_tte_seconds_predeclared": list(EXIT_TTE_SECONDS),
        "fee_provenance": {"taker_rate": "0.07", "formula": "rate*C*P*(1-P)",
                           "precision": "round up to next cent"},
        "labels": "READ-ONLY timing sweep; no strategy selection; fills/P&L theoretical; no orders",
        "arms": report_arms,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_RESIDUAL_EXIT_TIMING_SWEEP " + json.dumps(run(), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
