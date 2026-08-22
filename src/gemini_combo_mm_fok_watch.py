"""Continuous read-only WebSocket watch for q=1 same-Gemini combo FOK locks.

This is the production-observation companion to gemini_combo_mm_fok_preflight.
It does not create combos and never imports authenticated order entry.  Every five
minutes it refreshes the active registered combo catalog/canonical legs, subscribes
to Gemini bookTicker for each combo and component, and recomputes the two exact
conjunction payout covers on every book update.

A signal means current public q=1 top-of-book economics clear >=3c after conservative
cent-rounded taker fees.  It is NOT a fill and NOT realized P&L.  Live execution
still requires fresh depth revalidation plus the bounded sequential-FOK recovery
policy because separate orders are not atomic.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx
import websockets

import src.gemini_extreme_combo_live_canary as c
from src.gemini_combo_mm_fok_preflight import _catalog_specs, _opposite
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.sports_pushover_alerts import SportsPushoverAlerts

D = Decimal
ONE = D("1")
QTY = D("1")
TARGET = D("0.03")
TAKER = D("0.07")
REFRESH_SECONDS = max(60, min(900, int(os.getenv("GEMINI_COMBO_FOK_CATALOG_REFRESH_SECONDS", "300"))))
HEARTBEAT_SECONDS = max(10, min(300, int(os.getenv("GEMINI_COMBO_FOK_HEARTBEAT_SECONDS", "60"))))


def _outcome_ask(top: Any, outcome: str) -> tuple[D, D] | None:
    if outcome.upper() == "YES":
        p, q = D(str(top.yes_ask)), D(str(top.yes_ask_size))
    else:
        bid, q = D(str(top.yes_bid)), D(str(top.yes_bid_size))
        p = ONE - bid
    if q < QTY or not D("0") < p < ONE:
        return None
    return p, q


def _evaluate(spec: Mapping[str, Any], tops: Mapping[str, Any], *, direction: str) -> dict[str, Any] | None:
    combo = str(spec["combo"])
    required = list(spec["active_required_legs"])
    combo_outcome = "YES" if direction == "COMBO_YES_PLUS_COMPLEMENTS" else "NO"
    component_specs = (
        [(symbol, _opposite(outcome)) for symbol, outcome in required]
        if combo_outcome == "YES"
        else required
    )
    combo_top = tops.get(combo)
    if combo_top is None:
        return None
    ct = _outcome_ask(combo_top, combo_outcome)
    if ct is None:
        return None
    cp, cd = ct
    rows = []
    all_in = cp + c._fee(TAKER, cp, QTY)
    ts_values = [int(combo_top.ts_ms)]
    for symbol, outcome in component_specs:
        top = tops.get(symbol)
        if top is None:
            return None
        priced = _outcome_ask(top, outcome)
        if priced is None:
            return None
        p, depth = priced
        fee = c._fee(TAKER, p, QTY)
        all_in += p + fee
        ts_values.append(int(top.ts_ms))
        rows.append({"symbol": symbol, "outcome": outcome, "ask": str(p), "depth": str(depth), "fee": str(fee)})
    locked = ONE - all_in
    return {
        "combo": combo,
        "name": str(spec.get("name") or ""),
        "direction": direction,
        "combo_outcome": combo_outcome,
        "combo_ask": str(cp),
        "combo_depth": str(cd),
        "combo_fee": str(c._fee(TAKER, cp, QTY)),
        "components": rows,
        "all_in": str(all_in),
        "locked": str(locked),
        "quote_skew_ms": max(ts_values) - min(ts_values),
        "economic_executable": locked >= TARGET,
    }


async def _notify(sink: SportsPushoverAlerts, signal: Mapping[str, Any]) -> None:
    if not sink.configured:
        return
    try:
        legs = ", ".join(f"{x['outcome']} {x['ask']}" for x in signal.get("components") or [])
        await sink.send(
            title=f"Gemini combo FOK edge +${D(str(signal['locked'])):.2f}",
            message=(
                f"READ-ONLY executable candidate\n{signal['name']}\n"
                f"{signal['direction']}\nCombo {signal['combo_outcome']} @ {signal['combo_ask']}\n"
                f"Hedges: {legs}\nAll-in ${D(str(signal['all_in'])):.2f} -> $1.00\n"
                f"Quote skew {signal['quote_skew_ms']}ms. No order was placed."
            )[:1024],
        )
    except Exception as exc:
        print("GEMINI_COMBO_FOK_WATCH_ALERT_ERROR " + json.dumps({"error": f"{type(exc).__name__}:{str(exc)[:180]}"}, separators=(",", ":")), flush=True)


async def _watch_session(http: httpx.AsyncClient) -> None:
    catalog, raw_specs = await _catalog_specs(http)
    specs = [x for x in raw_specs if not x.get("blocker")]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    all_symbols: set[str] = set()
    for spec in specs:
        symbols = {str(spec["combo"]), *(str(x[0]) for x in spec["active_required_legs"])}
        all_symbols.update(symbols)
        for symbol in symbols:
            by_symbol.setdefault(symbol, []).append(spec)

    start = time.monotonic()
    last_heartbeat = 0.0
    tops: dict[str, Any] = {}
    active_signals: dict[tuple[str, str], str] = {}
    sink = SportsPushoverAlerts()
    print("GEMINI_COMBO_FOK_WATCH_CATALOG " + json.dumps({
        "catalog": catalog,
        "valid_combo_count": len(specs),
        "symbol_count": len(all_symbols),
        "refresh_seconds": REFRESH_SECONDS,
        "order_entry": False,
    }, separators=(",", ":"), default=str), flush=True)
    if not all_symbols:
        await asyncio.sleep(min(REFRESH_SECONDS, 30))
        return

    async with websockets.connect(GEMINI_WS, open_timeout=8.0, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({
            "id": f"combo-fok-{int(time.time())}",
            "method": "SUBSCRIBE",
            "params": [f"{s}@bookTicker" for s in sorted(all_symbols)],
        }, separators=(",", ":")))
        while time.monotonic() - start < REFRESH_SECONDS:
            payload = None
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                payload = json.loads(raw)
            except asyncio.TimeoutError:
                pass
            if isinstance(payload, Mapping):
                if payload.get("status") is not None:
                    if int(payload.get("status")) >= 400:
                        raise RuntimeError(f"Gemini WS subscription error {payload}")
                elif payload.get("s"):
                    try:
                        top = parse_gemini_book_ticker(dict(payload), received_ms=int(time.time() * 1000))
                        symbol = str(top.market_id)
                        tops[symbol] = top
                        for spec in by_symbol.get(symbol, []):
                            for direction in ("COMBO_YES_PLUS_COMPLEMENTS", "COMBO_NO_PLUS_SELECTED"):
                                signal = _evaluate(spec, tops, direction=direction)
                                key = (str(spec["combo"]), direction)
                                if signal is None or not signal["economic_executable"]:
                                    active_signals.pop(key, None)
                                    continue
                                fingerprint = f"{signal['combo_ask']}|{signal['all_in']}|{signal['locked']}"
                                if active_signals.get(key) == fingerprint:
                                    continue
                                active_signals[key] = fingerprint
                                print("GEMINI_COMBO_FOK_WATCH_EXECUTABLE " + json.dumps({
                                    **signal,
                                    "target_locked": str(TARGET),
                                    "execution_atomicity": "SEPARATE_FOK_ORDERS_NOT_ATOMIC",
                                    "order_entry": False,
                                    "realized": False,
                                }, separators=(",", ":"), default=str), flush=True)
                                await _notify(sink, signal)
                    except Exception as exc:
                        print("GEMINI_COMBO_FOK_WATCH_PARSE_ERROR " + json.dumps({"error": f"{type(exc).__name__}:{str(exc)[:180]}"}, separators=(",", ":")), flush=True)

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                last_heartbeat = now
                print("GEMINI_COMBO_FOK_WATCH_HEARTBEAT " + json.dumps({
                    "valid_combo_count": len(specs),
                    "subscribed_symbols": len(all_symbols),
                    "symbols_with_snapshot": len(tops),
                    "active_executable_signals": len(active_signals),
                    "order_entry": False,
                }, separators=(",", ":")), flush=True)


async def supervisor() -> None:
    while True:
        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                await _watch_session(http)
        except Exception as exc:
            print("GEMINI_COMBO_FOK_WATCH_ERROR " + json.dumps({"error": f"{type(exc).__name__}:{str(exc)[:240]}", "order_entry": False}, separators=(",", ":")), flush=True)
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    asyncio.run(supervisor())
