"""Rolling read-only fill/adverse-selection shadow for nested Kalshi maker arbs.

This is deliberately stricter than the one-shot trade-through probe:
- automatically follows the nearest near-term ETH/SOL daily event;
- waits for BOTH maker and hedge orderbook snapshots before a shadow quote exists;
- recalculates on every relevant orderbook update;
- cancels/reprices whenever hedge economics or queue priority no longer preserve
  the requested guaranteed same-event profit;
- models a configurable maker-order activation/ack latency before a shadow quote
  can be fill-eligible;
- counts a plausible fill only when an opposite-side >=1 contract trade prints
  at/through an ACTIVE, queue-unambiguous shadow maker quote;
- records the pre-trade hedge plus 25ms/100ms post-trade hedge survival.

No order-manager or order-entry module is imported. No orders are placed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import logging
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_maker_aggressive_shadow import (
    most_aggressive_resting_price,
    scan_aggressive_series,
)
from src.kalshi_nested_maker_shadow import (
    D,
    maker_then_taker_profit,
    max_maker_price_for_target,
)
from src.kalshi_nested_trade_through_shadow import (
    KALSHI_WS,
    KALSHI_WS_SIGN_PATH,
    _ws_header_kw,
    hedge_top,
    trade_throughs_shadow,
)

logger = logging.getLogger("kalshi_arb.kalshi_nested_rolling_shadow")
NY = ZoneInfo("America/New_York")
MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
EVENT_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})$")


@dataclass(frozen=True)
class Candidate:
    series: str
    event_ticker: str
    event_close: datetime
    maker_ticker: str
    maker_side: str
    hedge_ticker: str
    hedge_side: str
    price_ranges: tuple[dict[str, Any], ...]
    fee_multiplier: Decimal
    target_profit: Decimal


@dataclass
class QuoteState:
    candidate: Candidate
    active_price: Decimal | None = None
    posted_monotonic: float | None = None
    fill_eligible_monotonic: float | None = None
    post_count: int = 0
    cancel_count: int = 0
    plausible_fill_count: int = 0
    probe_eligible_fill_count: int = 0
    ignored_small_trade_count: int = 0
    ignored_activation_trade_count: int = 0
    ignored_queue_trade_count: int = 0
    cooldown_until: float = 0.0
    last_reason: str = "NOT_READY"


def parse_event_close(event_ticker: str) -> datetime | None:
    """Parse Kalshi daily event suffix YYMONDDHH as America/New_York close time."""
    match = EVENT_RE.search(event_ticker.upper())
    if not match:
        return None
    yy, mon, day, hour = match.groups()
    month = MONTHS.get(mon)
    if month is None:
        return None
    try:
        return datetime(2000 + int(yy), month, int(day), int(hour), tzinfo=NY)
    except ValueError:
        return None


def nearest_future_event(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    max_time_to_close_seconds: float | None = None,
) -> str | None:
    """Choose the nearest future event, optionally rejecting long capital lockups."""
    now = now or datetime.now(NY)
    latest = None
    if max_time_to_close_seconds is not None:
        latest = now + timedelta(seconds=max_time_to_close_seconds)

    choices: dict[str, datetime] = {}
    for row in rows:
        ticker = str(row.get("event_ticker") or "")
        close = parse_event_close(ticker)
        if close is None or close <= now:
            continue
        if latest is not None and close > latest:
            continue
        choices[ticker] = close
    if not choices:
        return None
    return min(choices, key=lambda ticker: choices[ticker])


def _side_bid(book: KalshiOrderBook, side: str) -> Decimal:
    levels = book.yes if side == "yes" else book.no
    return max(levels) if levels else D("0")


def _side_ask_for_maker(book: KalshiOrderBook, side: str) -> Decimal:
    """Resting-side ask ceiling. Missing opposite bids means no displayed ask < $1."""
    opposite = book.no if side == "yes" else book.yes
    return D("1") - max(opposite) if opposite else D("1")


def _trade_size_fp(trade_msg: dict[str, Any]) -> Decimal:
    raw = trade_msg.get("count_fp")
    if raw is None:
        raw = trade_msg.get("count")
    try:
        return D(str(raw or "0"))
    except (InvalidOperation, ValueError):
        return D("0")


def trade_fill_gate(
    *,
    state: QuoteState,
    maker_book: KalshiOrderBook,
    trade_msg: dict[str, Any],
    now_mono: float,
    min_trade_size: Decimal = D("1"),
) -> tuple[bool, str]:
    """Conservative shadow-fill gate for one-lot maker-fill evidence."""
    active = state.active_price
    if active is None:
        return False, "NO_ACTIVE_QUOTE"

    eligible_at = state.fill_eligible_monotonic
    if eligible_at is None or now_mono < eligible_at:
        return False, "ACTIVATION_LATENCY"

    trade_size = _trade_size_fp(trade_msg)
    if trade_size < min_trade_size:
        return False, "TRADE_SIZE_LT_1"

    external_bid = _side_bid(maker_book, state.candidate.maker_side)
    if external_bid >= active:
        return False, "QUEUE_PRIORITY_AMBIGUOUS"

    if not trade_throughs_shadow(
        maker_side=state.candidate.maker_side,
        maker_price=active,
        trade_msg=trade_msg,
    ):
        return False, "NO_TRADE_THROUGH"

    return True, "PLAUSIBLE_FILL_TRADE_THROUGH"


def desired_shadow_quote(
    *,
    candidate: Candidate,
    maker_book: KalshiOrderBook,
    hedge_book: KalshiOrderBook,
) -> tuple[Decimal | None, dict[str, Any]]:
    """Current valid maker quote and economics, or None with a fail-closed reason."""
    hedge = hedge_top(hedge_book, candidate.hedge_side)
    if hedge is None:
        return None, {"reason": "NO_HEDGE_ASK"}
    hedge_price, hedge_size = hedge
    if hedge_size < 1:
        return None, {"reason": "HEDGE_DEPTH_LT_1", "hedge_size": str(hedge_size)}

    maker_bid = _side_bid(maker_book, candidate.maker_side)
    maker_ask = _side_ask_for_maker(maker_book, candidate.maker_side)
    if maker_bid >= maker_ask:
        return None, {
            "reason": "MAKER_BOOK_CROSSED",
            "maker_bid": str(maker_bid),
            "maker_ask": str(maker_ask),
        }

    max_price = max_maker_price_for_target(
        hedge_price=hedge_price,
        fee_multiplier=candidate.fee_multiplier,
        target_profit=candidate.target_profit,
    )
    quote = most_aggressive_resting_price(
        best_bid=maker_bid,
        best_ask=maker_ask,
        max_price=max_price,
        price_ranges=list(candidate.price_ranges),
    )
    if quote is None:
        return None, {
            "reason": "NO_VALID_INSIDE_QUOTE_AT_TARGET",
            "maker_bid": str(maker_bid),
            "maker_ask": str(maker_ask),
            "hedge_price": str(hedge_price),
            "hedge_size": str(hedge_size),
            "max_maker_price": str(max_price),
        }
    profit, fee = maker_then_taker_profit(
        maker_price=quote,
        hedge_price=hedge_price,
        fee_multiplier=candidate.fee_multiplier,
    )
    if profit < candidate.target_profit:
        return None, {"reason": "TARGET_MATH_FAIL_CLOSED", "profit": str(profit)}
    return quote, {
        "reason": "READY",
        "maker_bid": str(maker_bid),
        "maker_ask": str(maker_ask),
        "hedge_price": str(hedge_price),
        "hedge_size": str(hedge_size),
        "taker_fee": str(fee),
        "locked_profit": str(profit),
        "max_maker_price": str(max_price),
    }


async def build_candidates(
    *,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    max_time_to_close_seconds: float,
) -> list[Candidate]:
    out: list[Candidate] = []
    now = datetime.now(NY)
    for series_ticker in series:
        summary = await scan_aggressive_series(
            series_ticker,
            target_profit=target_profit,
            top_n=1000,
        )
        rows = list(summary.get("top_quotes") or [])
        all_future_event = nearest_future_event(rows, now=now)
        event_ticker = nearest_future_event(
            rows,
            now=now,
            max_time_to_close_seconds=max_time_to_close_seconds,
        )
        if not event_ticker:
            if all_future_event:
                close = parse_event_close(all_future_event)
                logger.info(
                    "NESTED_ROLLING_NO_NEAR_TERM_EVENT series=%s nearest_candidate_event=%s "
                    "nearest_candidate_close=%s max_time_to_close_seconds=%s",
                    series_ticker,
                    all_future_event,
                    close.isoformat() if close else None,
                    max_time_to_close_seconds,
                )
            else:
                logger.info(
                    "NESTED_ROLLING_NO_NEAR_TERM_QUOTES series=%s "
                    "max_time_to_close_seconds=%s",
                    series_ticker,
                    max_time_to_close_seconds,
                )
            continue

        close = parse_event_close(event_ticker)
        assert close is not None
        multiplier_raw = (summary.get("fee_metadata") or {}).get("fee_multiplier")
        if multiplier_raw is None:
            logger.info(
                "NESTED_ROLLING_FEE_FAIL_CLOSED series=%s event=%s reason=MISSING_FEE_MULTIPLIER",
                series_ticker,
                event_ticker,
            )
            continue
        multiplier = D(str(multiplier_raw))
        event_rows = [
            r for r in rows if str(r.get("event_ticker") or "") == event_ticker
        ]

        best: dict[tuple[str, str], dict[str, Any]] = {}
        for row in event_rows:
            key = (str(row["maker_ticker"]), str(row["maker_side"]))
            prev = best.get(key)
            if prev is None:
                best[key] = row
                continue
            current_key = (
                D(str(row["shadow_quote"])),
                -D(str(row["hedge_top_ask"])),
            )
            prev_key = (
                D(str(prev["shadow_quote"])),
                -D(str(prev["hedge_top_ask"])),
            )
            if current_key > prev_key:
                best[key] = row

        rows_best = list(best.values())
        rows_best.sort(key=lambda r: (
            D(str(r["distance_below_ask"])),
            -D(str(r["guaranteed_profit_if_shadow_quote_fills_and_hedge_still_available"])),
        ))
        per_series_cap = max(1, max_candidates // max(1, len(series)))
        for row in rows_best[:per_series_cap]:
            out.append(Candidate(
                series=series_ticker,
                event_ticker=event_ticker,
                event_close=close,
                maker_ticker=str(row["maker_ticker"]),
                maker_side=str(row["maker_side"]),
                hedge_ticker=str(row["hedge_ticker"]),
                hedge_side=str(row["hedge_side"]),
                price_ranges=tuple(row.get("price_ranges") or []),
                fee_multiplier=multiplier,
                target_profit=target_profit,
            ))
    return out[:max_candidates]


def _log_transition(
    kind: str,
    state: QuoteState,
    *,
    price: Decimal | None,
    economics: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    c = state.candidate
    payload = {
        "series": c.series,
        "event_ticker": c.event_ticker,
        "event_close": c.event_close.isoformat(),
        "maker_ticker": c.maker_ticker,
        "maker_side": c.maker_side,
        "shadow_quote": str(price) if price is not None else None,
        "hedge_ticker": c.hedge_ticker,
        "hedge_side": c.hedge_side,
        "target_profit": str(c.target_profit),
        "economics": economics,
        "post_count": state.post_count,
        "cancel_count": state.cancel_count,
        "plausible_fill_count": state.plausible_fill_count,
        "probe_eligible_fill_count": state.probe_eligible_fill_count,
        "read_only": True,
        "order_entry": False,
    }
    if extra:
        payload.update(extra)
    logger.info(
        "NESTED_ROLLING_%s %s",
        kind,
        json.dumps(payload, separators=(",", ":"), default=str),
    )


def _cancel_state(
    state: QuoteState,
    *,
    reason: str,
    economics: dict[str, Any] | None = None,
) -> None:
    if state.active_price is None:
        return
    old = state.active_price
    state.active_price = None
    state.posted_monotonic = None
    state.fill_eligible_monotonic = None
    state.cancel_count += 1
    payload = {"reason": reason}
    if economics:
        payload.update(economics)
    _log_transition("CANCEL", state, price=old, economics=payload)


def _reprice_state(
    *,
    state: QuoteState,
    books: dict[str, KalshiOrderBook],
    snapshots: set[str],
    now_mono: float,
    activation_latency_ms: float,
) -> None:
    c = state.candidate
    if c.maker_ticker not in snapshots or c.hedge_ticker not in snapshots:
        return

    maker_book = books[c.maker_ticker]
    external_bid = _side_bid(maker_book, c.maker_side)
    if state.active_price is not None and external_bid >= state.active_price:
        old = state.active_price
        _cancel_state(
            state,
            reason="QUEUE_PRIORITY_LOST",
            economics={
                "external_maker_bid": str(external_bid),
                "old_shadow_quote": str(old),
            },
        )

    quote, econ = desired_shadow_quote(
        candidate=c,
        maker_book=maker_book,
        hedge_book=books[c.hedge_ticker],
    )
    state.last_reason = str(econ.get("reason") or "UNKNOWN")

    if now_mono < state.cooldown_until:
        _cancel_state(state, reason="POST_FILL_COOLDOWN")
        return

    if quote is None:
        _cancel_state(
            state,
            reason=str(econ.get("reason") or "NOT_READY"),
            economics=econ,
        )
        return

    if state.active_price == quote:
        return

    if state.active_price is not None:
        _cancel_state(
            state,
            reason="REPRICE",
            economics={"next_price": str(quote), **econ},
        )

    state.active_price = quote
    state.posted_monotonic = now_mono
    state.fill_eligible_monotonic = now_mono + (activation_latency_ms / 1000.0)
    state.post_count += 1
    _log_transition(
        "POST",
        state,
        price=quote,
        economics=econ,
        extra={
            "zero_queue_ahead_at_price_when_posted": True,
            "activation_latency_ms": activation_latency_ms,
        },
    )


def _hedge_snapshot(
    c: Candidate,
    books: dict[str, KalshiOrderBook],
    *,
    label: str,
    trade_id: str,
) -> dict[str, Any]:
    top = hedge_top(books[c.hedge_ticker], c.hedge_side)
    if top is None:
        return {
            "label": label,
            "trade_id": trade_id,
            "hedge_available": False,
            "target_survives": False,
        }
    price, size = top
    return {
        "label": label,
        "trade_id": trade_id,
        "hedge_available": size >= 1,
        "hedge_price": str(price),
        "hedge_size": str(size),
    }


def _post_fill_snapshot(
    *,
    c: Candidate,
    maker_fill_price: Decimal,
    books: dict[str, KalshiOrderBook],
    label: str,
    trade_id: str,
) -> dict[str, Any]:
    row = _hedge_snapshot(c, books, label=label, trade_id=trade_id)
    if not row.get("hedge_available"):
        row["target_survives"] = False
        return row
    hedge_price = D(str(row["hedge_price"]))
    profit, fee = maker_then_taker_profit(
        maker_price=maker_fill_price,
        hedge_price=hedge_price,
        fee_multiplier=c.fee_multiplier,
    )
    row.update({
        "maker_fill_price": str(maker_fill_price),
        "taker_fee": str(fee),
        "locked_profit": str(profit),
        "target_profit": str(c.target_profit),
        "target_survives": profit >= c.target_profit,
    })
    return row


async def _fill_followup(
    *,
    state: QuoteState,
    maker_fill_price: Decimal,
    books: dict[str, KalshiOrderBook],
    trade_id: str,
) -> None:
    c = state.candidate
    await asyncio.sleep(0.025)
    plus_25 = _post_fill_snapshot(
        c=c,
        maker_fill_price=maker_fill_price,
        books=books,
        label="PLUS_25MS",
        trade_id=trade_id,
    )
    logger.info(
        "NESTED_ROLLING_HEDGE_AFTER %s",
        json.dumps(plus_25, separators=(",", ":"), default=str),
    )

    await asyncio.sleep(0.075)
    plus_100 = _post_fill_snapshot(
        c=c,
        maker_fill_price=maker_fill_price,
        books=books,
        label="PLUS_100MS",
        trade_id=trade_id,
    )
    logger.info(
        "NESTED_ROLLING_HEDGE_AFTER %s",
        json.dumps(plus_100, separators=(",", ":"), default=str),
    )

    eligible = bool(plus_25.get("target_survives") and plus_100.get("target_survives"))
    if eligible:
        state.probe_eligible_fill_count += 1
    logger.info(
        "NESTED_ROLLING_FILL_FOLLOWUP %s",
        json.dumps({
            "trade_id": trade_id,
            "series": c.series,
            "event_ticker": c.event_ticker,
            "maker_ticker": c.maker_ticker,
            "maker_side": c.maker_side,
            "maker_fill_price": str(maker_fill_price),
            "plus_25ms": plus_25,
            "plus_100ms": plus_100,
            "live_probe_eligible_shadow_fill": eligible,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )


async def watch_cycle(
    *,
    cycle_seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    max_time_to_close_seconds: float,
    activation_latency_ms: float,
) -> dict[str, Any]:
    candidates = await build_candidates(
        series=series,
        target_profit=target_profit,
        max_candidates=max_candidates,
        max_time_to_close_seconds=max_time_to_close_seconds,
    )
    if not candidates:
        logger.info(
            "NESTED_ROLLING_CYCLE %s",
            json.dumps({
                "candidates": 0,
                "reason": "NO_NEAR_TERM_EVENT_QUOTES",
                "max_time_to_close_seconds": max_time_to_close_seconds,
                "read_only": True,
            }, separators=(",", ":")),
        )
        await asyncio.sleep(min(cycle_seconds, 30.0))
        return {"candidates": 0, "fills": 0, "probe_eligible_fills": 0}

    market_tickers = sorted({x for c in candidates for x in (c.maker_ticker, c.hedge_ticker)})
    books = {ticker: KalshiOrderBook(ticker) for ticker in market_tickers}
    snapshots: set[str] = set()
    states = [QuoteState(candidate=c) for c in candidates]
    by_market: dict[str, list[QuoteState]] = {}
    by_maker: dict[str, list[QuoteState]] = {}
    for state in states:
        c = state.candidate
        by_market.setdefault(c.maker_ticker, []).append(state)
        by_market.setdefault(c.hedge_ticker, []).append(state)
        by_maker.setdefault(c.maker_ticker, []).append(state)

    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    logger.info(
        "NESTED_ROLLING_CYCLE %s",
        json.dumps({
            "candidates": len(candidates),
            "markets": len(market_tickers),
            "events": sorted({c.event_ticker for c in candidates}),
            "nearest_closes": sorted({c.event_close.isoformat() for c in candidates}),
            "target_profit": str(target_profit),
            "cycle_seconds": cycle_seconds,
            "max_time_to_close_seconds": max_time_to_close_seconds,
            "activation_latency_ms": activation_latency_ms,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    deadline = time.monotonic() + cycle_seconds
    fills = 0
    survived_pre_trade = 0
    delayed: set[asyncio.Task] = set()
    async with websockets.connect(KALSHI_WS, open_timeout=5.0, **_ws_header_kw(headers)) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta", "trade"], "market_tickers": market_tickers},
        }))
        while time.monotonic() < deadline:
            timeout = min(0.25, max(0.01, deadline - time.monotonic()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                now_mono = time.monotonic()
                for state in states:
                    _reprice_state(
                        state=state,
                        books=books,
                        snapshots=snapshots,
                        now_mono=now_mono,
                        activation_latency_ms=activation_latency_ms,
                    )
                continue

            receive_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("type") == "error":
                raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
            msg_type = payload.get("type")
            msg = payload.get("msg") or {}
            if msg_type in {"orderbook_snapshot", "orderbook_delta"}:
                ticker = str(msg.get("market_ticker") or "")
                book = books.get(ticker)
                if book is None:
                    continue
                book.apply(payload)
                if msg_type == "orderbook_snapshot":
                    snapshots.add(ticker)
                now_mono = time.monotonic()
                for state in by_market.get(ticker, []):
                    _reprice_state(
                        state=state,
                        books=books,
                        snapshots=snapshots,
                        now_mono=now_mono,
                        activation_latency_ms=activation_latency_ms,
                    )
                continue
            if msg_type != "trade":
                continue

            ticker = str(msg.get("market_ticker") or msg.get("ticker") or "")
            trade_id = str(msg.get("trade_id") or "")
            trade_size = _trade_size_fp(msg)
            now_mono = time.monotonic()
            for state in by_maker.get(ticker, []):
                active = state.active_price
                if active is None:
                    continue
                eligible, gate_reason = trade_fill_gate(
                    state=state,
                    maker_book=books[state.candidate.maker_ticker],
                    trade_msg=msg,
                    now_mono=now_mono,
                )
                if not eligible:
                    if gate_reason == "TRADE_SIZE_LT_1":
                        state.ignored_small_trade_count += 1
                    elif gate_reason == "ACTIVATION_LATENCY":
                        state.ignored_activation_trade_count += 1
                    elif gate_reason == "QUEUE_PRIORITY_AMBIGUOUS":
                        state.ignored_queue_trade_count += 1
                        _cancel_state(
                            state,
                            reason="QUEUE_PRIORITY_AMBIGUOUS_AT_TRADE",
                            economics={
                                "trade_id": trade_id,
                                "trade_count_fp": str(trade_size),
                            },
                        )
                    continue

                fills += 1
                state.plausible_fill_count += 1
                age_ms = None
                if state.posted_monotonic is not None:
                    age_ms = (now_mono - state.posted_monotonic) * 1000
                pre = _post_fill_snapshot(
                    c=state.candidate,
                    maker_fill_price=active,
                    books=books,
                    label="LAST_BOOK_BEFORE_TRADE_EVENT",
                    trade_id=trade_id,
                )
                if pre.get("target_survives"):
                    survived_pre_trade += 1
                event = {
                    "series": state.candidate.series,
                    "event_ticker": state.candidate.event_ticker,
                    "maker_ticker": state.candidate.maker_ticker,
                    "maker_side": state.candidate.maker_side,
                    "shadow_fill_price": str(active),
                    "quote_age_ms": age_ms,
                    "activation_latency_ms": activation_latency_ms,
                    "trade_id": trade_id,
                    "trade_ts_ms": msg.get("ts_ms"),
                    "receive_ts_ms": receive_ms,
                    "trade_yes_price": msg.get("yes_price_dollars"),
                    "trade_no_price": msg.get("no_price_dollars"),
                    "taker_outcome_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
                    "trade_count_fp": str(trade_size),
                    "pre_trade_hedge": pre,
                    "classification": "PLAUSIBLE_FILL_TRADE_THROUGH",
                    "actual_order_placed": False,
                    "read_only": True,
                    "order_entry": False,
                }
                logger.info("NESTED_ROLLING_FILL %s", json.dumps(event, separators=(",", ":"), default=str))
                state.active_price = None
                state.posted_monotonic = None
                state.fill_eligible_monotonic = None
                state.cooldown_until = time.monotonic() + 1.0
                task = asyncio.create_task(_fill_followup(
                    state=state,
                    maker_fill_price=active,
                    books=books,
                    trade_id=trade_id,
                ))
                delayed.add(task)
                task.add_done_callback(delayed.discard)

    if delayed:
        await asyncio.gather(*delayed, return_exceptions=True)
    summary = {
        "candidates": len(candidates),
        "markets": len(market_tickers),
        "fills": fills,
        "probe_eligible_fills": sum(s.probe_eligible_fill_count for s in states),
        "target_survived_last_book_before_trade": survived_pre_trade,
        "posts": sum(s.post_count for s in states),
        "cancels": sum(s.cancel_count for s in states),
        "ignored_small_trades": sum(s.ignored_small_trade_count for s in states),
        "ignored_activation_trades": sum(s.ignored_activation_trade_count for s in states),
        "ignored_queue_trades": sum(s.ignored_queue_trade_count for s in states),
        "target_profit": str(target_profit),
        "max_time_to_close_seconds": max_time_to_close_seconds,
        "activation_latency_ms": activation_latency_ms,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("NESTED_ROLLING_SUMMARY %s", json.dumps(summary, separators=(",", ":")))
    return summary


async def rolling_watch(
    *,
    seconds: float,
    refresh_seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    max_time_to_close_seconds: float,
    activation_latency_ms: float,
) -> None:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    if refresh_seconds < 30:
        raise ValueError("refresh_seconds must be >= 30")
    if max_time_to_close_seconds <= 0:
        raise ValueError("max_time_to_close_seconds must be > 0")
    if activation_latency_ms < 0:
        raise ValueError("activation_latency_ms must be >= 0")
    start = time.monotonic()
    cycles = 0
    while seconds <= 0 or time.monotonic() - start < seconds:
        remaining = refresh_seconds if seconds <= 0 else min(
            refresh_seconds,
            max(0.1, seconds - (time.monotonic() - start)),
        )
        if remaining <= 0:
            break
        cycles += 1
        try:
            await watch_cycle(
                cycle_seconds=remaining,
                series=series,
                target_profit=target_profit,
                max_candidates=max_candidates,
                max_time_to_close_seconds=max_time_to_close_seconds,
                activation_latency_ms=activation_latency_ms,
            )
        except Exception as exc:
            logger.exception("NESTED_ROLLING_CYCLE_ERROR cycle=%s error=%s", cycles, str(exc)[:300])
            await asyncio.sleep(2.0)
    logger.info("NESTED_ROLLING_DONE %s", json.dumps({
        "cycles": cycles,
        "seconds": seconds,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=7200.0)
    parser.add_argument("--refresh-seconds", type=float, default=300.0)
    parser.add_argument("--series", nargs="+", default=["KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument(
        "--max-time-to-close-seconds",
        type=float,
        default=21600.0,
        help="Reject candidate events more than this many seconds from close (default 6h).",
    )
    parser.add_argument(
        "--activation-latency-ms",
        type=float,
        default=25.0,
        help="Shadow maker order cannot be fill-eligible until this delay has elapsed.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(rolling_watch(
        seconds=args.seconds,
        refresh_seconds=args.refresh_seconds,
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        max_candidates=args.max_candidates,
        max_time_to_close_seconds=args.max_time_to_close_seconds,
        activation_latency_ms=args.activation_latency_ms,
    ))


if __name__ == "__main__":
    main()
