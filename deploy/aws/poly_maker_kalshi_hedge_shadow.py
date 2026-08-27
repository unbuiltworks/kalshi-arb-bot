#!/usr/bin/env python3
"""Read-only Polymarket-maker -> Kalshi-taker sports shadow.

AWS-only measurement code. It intentionally lives outside src/ so the existing
Railway/AWS strategy-parity invariant stays byte-for-byte intact.

Touch never counts as a fill. Actual Polymarket trade prints consume displayed
queue, trade-through proves fill, and cancellations ahead are never credited.
After a simulated q=1 fill, the q=1 Kalshi hedge is marked at
0/20/40/60/80/120/250/500ms. No order-entry code is imported or called.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import logging
import os
import signal
import statistics
import time
from typing import Any

import httpx
import websockets
from polymarket_us import PolymarketUS

from config.settings import WS_PING_INTERVAL_S
from src import sports_kp_generic_mlb_game_breadth as breadth
from src.kalshi_cross_book_v2 import KalshiCrossBooks
from src.polymarket_kalshi_shadow import kalshi_taker_fee
from src.polymarket_kalshi_shadow_v2 import PolyBookStore, refresh_poly_books
from src.sports_kp_generic_mlb_game_breadth_fullgame import is_clearly_partial_game_label
from src.sports_kp_generic_mlb_game_maker_census import _levels, _maker_price

logger = logging.getLogger("kalshi_arb.aws_poly_maker_kalshi_hedge_shadow")
D = Decimal
ONE = D("1")
DELAYS_MS = (0, 20, 40, 60, 80, 120, 250, 500)
TARGETS = ((D("0.01"), "1c"), (D("0.02"), "2c"), (D("0.03"), "3c"))
POLY_RECONNECT_MS = 2500
EXEC_HEALTH_MS = 1000


def _opposite(side: str) -> str:
    return "NO" if str(side).upper() == "YES" else "YES"


def _amount(v: Any) -> Decimal | None:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return D(str(v))
    except Exception:
        return None


def _median(xs: list[int]) -> float | None:
    return round(float(statistics.median(xs)), 3) if xs else None


@dataclass(frozen=True)
class PolyTrade:
    slug: str
    yes_price: Decimal
    quantity: Decimal
    taker_side: str
    taker_intent: str
    trade_time: str
    received_ms: int
    fingerprint: str


def _parse_trade(payload: Any) -> PolyTrade | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("trade"), dict):
        payload = payload["trade"]
    slug = str(payload.get("marketSlug") or payload.get("market_slug") or "")
    price = _amount(payload.get("price"))
    qty = _amount(payload.get("quantity"))
    taker = payload.get("taker") if isinstance(payload.get("taker"), dict) else {}
    side = str(taker.get("side") or taker.get("order_side") or "").upper()
    if side.endswith("_BUY"):
        side = "BUY"
    elif side.endswith("_SELL"):
        side = "SELL"
    intent = str(taker.get("intent") or taker.get("order_intent") or "").upper()
    trade_time = str(payload.get("tradeTime") or payload.get("trade_time") or "")
    if not slug or price is None or qty is None or qty <= 0 or side not in {"BUY", "SELL"}:
        return None
    if not (D("0") <= price <= D("1")):
        return None
    material = f"{slug}|{price}|{qty}|{side}|{intent}|{trade_time}"
    fp = hashlib.sha256(material.encode()).hexdigest()
    return PolyTrade(slug, price, qty, side, intent, trade_time, int(time.time() * 1000), fp)


def _consume_trade(*, maker_order_side: str, yes_limit: Decimal, quote_mode: str,
                   queue_ahead: Decimal, trade: PolyTrade) -> tuple[str | None, Decimal]:
    """Consume one public print against a hypothetical resting YES-instrument order."""
    maker_order_side = maker_order_side.upper()
    wanted_taker = "SELL" if maker_order_side == "BUY" else "BUY"
    if trade.taker_side != wanted_taker:
        return None, queue_ahead
    through = (
        maker_order_side == "BUY" and trade.yes_price < yes_limit
    ) or (
        maker_order_side == "SELL" and trade.yes_price > yes_limit
    )
    if through:
        return "TRADE_THROUGH", queue_ahead
    if trade.yes_price != yes_limit:
        return None, queue_ahead
    if quote_mode == "IMPROVE_BID_ONE_TICK":
        return ("EXACT_PRICE_EMPTY_TICK", queue_ahead) if trade.quantity >= 1 else (None, queue_ahead)
    remaining = queue_ahead - trade.quantity
    return ("EXACT_PRICE_QUEUE_EXHAUSTED", remaining) if remaining < 0 else (None, remaining)


class HealthKalshiBooks(KalshiCrossBooks):
    def __init__(self, tickers: list[str]) -> None:
        super().__init__(tickers)
        self.connected = False
        self.last_ws_message_ms = 0
        self.ws_messages = 0
        self.reconnects = 0

    async def _run_once(self) -> None:
        async with websockets.connect(
            self.ws_url,
            additional_headers=self._headers(),
            ping_interval=WS_PING_INTERVAL_S,
        ) as ws:
            self._ws = ws
            self.connected = True
            await ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": list(self.tickers),
                    "use_yes_price": False,
                },
            }))
            logger.info("PKMM_KALSHI_WS subscribed_tickers=%d", len(self.tickers))
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    self.last_ws_message_ms = int(time.time() * 1000)
                    self.ws_messages += 1
                    msg = json.loads(raw)
                    typ = msg.get("type")
                    if typ in {"orderbook_snapshot", "orderbook_delta"}:
                        self._validate_sequence(msg)
                    if typ == "orderbook_snapshot":
                        await self.apply_snapshot(msg)
                    elif typ == "orderbook_delta":
                        await self.apply_delta(msg)
                    elif typ == "error":
                        raise RuntimeError(f"Kalshi WS error: {msg.get('msg')}")
            finally:
                self.connected = False

    async def run(self) -> None:
        self._running = True
        backoff = 0.25
        while self._running:
            try:
                await self._run_once()
                backoff = 0.25
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reconnects += 1
                if "429" in str(exc):
                    backoff = max(backoff, 15.0)
                logger.warning("PKMM_KALSHI_WS_RESET reconnects=%d error=%r retry=%.2f",
                               self.reconnects, exc, backoff)
            if not self._running:
                return
            await self._invalidate_all()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


class PolyFeed:
    def __init__(self, client: PolymarketUS, store: PolyBookStore, slugs: list[str]) -> None:
        self.client = client
        self.store = store
        self.slugs = slugs
        self.trades: asyncio.Queue[PolyTrade] = asyncio.Queue(maxsize=50000)
        self.connected = False
        self.trade_updates = 0
        self.dropped = 0
        self.duplicates = 0
        self.reconnects = 0
        self.last_message_ms = 0
        self._recent = deque(maxlen=20000)
        self._seen: set[str] = set()

    def _on_book(self, data: Any) -> None:
        if self.store.ingest(data, source="ws"):
            self.last_message_ms = int(time.time() * 1000)

    def _on_trade(self, data: Any) -> None:
        t = _parse_trade(data)
        if t is None:
            return
        self.last_message_ms = t.received_ms
        if t.fingerprint in self._seen:
            self.duplicates += 1
            return
        if len(self._recent) == self._recent.maxlen:
            old = self._recent.popleft()
            self._seen.discard(old)
        self._recent.append(t.fingerprint)
        self._seen.add(t.fingerprint)
        self.trade_updates += 1
        try:
            self.trades.put_nowait(t)
        except asyncio.QueueFull:
            self.dropped += 1

    async def run(self) -> None:
        while True:
            ws = self.client.ws.markets()
            ws.on("market_data", self._on_book)
            ws.on("trade", self._on_trade)
            ws.on("error", lambda e: logger.warning("PKMM_POLY_WS_ERROR %r", e))
            started = int(time.time() * 1000)
            try:
                await ws.connect()
                self.connected = self.store.connected = True
                for i in range(0, len(self.slugs), 100):
                    chunk = self.slugs[i:i + 100]
                    await ws.subscribe(f"pkmm-books-{i//100}", "SUBSCRIPTION_TYPE_MARKET_DATA", chunk)
                    await ws.subscribe(f"pkmm-trades-{i//100}", "SUBSCRIPTION_TYPE_TRADE", chunk)
                logger.info("PKMM_POLY_WS_SUBSCRIBED slugs=%d books=true trades=true", len(self.slugs))
                while True:
                    await asyncio.sleep(0.25)
                    now = int(time.time() * 1000)
                    last = max(self.last_message_ms, int(getattr(self.store, "last_ws_message_ms", 0) or 0))
                    if last and now - last > POLY_RECONNECT_MS:
                        raise RuntimeError(f"poly_ws_stale silence_ms={now-last}")
                    if not last and now - started > 10000:
                        raise RuntimeError("poly_ws_no_initial_message")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reconnects += 1
                logger.warning("PKMM_POLY_WS_RECONNECT reconnects=%d error=%r", self.reconnects, exc)
            finally:
                self.connected = self.store.connected = False
                try:
                    await ws.close()
                except Exception:
                    pass
            await asyncio.sleep(0.5)


@dataclass
class Quote:
    quote_id: int
    key: str
    group_key: str
    family: str
    poly_slug: str
    maker_outcome: str
    maker_price: Decimal
    maker_order_side: str
    yes_limit: Decimal
    quote_mode: str
    queue_ahead: Decimal
    kalshi_ticker: str
    hedge_side: str
    hedge_open: Decimal
    profit_open: Decimal
    opened_ms: int
    pair: Any = field(repr=False)


@dataclass
class Bucket:
    completed: int = 0
    executable: int = 0
    ws_unhealthy: int = 0
    hedge_missing: int = 0
    depth_insufficient: int = 0
    pnl: Decimal = D("0")
    gte_1c: int = 0
    gte_2c: int = 0
    gte_3c: int = 0


class Shadow:
    def __init__(self, pairs: list[Any], pstore: PolyBookStore, pfeed: PolyFeed,
                 kalshi: HealthKalshiBooks, *, kalshi_rate: Decimal, min_edge: Decimal) -> None:
        self.pairs, self.pstore, self.pfeed, self.kalshi = pairs, pstore, pfeed, kalshi
        self.kalshi_rate, self.min_edge = kalshi_rate, min_edge
        self.active: dict[str, Quote] = {}
        self.seq = 0
        self.opens = self.reprices = self.cancels = 0
        self.qualifying_trades = self.fills = self.trade_through = self.exact = 0
        self.missing_at_fill = 0
        self.fill_ages: list[int] = []
        self.fills_family = {"moneyline": 0, "spread": 0, "game_total": 0}
        self.buckets = {d: Bucket() for d in DELAYS_MS}
        self.cur_gte = {"1c": 0, "2c": 0, "3c": 0}
        self.cur_family = {"moneyline": 0, "spread": 0, "game_total": 0}
        self.last_summary = 0.0

    def _routes(self, pair: Any, pq: Any, kq: Any) -> list[dict[str, Any]]:
        p, k = _levels(pq, pair.poly.side), _levels(kq, pair.kalshi.side)
        if p is None or k is None:
            return []
        out = []
        for leg, mb, ma, md, hk, hd in (
            ("PROP", "prop_bid", "prop_ask", "prop_bid_depth", "opp_ask", "opp_ask_depth"),
            ("OPP", "opp_bid", "opp_ask", "opp_bid_depth", "prop_ask", "prop_ask_depth"),
        ):
            mq = _maker_price(p[mb], p[ma], p[md])
            if mq is None or k[hd] < 1:
                continue
            maker_price, mode, queue = mq
            maker_outcome = pair.poly.side.upper() if leg == "PROP" else _opposite(pair.poly.side)
            hedge_side = _opposite(pair.kalshi.side) if leg == "PROP" else pair.kalshi.side.upper()
            hedge = k[hk]
            fee = kalshi_taker_fee(1, hedge, self.kalshi_rate)
            profit = ONE - maker_price - hedge - fee
            if profit < self.min_edge:
                continue
            order_side = "BUY" if maker_outcome == "YES" else "SELL"
            yes_limit = maker_price if maker_outcome == "YES" else ONE - maker_price
            out.append({
                "pair": pair, "key": f"{pair.poly.venue_market_id}|{maker_outcome}",
                "group": pair.group_key, "family": pair.family,
                "slug": pair.poly.venue_market_id, "maker_outcome": maker_outcome,
                "maker_price": maker_price, "order_side": order_side, "yes_limit": yes_limit,
                "mode": mode, "queue": queue, "ticker": pair.kalshi.venue_market_id,
                "hedge_side": hedge_side, "hedge": hedge, "hedge_depth": k[hd], "profit": profit,
            })
        return out

    async def quote_loop(self, scan_ms: int) -> None:
        while True:
            group_best: dict[str, dict[str, Any]] = {}
            for pair in self.pairs:
                pq = self.pstore.quote(pair.poly.venue_market_id, max_age_ms=120000)
                kq = await self.kalshi.quote(pair.kalshi.venue_market_id, max_age_ms=30000)
                if pq is None or kq is None:
                    continue
                for r in self._routes(pair, pq, kq):
                    old = group_best.get(r["group"])
                    if old is None or (r["profit"], r["hedge_depth"]) > (old["profit"], old["hedge_depth"]):
                        group_best[r["group"]] = r
            best: dict[str, dict[str, Any]] = {}
            for r in group_best.values():
                old = best.get(r["key"])
                if old is None or (r["profit"], r["hedge_depth"]) > (old["profit"], old["hedge_depth"]):
                    best[r["key"]] = r
            vals = list(best.values())
            self.cur_gte = {name: sum(r["profit"] >= t for r in vals) for t, name in TARGETS}
            self.cur_family = {f: sum(r["family"] == f for r in vals) for f in self.cur_family}
            now = int(time.time() * 1000)
            for key, old in list(self.active.items()):
                new = best.get(key)
                changed = new is None or (
                    old.group_key != new["group"] or old.maker_price != new["maker_price"] or
                    old.yes_limit != new["yes_limit"] or old.kalshi_ticker != new["ticker"] or
                    old.hedge_side != new["hedge_side"]
                )
                if changed:
                    self.active.pop(key, None)
                    if new is None: self.cancels += 1
                    else: self.reprices += 1
            for key, r in best.items():
                if key in self.active:
                    continue
                self.seq += 1; self.opens += 1
                self.active[key] = Quote(
                    self.seq, key, r["group"], r["family"], r["slug"], r["maker_outcome"],
                    r["maker_price"], r["order_side"], r["yes_limit"], r["mode"], r["queue"],
                    r["ticker"], r["hedge_side"], r["hedge"], r["profit"], now, r["pair"]
                )
            self._summary()
            await asyncio.sleep(scan_ms / 1000)

    async def trade_loop(self) -> None:
        while True:
            trade = await self.pfeed.trades.get()
            for q in [q for q in self.active.values() if q.poly_slug == trade.slug]:
                wanted = "SELL" if q.maker_order_side == "BUY" else "BUY"
                if trade.taker_side == wanted:
                    self.qualifying_trades += 1
                reason, q.queue_ahead = _consume_trade(
                    maker_order_side=q.maker_order_side, yes_limit=q.yes_limit,
                    quote_mode=q.quote_mode, queue_ahead=q.queue_ahead, trade=trade)
                if reason is None:
                    continue
                self.active.pop(q.key, None)
                self.fills += 1
                self.fills_family[q.family] = self.fills_family.get(q.family, 0) + 1
                self.trade_through += int(reason == "TRADE_THROUGH")
                self.exact += int(reason != "TRADE_THROUGH")
                self.fill_ages.append(max(0, trade.received_ms - q.opened_ms))
                logger.warning("PKMM_SIM_FILL %s", json.dumps({
                    "quote_id": q.quote_id, "group_key": q.group_key, "family": q.family,
                    "poly_slug": q.poly_slug, "maker_outcome": q.maker_outcome,
                    "maker_price": str(q.maker_price), "yes_limit_price": str(q.yes_limit),
                    "maker_order_side_yes_instrument": q.maker_order_side,
                    "fill_reason": reason, "queue_ahead_after": str(q.queue_ahead),
                    "quote_age_ms": max(0, trade.received_ms - q.opened_ms),
                    "poly_trade_yes_price": str(trade.yes_price), "poly_trade_quantity": str(trade.quantity),
                    "poly_trade_taker_side": trade.taker_side, "poly_trade_taker_intent": trade.taker_intent,
                    "kalshi_ticker": q.kalshi_ticker, "kalshi_hedge_side": q.hedge_side,
                    "profit_at_quote_open": str(q.profit_open), "read_only": True,
                    "order_entry": False, "capacity_credit_usd_per_day": 0,
                }, separators=(",", ":")))
                await self._mark(q, 0)
                for d in DELAYS_MS[1:]:
                    asyncio.create_task(self._delay_mark(q, d))

    async def _hedge(self, q: Quote) -> tuple[Decimal, Decimal, int] | None:
        kq = await self.kalshi.quote(q.kalshi_ticker, max_age_ms=30000)
        if kq is None:
            return None
        return (kq.yes_ask, kq.yes_depth, kq.received_ms) if q.hedge_side == "YES" else (kq.no_ask, kq.no_depth, kq.received_ms)

    def _healthy(self) -> tuple[bool, int | None]:
        if not self.kalshi.connected or not self.kalshi.last_ws_message_ms:
            return False, None
        silence = max(0, int(time.time() * 1000) - self.kalshi.last_ws_message_ms)
        return silence <= EXEC_HEALTH_MS, silence

    async def _delay_mark(self, q: Quote, delay: int) -> None:
        await asyncio.sleep(delay / 1000)
        await self._mark(q, delay)

    async def _mark(self, q: Quote, delay: int) -> None:
        b = self.buckets[delay]; b.completed += 1
        healthy, silence = self._healthy(); hedge = await self._hedge(q)
        status = "EXECUTABLE"; hp = hd = hrecv = fee = profit = None
        if not healthy:
            b.ws_unhealthy += 1; status = "WS_UNHEALTHY"
        elif hedge is None:
            b.hedge_missing += 1; status = "HEDGE_MISSING"
            if delay == 0: self.missing_at_fill += 1
        else:
            hp, hd, hrecv = hedge
            if hd < 1:
                b.depth_insufficient += 1; status = "DEPTH_INSUFFICIENT"
            else:
                fee = kalshi_taker_fee(1, hp, self.kalshi_rate)
                profit = ONE - q.maker_price - hp - fee
                b.executable += 1; b.pnl += profit
                b.gte_1c += int(profit >= D("0.01")); b.gte_2c += int(profit >= D("0.02")); b.gte_3c += int(profit >= D("0.03"))
        logger.info("PKMM_HEDGE_MARKOUT %s", json.dumps({
            "quote_id": q.quote_id, "family": q.family, "delay_ms": delay, "status": status,
            "kalshi_ws_healthy": healthy, "kalshi_global_ws_silence_ms": silence,
            "maker_price": str(q.maker_price), "kalshi_ticker": q.kalshi_ticker,
            "kalshi_hedge_side": q.hedge_side, "kalshi_hedge_price": str(hp) if hp is not None else None,
            "kalshi_hedge_depth": str(hd) if hd is not None else None, "kalshi_book_received_ms": hrecv,
            "kalshi_taker_fee": str(fee) if fee is not None else None,
            "profit_zero_poly_maker_fee": str(profit) if profit is not None else None,
            "read_only": True, "order_entry": False,
        }, separators=(",", ":")))

    def _mark_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"classification": "POLY_MAKER_KALSHI_TAKER_MARKOUT_SHADOW_RULE_INCOMPLETE",
            "capacity_credit_usd_per_day": 0, "rules_complete": False, "read_only": True, "order_entry": False}
        for d, b in self.buckets.items():
            avg = b.pnl / D(b.executable) if b.executable else None
            out[f"t{d}ms"] = {"completed": b.completed, "executable": b.executable,
                "executable_coverage": round(b.executable / b.completed, 6) if b.completed else 0.0,
                "ws_unhealthy": b.ws_unhealthy, "hedge_missing": b.hedge_missing,
                "depth_insufficient": b.depth_insufficient, "executable_pnl_usd": str(b.pnl),
                "avg_pnl_per_executable_fill": str(avg) if avg is not None else None,
                "gte_1c": b.gte_1c, "gte_2c": b.gte_2c, "gte_3c": b.gte_3c}
        return out

    def _summary(self) -> None:
        now = time.monotonic()
        if now - self.last_summary < 15:
            return
        ksil = max(0, int(time.time() * 1000) - self.kalshi.last_ws_message_ms) if self.kalshi.last_ws_message_ms else None
        plast = max(self.pfeed.last_message_ms, int(getattr(self.pstore, "last_ws_message_ms", 0) or 0))
        psil = max(0, int(time.time() * 1000) - plast) if plast else None
        logger.warning("PKMM_FILL_SHADOW_SUMMARY %s", json.dumps({
            "classification": "POLY_MAKER_KALSHI_TAKER_PESSIMISTIC_PUBLIC_TRADE_FILL_SHADOW",
            "matched_pairs": len(self.pairs), "matched_by_family": {f: sum(p.family == f for p in self.pairs) for f in self.cur_family},
            "active_shadow_quotes": len(self.active), "current_quote_feasible_positive": len(self.active),
            "current_quote_gte_1c": self.cur_gte["1c"], "current_quote_gte_2c": self.cur_gte["2c"], "current_quote_gte_3c": self.cur_gte["3c"],
            "current_quotes_by_family": self.cur_family, "quote_opens": self.opens, "quote_reprices": self.reprices, "quote_cancels": self.cancels,
            "poly_trade_prints_seen": self.pfeed.trade_updates, "poly_duplicate_trades": self.pfeed.duplicates, "poly_dropped_trades": self.pfeed.dropped,
            "qualifying_opposite_taker_prints": self.qualifying_trades, "simulated_q1_fills": self.fills,
            "fills_by_family": self.fills_family, "trade_through_fills": self.trade_through, "exact_price_fills": self.exact,
            "median_quote_age_at_fill_ms": _median(self.fill_ages), "hedge_missing_at_fill": self.missing_at_fill,
            "poly_connected": self.pfeed.connected, "poly_ws_updates": self.pstore.ws_updates, "poly_trade_updates": self.pfeed.trade_updates,
            "poly_ws_reconnects": self.pfeed.reconnects, "poly_global_ws_silence_ms": psil,
            "kalshi_connected": self.kalshi.connected, "kalshi_initialized_books": self.kalshi.initialized_count,
            "kalshi_ws_messages": self.kalshi.ws_messages, "kalshi_ws_reconnects": self.kalshi.reconnects, "kalshi_global_ws_silence_ms": ksil,
            "maker_fill_rule": "TRADE_THROUGH_OR_EXACT_PRICE_AFTER_DISPLAYED_QUEUE_EXHAUSTION_TOUCH_NEVER_FILLS_CANCELLATIONS_NOT_CREDITED",
            "polymarket_price_semantics": "TRADE_PRICE_ALWAYS_YES_INSTRUMENT_NO_MAKER_BID_IS_SELL_YES_AT_1_MINUS_NO_PRICE",
            "polymarket_maker_fee_model": "ZERO_FEE_REBATE_IGNORED", "rules_complete": False,
            "capacity_credit_usd_per_day": 0, "read_only": True, "order_entry": False,
        }, separators=(",", ":")))
        logger.warning("PKMM_MARKOUT_SUMMARY %s", json.dumps(self._mark_summary(), separators=(",", ":")))
        self.last_summary = now


async def discover_pairs() -> list[Any]:
    async with httpx.AsyncClient(timeout=10.0) as http:
        pe, ke, _ = await breadth._fetch_inputs(http)
    ps = [s for s in breadth.normalize_polymarket_mlb(pe)
          if not is_clearly_partial_game_label(s.source_label) and not is_clearly_partial_game_label(s.venue_market_id)]
    ks = breadth.normalize_kalshi_mlb(ke)
    pairs, ambiguous, expanded = breadth._pair_normal_event_sides(ps, ks)
    pairs = [p for p in pairs if p.family in {"moneyline", "spread", "game_total"}]
    logger.warning("PKMM_DISCOVERY matched=%d ambiguous_rejected=%d expanded_shared=%d", len(pairs), ambiguous, expanded)
    return pairs


async def main_async(args: argparse.Namespace) -> None:
    pairs = await discover_pairs()
    if not pairs:
        raise SystemExit("No clean K/P MLB normal-event pairs")
    key = os.getenv("POLYMARKET_KEY_ID", "").strip(); secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")
    if not os.getenv("KALSHI_API_KEY_ID", "").strip() or not os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip():
        raise SystemExit("Kalshi read-only WS credentials are required")
    slugs = sorted({p.poly.venue_market_id for p in pairs}); tickers = sorted({p.kalshi.venue_market_id for p in pairs})
    client = PolymarketUS(key_id=key, secret_key=secret, timeout=10.0)
    pstore = PolyBookStore(); pfeed = PolyFeed(client, pstore, slugs); kalshi = HealthKalshiBooks(tickers)
    shadow = Shadow(pairs, pstore, pfeed, kalshi,
                    kalshi_rate=D(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07")), min_edge=D(str(args.min_edge)))
    logger.warning("PKMM_START %s", json.dumps({"matched_pairs": len(pairs), "poly_slugs": len(slugs), "kalshi_tickers": len(tickers),
        "scan_ms": args.scan_ms, "min_edge": str(args.min_edge), "delays_ms": DELAYS_MS,
        "maker_venue": "POLYMARKET", "hedge_venue": "KALSHI",
        "maker_fill_model": "PESSIMISTIC_PUBLIC_TRADE_TRADE_THROUGH_PLUS_QUEUE_EXHAUSTION",
        "rules_complete": False, "capacity_credit_usd_per_day": 0, "read_only": True, "order_entry": False}, separators=(",", ":")))
    tasks = {
        asyncio.create_task(refresh_poly_books(pstore, slugs, interval_s=10.0)),
        asyncio.create_task(pfeed.run()), asyncio.create_task(kalshi.run()),
        asyncio.create_task(shadow.quote_loop(args.scan_ms)), asyncio.create_task(shadow.trade_loop()),
        asyncio.create_task(_wait_stop()),
    }
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in done:
        if not t.cancelled() and t.exception() is not None:
            raise t.exception()
    for t in tasks: t.cancel()
    await kalshi.close()
    try: client.close()
    except Exception: pass
    await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_stop() -> None:
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop.set)
        except NotImplementedError: pass
    await stop.wait()


def self_test() -> None:
    raw = {"marketSlug": "m", "price": {"value": "0.57"}, "quantity": {"value": "6"},
           "tradeTime": "t", "taker": {"side": "ORDER_SIDE_SELL", "intent": "ORDER_INTENT_SELL_LONG"}}
    t = _parse_trade(raw); assert t and t.taker_side == "SELL" and t.yes_price == D("0.57")
    reason, q = _consume_trade(maker_order_side="BUY", yes_limit=D("0.57"), quote_mode="JOIN_BEST_BID", queue_ahead=D("10"), trade=t)
    assert reason is None and q == D("4")
    t2 = PolyTrade("m", D("0.57"), D("5"), "SELL", "", "", 0, "x")
    reason, q = _consume_trade(maker_order_side="BUY", yes_limit=D("0.57"), quote_mode="JOIN_BEST_BID", queue_ahead=q, trade=t2)
    assert reason == "EXACT_PRICE_QUEUE_EXHAUSTED" and q == D("-1")
    tb = PolyTrade("m", D("0.56"), D("1"), "SELL", "", "", 0, "y")
    assert _consume_trade(maker_order_side="BUY", yes_limit=D("0.57"), quote_mode="JOIN_BEST_BID", queue_ahead=D("100"), trade=tb)[0] == "TRADE_THROUGH"
    # NO maker bid 0.57 == SELL YES at 0.43; a taker BUY print at 0.44 trades through it.
    tn = PolyTrade("m", D("0.44"), D("1"), "BUY", "", "", 0, "z")
    assert _consume_trade(maker_order_side="SELL", yes_limit=D("0.43"), quote_mode="JOIN_BEST_BID", queue_ahead=D("100"), trade=tn)[0] == "TRADE_THROUGH"
    assert _parse_trade({"trade": raw}) is not None
    print("PKMM_SELF_TEST_OK")


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--scan-ms", type=int, default=50); ap.add_argument("--min-edge", type=float, default=0.001); ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test: self_test(); return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
