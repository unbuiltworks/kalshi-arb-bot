"""Read-only trade-through fill shadow for same-event nested maker quotes.

This service never places orders. It takes the aggressive inside-spread maker
quotes produced by ``kalshi_nested_maker_aggressive_shadow`` and asks whether a
real public trade later PRINTS THROUGH the hypothetical maker price. Because the
shadow quote initially improves the displayed best bid at a previously empty
price, an opposite-side trade through that price is strong evidence the resting
order would have been marketable to the incoming taker. It is still labelled a
shadow/upper-bound fill, not an actual fill.

At each trade-through, the current complementary hedge is read from an in-memory
Kalshi orderbook reconstructed from snapshot + deltas. We record the guaranteed
same-event payoff after the mapped taker fee at 0ms, then again after 25ms and
100ms to measure hedge survival/adverse selection.

No order-manager or order-entry module is imported.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import inspect
import json
import logging
import time
from typing import Any

import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.kalshi_nested_maker_aggressive_shadow import scan_aggressive_series
from src.kalshi_nested_maker_shadow import D, maker_then_taker_profit

logger = logging.getLogger("kalshi_arb.kalshi_nested_trade_through_shadow")
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_WS_SIGN_PATH = "/trade-api/ws/v2"


@dataclass(frozen=True)
class ShadowQuote:
    series: str
    event_ticker: str
    maker_ticker: str
    maker_side: str
    maker_price: Decimal
    maker_best_bid_at_post: Decimal
    maker_best_ask_at_post: Decimal
    hedge_ticker: str
    hedge_side: str
    target_profit: Decimal
    fee_multiplier: Decimal


def _ws_header_kw(headers: dict[str, str]) -> dict[str, Any]:
    params = inspect.signature(websockets.connect).parameters
    key = "additional_headers" if "additional_headers" in params else "extra_headers"
    return {key: headers}


def _opposite(side: str) -> str:
    if side == "yes":
        return "no"
    if side == "no":
        return "yes"
    raise ValueError(f"invalid side {side!r}")


def trade_throughs_shadow(*, maker_side: str, maker_price: Decimal, trade_msg: dict[str, Any]) -> bool:
    """Whether an opposite taker printed at/through our hypothetical maker bid.

    Kalshi public trades report the taker's directional outcome. A resting YES bid
    is consumed by a NO taker; a resting NO bid is consumed by a YES taker. On the
    maker outcome's own price scale, a print at or below our bid means the incoming
    order reached our price or walked through it.
    """
    if maker_side not in {"yes", "no"} or maker_price < 0 or maker_price > 1:
        raise ValueError("invalid maker side/price")
    taker = str(
        trade_msg.get("taker_outcome_side")
        or trade_msg.get("taker_side")
        or ""
    ).lower()
    if taker != _opposite(maker_side):
        return False
    raw = trade_msg.get(f"{maker_side}_price_dollars")
    if raw is None:
        return False
    trade_price = D(str(raw))
    return trade_price <= maker_price


def hedge_top(book: KalshiOrderBook, side: str) -> tuple[Decimal, Decimal] | None:
    """Executable buy ask + size for YES or NO from Kalshi's bid-only book."""
    if side == "yes":
        if not book.no:
            return None
        no_bid = max(book.no)
        return D("1") - no_bid, book.no[no_bid]
    if side == "no":
        if not book.yes:
            return None
        yes_bid = max(book.yes)
        return D("1") - yes_bid, book.yes[yes_bid]
    raise ValueError(f"invalid hedge side {side!r}")


async def _build_quotes(
    *,
    series: list[str],
    target_profit: Decimal,
    top_n_per_series: int,
    event_suffix: str | None,
    max_quotes: int,
) -> list[ShadowQuote]:
    out: list[ShadowQuote] = []
    for ticker in series:
        summary = await scan_aggressive_series(
            ticker,
            target_profit=target_profit,
            top_n=max(top_n_per_series, max_quotes),
        )
        fee_meta = await _fee_meta(ticker)
        multiplier = D(fee_meta.fee_multiplier)
        rows = list(summary.get("top_quotes") or [])
        if event_suffix:
            rows = [r for r in rows if str(r.get("event_ticker") or "").endswith(event_suffix)]
        # One hypothetical resting quote per maker market. If several nested hedges
        # share it, keep the cheapest hedge observed at post time.
        best: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row["maker_ticker"]),
                str(row["maker_side"]),
                str(row["shadow_quote"]),
            )
            prev = best.get(key)
            if prev is None or D(str(row["hedge_top_ask"])) < D(str(prev["hedge_top_ask"])):
                best[key] = row
        for row in best.values():
            out.append(ShadowQuote(
                series=ticker,
                event_ticker=str(row["event_ticker"]),
                maker_ticker=str(row["maker_ticker"]),
                maker_side=str(row["maker_side"]),
                maker_price=D(str(row["shadow_quote"])),
                maker_best_bid_at_post=D(str(row["current_best_bid"])),
                maker_best_ask_at_post=D(str(row["current_best_ask"])),
                hedge_ticker=str(row["hedge_ticker"]),
                hedge_side=str(row["hedge_side"]),
                target_profit=target_profit,
                fee_multiplier=multiplier,
            ))
    # Prefer quotes nearest the opposing ask, then larger initial bid improvement.
    out.sort(key=lambda q: (
        q.maker_best_ask_at_post - q.maker_price,
        -(q.maker_price - q.maker_best_bid_at_post),
    ))
    return out[:max_quotes]


async def _fee_meta(series_ticker: str):
    import httpx
    async with httpx.AsyncClient(timeout=20.0) as http:
        return await fetch_series_fee_metadata(http, series_ticker)


def _snapshot(
    *,
    q: ShadowQuote,
    books: dict[str, KalshiOrderBook],
    delay_ms: int,
    trade_id: str,
    trade_ts_ms: int | None,
    receive_ts_ms: int,
) -> dict[str, Any]:
    book = books.get(q.hedge_ticker)
    top = hedge_top(book, q.hedge_side) if book is not None else None
    if top is None:
        return {
            "trade_id": trade_id,
            "delay_ms": delay_ms,
            "hedge_available": False,
            "target_survives": False,
            "read_only": True,
        }
    hedge_price, hedge_size = top
    profit, taker_fee = maker_then_taker_profit(
        maker_price=q.maker_price,
        hedge_price=hedge_price,
        fee_multiplier=q.fee_multiplier,
    )
    return {
        "trade_id": trade_id,
        "delay_ms": delay_ms,
        "trade_ts_ms": trade_ts_ms,
        "receive_ts_ms": receive_ts_ms,
        "hedge_available": hedge_size >= 1,
        "hedge_price": str(hedge_price),
        "hedge_size": str(hedge_size),
        "taker_fee": str(taker_fee),
        "locked_profit": str(profit),
        "target_profit": str(q.target_profit),
        "target_survives": hedge_size >= 1 and profit >= q.target_profit,
        "read_only": True,
    }


async def _delayed_snapshot(
    *,
    delay_ms: int,
    q: ShadowQuote,
    books: dict[str, KalshiOrderBook],
    trade_id: str,
    trade_ts_ms: int | None,
    receive_ts_ms: int,
) -> None:
    await asyncio.sleep(delay_ms / 1000)
    row = _snapshot(
        q=q,
        books=books,
        delay_ms=delay_ms,
        trade_id=trade_id,
        trade_ts_ms=trade_ts_ms,
        receive_ts_ms=receive_ts_ms,
    )
    logger.info("NESTED_TRADE_THROUGH_HEDGE %s", json.dumps(row, separators=(",", ":")))


async def watch(
    *,
    seconds: float,
    series: list[str],
    target_profit: Decimal,
    event_suffix: str | None,
    max_quotes: int,
) -> None:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    private_key = load_private_key()

    quotes = await _build_quotes(
        series=series,
        target_profit=target_profit,
        top_n_per_series=100,
        event_suffix=event_suffix,
        max_quotes=max_quotes,
    )
    if not quotes:
        logger.info("NESTED_TRADE_THROUGH_START %s", json.dumps({
            "quotes": 0,
            "event_suffix": event_suffix,
            "target_profit": str(target_profit),
            "reason": "NO_AGGRESSIVE_QUOTES",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        return

    market_tickers = sorted({x for q in quotes for x in (q.maker_ticker, q.hedge_ticker)})
    by_maker: dict[str, list[ShadowQuote]] = {}
    for q in quotes:
        by_maker.setdefault(q.maker_ticker, []).append(q)
    books = {ticker: KalshiOrderBook(ticker) for ticker in market_tickers}

    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    logger.info("NESTED_TRADE_THROUGH_START %s", json.dumps({
        "quotes": len(quotes),
        "markets": len(market_tickers),
        "event_suffix": event_suffix,
        "target_profit": str(target_profit),
        "seconds": seconds,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    for q in quotes:
        logger.info("NESTED_TRADE_THROUGH_POST %s", json.dumps({
            "series": q.series,
            "event_ticker": q.event_ticker,
            "maker_ticker": q.maker_ticker,
            "maker_side": q.maker_side,
            "shadow_quote": str(q.maker_price),
            "best_bid_at_post": str(q.maker_best_bid_at_post),
            "best_ask_at_post": str(q.maker_best_ask_at_post),
            "hedge_ticker": q.hedge_ticker,
            "hedge_side": q.hedge_side,
            "target_profit": str(q.target_profit),
            "classification": "SHADOW_POST_ONLY",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    deadline = time.monotonic() + seconds
    trade_throughs = 0
    target_survived_at_receive = 0
    delayed_tasks: set[asyncio.Task] = set()

    async with websockets.connect(
        KALSHI_WS,
        open_timeout=5.0,
        **_ws_header_kw(headers),
    ) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "trade"],
                "market_tickers": market_tickers,
            },
        }))
        while time.monotonic() < deadline:
            timeout = min(1.0, max(0.01, deadline - time.monotonic()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            receive_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("type") == "error":
                raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
            msg_type = payload.get("type")
            if msg_type in {"orderbook_snapshot", "orderbook_delta"}:
                ticker = str((payload.get("msg") or {}).get("market_ticker") or "")
                book = books.get(ticker)
                if book is not None:
                    book.apply(payload)
                continue
            if msg_type != "trade":
                continue
            msg = payload.get("msg") or {}
            ticker = str(msg.get("market_ticker") or msg.get("ticker") or "")
            if ticker not in by_maker:
                continue
            trade_id = str(msg.get("trade_id") or "")
            trade_ts_ms = int(msg["ts_ms"]) if msg.get("ts_ms") is not None else None
            for q in by_maker[ticker]:
                if not trade_throughs_shadow(
                    maker_side=q.maker_side,
                    maker_price=q.maker_price,
                    trade_msg=msg,
                ):
                    continue
                trade_throughs += 1
                snap = _snapshot(
                    q=q,
                    books=books,
                    delay_ms=0,
                    trade_id=trade_id,
                    trade_ts_ms=trade_ts_ms,
                    receive_ts_ms=receive_ms,
                )
                if snap.get("target_survives"):
                    target_survived_at_receive += 1
                event = {
                    "series": q.series,
                    "event_ticker": q.event_ticker,
                    "maker_ticker": q.maker_ticker,
                    "maker_side": q.maker_side,
                    "shadow_quote": str(q.maker_price),
                    "trade_yes_price": msg.get("yes_price_dollars"),
                    "trade_no_price": msg.get("no_price_dollars"),
                    "taker_outcome_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
                    "trade_count_fp": msg.get("count_fp"),
                    "trade_ts_ms": trade_ts_ms,
                    "receive_ts_ms": receive_ms,
                    "exchange_to_receive_ms": (receive_ms - trade_ts_ms) if trade_ts_ms is not None else None,
                    "hedge_ticker": q.hedge_ticker,
                    "hedge_side": q.hedge_side,
                    "at_receive": snap,
                    "classification": "TRADE_THROUGH_SHADOW_FILL_UPPER_BOUND",
                    "actual_order_placed": False,
                    "read_only": True,
                    "order_entry": False,
                }
                logger.info("NESTED_TRADE_THROUGH %s", json.dumps(event, separators=(",", ":"), default=str))
                for delay in (25, 100):
                    task = asyncio.create_task(_delayed_snapshot(
                        delay_ms=delay,
                        q=q,
                        books=books,
                        trade_id=trade_id,
                        trade_ts_ms=trade_ts_ms,
                        receive_ts_ms=receive_ms,
                    ))
                    delayed_tasks.add(task)
                    task.add_done_callback(delayed_tasks.discard)

    if delayed_tasks:
        await asyncio.gather(*delayed_tasks, return_exceptions=True)
    logger.info("NESTED_TRADE_THROUGH_SUMMARY %s", json.dumps({
        "quotes": len(quotes),
        "markets": len(market_tickers),
        "trade_throughs": trade_throughs,
        "target_survived_at_receive": target_survived_at_receive,
        "target_survival_rate_at_receive": (
            target_survived_at_receive / trade_throughs if trade_throughs else None
        ),
        "target_profit": str(target_profit),
        "event_suffix": event_suffix,
        "classification": "SHADOW_ONLY",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--series", nargs="+", default=["KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--event-suffix", default=None)
    parser.add_argument("--max-quotes", type=int, default=30)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(watch(
        seconds=args.seconds,
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        event_suffix=args.event_suffix,
        max_quotes=args.max_quotes,
    ))


if __name__ == "__main__":
    main()
