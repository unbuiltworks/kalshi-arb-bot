"""Public-settlement policy x execution-shell sweep for Gemini BTC5m.

The prior shell sweep used the historical end-of-window proxy for directional
residuals.  That proxy is often ambiguous and materially understated residual
loss.  This report holds the canonical L5 model/fill semantics fixed, resolves
terminal residuals from Gemini's published event outcomes, and compares shells
on complete economics.

All fills remain hypothetical strict trade-through shadow evidence.  No orders,
credentials, or private endpoints are used.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src import gemini_l5_inventory_cf as invcf
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT = ROOT / "analysis/l5/gemini_l5_public_settlement_shell_sweep.json"
API = "https://api.gemini.com/v1/prediction-markets/events/{}"
POLICIES = ("modal", "stochastic")
SHELLS = ("touch_cancel_30s", "rest_60s", "rest_120s", "rest_180s", "reprice_180s")


def _d(v, default="0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def _event_json(event_ticker: str) -> dict:
    req = Request(API.format(quote(event_ticker, safe="")), headers={"User-Agent": "l5-research/1"})
    with urlopen(req, timeout=10) as resp:  # nosec - fixed Gemini HTTPS host
        return json.loads(resp.read().decode("utf-8"))


def _contract(event: dict, symbol: str) -> dict | None:
    for c in event.get("contracts") or []:
        vals = {str(c.get(k) or "") for k in
                ("instrumentSymbol", "ticker", "symbol", "contractTicker")}
        if symbol in vals:
            return c
    cs = list(event.get("contracts") or [])
    return cs[0] if len(cs) == 1 else None


def _resolution_side(event: dict, symbol: str) -> str | None:
    side = str((_contract(event, symbol) or {}).get("resolutionSide") or "").lower()
    return side if side in ("yes", "no") else None


def _residual_pnl(row: dict, side: str) -> D | None:
    inv = row.get("final_inventory") or {}
    residual = _d(inv.get("residual"))
    if residual == 0:
        return D("0")
    yes_held = residual > 0
    avg = inv.get("avg_up") if yes_held else inv.get("avg_dn")
    if avg is None:
        return None
    q = abs(residual)
    cost = _d(avg)
    fee = tb.maker_fee(cost, q)
    won = (side == "yes") if yes_held else (side == "no")
    return ((D("1") - cost) * q - fee) if won else (-cost * q - fee)


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 4) if xs else None


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    cache: dict[str, dict] = {}
    fetch_errors: dict[str, str] = {}

    def resolution(symbol: str) -> str | None:
        ev = tb.event_of_symbol(symbol)
        try:
            if ev not in cache:
                cache[ev] = _event_json(ev)
                time.sleep(0.02)
            return _resolution_side(cache[ev], symbol)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            fetch_errors[symbol] = f"{type(exc).__name__}:{exc}"
            return None

    old_policy, old_shell = invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL
    arms: dict[str, dict[str, dict]] = {}
    try:
        for policy in POLICIES:
            invcf.PRIMARY_POLICY = policy
            arms[policy] = {}
            for shell in SHELLS:
                invcf.PRIMARY_SHELL = shell
                rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art)
                        for s in symbols]
                active = [r for r in rows if int(r.get("fills") or 0) > 0]
                residual = [r for r in active
                            if _d((r.get("final_inventory") or {}).get("residual")) != 0]
                pair = sum((_d(r.get("pair_pnl_theoretical")) for r in rows), D("0"))
                rpnl = D("0")
                resolved = 0
                residual_abs = D("0")
                winners = losers = 0
                for r in residual:
                    residual_abs += abs(_d((r.get("final_inventory") or {}).get("residual")))
                    side = resolution(str(r.get("symbol")))
                    pnl = _residual_pnl(r, side) if side else None
                    if pnl is None:
                        continue
                    resolved += 1
                    rpnl += pnl
                    winners += int(pnl > 0)
                    losers += int(pnl < 0)
                complete = resolved == len(residual)
                net = pair + rpnl if complete else None
                matched_qty = sum((_d((r.get("final_inventory") or {}).get("matched"))
                                   for r in rows), D("0"))
                completed = sum(int(r.get("completed_cycles") or 0) for r in rows)
                two_plus = sum(1 for r in rows if int(r.get("completed_cycles") or 0) >= 2)
                fill_residuals = [float(abs(_d((r.get("final_inventory") or {}).get("residual"))))
                                  for r in active]
                arms[policy][shell] = {
                    "windows": len(rows),
                    "windows_with_fills": len(active),
                    "strict_shadow_fills": sum(int(r.get("fills") or 0) for r in rows),
                    "completed_cycles_total": completed,
                    "windows_with_2plus_completed_cycles": two_plus,
                    "matched_qty_total": str(matched_qty),
                    "pair_pnl_theoretical_total": str(pair),
                    "pair_edge_per_matched_unit": (str(pair / matched_qty) if matched_qty else None),
                    "terminal_residual_windows": len(residual),
                    "terminal_residual_abs_total": str(residual_abs),
                    "terminal_residual_abs_p50_fill_windows": _median(fill_residuals),
                    "public_settlement_resolved_residual_windows": resolved,
                    "public_settlement_coverage_pct": (round(100.0 * resolved / len(residual), 2)
                                                       if residual else 100.0),
                    "residual_winner_windows": winners,
                    "residual_loser_windows": losers,
                    "residual_pnl_public_settlement_total": str(rpnl),
                    "shadow_net_public_settlement_total": (str(net) if net is not None else None),
                    "shadow_net_complete": complete,
                }
    finally:
        invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = old_policy, old_shell

    # Ranking is descriptive only and excludes incomplete settlement coverage.
    ranked = []
    for policy, shells in arms.items():
        for shell, m in shells.items():
            if m["shadow_net_complete"] and m["shadow_net_public_settlement_total"] is not None:
                ranked.append({"policy": policy, "shell": shell,
                               "shadow_net": m["shadow_net_public_settlement_total"],
                               "matched_qty": m["matched_qty_total"],
                               "residual_abs": m["terminal_residual_abs_total"]})
    ranked.sort(key=lambda x: _d(x["shadow_net"]), reverse=True)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_PUBLIC_SETTLEMENT_SHELL_SWEEP",
        "labels": "strict trade-through fills remain hypothetical; public Gemini settlement resolves residual outcome; no realized P&L; no orders",
        "classification": {
            "policy": "SHARED L5 BEHAVIOR",
            "shell": "VENUE-SPECIFIC EXECUTION",
            "settlement": "VENUE-SPECIFIC ECONOMICS / public resolved outcome",
        },
        "symbols": len(symbols),
        "arms": arms,
        "ranking_by_complete_public_settlement_shadow_net": ranked,
        "event_fetches": len(cache),
        "fetch_errors": fetch_errors,
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_PUBLIC_SETTLEMENT_SHELL_SWEEP " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
