"""Read-only pessimistic fill shadow for K->P MLB moneyline maker->taker routes.

This is the second-stage measurement behind the static maker quote census.
It never places orders. A hypothetical Kalshi maker quote receives ZERO fill
credit merely for touching or improving the book.

Fill model (deliberately strict / under-counting):
* if joining an existing Kalshi bid, displayed size at that price is queue ahead;
  only opposite-taker public trade volume at the exact price consumes that queue;
* if improving the bid into an empty tick, exact-price opposite-taker flow may
  fill q=1 because the hypothetical quote would have time priority there;
* any opposite-taker public trade THROUGH the hypothetical bid price proves the
  q=1 quote would have filled before the worse-priced trade;
* cancellations ahead are NOT credited, so fill rate is pessimistic.

On every simulated fill the current Polymarket opposite ask is recorded at
receipt time and again after 80/250/500ms. Quote economics are reported under
both the general Kalshi maker-fee formula and a zero-maker-fee upper bound.
Until exact series fee applicability and settlement-rule equivalence are
certified, capacity credit remains $0/day.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import json
import logging
import os
import signal
import statistics
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets
from polymarket_us import PolymarketUS

from config.settings import API_KEY_ID, WS_PING_INTERVAL_S, WS_URL
from src.auth import auth_headers, load_private_key
from src import sports_kp_generic_mlb_game_breadth as breadth
from src.sports_kp_generic_mlb_game_breadth_fullgame import is_clearly_partial_game_label
from src.sports_kp_generic_mlb_game_maker_census import _levels, _maker_price
from src.polymarket_kalshi_shadow import polymarket_taker_fee
from src.polymarket_kalshi_shadow_v2 import PolyBookStore, refresh_poly_books, run_poly_ws
from src.kalshi_cross_book_v2 import KalshiCrossBooks

logger = logging.getLogger("kalshi_arb.sports_kp_mlb_moneyline_maker_fill_shadow")
D = Decimal
ONE = D("1")
KALSHI_MAKER_RATE = D("0.0175")
DEFAULT_POLY_TAKER_RATE = D("0.05")
TARGETS = (D("0.01"), D("0.02"), D("0.03"))


def _kalshi_maker_fee_q1(price: Decimal) -> Decimal:
    """General maker formula; applicability is series-specific and separately gated."""
    raw = KALSHI_MAKER_RATE * price * (ONE - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def _opposite(side: str) -> str:
    return "NO" if side.upper() == "YES" else "YES"


def _median(values: list[int]) -> float | None:
    return round(float(statistics.median(values)), 3) if values else None


@dataclass(frozen=True)
class PublicTrade:
    ticker: str
    yes_price: Decimal
    no_price: Decimal
    count: Decimal
    taker_side: str
    exchange_ts_ms: int | None
    received_ms: int
    trade_id: str


class KalshiTradeBooks(KalshiCrossBooks):
    """Orderbook stream plus public trade prints on one authenticated WS session."""

    def __init__(self, tickers: list[str] | tuple[str, ...]) -> None:
        super().__init__(tickers)
        self.trades: asyncio.Queue[PublicTrade] = asyncio.Queue(maxsize=20000)
        self.public_trades_seen = 0
        self.dropped_trades = 0
        self.last_trade_received_ms = 0

    async def _run_once(self) -> None:  # noqa: D401 - override parent transport
        async with websockets.connect(
            self.ws_url,
            additional_headers=self._headers(),
            ping_interval=WS_PING_INTERVAL_S,
        ) as ws:
            self._ws = ws
            await ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta", "trade"],
                    "market_tickers": list(self.tickers),
                    "use_yes_price": False,
                },
            }))
            logger.info("KALSHI_MAKER_SHADOW_WS subscribed_tickers=%d channels=orderbook_delta,trade", len(self.tickers))
            async for raw in ws:
                if not self._running:
                    break
                msg = json.loads(raw)
                typ = msg.get("type")
                if typ in {"orderbook_snapshot", "orderbook_delta"}:
                    self._validate_sequence(msg)
                if typ == "orderbook_snapshot":
                    await self.apply_snapshot(msg)
                elif typ == "orderbook_delta":
                    await self.apply_delta(msg)
                elif typ == "trade":
                    self._ingest_trade(msg)
                elif typ == "error":
                    raise RuntimeError(f"Kalshi WS error: {msg.get('msg')}")

    def _ingest_trade(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker") or "")
        if ticker not in self.tickers:
            return
        try:
            yes_price = D(str(body.get("yes_price_dollars")))
            no_price = D(str(body.get("no_price_dollars")))
            count = D(str(body.get("count_fp", body.get("count", "0"))))
        except Exception:
            return
        taker_side = str(
            body.get("taker_outcome_side")
            or body.get("taker_side")
            or ""
        ).upper()
        if taker_side not in {"YES", "NO"} or count <= 0:
            return
        now = int(time.time() * 1000)
        row = PublicTrade(
            ticker=ticker,
            yes_price=yes_price,
            no_price=no_price,
            count=count,
            taker_side=taker_side,
            exchange_ts_ms=_int_or_none(body.get("ts_ms")),
            received_ms=now,
            trade_id=str(body.get("trade_id") or ""),
        )
        self.public_trades_seen += 1
        self.last_trade_received_ms = now
        try:
            self.trades.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped_trades += 1


@dataclass
class ShadowQuote:
    quote_id: int
    group_key: str
    ticker: str
    maker_side: str
    maker_price: Decimal
    quote_mode: str
    queue_ahead: Decimal
    poly_slug: str
    poly_hedge_side: str
    poly_hedge_price_at_open: Decimal
    profit_maker_fee_at_open: Decimal
    profit_zero_fee_at_open: Decimal
    opened_ms: int
    pair: Any = field(repr=False)


@dataclass
class Stats:
    quote_opens: int = 0
    quote_reprices: int = 0
    quote_cancels: int = 0
    trade_prints: int = 0
    qualifying_opposite_taker_prints: int = 0
    simulated_fills: int = 0
    trade_through_fills: int = 0
    exact_price_fills: int = 0
    hedge_missing_at_fill: int = 0
    immediate_gte_maker_fee: dict[str, int] = field(default_factory=lambda: {"1c": 0, "2c": 0, "3c": 0})
    immediate_gte_zero_fee: dict[str, int] = field(default_factory=lambda: {"1c": 0, "2c": 0, "3c": 0})
    fill_quote_ages_ms: list[int] = field(default_factory=list)


class MakerFillShadow:
    def __init__(
        self,
        pairs: list[Any],
        poly_store: PolyBookStore,
        kalshi: KalshiTradeBooks,
        *,
        poly_taker_rate: Decimal,
        min_zero_fee_edge: Decimal,
    ) -> None:
        self.pairs = pairs
        self.poly_store = poly_store
        self.kalshi = kalshi
        self.poly_taker_rate = poly_taker_rate
        self.min_zero_fee_edge = min_zero_fee_edge
        self.active: dict[str, ShadowQuote] = {}
        self.quote_seq = 0
        self.stats = Stats()
        self.last_summary = 0.0
        self.current_quote_feasible = 0
        self.current_fee_formula_positive = 0
        self.current_fee_formula_gte_1c = 0
        self.current_fee_formula_gte_2c = 0
        self.current_fee_formula_gte_3c = 0
        self.current_zero_fee_gte_1c = 0
        self.current_zero_fee_gte_2c = 0
        self.current_zero_fee_gte_3c = 0

    def _route_candidates(self, pair: Any, pquote: Any, kquote: Any) -> list[dict[str, Any]]:
        p = _levels(pquote, pair.poly.side)
        k = _levels(kquote, pair.kalshi.side)
        if p is None or k is None:
            return []
        out: list[dict[str, Any]] = []
        for maker_leg, maker_bid_key, maker_ask_key, maker_depth_key, hedge_key in (
            ("PROP", "prop_bid", "prop_ask", "prop_bid_depth", "opp_ask"),
            ("OPP", "opp_bid", "opp_ask", "opp_bid_depth", "prop_ask"),
        ):
            mq = _maker_price(k[maker_bid_key], k[maker_ask_key], k[maker_depth_key])
            if mq is None:
                continue
            maker_price, quote_mode, queue_ahead = mq
            maker_side = pair.kalshi.side.upper() if maker_leg == "PROP" else _opposite(pair.kalshi.side)
            poly_hedge_side = _opposite(pair.poly.side) if maker_leg == "PROP" else pair.poly.side.upper()
            hedge_price = p[hedge_key]
            hedge_depth = p["opp_ask_depth" if maker_leg == "PROP" else "prop_ask_depth"]
            if hedge_depth < 1:
                continue
            hedge_fee = polymarket_taker_fee(1, hedge_price, self.poly_taker_rate)
            maker_fee = _kalshi_maker_fee_q1(maker_price)
            profit_maker_fee = ONE - maker_price - hedge_price - maker_fee - hedge_fee
            profit_zero_fee = ONE - maker_price - hedge_price - hedge_fee
            if profit_zero_fee < self.min_zero_fee_edge:
                continue
            out.append({
                "pair": pair,
                "group_key": pair.group_key,
                "ticker": pair.kalshi.venue_market_id,
                "maker_leg": maker_leg,
                "maker_side": maker_side,
                "maker_price": maker_price,
                "quote_mode": quote_mode,
                "queue_ahead": queue_ahead,
                "poly_slug": pair.poly.venue_market_id,
                "poly_hedge_side": poly_hedge_side,
                "poly_hedge_price": hedge_price,
                "poly_hedge_depth": hedge_depth,
                "maker_fee": maker_fee,
                "hedge_fee": hedge_fee,
                "profit_maker_fee": profit_maker_fee,
                "profit_zero_fee": profit_zero_fee,
            })
        return out

    async def quote_loop(self, scan_ms: int) -> None:
        while True:
            best: dict[str, dict[str, Any]] = {}
            for pair in self.pairs:
                p = self.poly_store.quote(pair.poly.venue_market_id, max_age_ms=120000)
                k = await self.kalshi.quote(pair.kalshi.venue_market_id, max_age_ms=30000)
                if p is None or k is None:
                    continue
                for row in self._route_candidates(pair, p, k):
                    cur = best.get(row["group_key"])
                    # Prefer fee-formula edge; zero-fee edge only breaks ties.
                    if cur is None or (row["profit_maker_fee"], row["profit_zero_fee"]) > (cur["profit_maker_fee"], cur["profit_zero_fee"]):
                        best[row["group_key"]] = row

            vals = list(best.values())
            self.current_quote_feasible = len(vals)
            self.current_fee_formula_positive = sum(1 for r in vals if r["profit_maker_fee"] > 0)
            self.current_fee_formula_gte_1c = sum(1 for r in vals if r["profit_maker_fee"] >= D("0.01"))
            self.current_fee_formula_gte_2c = sum(1 for r in vals if r["profit_maker_fee"] >= D("0.02"))
            self.current_fee_formula_gte_3c = sum(1 for r in vals if r["profit_maker_fee"] >= D("0.03"))
            self.current_zero_fee_gte_1c = sum(1 for r in vals if r["profit_zero_fee"] >= D("0.01"))
            self.current_zero_fee_gte_2c = sum(1 for r in vals if r["profit_zero_fee"] >= D("0.02"))
            self.current_zero_fee_gte_3c = sum(1 for r in vals if r["profit_zero_fee"] >= D("0.03"))

            now_ms = int(time.time() * 1000)
            # Cancel/reprice quotes no longer matching the current best route.
            for group, old in list(self.active.items()):
                new = best.get(group)
                changed = new is None or (
                    old.ticker != new["ticker"]
                    or old.maker_side != new["maker_side"]
                    or old.maker_price != new["maker_price"]
                    or old.poly_slug != new["poly_slug"]
                    or old.poly_hedge_side != new["poly_hedge_side"]
                )
                if changed:
                    self.active.pop(group, None)
                    if new is None:
                        self.stats.quote_cancels += 1
                    else:
                        self.stats.quote_reprices += 1

            for group, row in best.items():
                if group in self.active:
                    continue
                self.quote_seq += 1
                self.stats.quote_opens += 1
                self.active[group] = ShadowQuote(
                    quote_id=self.quote_seq,
                    group_key=group,
                    ticker=row["ticker"],
                    maker_side=row["maker_side"],
                    maker_price=row["maker_price"],
                    quote_mode=row["quote_mode"],
                    queue_ahead=row["queue_ahead"],
                    poly_slug=row["poly_slug"],
                    poly_hedge_side=row["poly_hedge_side"],
                    poly_hedge_price_at_open=row["poly_hedge_price"],
                    profit_maker_fee_at_open=row["profit_maker_fee"],
                    profit_zero_fee_at_open=row["profit_zero_fee"],
                    opened_ms=now_ms,
                    pair=row["pair"],
                )

            self._maybe_summary()
            await asyncio.sleep(scan_ms / 1000)

    async def trade_loop(self) -> None:
        while True:
            trade = await self.kalshi.trades.get()
            self.stats.trade_prints += 1
            candidates = [q for q in self.active.values() if q.ticker == trade.ticker]
            for q in candidates:
                # Our resting maker bid fills only against an opposite-outcome taker.
                if trade.taker_side != _opposite(q.maker_side):
                    continue
                self.stats.qualifying_opposite_taker_prints += 1
                trade_price = trade.yes_price if q.maker_side == "YES" else trade.no_price
                fill_reason: str | None = None
                if trade_price < q.maker_price:
                    fill_reason = "TRADE_THROUGH"
                elif trade_price == q.maker_price:
                    if q.quote_mode == "IMPROVE_BID_ONE_TICK":
                        if trade.count >= 1:
                            fill_reason = "EXACT_PRICE_EMPTY_TICK"
                    else:
                        q.queue_ahead -= trade.count
                        if q.queue_ahead < 0:
                            fill_reason = "EXACT_PRICE_QUEUE_EXHAUSTED"
                if fill_reason is not None:
                    self.active.pop(q.group_key, None)
                    self.stats.simulated_fills += 1
                    if fill_reason == "TRADE_THROUGH":
                        self.stats.trade_through_fills += 1
                    else:
                        self.stats.exact_price_fills += 1
                    self.stats.fill_quote_ages_ms.append(max(0, trade.received_ms - q.opened_ms))
                    await self._record_fill(q, trade, fill_reason)

    def _poly_hedge(self, q: ShadowQuote) -> tuple[Decimal, Decimal, int] | None:
        pq = self.poly_store.quote(q.poly_slug, max_age_ms=120000)
        if pq is None:
            return None
        p = _levels(pq, q.pair.poly.side)
        if p is None:
            return None
        if q.poly_hedge_side == q.pair.poly.side.upper():
            return p["prop_ask"], p["prop_ask_depth"], pq.received_ms
        return p["opp_ask"], p["opp_ask_depth"], pq.received_ms

    def _profit(self, maker_price: Decimal, hedge_price: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        hedge_fee = polymarket_taker_fee(1, hedge_price, self.poly_taker_rate)
        maker_fee = _kalshi_maker_fee_q1(maker_price)
        return (
            ONE - maker_price - hedge_price - maker_fee - hedge_fee,
            ONE - maker_price - hedge_price - hedge_fee,
            maker_fee,
            hedge_fee,
        )

    async def _record_fill(self, q: ShadowQuote, trade: PublicTrade, fill_reason: str) -> None:
        hedge = self._poly_hedge(q)
        if hedge is None:
            self.stats.hedge_missing_at_fill += 1
            logger.warning("KPMM_SIM_FILL %s", json.dumps({
                "quote_id": q.quote_id,
                "group_key": q.group_key,
                "ticker": q.ticker,
                "fill_reason": fill_reason,
                "maker_side": q.maker_side,
                "maker_price": str(q.maker_price),
                "trade_id": trade.trade_id,
                "trade_exchange_ts_ms": trade.exchange_ts_ms,
                "trade_received_ms": trade.received_ms,
                "quote_age_ms": max(0, trade.received_ms - q.opened_ms),
                "hedge_available": False,
                "capacity_credit_usd_per_day": 0,
                "read_only": True,
            }, separators=(",", ":")))
            return
        hedge_price, hedge_depth, hedge_received_ms = hedge
        profit_fee, profit_zero, maker_fee, hedge_fee = self._profit(q.maker_price, hedge_price)
        for target, key in ((D("0.01"), "1c"), (D("0.02"), "2c"), (D("0.03"), "3c")):
            if profit_fee >= target:
                self.stats.immediate_gte_maker_fee[key] += 1
            if profit_zero >= target:
                self.stats.immediate_gte_zero_fee[key] += 1
        payload = {
            "quote_id": q.quote_id,
            "group_key": q.group_key,
            "ticker": q.ticker,
            "maker_side": q.maker_side,
            "maker_price": str(q.maker_price),
            "maker_quote_mode": q.quote_mode,
            "fill_reason": fill_reason,
            "queue_ahead_after": str(q.queue_ahead),
            "quote_age_ms": max(0, trade.received_ms - q.opened_ms),
            "trade_id": trade.trade_id,
            "trade_count": str(trade.count),
            "trade_taker_side": trade.taker_side,
            "trade_exchange_ts_ms": trade.exchange_ts_ms,
            "trade_received_ms": trade.received_ms,
            "poly_slug": q.poly_slug,
            "poly_hedge_side": q.poly_hedge_side,
            "poly_hedge_price_at_open": str(q.poly_hedge_price_at_open),
            "poly_hedge_price_at_fill": str(hedge_price),
            "poly_hedge_depth_at_fill": str(hedge_depth),
            "poly_book_received_ms": hedge_received_ms,
            "maker_fee_if_applicable": str(maker_fee),
            "poly_taker_fee": str(hedge_fee),
            "profit_with_general_maker_fee": str(profit_fee),
            "profit_zero_maker_fee_upper": str(profit_zero),
            "rules_complete": False,
            "maker_fee_applicability_certified": False,
            "capacity_credit_usd_per_day": 0,
            "read_only": True,
            "order_entry": False,
        }
        logger.warning("KPMM_SIM_FILL %s", json.dumps(payload, separators=(",", ":")))
        for delay_ms in (80, 250, 500):
            asyncio.create_task(self._delayed_hedge(q, trade, delay_ms))

    async def _delayed_hedge(self, q: ShadowQuote, trade: PublicTrade, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000)
        hedge = self._poly_hedge(q)
        if hedge is None:
            logger.info("KPMM_HEDGE_SURVIVAL %s", json.dumps({
                "quote_id": q.quote_id,
                "delay_ms": delay_ms,
                "hedge_available": False,
                "read_only": True,
            }, separators=(",", ":")))
            return
        hedge_price, hedge_depth, received_ms = hedge
        profit_fee, profit_zero, maker_fee, hedge_fee = self._profit(q.maker_price, hedge_price)
        logger.info("KPMM_HEDGE_SURVIVAL %s", json.dumps({
            "quote_id": q.quote_id,
            "group_key": q.group_key,
            "delay_ms": delay_ms,
            "maker_price": str(q.maker_price),
            "poly_hedge_price": str(hedge_price),
            "poly_hedge_depth": str(hedge_depth),
            "poly_book_received_ms": received_ms,
            "maker_fee_if_applicable": str(maker_fee),
            "poly_taker_fee": str(hedge_fee),
            "profit_with_general_maker_fee": str(profit_fee),
            "profit_zero_maker_fee_upper": str(profit_zero),
            "gte_1c_general_maker_fee": profit_fee >= D("0.01"),
            "gte_2c_general_maker_fee": profit_fee >= D("0.02"),
            "gte_3c_general_maker_fee": profit_fee >= D("0.03"),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    def _maybe_summary(self) -> None:
        now = time.monotonic()
        if now - self.last_summary < 15:
            return
        logger.warning("KPMM_FILL_SHADOW_SUMMARY %s", json.dumps({
            "classification": "PESSIMISTIC_PUBLIC_TRADE_FILL_SHADOW_RULE_INCOMPLETE_NOT_EXECUTION_ELIGIBLE",
            "matched_moneyline_pairs": len(self.pairs),
            "active_shadow_quotes": len(self.active),
            "current_quote_feasible_zero_fee_positive": self.current_quote_feasible,
            "current_general_maker_fee_positive": self.current_fee_formula_positive,
            "current_general_maker_fee_gte_1c": self.current_fee_formula_gte_1c,
            "current_general_maker_fee_gte_2c": self.current_fee_formula_gte_2c,
            "current_general_maker_fee_gte_3c": self.current_fee_formula_gte_3c,
            "current_zero_maker_fee_gte_1c": self.current_zero_fee_gte_1c,
            "current_zero_maker_fee_gte_2c": self.current_zero_fee_gte_2c,
            "current_zero_maker_fee_gte_3c": self.current_zero_fee_gte_3c,
            "quote_opens": self.stats.quote_opens,
            "quote_reprices": self.stats.quote_reprices,
            "quote_cancels": self.stats.quote_cancels,
            "kalshi_public_trades_seen": self.kalshi.public_trades_seen,
            "qualifying_opposite_taker_prints": self.stats.qualifying_opposite_taker_prints,
            "simulated_q1_fills": self.stats.simulated_fills,
            "trade_through_fills": self.stats.trade_through_fills,
            "exact_price_fills": self.stats.exact_price_fills,
            "hedge_missing_at_fill": self.stats.hedge_missing_at_fill,
            "immediate_gte_general_maker_fee": self.stats.immediate_gte_maker_fee,
            "immediate_gte_zero_maker_fee": self.stats.immediate_gte_zero_fee,
            "median_quote_age_at_fill_ms": _median(self.stats.fill_quote_ages_ms),
            "poly_connected": self.poly_store.connected,
            "poly_ws_updates": self.poly_store.ws_updates,
            "kalshi_initialized_books": self.kalshi.initialized_count,
            "kalshi_dropped_public_trades": self.kalshi.dropped_trades,
            "maker_fill_rule": "TRADE_THROUGH_OR_EXACT_PRICE_AFTER_DISPLAYED_QUEUE_EXHAUSTION_TOUCH_NEVER_FILLS",
            "maker_fee_model": "GENERAL_0.0175_FORMULA_AND_ZERO_FEE_UPPER_BOUND_APPLICABILITY_UNCERTIFIED",
            "rules_complete": False,
            "capacity_credit_usd_per_day": 0,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        self.last_summary = now


async def discover_moneyline_pairs() -> list[Any]:
    async with httpx.AsyncClient(timeout=10.0) as http:
        poly_events, kalshi_events, _ = await breadth._fetch_inputs(http)
    poly_sides = [
        s for s in breadth.normalize_polymarket_mlb(poly_events)
        if not is_clearly_partial_game_label(s.source_label)
        and not is_clearly_partial_game_label(s.venue_market_id)
    ]
    kalshi_sides = breadth.normalize_kalshi_mlb(kalshi_events)
    pairs, _, _ = breadth._pair_normal_event_sides(poly_sides, kalshi_sides)
    return [p for p in pairs if p.family == "moneyline"]


async def _wait_for_stop() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()


async def main_async(args: argparse.Namespace) -> None:
    pairs = await discover_moneyline_pairs()
    if not pairs:
        raise SystemExit("No clean K/P MLB moneyline pairs")
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")
    if not os.getenv("KALSHI_API_KEY_ID", "").strip() or not os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip():
        raise SystemExit("Kalshi read-only WS credentials are required")

    slugs = sorted({p.poly.venue_market_id for p in pairs})
    tickers = sorted({p.kalshi.venue_market_id for p in pairs})
    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    poly_store = PolyBookStore()
    kalshi = KalshiTradeBooks(tickers)
    shadow = MakerFillShadow(
        pairs,
        poly_store,
        kalshi,
        poly_taker_rate=D(os.getenv("POLYMARKET_TAKER_FEE_RATE", str(DEFAULT_POLY_TAKER_RATE))),
        min_zero_fee_edge=D(str(args.min_zero_fee_edge)),
    )

    logger.warning("KPMM_FILL_SHADOW_START %s", json.dumps({
        "moneyline_pairs": len(pairs),
        "poly_slugs": len(slugs),
        "kalshi_tickers": len(tickers),
        "scan_ms": args.scan_ms,
        "min_zero_fee_edge": str(args.min_zero_fee_edge),
        "maker_fill_model": "PESSIMISTIC_PUBLIC_TRADE_TRADE_THROUGH_PLUS_QUEUE_EXHAUSTION",
        "read_only": True,
        "order_entry": False,
        "capacity_credit_usd_per_day": 0,
    }, separators=(",", ":")))

    rest_task = asyncio.create_task(refresh_poly_books(poly_store, slugs, interval_s=10.0))
    poly_task = asyncio.create_task(run_poly_ws(poly_client, poly_store, slugs))
    kalshi_task = asyncio.create_task(kalshi.run())
    quote_task = asyncio.create_task(shadow.quote_loop(args.scan_ms))
    trade_task = asyncio.create_task(shadow.trade_loop())
    stop_task = asyncio.create_task(_wait_for_stop())
    tasks = {rest_task, poly_task, kalshi_task, quote_task, trade_task, stop_task}
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc
    for task in tasks:
        task.cancel()
    await kalshi.close()
    try:
        poly_client.close()
    except Exception:
        pass
    await asyncio.gather(*tasks, return_exceptions=True)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only pessimistic K/P MLB moneyline maker-fill shadow")
    parser.add_argument("--scan-ms", type=int, default=50)
    parser.add_argument("--min-zero-fee-edge", default="0.001")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
