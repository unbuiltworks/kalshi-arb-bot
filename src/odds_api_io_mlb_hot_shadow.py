"""Read-only Odds-API.io + Gemini/Kalshi/Polymarket MLB hot shadow.

One Odds-API.io WebSocket connection supplies DraftKings/FanDuel updates. A single
league-scoped REST event discovery call seeds event identity; no sportsbook odds
polling is used. Prediction-market books reuse the existing event-driven hot
stores. Every fresh sportsbook market mutation re-evaluates only the matching
canonical contract key. No order transport exists in this module.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx
from polymarket_us import PolymarketUS
import websockets

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.mlb_game_side_normalization import canonical_team
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.the_odds_api_mlb_shadow import _half, _key, _mk_quote, _prob_cost
from src.three_venue_mlb_hot import (
    GeminiHotStore,
    HotStats,
    _gemini_loop,
    discover_hot_universe,
    priced_quotes_for_key,
)
from src.three_venue_mlb_price_scan import _dedupe
from src.universal_prediction_quant import MarketQuote, find_cross_venue_locks
from src.universal_sports_identity import SportsEventIdentity

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_hot_shadow")
D = Decimal
REST_EVENTS = "https://api.odds-api.io/v3/events"
WS_BASE = "wss://api.odds-api.io/v3/ws"


@dataclass
class RetailSignal:
    opened_ms: int
    fingerprint: str
    payload: dict[str, Any]


def _decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _event_start_ms(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return int(dt.timestamp() * 1000)


def _market_updated_ms(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return int(dt.timestamp() * 1000)


def _event_identity(raw: Mapping[str, Any]) -> SportsEventIdentity | None:
    home = canonical_team(raw.get("home"))
    away = canonical_team(raw.get("away"))
    start = _event_start_ms(raw.get("date"))
    if not home or not away or home == away or start is None:
        return None
    return SportsEventIdentity(
        sport="baseball",
        league="mlb",
        participants=tuple(sorted((home, away))),
        scheduled_start_ms=start,
    )


def _retail_book_name(raw: Any) -> str:
    return "".join(ch for ch in str(raw or "").strip().lower() if ch.isalnum())


def _retail_quote_payload(q: MarketQuote) -> dict[str, Any]:
    return {
        "venue": q.venue,
        "market_id": q.market_id,
        "settlement_key": q.settlement_key,
        "all_in_yes_cost": str(q.yes_cost),
        "all_in_no_cost": str(q.no_cost),
        "yes_depth": str(q.yes_depth),
        "no_depth": str(q.no_depth),
        "received_ms": q.received_ms,
        "depth_source": "odds_api_io_websocket_q1",
        "settlement_confidence": q.settlement_confidence,
    }


def _quotes_from_market(
    *,
    event_id: str,
    event: Mapping[str, Any],
    book_raw: str,
    market: Mapping[str, Any],
    received_ms: int,
    freshness_ms: int,
) -> list[MarketQuote]:
    event_ident = _event_identity(event)
    if event_ident is None:
        return []
    home = canonical_team(event.get("home"))
    away = canonical_team(event.get("away"))
    if not home or not away:
        return []
    book = _retail_book_name(book_raw)
    if not book:
        return []

    updated_ms = _market_updated_ms(market.get("updatedAt"))
    if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
        return []

    rows = market.get("odds")
    if not isinstance(rows, list):
        return []
    name = str(market.get("name") or "").strip().lower()
    out: list[MarketQuote] = []

    if name == "ml":
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            home_cost = _prob_cost(row.get("home"))
            away_cost = _prob_cost(row.get("away"))
            if home_cost is None or away_cost is None:
                continue
            home_key = _key(
                book=book, event=event_ident, family="moneyline", subject=home,
                stat="", comparator="YES", line=None,
            )
            away_key = _key(
                book=book, event=event_ident, family="moneyline", subject=away,
                stat="", comparator="YES", line=None,
            )
            out.append(_mk_quote(
                book=book,
                event_id=f"{event_id}:ml:{home}",
                settlement_key=home_key,
                yes_cost=home_cost,
                no_cost=away_cost,
                received_ms=received_ms,
            ))
            out.append(_mk_quote(
                book=book,
                event_id=f"{event_id}:ml:{away}",
                settlement_key=away_key,
                yes_cost=away_cost,
                no_cost=home_cost,
                received_ms=received_ms,
            ))
            break

    elif name == "spread":
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            home_point = _decimal(row.get("hdp"))
            home_cost = _prob_cost(row.get("home"))
            away_cost = _prob_cost(row.get("away"))
            if home_point is None or not _half(home_point) or home_cost is None or away_cost is None:
                continue
            home_key = _key(
                book=book, event=event_ident, family="spread", subject=home,
                stat="runs", comparator="GT", line=-home_point,
            )
            away_key = _key(
                book=book, event=event_ident, family="spread", subject=away,
                stat="runs", comparator="GT", line=home_point,
            )
            line_token = format(home_point.normalize(), "f")
            out.append(_mk_quote(
                book=book,
                event_id=f"{event_id}:spread:{home}:{line_token}",
                settlement_key=home_key,
                yes_cost=home_cost,
                no_cost=away_cost,
                received_ms=received_ms,
            ))
            out.append(_mk_quote(
                book=book,
                event_id=f"{event_id}:spread:{away}:{line_token}",
                settlement_key=away_key,
                yes_cost=away_cost,
                no_cost=home_cost,
                received_ms=received_ms,
            ))

    elif name == "totals":
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            point = _decimal(row.get("hdp"))
            over = _prob_cost(row.get("over"))
            under = _prob_cost(row.get("under"))
            if point is None or not _half(point) or over is None or under is None:
                continue
            key = _key(
                book=book, event=event_ident, family="game_total", subject="game",
                stat="runs", comparator="GT", line=point,
            )
            line_token = format(point.normalize(), "f")
            out.append(_mk_quote(
                book=book,
                event_id=f"{event_id}:total:{line_token}",
                settlement_key=key,
                yes_cost=over,
                no_cost=under,
                received_ms=received_ms,
            ))
    return out


class OddsIoStore:
    def __init__(self, eligible_keys: set[str], *, max_queue: int = 20000) -> None:
        self.eligible_keys = eligible_keys
        self.events: dict[str, dict[str, Any]] = {}
        self.quotes: dict[str, dict[str, MarketQuote]] = {}
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.dropped_updates = 0
        self.ws_messages = 0
        self.ws_updates = 0
        self.unknown_events = 0
        self.stale_markets = 0
        self.accepted_quotes = 0
        self.last_seq = 0
        self.connected = False
        self.by_book: Counter[str] = Counter()
        self._latency_ms: deque[int] = deque(maxlen=5000)
        self.rest_requests = 0
        self.rest_remaining = ""
        self.rest_limit = ""
        self.rest_reset = ""

    def set_events(self, rows: list[Mapping[str, Any]]) -> None:
        self.events = {
            str(row.get("id")): dict(row)
            for row in rows
            if row.get("id") is not None and _event_identity(row) is not None
        }

    def clear_quotes(self) -> None:
        self.quotes.clear()

    def _notify(self, key: str, book: str, received_ms: int) -> None:
        update = BookUpdate(
            venue=f"sportsbook:{book}",
            market_id=key,
            received_ms=received_ms,
            mono_ns=time.perf_counter_ns(),
            kind="websocket",
        )
        try:
            self.updates.put_nowait(update)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(update)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    def ingest(self, message: Mapping[str, Any], *, freshness_ms: int) -> int:
        self.ws_messages += 1
        kind = str(message.get("type") or "")
        seq = message.get("seq")
        try:
            seq_i = int(seq)
        except (TypeError, ValueError):
            seq_i = 0
        if seq_i > self.last_seq:
            self.last_seq = seq_i
        if kind not in {"created", "updated", "deleted", "no_markets"}:
            return 0

        event_id = str(message.get("id") or "")
        book = _retail_book_name(message.get("bookie") or message.get("bookmaker"))
        if not event_id or not book:
            return 0
        self.by_book[book] += 1

        if kind in {"deleted", "no_markets"}:
            removed = 0
            for key in list(self.quotes):
                if book in self.quotes[key] and self.quotes[key][book].market_id.startswith(f"{event_id}:"):
                    self.quotes[key].pop(book, None)
                    removed += 1
                    self._notify(key, book, int(time.time() * 1000))
                if not self.quotes[key]:
                    self.quotes.pop(key, None)
            return removed

        event = self.events.get(event_id)
        if event is None:
            self.unknown_events += 1
            return 0

        received_ms = int(time.time() * 1000)
        raw_ts = message.get("timestamp")
        try:
            msg_ts = float(raw_ts)
            if msg_ts > 10_000_000_000:
                msg_ts /= 1000.0
            latency = max(0, int((time.time() - msg_ts) * 1000))
            if latency < 120_000:
                self._latency_ms.append(latency)
        except (TypeError, ValueError):
            pass

        accepted = 0
        markets = message.get("markets")
        if not isinstance(markets, list):
            return 0
        for market in markets:
            if not isinstance(market, Mapping):
                continue
            updated_ms = _market_updated_ms(market.get("updatedAt"))
            if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
                self.stale_markets += 1
                continue
            for quote in _quotes_from_market(
                event_id=event_id,
                event=event,
                book_raw=book,
                market=market,
                received_ms=received_ms,
                freshness_ms=freshness_ms,
            ):
                if quote.settlement_key not in self.eligible_keys:
                    continue
                per_key = self.quotes.setdefault(quote.settlement_key, {})
                prior = per_key.get(book)
                signature = None if prior is None else (
                    prior.yes_cost, prior.no_cost, prior.market_id
                )
                next_signature = (quote.yes_cost, quote.no_cost, quote.market_id)
                per_key[book] = quote
                self.accepted_quotes += 1
                accepted += 1
                if next_signature != signature:
                    self._notify(quote.settlement_key, book, received_ms)
        if kind == "updated":
            self.ws_updates += 1
        return accepted

    def quotes_for_key(self, key: str, *, max_age_ms: int) -> list[MarketQuote]:
        now = int(time.time() * 1000)
        return [
            q for q in self.quotes.get(key, {}).values()
            if now - q.received_ms <= max_age_ms
        ]

    def latency_payload(self) -> dict[str, int | None]:
        vals = sorted(self._latency_ms)
        if not vals:
            return {"p50_ms": None, "p95_ms": None}
        return {
            "p50_ms": vals[len(vals) // 2],
            "p95_ms": vals[min(len(vals) - 1, int(len(vals) * 0.95))],
        }


async def fetch_mlb_events(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    league_slug: str,
    store: OddsIoStore,
) -> list[dict[str, Any]]:
    response = await http.get(
        REST_EVENTS,
        params={
            "apiKey": api_key,
            "sport": "baseball",
            "league": league_slug,
            "status": "pending,live",
        },
    )
    store.rest_requests += 1
    store.rest_remaining = response.headers.get("x-ratelimit-remaining", "")
    store.rest_limit = response.headers.get("x-ratelimit-limit", "")
    store.rest_reset = response.headers.get("x-ratelimit-reset", "")
    response.raise_for_status()
    body = response.json()
    rows = [dict(x) for x in body if isinstance(x, Mapping)] if isinstance(body, list) else []
    logger.info(
        "ODDS_API_IO_MLB_EVENT_SNAPSHOT %s",
        json.dumps({
            "events": len(rows),
            "rest_requests_this_process": store.rest_requests,
            "rate_limit_remaining": store.rest_remaining,
            "rate_limit_limit": store.rest_limit,
            "rate_limit_reset": store.rest_reset,
        }, separators=(",", ":")),
    )
    return rows


def _ws_url(*, api_key: str, league_slug: str, markets: str, last_seq: int = 0) -> str:
    params = {
        "apiKey": api_key,
        "leagues": league_slug,
        "markets": markets,
    }
    if last_seq > 0:
        params["lastSeq"] = str(last_seq)
    return f"{WS_BASE}?{urlencode(params)}"


async def run_odds_io_ws(
    store: OddsIoStore,
    *,
    api_key: str,
    league_slug: str,
    markets: str,
    freshness_ms: int,
) -> None:
    backoff = 1.0
    while True:
        try:
            url = _ws_url(
                api_key=api_key,
                league_slug=league_slug,
                markets=markets,
                last_seq=store.last_seq,
            )
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=8 * 1024 * 1024,
            ) as ws:
                store.connected = True
                backoff = 1.0
                async for raw in ws:
                    try:
                        message = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(message, Mapping):
                        continue
                    kind = str(message.get("type") or "")
                    if kind == "welcome":
                        logger.info(
                            "ODDS_API_IO_HOT_WELCOME %s",
                            json.dumps({
                                "bookmakers": message.get("bookmakers"),
                                "league_filter": message.get("leagues_filter") or message.get("league_filter"),
                                "market_filter": message.get("market_filter"),
                                "warning": message.get("warning"),
                                "read_only": True,
                                "order_entry": False,
                            }, separators=(",", ":"), default=str),
                        )
                        continue
                    if kind == "resync_required":
                        logger.warning(
                            "ODDS_API_IO_HOT_RESYNC_REQUIRED %s",
                            json.dumps({
                                "reason": message.get("reason"),
                                "last_seq": store.last_seq,
                                "action": "FLUSH_RETAIL_QUOTES_AND_RECONNECT_FRESH",
                            }, separators=(",", ":"), default=str),
                        )
                        store.clear_quotes()
                        store.last_seq = 0
                        break
                    store.ingest(message, freshness_ms=freshness_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.connected = False
            logger.warning(
                "ODDS_API_IO_HOT_WS_DISCONNECTED error_type=%s retry_s=%.1f last_seq=%s",
                type(exc).__name__, backoff, store.last_seq,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)
        finally:
            store.connected = False


async def _next_update(
    retail: OddsIoStore,
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
) -> BookUpdate:
    tasks: list[asyncio.Task[BookUpdate]] = [
        asyncio.create_task(retail.updates.get()),
        asyncio.create_task(gemini.updates.get()),
    ]
    if poly is not None:
        tasks.append(asyncio.create_task(poly.updates.get()))
    if kalshi is not None:
        tasks.append(asyncio.create_task(kalshi.updates.get()))
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


def _retail_prediction_locks(quotes: list[MarketQuote]) -> list[Any]:
    locks = _dedupe(find_cross_venue_locks(
        quotes,
        min_unit_profit=D("0"),
        max_quote_skew_ms=None,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    return [
        lock for lock in locks
        if str(lock.yes_venue).startswith("sportsbook:") != str(lock.no_venue).startswith("sportsbook:")
    ]


async def evaluate_key(
    key: str,
    trigger: BookUpdate,
    universe: Any,
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
    retail: OddsIoStore,
    active: dict[str, RetailSignal],
    stats: HotStats,
    *,
    direct_ws_age_ms: int,
    gemini_age_ms: int,
    retail_age_ms: int,
) -> None:
    direct = await priced_quotes_for_key(
        key,
        universe,
        poly,
        kalshi,
        gemini,
        ws_book_max_age_ms=direct_ws_age_ms,
        gemini_max_age_ms=gemini_age_ms,
    )
    retail_quotes = retail.quotes_for_key(key, max_age_ms=retail_age_ms)
    quotes = [row.quote for row in direct] + retail_quotes
    stats.evaluations += 1
    if retail_quotes and direct:
        stats.comparable += 1

    locks = _retail_prediction_locks(quotes)
    now_ms = int(time.time() * 1000)
    prior = active.get(key)
    if not locks:
        if prior is not None:
            stats.closes += 1
            payload = {
                **prior.payload,
                "event": "CLOSE",
                "closed_ms": now_ms,
                "lifetime_ms": now_ms - prior.opened_ms,
                "close_reason": "NO_FRESH_RETAIL_VS_PREDICTION_LOCK",
                "trigger_venue": trigger.venue,
                "trigger_kind": trigger.kind,
                "read_only": True,
                "order_entry": False,
            }
            logger.info(
                "ODDS_API_IO_MLB_HOT_CLOSE %s",
                json.dumps(payload, separators=(",", ":"), default=str),
            )
            active.pop(key, None)
        return

    stats.positive += 1
    locks = locks[:10]
    payload = {
        "event": "OPEN" if prior is None else "MOVE",
        "ts_ms": now_ms,
        "settlement_key": key,
        "trigger_venue": trigger.venue,
        "trigger_kind": trigger.kind,
        "direct_quotes": [row.as_dict() for row in direct],
        "retail_quotes": [_retail_quote_payload(q) for q in retail_quotes],
        "locks": [row.as_dict() for row in locks],
        "best_unit_profit": str(locks[0].unit_profit),
        "rule_gate": "SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
    }
    fingerprint = json.dumps(payload["locks"], sort_keys=True, separators=(",", ":"))
    if prior is not None and prior.fingerprint == fingerprint:
        return
    if prior is None:
        stats.opens += 1
        active[key] = RetailSignal(now_ms, fingerprint, payload)
    else:
        stats.moves += 1
        prior.fingerprint = fingerprint
        prior.payload = payload
    logger.info(
        "ODDS_API_IO_MLB_HOT_%s %s",
        payload["event"],
        json.dumps(payload, separators=(",", ":"), default=str),
    )


async def heartbeat(
    universe: Any,
    stats: HotStats,
    active: dict[str, RetailSignal],
    retail: OddsIoStore,
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
) -> None:
    while True:
        await asyncio.sleep(15)
        payload = {
            **universe.diagnostics,
            **stats.payload(),
            "active_retail_prediction_signals": len(active),
            "odds_io_connected": retail.connected,
            "odds_io_events": len(retail.events),
            "odds_io_cached_contract_keys": len(retail.quotes),
            "odds_io_ws_messages": retail.ws_messages,
            "odds_io_ws_updates": retail.ws_updates,
            "odds_io_by_book": dict(retail.by_book),
            "odds_io_unknown_events": retail.unknown_events,
            "odds_io_stale_markets_rejected": retail.stale_markets,
            "odds_io_accepted_quotes": retail.accepted_quotes,
            "odds_io_last_seq": retail.last_seq,
            "odds_io_latency": retail.latency_payload(),
            "odds_io_rest_requests_this_process": retail.rest_requests,
            "odds_io_rate_limit_remaining": retail.rest_remaining,
            "odds_io_rate_limit_limit": retail.rest_limit,
            "odds_io_rate_limit_reset": retail.rest_reset,
            "poly_connected": None if poly is None else poly.connected,
            "kalshi_initialized_books": 0 if kalshi is None else kalshi.initialized_count,
            "gemini_cached_quotes": len(gemini.quotes),
            "read_only": True,
            "order_entry": False,
        }
        logger.info(
            "ODDS_API_IO_MLB_HOT_SUMMARY %s",
            json.dumps(payload, separators=(",", ":"), default=str),
        )


async def run_session(args: argparse.Namespace) -> None:
    api_key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_IO_API_KEY is required")

    async with httpx.AsyncClient(timeout=30.0) as http:
        universe = await discover_hot_universe(http)
    if not universe.contracts:
        logger.warning("ODDS_API_IO_MLB_HOT_EMPTY_DIRECT_UNIVERSE")
        await asyncio.sleep(min(args.discovery_refresh_seconds, 60.0))
        return

    if universe.poly_slugs:
        if not os.getenv("POLYMARKET_KEY_ID", "").strip() or not os.getenv("POLYMARKET_SECRET_KEY", "").strip():
            raise RuntimeError("Polymarket market-data credentials are required")
    if universe.kalshi_tickers:
        if not os.getenv("KALSHI_API_KEY_ID", "").strip() or not (
            os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip()
            or os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
        ):
            raise RuntimeError("Kalshi market-data credentials are required")

    retail = OddsIoStore(set(universe.contracts))
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await fetch_mlb_events(
            http,
            api_key=api_key,
            league_slug=args.league_slug,
            store=retail,
        )
    retail.set_events(events)

    poly: EventingPolyBookStore | None = EventingPolyBookStore() if universe.poly_slugs else None
    kalshi: EventingKalshiCrossBooks | None = EventingKalshiCrossBooks(universe.kalshi_tickers) if universe.kalshi_tickers else None
    gemini = GeminiHotStore(universe)
    gemini.ingest(universe.initial_gemini_events, notify=False)

    tasks: list[asyncio.Task[Any]] = []
    poly_client: PolymarketUS | None = None
    if poly is not None:
        async with httpx.AsyncClient(timeout=8.0) as seed_http:
            sem = asyncio.Semaphore(20)
            poly.notifications_enabled = False
            try:
                await asyncio.gather(*(
                    _fetch_poly_book(seed_http, poly, slug, sem)
                    for slug in universe.poly_slugs
                ))
            finally:
                poly.notifications_enabled = True
        poly_client = PolymarketUS(
            key_id=os.environ["POLYMARKET_KEY_ID"],
            secret_key=os.environ["POLYMARKET_SECRET_KEY"],
            timeout=10.0,
        )
        tasks.append(asyncio.create_task(
            run_poly_ws(poly_client, poly, universe.poly_slugs),
            name="poly_ws",
        ))
    if kalshi is not None:
        tasks.append(asyncio.create_task(kalshi.run(), name="kalshi_ws"))

    tasks.append(asyncio.create_task(
        _gemini_loop(gemini, args.gemini_poll_seconds),
        name="gemini_poll",
    ))
    tasks.append(asyncio.create_task(
        run_odds_io_ws(
            retail,
            api_key=api_key,
            league_slug=args.league_slug,
            markets=args.markets,
            freshness_ms=args.retail_market_freshness_ms,
        ),
        name="odds_io_ws",
    ))

    deadline = time.monotonic() + args.startup_timeout_seconds
    while time.monotonic() < deadline:
        failed = next((task for task in tasks if task.done()), None)
        if failed is not None:
            break
        poly_ready = poly is None or poly.connected
        kalshi_ready = kalshi is None or kalshi.initialized_count > 0
        if poly_ready and kalshi_ready and retail.connected:
            break
        await asyncio.sleep(0.025)

    for task in tasks:
        if task.done():
            exc = task.exception()
            if exc is not None:
                raise RuntimeError(f"feed task failed during startup: {exc}")
    if (
        (poly is not None and not poly.connected)
        or (kalshi is not None and kalshi.initialized_count == 0)
        or not retail.connected
    ):
        raise RuntimeError(
            f"hot startup timeout poly_connected={None if poly is None else poly.connected} "
            f"kalshi_initialized={0 if kalshi is None else kalshi.initialized_count} "
            f"odds_io_connected={retail.connected}"
        )

    stats = HotStats()
    active: dict[str, RetailSignal] = {}
    hb = asyncio.create_task(
        heartbeat(universe, stats, active, retail, poly, kalshi, gemini),
        name="heartbeat",
    )
    tasks.append(hb)
    logger.info(
        "ODDS_API_IO_MLB_HOT_READY %s",
        json.dumps({
            **universe.diagnostics,
            "odds_io_event_map": len(retail.events),
            "odds_io_markets": args.markets.split(","),
            "discovery_refresh_seconds": args.discovery_refresh_seconds,
            "retail_market_freshness_ms": args.retail_market_freshness_ms,
            "retail_quote_max_age_ms": args.retail_quote_max_age_ms,
            "rest_budget_target_requests_per_hour": round(3600 / args.discovery_refresh_seconds, 2),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )

    end_at = time.monotonic() + args.discovery_refresh_seconds
    try:
        while time.monotonic() < end_at:
            for task in tasks:
                if task.done() and task is not hb:
                    exc = task.exception()
                    raise RuntimeError(f"hot feed task exited: {exc or task.get_name()}")
            try:
                trigger = await asyncio.wait_for(
                    _next_update(retail, poly, kalshi, gemini),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                continue
            stats.triggers += 1
            if trigger.venue.startswith("sportsbook:"):
                keys = (trigger.market_id,) if trigger.market_id in universe.contracts else ()
            else:
                keys = universe.market_to_keys.get((trigger.venue, trigger.market_id), ())
            for key in keys:
                await evaluate_key(
                    key,
                    trigger,
                    universe,
                    poly,
                    kalshi,
                    gemini,
                    retail,
                    active,
                    stats,
                    direct_ws_age_ms=args.direct_ws_book_max_age_ms,
                    gemini_age_ms=args.gemini_max_age_ms,
                    retail_age_ms=args.retail_quote_max_age_ms,
                )
                stats.observe_latency((time.perf_counter_ns() - trigger.mono_ns) / 1_000_000)
    finally:
        for task in tasks:
            task.cancel()
        if kalshi is not None:
            await kalshi.close()
        if poly_client is not None:
            try:
                poly_client.close()
            except Exception:
                pass
        await asyncio.gather(*tasks, return_exceptions=True)


async def supervise(args: argparse.Namespace) -> None:
    backoff = 1.0
    while True:
        try:
            await run_session(args)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ODDS_API_IO_MLB_HOT_SESSION_ERROR %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-slug", default="usa-mlb")
    parser.add_argument("--markets", default="ML,Spread,Totals")
    parser.add_argument("--discovery-refresh-seconds", type=float, default=600.0)
    parser.add_argument("--gemini-poll-seconds", type=float, default=1.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--direct-ws-book-max-age-ms", type=int, default=60000)
    parser.add_argument("--gemini-max-age-ms", type=int, default=2500)
    parser.add_argument("--retail-market-freshness-ms", type=int, default=3000)
    parser.add_argument("--retail-quote-max-age-ms", type=int, default=3000)
    args = parser.parse_args()
    if args.discovery_refresh_seconds < 600:
        raise SystemExit("discovery refresh must be >= 600 seconds to protect free REST quota")
    if args.gemini_poll_seconds < 0.25:
        raise SystemExit("gemini poll must be >= 0.25 seconds")
    if args.retail_market_freshness_ms < 500 or args.retail_quote_max_age_ms < 500:
        raise SystemExit("retail freshness windows must be >= 500ms")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    asyncio.run(supervise(args))


if __name__ == "__main__":
    main()
