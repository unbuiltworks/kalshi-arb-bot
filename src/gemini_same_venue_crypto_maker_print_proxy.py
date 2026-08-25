"""Zero-risk public-print proxy for same-Gemini maker adverse selection.

This is measurement only. It never places an order.

A maker-indicated ladder row says a resting maker quote would preserve a target
against the *current* sibling hedge. To avoid manufacturing fills with real q1
orders during the census, this proxy watches public Gemini trade prints. When a
print occurs at/through a would-be maker price on the aggressor side that could
have interacted with our hypothetical maker order, it records the sibling hedge
at +10/+25/+50/+100/+250ms from local print receipt.

This is explicitly NOT fill proof: queue position is unknown and a public print at
our price does not prove our order would have executed. The output is only a
fill-conditioned adverse-selection proxy.
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

from src import gemini_same_venue_crypto_ladder_scan as scan
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

D = Decimal
ONE = D("1")
HORIZONS_MS = (10, 25, 50, 100, 250)
REFRESH_SECONDS = max(20.0, min(180.0, float(os.getenv("GEMINI_MAKER_PROXY_REFRESH_S", "60"))))
MAX_ROWS = max(4, min(50, int(os.getenv("GEMINI_MAKER_PROXY_MAX_ROWS", "30"))))


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _emit(stage: str, **fields: Any) -> None:
    print(stage + " " + json.dumps(fields, separators=(",", ":"), default=str), flush=True)


async def _maker_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refresh a bounded representative maker-indicated set from existing scanner code."""
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        strict = [(event, scan.ladder_contracts(event)) for event in events]
        symbols = {scan._symbol(c) for _, contracts in strict for c in contracts}
        books, statuses = await scan._books(http, symbols)
    actionable, joins, telemetry = scan.score(events, books)
    # score() already ranks actionable and join rows by proximity/depth/expiry.
    rows = (actionable + joins)[:MAX_ROWS]
    for row in rows:
        row["edge_key"] = "|".join((
            str(row.get("event_ticker") or ""),
            str(row.get("maker_symbol") or ""),
            str(row.get("maker_outcome") or ""),
            str(row.get("hedge_symbol") or ""),
            str(row.get("hedge_outcome") or ""),
        ))
    return rows, {
        **telemetry,
        "books_requested": len(symbols),
        "books_ok": sum(1 for s in symbols if statuses.get(s) == 200),
        "maker_proxy_rows": len(rows),
        "maker_proxy_row_limit": MAX_ROWS,
    }


def _trade_price(payload: Mapping[str, Any]) -> D | None:
    # Gemini's prediction-market public stream currently follows compact market-data
    # fields. Be defensive so a schema change degrades measurement rather than
    # inventing a price.
    for key in ("p", "price"):
        if payload.get(key) is not None:
            return _d(payload.get(key))
    return None


def _trade_id(payload: Mapping[str, Any], received_ms: int) -> str:
    for key in ("t", "tid", "tradeId", "trade_id"):
        if payload.get(key) not in (None, ""):
            return str(payload.get(key))
    return f"local-{received_ms}"


def _could_hit_maker(row: Mapping[str, Any], *, yes_trade_price: D, buyer_is_maker: bool) -> bool:
    quote = _d(row.get("safe_maker_quote"))
    if quote is None:
        return False
    outcome = str(row.get("maker_outcome") or "").upper()
    if outcome == "YES":
        # Hypothetical maker is a YES bid. A public trade can proxy interaction only
        # when the buyer was maker and the YES trade price is at/below our bid.
        return buyer_is_maker and yes_trade_price <= quote
    if outcome == "NO":
        # BUY NO(q) is economically the YES offer at 1-q. It can proxy interaction
        # when the buyer was taker and trades YES at/above that equivalent offer.
        return (not buyer_is_maker) and yes_trade_price >= ONE - quote
    return False


def _hedge_top(row: Mapping[str, Any], top: Any) -> tuple[D, D] | None:
    outcome = str(row.get("hedge_outcome") or "").upper()
    if outcome == "YES":
        ask, depth = D(str(top.yes_ask)), D(str(top.yes_ask_size))
    elif outcome == "NO":
        ask, depth = ONE - D(str(top.yes_bid)), D(str(top.yes_bid_size))
    else:
        return None
    if not (D("0") < ask < ONE) or depth <= 0:
        return None
    return ask, depth


async def _measure_horizons(
    row: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    print_received_ms: int,
    yes_trade_price: D,
    tops: dict[str, Any],
) -> None:
    maker_quote = _d(row.get("safe_maker_quote"))
    if maker_quote is None:
        return
    trade_id = _trade_id(payload, print_received_ms)
    last_ms = 0
    for horizon_ms in HORIZONS_MS:
        await asyncio.sleep(max(0.0, (horizon_ms - last_ms) / 1000.0))
        last_ms = horizon_ms
        now_ms = int(time.time() * 1000)
        hedge_symbol = str(row.get("hedge_symbol") or "")
        top = tops.get(hedge_symbol)
        if top is None:
            _emit("GEMINI_MAKER_PRINT_PROXY", edge_key=row.get("edge_key"), trade_id=trade_id,
                  horizon_ms=horizon_ms, maker_symbol=row.get("maker_symbol"), maker_outcome=row.get("maker_outcome"),
                  maker_quote=str(maker_quote), yes_trade_price=str(yes_trade_price), hedge_symbol=hedge_symbol,
                  hedge_outcome=row.get("hedge_outcome"), hedge_available=False,
                  classification="MAKER_PRINT_PROXY_NOT_FILL_PROOF", order_entry=False, realized=False)
            continue
        state = _hedge_top(row, top)
        if state is None:
            _emit("GEMINI_MAKER_PRINT_PROXY", edge_key=row.get("edge_key"), trade_id=trade_id,
                  horizon_ms=horizon_ms, maker_symbol=row.get("maker_symbol"), maker_outcome=row.get("maker_outcome"),
                  maker_quote=str(maker_quote), yes_trade_price=str(yes_trade_price), hedge_symbol=hedge_symbol,
                  hedge_outcome=row.get("hedge_outcome"), hedge_available=False,
                  hedge_book_received_ms=int(top.ts_ms), hedge_book_age_ms=max(0, now_ms-int(top.ts_ms)),
                  classification="MAKER_PRINT_PROXY_NOT_FILL_PROOF", order_entry=False, realized=False)
            continue
        hedge_ask, hedge_depth = state
        post_print_edge = scan.locked_profit(maker_quote, hedge_ask)
        _emit("GEMINI_MAKER_PRINT_PROXY",
              edge_key=row.get("edge_key"), trade_id=trade_id, horizon_ms=horizon_ms,
              print_received_ms=print_received_ms,
              maker_symbol=row.get("maker_symbol"), maker_outcome=row.get("maker_outcome"),
              maker_action=row.get("maker_action"), maker_quote=str(maker_quote), yes_trade_price=str(yes_trade_price),
              hedge_symbol=hedge_symbol, hedge_outcome=row.get("hedge_outcome"),
              hedge_available=True, hedge_ask=str(hedge_ask), hedge_depth=str(hedge_depth),
              post_print_locked_profit_q1=str(post_print_edge),
              survives_1c=post_print_edge >= D("0.01"),
              survives_2c=post_print_edge >= D("0.02"),
              survives_3c=post_print_edge >= D("0.03"),
              hedge_book_received_ms=int(top.ts_ms), hedge_book_age_ms=max(0, now_ms-int(top.ts_ms)),
              hedge_book_updated_after_print=int(top.ts_ms) >= print_received_ms,
              classification="MAKER_PRINT_PROXY_NOT_FILL_PROOF",
              queue_position_known=False, order_entry=False, realized=False)


async def _window(rows: list[dict[str, Any]], seconds: float) -> dict[str, int]:
    maker_symbols = sorted({str(r.get("maker_symbol") or "") for r in rows if r.get("maker_symbol")})
    all_symbols = sorted({str(r.get(k) or "") for r in rows for k in ("maker_symbol", "hedge_symbol") if r.get(k)})
    by_maker: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_maker.setdefault(str(row.get("maker_symbol") or ""), []).append(row)
    if not maker_symbols or not all_symbols:
        await asyncio.sleep(seconds)
        return {"trades_seen": 0, "proxy_hits": 0, "book_updates": 0, "unsupported_trade_price": 0}

    params = [f"{s}@bookTicker" for s in all_symbols] + [f"{s}@trade" for s in maker_symbols]
    tops: dict[str, Any] = {}
    stats = {"trades_seen": 0, "proxy_hits": 0, "book_updates": 0, "unsupported_trade_price": 0}
    tasks: set[asyncio.Task] = set()
    async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
        await ws.send(json.dumps({"id": "gem-maker-print-proxy", "method": "SUBSCRIBE", "params": params}, separators=(",", ":")))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            timeout = min(1.0, max(0.01, deadline-time.monotonic()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            payload = json.loads(raw)
            if payload.get("status") is not None:
                if int(payload.get("status", 0)) >= 400:
                    raise RuntimeError(f"Gemini maker proxy subscription failed: {payload}")
                continue
            symbol = str(payload.get("s") or "")
            if not symbol:
                continue
            # bookTicker has best bid/ask fields; trade frames have q/m and price.
            if payload.get("b") is not None or payload.get("a") is not None:
                try:
                    tops[symbol] = parse_gemini_book_ticker(payload, received_ms=int(time.time()*1000))
                    stats["book_updates"] += 1
                except Exception:
                    pass
                continue
            if payload.get("q") is None or payload.get("m") is None:
                continue
            stats["trades_seen"] += 1
            price = _trade_price(payload)
            if price is None:
                stats["unsupported_trade_price"] += 1
                continue
            received_ms = int(time.time() * 1000)
            buyer_is_maker = bool(payload.get("m"))
            for row in by_maker.get(symbol, []):
                if not _could_hit_maker(row, yes_trade_price=price, buyer_is_maker=buyer_is_maker):
                    continue
                stats["proxy_hits"] += 1
                task = asyncio.create_task(_measure_horizons(row, payload=payload, print_received_ms=received_ms,
                                                             yes_trade_price=price, tops=tops))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return stats


async def run_forever() -> None:
    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            rows, diag = await _maker_rows()
            refresh_elapsed = time.monotonic() - started
            window_s = max(5.0, REFRESH_SECONDS-refresh_elapsed)
            stats = await _window(rows, window_s)
            _emit("GEMINI_MAKER_PRINT_PROXY_HEARTBEAT", cycle=cycle, **diag, **stats,
                  window_seconds=round(window_s, 3), horizons_ms=list(HORIZONS_MS),
                  classification="MAKER_PRINT_PROXY_NOT_FILL_PROOF", read_only=True, order_entry=False, realized=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _emit("GEMINI_MAKER_PRINT_PROXY_ERROR", cycle=cycle, error_type=type(exc).__name__, error=str(exc)[:240],
                  classification="MAKER_PRINT_PROXY_NOT_FILL_PROOF", read_only=True, order_entry=False)
            await asyncio.sleep(5.0)


if __name__ == "__main__":
    asyncio.run(run_forever())
