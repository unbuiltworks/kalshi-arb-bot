"""Read-only maker-first shadow for Gemini <-> Kalshi 15-minute prediction markets.

This service asks a narrow execution question without placing orders:

    If a q=1 post-only Kalshi bid had rested at a price that still left a
    configurable profit after a Gemini taker hedge, would real public Kalshi
    trade prints have consumed that quote, and what hedge was actually available
    when the fill became observable?

Important safety properties:
- no order transport or write endpoint is imported;
- Kalshi authentication is used only for market-data WebSocket access;
- fills are inferred from the public ``trade`` channel, not from cancellations;
- displayed queue ahead is consumed before a hypothetical join-the-best quote;
- trade-through is treated as a high-confidence fill;
- stale Gemini hedge books are rejected;
- every output remains INDEX_MISMATCH_RESEARCH_ONLY until settlement equivalence
  is separately certified.

This v1 deliberately quotes only q=1 and cent ticks. It measures three policies:
JOIN, IMPROVE_1T, and MAX_SAFE, at several required-profit thresholds.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import json
import logging
from pathlib import Path
import time
from typing import Any, Iterable

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_aligned_watch import Pair
from src.gemini_kalshi_btc_ws_shadow import KALSHI_WS, KALSHI_WS_SIGN_PATH, _ws_header_kw
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import (
    KalshiSeriesFeeMetadata,
    fetch_series_fee_metadata,
)
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.gemini_kalshi_multi_ws_hot import GuardedKalshiOrderBook
from src.gemini_kalshi_multi_ws_shadow import ASSET_SERIES, PairDiscoveryCache

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_shadow")
SETTLEMENT_STATUS = "INDEX_MISMATCH_RESEARCH_ONLY"
TICK = D("0.01")
QTY = D("1")
CENTICENT = D("0.0001")
HEARTBEAT_S = 10.0
MAX_QUEUE = 50_000

POLICIES = ("JOIN", "IMPROVE_1T", "MAX_SAFE")


def _ceil_centicent(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("fee cannot be negative")
    return (value / CENTICENT).to_integral_value(rounding=ROUND_CEILING) * CENTICENT


def kalshi_maker_fee(
    price: Decimal,
    quantity: Decimal,
    *,
    fee_type: str,
    fee_multiplier: Decimal,
) -> Decimal:
    """Conservative maker trade-fee model from live Kalshi series metadata.

    ``quadratic`` series have no maker fee. ``quadratic_with_maker_fees`` uses
    the published 0.0175 curve. The exchange's current fixed-point docs describe
    the trade-fee component at centicent precision; actual fill records remain
    the authority for reconciliation once a live maker probe is eventually run.
    """
    if quantity < 0 or price < 0 or price > 1 or fee_multiplier < 0:
        raise ValueError("invalid maker fee inputs")
    if fee_type == "quadratic":
        return D("0")
    if fee_type != "quadratic_with_maker_fees":
        raise ValueError(f"unsupported maker fee_type={fee_type!r}")
    return _ceil_centicent(
        D("0.0175") * fee_multiplier * quantity * price * (D("1") - price)
    )


@dataclass(frozen=True)
class MakerEconomics:
    direction: str
    maker_outcome: str
    hedge_outcome: str
    maker_price: Decimal
    hedge_price: Decimal
    maker_fee: Decimal
    hedge_fee: Decimal
    all_in_cost: Decimal
    locked_profit: Decimal


@dataclass
class RestingQuote:
    key: str
    asset: str
    direction: str
    maker_outcome: str
    hedge_outcome: str
    policy: str
    target_profit: Decimal
    price: Decimal
    opened_ms: int
    queue_ahead: Decimal
    remaining: Decimal = QTY
    traded_at_level: Decimal = D("0")
    fills: Decimal = D("0")


@dataclass(frozen=True)
class KalshiTrade:
    ticker: str
    yes_price: Decimal
    no_price: Decimal
    quantity: Decimal
    taker_side: str
    ts_ms: int


@dataclass
class MakerStats:
    asset: str
    quotes_opened: int = 0
    quotes_repriced: int = 0
    quote_cancels: int = 0
    public_trades: int = 0
    simulated_fills: int = 0
    stale_hedges: int = 0
    net_profit: Decimal = D("0")
    by_policy: dict[str, dict[str, Decimal | int]] = field(default_factory=dict)

    def record_fill(self, policy: str, profit: Decimal) -> None:
        self.simulated_fills += 1
        self.net_profit += profit
        row = self.by_policy.setdefault(
            policy, {"fills": 0, "profit": D("0")}
        )
        row["fills"] = int(row["fills"]) + 1
        row["profit"] = D(str(row["profit"])) + profit

    def payload(self, active_quotes: int, queue_size: int) -> dict[str, Any]:
        by_policy = {
            policy: {
                "fills": int(row["fills"]),
                "profit": str(row["profit"]),
            }
            for policy, row in sorted(self.by_policy.items())
        }
        return {
            "asset": self.asset,
            "quotes_opened": self.quotes_opened,
            "quotes_repriced": self.quotes_repriced,
            "quote_cancels": self.quote_cancels,
            "public_trades": self.public_trades,
            "simulated_fills": self.simulated_fills,
            "stale_hedges": self.stale_hedges,
            "simulated_net_profit": str(self.net_profit),
            "active_quotes": active_quotes,
            "queue_size": queue_size,
            "by_policy": by_policy,
            "settlement_status": SETTLEMENT_STATUS,
            "execution_eligible": False,
            "read_only": True,
            "order_entry": False,
        }


def maker_side_book(k: TopOfBook, outcome: str) -> tuple[Decimal, Decimal, Decimal]:
    """Return best bid, bid size, and ask for the maker outcome."""
    outcome = outcome.upper()
    if outcome == "YES":
        return k.yes_bid, k.yes_bid_size, k.yes_ask
    if outcome == "NO":
        return k.no_bid, k.yes_ask_size, k.no_ask
    raise ValueError(f"unknown outcome={outcome!r}")


def hedge_price_and_size(g: TopOfBook, outcome: str) -> tuple[Decimal, Decimal]:
    outcome = outcome.upper()
    if outcome == "YES":
        return g.yes_ask, g.yes_ask_size
    if outcome == "NO":
        return g.no_ask, g.no_ask_size
    raise ValueError(f"unknown outcome={outcome!r}")


def quote_economics(
    *,
    direction: str,
    maker_price: Decimal,
    gemini: TopOfBook,
    fee_meta: KalshiSeriesFeeMetadata,
) -> MakerEconomics | None:
    if direction == "GEMINI_YES_KALSHI_NO":
        maker_outcome, hedge_outcome = "NO", "YES"
    elif direction == "KALSHI_YES_GEMINI_NO":
        maker_outcome, hedge_outcome = "YES", "NO"
    else:
        raise ValueError(f"unknown direction={direction!r}")

    hedge_price, hedge_size = hedge_price_and_size(gemini, hedge_outcome)
    if hedge_size < QTY or not (D("0") < hedge_price < D("1")):
        return None
    maker_fee = kalshi_maker_fee(
        maker_price,
        QTY,
        fee_type=fee_meta.fee_type,
        fee_multiplier=D(fee_meta.fee_multiplier),
    )
    hedge_fee = gemini_predictions_taker_fee(hedge_price, QTY)
    all_in = maker_price + maker_fee + hedge_price + hedge_fee
    return MakerEconomics(
        direction=direction,
        maker_outcome=maker_outcome,
        hedge_outcome=hedge_outcome,
        maker_price=maker_price,
        hedge_price=hedge_price,
        maker_fee=maker_fee,
        hedge_fee=hedge_fee,
        all_in_cost=all_in,
        locked_profit=QTY - all_in,
    )


def max_safe_maker_price(
    *,
    direction: str,
    gemini: TopOfBook,
    fee_meta: KalshiSeriesFeeMetadata,
    target_profit: Decimal,
    tick: Decimal = TICK,
) -> Decimal | None:
    if target_profit < 0 or tick <= 0:
        raise ValueError("invalid quote threshold")
    steps = int((D("0.99") / tick).to_integral_value())
    best: Decimal | None = None
    for i in range(1, steps + 1):
        price = tick * i
        if price >= D("1"):
            break
        econ = quote_economics(
            direction=direction,
            maker_price=price,
            gemini=gemini,
            fee_meta=fee_meta,
        )
        if econ is not None and econ.locked_profit >= target_profit:
            best = price
    return best


def desired_maker_price(
    *,
    policy: str,
    best_bid: Decimal,
    best_ask: Decimal,
    economic_max: Decimal,
    tick: Decimal = TICK,
) -> Decimal | None:
    """Choose a post-only bid without crossing the current ask."""
    if policy not in POLICIES:
        raise ValueError(f"unknown maker policy={policy!r}")
    if tick <= 0 or economic_max <= 0:
        return None
    post_only_cap = best_ask - tick
    if post_only_cap <= 0:
        return None
    safe_cap = min(economic_max, post_only_cap)
    if safe_cap <= 0:
        return None

    if policy == "JOIN":
        price = best_bid
    elif policy == "IMPROVE_1T":
        price = best_bid + tick
    else:
        price = safe_cap

    price = min(price, safe_cap)
    if price <= 0 or price >= best_ask:
        return None
    return price


def parse_trade(payload: dict[str, Any], ticker: str) -> KalshiTrade | None:
    if str(payload.get("type") or "") != "trade":
        return None
    msg = payload.get("msg") or {}
    if str(msg.get("market_ticker") or "") != ticker:
        return None
    try:
        yes_price = D(str(msg["yes_price_dollars"]))
        no_price = D(str(msg["no_price_dollars"]))
        quantity = D(str(msg.get("count_fp") or "0"))
        ts_ms = int(msg.get("ts_ms") or int(msg.get("ts") or 0) * 1000)
    except (KeyError, ValueError, TypeError):
        return None
    taker_side = str(msg.get("taker_side") or "").lower()
    if taker_side not in {"yes", "no"} or quantity <= 0:
        return None
    return KalshiTrade(
        ticker=ticker,
        yes_price=yes_price,
        no_price=no_price,
        quantity=quantity,
        taker_side=taker_side,
        ts_ms=ts_ms,
    )


def trade_consumption(quote: RestingQuote, trade: KalshiTrade) -> Decimal:
    """Return quantity from this print that conservatively consumes quote priority.

    For a resting BUY YES, a NO taker sells YES into YES bids. For a resting
    BUY NO, a YES taker sells NO into NO bids. A print below our bid means all
    better/equal bid liquidity had to be consumed first, so the remaining q=1
    quote is treated as fully filled.
    """
    if quote.maker_outcome == "YES":
        if trade.taker_side != "no":
            return D("0")
        trade_price = trade.yes_price
    else:
        if trade.taker_side != "yes":
            return D("0")
        trade_price = trade.no_price

    if trade_price > quote.price:
        return D("0")
    if trade_price < quote.price:
        return quote.queue_ahead + quote.remaining
    return trade.quantity


def apply_trade_to_quote(
    quote: RestingQuote, trade: KalshiTrade
) -> Decimal:
    """Consume queue ahead, then return newly simulated filled quantity."""
    consumption = trade_consumption(quote, trade)
    if consumption <= 0 or quote.remaining <= 0:
        return D("0")

    if quote.queue_ahead > 0:
        ahead_used = min(quote.queue_ahead, consumption)
        quote.queue_ahead -= ahead_used
        consumption -= ahead_used
        quote.traded_at_level += ahead_used
    if consumption <= 0:
        return D("0")

    fill = min(quote.remaining, consumption)
    quote.remaining -= fill
    quote.fills += fill
    quote.traded_at_level += fill
    return fill


def _quote_key(direction: str, policy: str, target_profit: Decimal) -> str:
    return f"{direction}|{policy}|{target_profit}"


def _kalshi_subscription(ticker: str) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "trade"],
            "market_ticker": ticker,
            "use_yes_price": False,
        },
    }


def _enqueue(queue: asyncio.Queue, item: tuple[str, int, Any], stats: MakerStats) -> None:
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(item)


async def _gemini_stream(symbol: str, queue: asyncio.Queue, stats: MakerStats) -> None:
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": f"maker-{stats.asset.lower()}",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@bookTicker"],
                }))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini WS subscription error: {payload}")
                        continue
                    if str(payload.get("s") or "").lower() != symbol.lower():
                        continue
                    try:
                        top = parse_gemini_book_ticker(payload, received_ms=receive_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    _enqueue(queue, ("gemini", receive_ms, top), stats)
            raise RuntimeError(f"Gemini WS ended for {symbol}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "MAKER_SHADOW_GEMINI_RECONNECT %s",
                json.dumps({
                    "asset": stats.asset,
                    "symbol": symbol,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


async def _kalshi_stream(ticker: str, queue: asyncio.Queue, stats: MakerStats) -> None:
    private_key = load_private_key()
    backoff = 0.25
    while True:
        try:
            headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
            book = GuardedKalshiOrderBook(ticker)
            async with websockets.connect(
                KALSHI_WS, open_timeout=5.0, **_ws_header_kw(headers)
            ) as ws:
                await ws.send(json.dumps(_kalshi_subscription(ticker)))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("type") == "error":
                        raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
                    trade = parse_trade(payload, ticker)
                    if trade is not None:
                        stats.public_trades += 1
                        _enqueue(queue, ("trade", receive_ms, trade), stats)
                        continue
                    if not book.apply(payload):
                        continue
                    top = book.top(receive_ms)
                    if top is not None:
                        _enqueue(queue, ("kalshi", receive_ms, top), stats)
            raise RuntimeError(f"Kalshi WS ended for {ticker}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "MAKER_SHADOW_KALSHI_RECONNECT %s",
                json.dumps({
                    "asset": stats.asset,
                    "ticker": ticker,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                }, separators=(",", ":")),
            )
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


def _direction_outcomes(direction: str) -> tuple[str, str]:
    if direction == "GEMINI_YES_KALSHI_NO":
        return "NO", "YES"
    if direction == "KALSHI_YES_GEMINI_NO":
        return "YES", "NO"
    raise ValueError(direction)


def _refresh_quotes(
    *,
    active: dict[str, RestingQuote],
    asset: str,
    now_ms: int,
    gemini: TopOfBook,
    kalshi: TopOfBook,
    fee_meta: KalshiSeriesFeeMetadata,
    targets: Iterable[Decimal],
    stats: MakerStats,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for direction in ("GEMINI_YES_KALSHI_NO", "KALSHI_YES_GEMINI_NO"):
        maker_outcome, hedge_outcome = _direction_outcomes(direction)
        best_bid, bid_size, best_ask = maker_side_book(kalshi, maker_outcome)
        for target in targets:
            economic_max = max_safe_maker_price(
                direction=direction,
                gemini=gemini,
                fee_meta=fee_meta,
                target_profit=target,
            )
            for policy in POLICIES:
                key = _quote_key(direction, policy, target)
                desired = None
                if economic_max is not None:
                    desired = desired_maker_price(
                        policy=policy,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        economic_max=economic_max,
                    )
                prior = active.get(key)
                if desired is None:
                    if prior is not None:
                        active.pop(key, None)
                        stats.quote_cancels += 1
                        events.append({
                            "event": "CANCEL",
                            "key": key,
                            "reason": "NO_LONGER_ECONOMIC_OR_POST_ONLY",
                            "price": str(prior.price),
                            "age_ms": max(0, now_ms - prior.opened_ms),
                        })
                    continue
                if prior is not None and prior.price == desired:
                    continue
                if prior is not None:
                    active.pop(key, None)
                    stats.quotes_repriced += 1
                    events.append({
                        "event": "REPRICE",
                        "key": key,
                        "old_price": str(prior.price),
                        "new_price": str(desired),
                        "age_ms": max(0, now_ms - prior.opened_ms),
                    })

                # Joining the displayed best bid sits behind displayed size. A price
                # improvement has no displayed queue ahead in the observed book.
                queue_ahead = bid_size if desired == best_bid else D("0")
                active[key] = RestingQuote(
                    key=key,
                    asset=asset,
                    direction=direction,
                    maker_outcome=maker_outcome,
                    hedge_outcome=hedge_outcome,
                    policy=policy,
                    target_profit=target,
                    price=desired,
                    opened_ms=now_ms,
                    queue_ahead=queue_ahead,
                )
                stats.quotes_opened += 1
                events.append({
                    "event": "OPEN",
                    "key": key,
                    "direction": direction,
                    "maker_outcome": maker_outcome,
                    "hedge_outcome": hedge_outcome,
                    "policy": policy,
                    "target_profit": str(target),
                    "price": str(desired),
                    "queue_ahead": str(queue_ahead),
                    "best_bid": str(best_bid),
                    "best_ask": str(best_ask),
                    "economic_max": str(economic_max),
                })
    return events


def _process_trade(
    *,
    active: dict[str, RestingQuote],
    trade: KalshiTrade,
    receive_ms: int,
    gemini: TopOfBook | None,
    fee_meta: KalshiSeriesFeeMetadata,
    max_hedge_age_ms: int,
    stats: MakerStats,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key, quote in list(active.items()):
        fill = apply_trade_to_quote(quote, trade)
        if fill <= 0:
            continue
        # v1 is q=1 only; any fill is immediately removed from the quoting set.
        active.pop(key, None)
        hedge_age = None if gemini is None else max(0, receive_ms - gemini.ts_ms)
        if gemini is None or hedge_age is None or hedge_age > max_hedge_age_ms:
            stats.stale_hedges += 1
            events.append({
                "event": "FILL_HEDGE_UNAVAILABLE",
                "key": key,
                "fill_qty": str(fill),
                "maker_price": str(quote.price),
                "queue_ahead_remaining": str(quote.queue_ahead),
                "trade_yes_price": str(trade.yes_price),
                "trade_no_price": str(trade.no_price),
                "trade_qty": str(trade.quantity),
                "taker_side": trade.taker_side,
                "trade_ts_ms": trade.ts_ms,
                "hedge_age_ms": hedge_age,
            })
            continue

        econ = quote_economics(
            direction=quote.direction,
            maker_price=quote.price,
            gemini=gemini,
            fee_meta=fee_meta,
        )
        if econ is None:
            stats.stale_hedges += 1
            events.append({
                "event": "FILL_HEDGE_UNAVAILABLE",
                "key": key,
                "fill_qty": str(fill),
                "maker_price": str(quote.price),
                "hedge_age_ms": hedge_age,
                "reason": "NO_Q1_HEDGE_DEPTH",
            })
            continue

        # q=1 quotes only. If a fractional public print implies a partial fill,
        # scale the economics linearly for telemetry and keep it research-only.
        realized = econ.locked_profit * fill
        stats.record_fill(quote.policy, realized)
        events.append({
            "event": "SIMULATED_FILL",
            "key": key,
            "direction": quote.direction,
            "maker_outcome": quote.maker_outcome,
            "hedge_outcome": quote.hedge_outcome,
            "policy": quote.policy,
            "target_profit": str(quote.target_profit),
            "quote_opened_ms": quote.opened_ms,
            "quote_age_ms": max(0, receive_ms - quote.opened_ms),
            "fill_qty": str(fill),
            "maker_price": str(quote.price),
            "maker_fee": str(econ.maker_fee),
            "hedge_price": str(econ.hedge_price),
            "hedge_fee": str(econ.hedge_fee),
            "hedge_book_age_ms": hedge_age,
            "post_fill_all_in_cost_q1": str(econ.all_in_cost),
            "post_fill_locked_profit_q1": str(econ.locked_profit),
            "simulated_fill_profit": str(realized),
            "trade_yes_price": str(trade.yes_price),
            "trade_no_price": str(trade.no_price),
            "trade_qty": str(trade.quantity),
            "taker_side": trade.taker_side,
            "trade_ts_ms": trade.ts_ms,
            "fill_confidence": "PUBLIC_TRADE_QUEUE_CONSUMPTION",
            "settlement_status": SETTLEMENT_STATUS,
            "execution_eligible": False,
            "read_only": True,
            "order_entry": False,
        })
    return events


async def _capture_pair(
    *,
    asset: str,
    pair: Pair,
    fee_meta: KalshiSeriesFeeMetadata,
    output: Path,
    targets: list[Decimal],
    max_book_skew_ms: int,
    max_hedge_age_ms: int,
    overall_deadline: float | None,
) -> None:
    queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
    stats = MakerStats(asset=asset)
    tasks = [
        asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    active: dict[str, RestingQuote] = {}
    next_heartbeat = time.monotonic() + HEARTBEAT_S
    remaining_window_s = max(0.0, pair.window_end.timestamp() - time.time() + 0.75)
    pair_deadline = time.monotonic() + remaining_window_s
    if overall_deadline is not None:
        pair_deadline = min(pair_deadline, overall_deadline)

    logger.info(
        "MAKER_SHADOW_READY %s",
        json.dumps({
            "asset": asset,
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "targets": [str(x) for x in targets],
            "policies": list(POLICIES),
            "quantity": "1",
            "tick": str(TICK),
            "max_book_skew_ms": max_book_skew_ms,
            "max_hedge_age_ms": max_hedge_age_ms,
            "kalshi_fee_type": fee_meta.fee_type,
            "kalshi_fee_multiplier": fee_meta.fee_multiplier,
            "fill_source": "KALSHI_PUBLIC_TRADE_WS",
            "settlement_status": SETTLEMENT_STATUS,
            "execution_eligible": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("a", encoding="utf-8") as fh:
            while time.monotonic() < pair_deadline:
                timeout = max(0.01, min(0.5, pair_deadline - time.monotonic()))
                try:
                    kind, receive_ms, payload = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    continue

                emitted: list[dict[str, Any]] = []
                if kind == "gemini":
                    g = payload
                elif kind == "kalshi":
                    k = payload
                else:
                    emitted.extend(_process_trade(
                        active=active,
                        trade=payload,
                        receive_ms=receive_ms,
                        gemini=g,
                        fee_meta=fee_meta,
                        max_hedge_age_ms=max_hedge_age_ms,
                        stats=stats,
                    ))

                # Do not reopen/reprice from a trade event against a pre-trade book.
                # Wait for the next venue book update so queue-ahead is not stale.
                if kind in {"gemini", "kalshi"} and g is not None and k is not None:
                    skew = abs(g.ts_ms - k.ts_ms)
                    if skew <= max_book_skew_ms:
                        emitted.extend(_refresh_quotes(
                            active=active,
                            asset=asset,
                            now_ms=receive_ms,
                            gemini=g,
                            kalshi=k,
                            fee_meta=fee_meta,
                            targets=targets,
                            stats=stats,
                        ))
                    elif active:
                        for key, quote in list(active.items()):
                            emitted.append({
                                "event": "CANCEL",
                                "key": key,
                                "reason": "BOOK_SKEW",
                                "price": str(quote.price),
                                "age_ms": max(0, receive_ms - quote.opened_ms),
                                "book_skew_ms": skew,
                            })
                            stats.quote_cancels += 1
                        active.clear()

                for row in emitted:
                    full = {
                        "asset": asset,
                        "receive_ts_ms": receive_ms,
                        "gemini_symbol": pair.gemini_symbol,
                        "kalshi_ticker": pair.kalshi_ticker,
                        **row,
                        "settlement_status": SETTLEMENT_STATUS,
                        "execution_eligible": False,
                        "read_only": True,
                        "order_entry": False,
                    }
                    fh.write(json.dumps(full, separators=(",", ":"), default=str) + "\n")

                if time.monotonic() >= next_heartbeat:
                    logger.info(
                        "MAKER_SHADOW_SUMMARY %s",
                        json.dumps(
                            stats.payload(len(active), queue.qsize()),
                            separators=(",", ":"),
                            default=str,
                        ),
                    )
                    next_heartbeat = time.monotonic() + HEARTBEAT_S
            fh.flush()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def collect_asset(
    *,
    http: httpx.AsyncClient,
    discovery: PairDiscoveryCache,
    asset: str,
    output_dir: Path,
    targets: list[Decimal],
    max_book_skew_ms: int,
    max_hedge_age_ms: int,
    fee_meta: KalshiSeriesFeeMetadata,
    overall_deadline: float | None,
) -> None:
    while overall_deadline is None or time.monotonic() < overall_deadline:
        pair = await discovery.current_pair(http, asset)
        if pair is None:
            logger.info("MAKER_SHADOW_WAITING_FOR_PAIR asset=%s", asset)
            await asyncio.sleep(0.5)
            continue
        await _capture_pair(
            asset=asset,
            pair=pair,
            fee_meta=fee_meta,
            output=output_dir / f"maker_shadow_{asset.lower()}.jsonl",
            targets=targets,
            max_book_skew_ms=max_book_skew_ms,
            max_hedge_age_ms=max_hedge_age_ms,
            overall_deadline=overall_deadline,
        )


async def collect_all(
    *,
    assets: list[str],
    seconds: float,
    output_dir: Path,
    targets: list[Decimal],
    max_book_skew_ms: int,
    max_hedge_age_ms: int,
) -> None:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    load_private_key()
    assets = [a.upper() for a in assets]
    unknown = [a for a in assets if a not in ASSET_SERIES]
    if unknown:
        raise ValueError(f"unsupported assets={unknown}")
    if any(x < 0 for x in targets):
        raise ValueError("target profits must be non-negative")

    overall_deadline = None if seconds <= 0 else time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=20.0) as http:
        fee_meta: dict[str, KalshiSeriesFeeMetadata] = {}
        for asset in assets:
            meta = await fetch_series_fee_metadata(http, ASSET_SERIES[asset])
            # Maker fee mapping must be known at startup; fail closed otherwise.
            kalshi_maker_fee(
                D("0.50"), QTY,
                fee_type=meta.fee_type,
                fee_multiplier=D(meta.fee_multiplier),
            )
            fee_meta[asset] = meta
            logger.info(
                "MAKER_SHADOW_FEE_GATE %s",
                json.dumps({
                    **meta.as_dict(),
                    "maker_formula_status": (
                        "NO_MAKER_FEE"
                        if meta.fee_type == "quadratic"
                        else "MAPPED_QUADRATIC_MAKER"
                    ),
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
            await asyncio.sleep(0.10)

        discovery = PairDiscoveryCache(ttl_s=1.0)
        await asyncio.gather(*[
            collect_asset(
                http=http,
                discovery=discovery,
                asset=asset,
                output_dir=output_dir,
                targets=targets,
                max_book_skew_ms=max_book_skew_ms,
                max_hedge_age_ms=max_hedge_age_ms,
                fee_meta=fee_meta[asset],
                overall_deadline=overall_deadline,
            )
            for asset in assets
        ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL", "XRP"])
    parser.add_argument("--seconds", type=float, default=0.0, help="0 = run continuously")
    parser.add_argument(
        "--targets", nargs="+", type=Decimal,
        default=[D("0.01"), D("0.02"), D("0.03"), D("0.05")],
    )
    parser.add_argument("--max-book-skew-ms", type=int, default=50)
    parser.add_argument("--max-hedge-age-ms", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/maker-shadow"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(collect_all(
        assets=args.assets,
        seconds=args.seconds,
        output_dir=args.output_dir,
        targets=list(args.targets),
        max_book_skew_ms=max(0, args.max_book_skew_ms),
        max_hedge_age_ms=max(0, args.max_hedge_age_ms),
    ))


if __name__ == "__main__":
    main()
