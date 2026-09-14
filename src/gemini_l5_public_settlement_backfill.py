"""Resolve historical Gemini L5 shadow residuals from Gemini's public event API.

The replay/fills remain hypothetical strict trade-through evidence.  This module
only replaces the old end-of-window mid proxy with the venue's published
settlement/resolution side for historical contracts, so residual economics are
not silently treated as zero when the proxy is ambiguous.

No credentials and no order endpoints are used.
"""
from __future__ import annotations

import json
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
OUT = ROOT / "analysis/l5/gemini_l5_public_settlement_backfill.json"
API = "https://api.gemini.com/v1/prediction-markets/events/{}"


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
        vals = {
            str(c.get("instrumentSymbol") or ""), str(c.get("ticker") or ""),
            str(c.get("symbol") or ""), str(c.get("contractTicker") or ""),
        }
        if symbol in vals:
            return c
    # Short-duration events normally contain a single UP contract.  Only use
    # that fallback when it is unambiguous.
    cs = list(event.get("contracts") or [])
    return cs[0] if len(cs) == 1 else None


def _resolution_side(event: dict, symbol: str) -> str | None:
    c = _contract(event, symbol)
    side = str((c or {}).get("resolutionSide") or "").lower()
    if side in ("yes", "no"):
        return side
    return None


def _residual_pnl(row: dict, resolution_side: str) -> D | None:
    inv = row.get("final_inventory") or {}
    residual = _d(inv.get("residual"))
    if residual == 0:
        return D("0")
    q = abs(residual)
    yes_held = residual > 0
    avg = inv.get("avg_up") if yes_held else inv.get("avg_dn")
    if avg is None:
        return None
    cost = _d(avg)
    fee = tb.maker_fee(cost, q)
    won = (resolution_side == "yes") if yes_held else (resolution_side == "no")
    return ((D("1") - cost) * q - fee) if won else (-cost * q - fee)


def run() -> dict:
    model, art = pv2.load_v2(), fid.load()
    bbo, prints = tb.load_bbo(BBO), tb.load_prints(PRINTS)
    symbols = sorted(set(bbo) & set(prints))
    old_policy, old_shell = invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL
    try:
        invcf.PRIMARY_POLICY = "stochastic"
        invcf.PRIMARY_SHELL = "reprice_180s"
        rows = [invcf.evaluate(s, bbo[s], prints[s], model=model, art=art) for s in symbols]
    finally:
        invcf.PRIMARY_POLICY, invcf.PRIMARY_SHELL = old_policy, old_shell

    active = [r for r in rows if int(r.get("fills") or 0) > 0]
    residual_rows = [r for r in active if _d((r.get("final_inventory") or {}).get("residual")) != 0]
    event_cache: dict[str, dict] = {}
    errors: dict[str, str] = {}
    details = []
    resolved_residual_total = D("0")
    pair_total = sum((_d(r.get("pair_pnl_theoretical")) for r in rows), D("0"))

    for r in residual_rows:
        symbol = str(r.get("symbol"))
        event_ticker = tb.event_of_symbol(symbol)
        try:
            if event_ticker not in event_cache:
                event_cache[event_ticker] = _event_json(event_ticker)
                time.sleep(0.03)
            event = event_cache[event_ticker]
            side = _resolution_side(event, symbol)
            status = str(event.get("status") or "").lower()
            pnl = _residual_pnl(r, side) if side else None
            if pnl is not None:
                resolved_residual_total += pnl
            details.append({
                "symbol": symbol,
                "event_ticker": event_ticker,
                "event_status": status,
                "resolved_at": event.get("resolvedAt"),
                "resolution_side": side,
                "settlement_value": ((event.get("settlement") or {}).get("value")),
                "final_residual": (r.get("final_inventory") or {}).get("residual"),
                "avg_up": (r.get("final_inventory") or {}).get("avg_up"),
                "avg_dn": (r.get("final_inventory") or {}).get("avg_dn"),
                "residual_pnl_public_settlement": (str(pnl) if pnl is not None else None),
            })
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors[symbol] = f"{type(exc).__name__}:{exc}"
            details.append({"symbol": symbol, "event_ticker": event_ticker,
                            "resolution_side": None,
                            "residual_pnl_public_settlement": None})

    resolved = [d for d in details if d.get("residual_pnl_public_settlement") is not None]
    unresolved = [d for d in details if d.get("residual_pnl_public_settlement") is None]
    net = pair_total + resolved_residual_total
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HISTORICAL_REPLAY_PUBLIC_SETTLEMENT_BACKFILL",
        "arm": "canonical stochastic / Gemini reprice_180s / q=1 / strict trade-through",
        "labels": "public Gemini resolution only; fills remain hypothetical shadow evidence; no realized P&L",
        "public_endpoint": "/v1/prediction-markets/events/{eventTicker}",
        "windows_with_fills": len(active),
        "residual_terminal_windows": len(residual_rows),
        "residual_windows_resolved": len(resolved),
        "residual_windows_unresolved": len(unresolved),
        "residual_resolution_coverage_pct": (round(100.0 * len(resolved) / len(residual_rows), 2)
                                             if residual_rows else 100.0),
        "pair_pnl_theoretical_total": str(pair_total),
        "residual_pnl_public_settlement_total": str(resolved_residual_total),
        "shadow_net_with_public_settlement_known_total": str(net),
        "shadow_net_is_complete": len(unresolved) == 0,
        "errors": errors,
        "details": details,
        "classification": {
            "fills": "VENUE-SPECIFIC EXECUTION / hypothetical",
            "settlement": "VENUE-SPECIFIC ECONOMICS / public resolved outcome",
            "realized": False,
        },
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_PUBLIC_SETTLEMENT " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
