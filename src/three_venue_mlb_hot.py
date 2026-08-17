"""Event-driven read-only MLB game-line hot scanner across three venues.

Cold discovery identifies exact normal-event counterpart keys. Polymarket US and
Kalshi books are then maintained from authenticated market-data WebSockets, while
Gemini prediction-market prices are refreshed from its single public active-events
endpoint. Every accepted book mutation wakes only the affected canonical keys and
re-runs fee-inclusive q=1 lock detection from in-memory books.

There is deliberately no order transport in this module. Positive output remains
research-only until full venue rule fingerprints and a separate exact execution
revalidation gate are implemented.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import NormalizedSide, normalize_gemini_mlb, normalize_polymarket_mlb
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import TopBook
from src.three_venue_mlb_price_scan import _dedupe, _eligible_keys, _gemini_contracts, _kalshi_series
from src.three_venue_mlb_snapshot_pricing import _series_multipliers
from src.three_venue_pricing_core import PricedQuote, gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import LockOpportunity, find_cross_venue_locks
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.three_venue_mlb_hot")
D = Decimal


@dataclass(frozen=True)
class HotContract:
    key: str
    sides: dict[str, NormalizedSide]
    kalshi_fee_multiplier: Decimal | None

    @property
    def venues(self) -> tuple[str, ...]:
        return tuple(sorted(self.sides))


@dataclass(frozen=True)
class HotUniverse:
    contracts: dict[str, HotContract]
    market_to_keys: dict[tuple[str, str], tuple[str, ...]]
    initial_gemini_events: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    @property
    def poly_slugs(self) -> list[str]:
        return sorted({c.sides["polymarket"].venue_market_id for c in self.contracts.values() if "polymarket" in c.sides})

    @property
    def kalshi_tickers(self) -> list[str]:
        return sorted({c.sides["kalshi"].venue_market_id for c in self.contracts.values() if "kalshi" in c.sides})


@dataclass
class ActiveSignal:
    opened_ms: int
    fingerprint: str
    payload: dict[str, Any]


class HotStats:
    def __init__(self) -> None:
        self.started_ms = int(time.time() * 1000)
        self.triggers = 0
        self.evaluations = 0
        self.comparable = 0
        self.positive = 0
        self.opens = 0
        self.moves = 0
        self.closes = 0
        self._latency_ms: deque[float] = deque(maxlen=10000)

    def observe_latency(self, value: float) -> None:
        self._latency_ms.append(max(0.0, value))

    def payload(self) -> dict[str, Any]:
        values = sorted(self._latency_ms)
        p95 = None if not values else values[min(len(values) - 1, max(0, int(len(values) * 0.95) - 1))]
        return {
            "uptime_s": round((int(time.time() * 1000) - self.started_ms) / 1000, 1),
            "triggers": self.triggers,
            "evaluations": self.evaluations,
            "comparable_evaluations": self.comparable,
            "positive_evaluations": self.positive,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "trigger_to_eval_samples": len(values),
            "trigger_to_eval_avg_ms": None if not values else round(sum(values) / len(values), 3),
            "trigger_to_eval_p95_ms": None if p95 is None else round(p95, 3),
            "trigger_to_eval_max_ms": None if not values else round(values[-1], 3),
        }


class GeminiHotStore:
    """Keep fee-inclusive q=1 Gemini quotes and wake only changed instruments."""

    def __init__(self, universe: HotUniverse, *, max_queue: int = 20000) -> None:
        self.universe = universe
        self.quotes: dict[str, PricedQuote] = {}
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.dropped_updates = 0
        self.refreshes = 0
        self.refresh_errors = 0
        self.last_refresh_ms = 0
        self.last_refresh_error = ""

    def _notify(self, market_id: str) -> None:
        event = BookUpdate(
            venue="gemini", market_id=market_id,
            received_ms=int(time.time() * 1000), mono_ns=time.perf_counter_ns(), kind="poll",
        )
        try:
            self.updates.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    def ingest(self, events: list[dict[str, Any]], *, notify: bool) -> int:
        contracts = _gemini_contracts(events)
        received_ms = int(time.time() * 1000)
        changed = 0
        for key, hot in self.universe.contracts.items():
            side = hot.sides.get("gemini")
            if side is None:
                continue
            book = gemini_contract_book(contracts.get(side.venue_market_id, {}))
            if book is None:
                continue
            row = make_priced_quote(
                side, book, received_ms=received_ms,
                depth_source="gemini_active_events_q1", category="sports_mlb_game",
            )
            if row is None:
                continue
            prior = self.quotes.get(key)
            sig = (row.raw_yes_ask, row.raw_no_ask, row.quote.yes_cost, row.quote.no_cost)
            prior_sig = None if prior is None else (prior.raw_yes_ask, prior.raw_no_ask, prior.quote.yes_cost, prior.quote.no_cost)
            self.quotes[key] = row
            if sig != prior_sig:
                changed += 1
                if notify:
                    self._notify(side.venue_market_id)
        self.refreshes += 1
        self.last_refresh_ms = received_ms
        return changed

    def quote(self, key: str, *, max_age_ms: int) -> PricedQuote | None:
        row = self.quotes.get(key)
        if row is None:
            return None
        if int(time.time() * 1000) - row.quote.received_ms > max_age_ms:
            return None
        return row


async def discover_hot_universe(http: httpx.AsyncClient) -> HotUniverse:
    gemini, poly_raw, kalshi = await asyncio.gather(
        fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
    )
    poly = full_game_only_events(poly_raw)
    rows = normalize_gemini_mlb(gemini) + normalize_polymarket_mlb(poly) + normalize_kalshi_mlb_eastern(kalshi)
    eligible = _eligible_keys(rows)

    grouped: dict[str, dict[str, list[NormalizedSide]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.contract_key in eligible:
            grouped[row.contract_key][row.identity.venue].append(row)

    ktoseries = _kalshi_series(kalshi)
    sem = asyncio.Semaphore(8)
    errors: list[dict[str, str]] = []
    series = sorted({ktoseries.get(row.venue_market_id, "") for row in rows if row.identity.venue == "kalshi" and row.contract_key in eligible and ktoseries.get(row.venue_market_id, "")})
    multipliers = await _series_multipliers(http, sem, series, errors)

    contracts: dict[str, HotContract] = {}
    ambiguous_keys = 0
    fee_blocked_keys = 0
    for key, by_venue in grouped.items():
        chosen: dict[str, NormalizedSide] = {}
        ambiguous = False
        for venue, candidates in by_venue.items():
            unique: dict[tuple[str, str], NormalizedSide] = {
                (row.venue_market_id, row.side): row for row in candidates
            }
            if len(unique) != 1:
                ambiguous = True
                break
            chosen[venue] = next(iter(unique.values()))
        if ambiguous:
            ambiguous_keys += 1
            continue
        if len(chosen) < 2:
            continue
        multiplier = None
        if "kalshi" in chosen:
            series_ticker = ktoseries.get(chosen["kalshi"].venue_market_id, "")
            multiplier = multipliers.get(series_ticker)
            if multiplier is None:
                fee_blocked_keys += 1
                continue
        contracts[key] = HotContract(key=key, sides=chosen, kalshi_fee_multiplier=multiplier)

    market_to_keys_raw: dict[tuple[str, str], set[str]] = defaultdict(set)
    for key, contract in contracts.items():
        for venue, side in contract.sides.items():
            market_to_keys_raw[(venue, side.venue_market_id)].add(key)
    market_to_keys = {k: tuple(sorted(v)) for k, v in market_to_keys_raw.items()}

    venue_sets = Counter("+".join(c.venues) for c in contracts.values())
    families = Counter(next(iter(c.sides.values())).identity.market_family for c in contracts.values())
    diagnostics = {
        "raw_events": {"gemini": len(gemini), "polymarket": len(poly_raw), "kalshi": len(kalshi)},
        "eligible_identity_keys": len(eligible),
        "hot_contracts": len(contracts),
        "ambiguous_keys_rejected": ambiguous_keys,
        "kalshi_fee_blocked_keys": fee_blocked_keys,
        "hot_by_venue_set": dict(venue_sets),
        "hot_by_family": dict(families),
        "polymarket_subscriptions": len({s.venue_market_id for c in contracts.values() for v, s in c.sides.items() if v == "polymarket"}),
        "kalshi_subscriptions": len({s.venue_market_id for c in contracts.values() for v, s in c.sides.items() if v == "kalshi"}),
        "gemini_instruments": len({s.venue_market_id for c in contracts.values() for v, s in c.sides.items() if v == "gemini"}),
        "fee_metadata_errors": errors[:20],
        "read_only": True,
        "order_entry": False,
    }
    return HotUniverse(contracts, market_to_keys, gemini, diagnostics)


def _book_from_poly(quote: Any) -> TopBook:
    return TopBook(quote.yes_ask, quote.yes_depth, quote.no_ask, quote.no_depth)


def _book_from_kalshi(quote: Any) -> TopBook:
    return TopBook(quote.yes_ask, quote.yes_depth, quote.no_ask, quote.no_depth)


async def priced_quotes_for_key(
    key: str,
    universe: HotUniverse,
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
    *,
    ws_book_max_age_ms: int,
    gemini_max_age_ms: int,
) -> list[PricedQuote]:
    hot = universe.contracts[key]
    out: list[PricedQuote] = []
    for venue, side in hot.sides.items():
        row: PricedQuote | None = None
        if venue == "gemini":
            row = gemini.quote(key, max_age_ms=gemini_max_age_ms)
        elif venue == "polymarket" and poly is not None and poly.connected:
            q = poly.quote(side.venue_market_id, max_age_ms=ws_book_max_age_ms)
            if q is not None:
                row = make_priced_quote(
                    side, _book_from_poly(q), received_ms=q.received_ms,
                    depth_source="polymarket_websocket_top_book", category="sports_mlb_game",
                )
        elif venue == "kalshi" and kalshi is not None:
            q = await kalshi.quote(side.venue_market_id, max_age_ms=ws_book_max_age_ms)
            if q is not None:
                row = make_priced_quote(
                    side, _book_from_kalshi(q), received_ms=q.received_ms,
                    kalshi_fee_multiplier=hot.kalshi_fee_multiplier,
                    depth_source="kalshi_websocket_top_book", category="sports_mlb_game",
                )
        if row is not None:
            out.append(row)
    return out


def _signal_fingerprint(opps: list[LockOpportunity]) -> str:
    return json.dumps([row.as_dict() for row in opps], sort_keys=True, separators=(",", ":"))


def _append_journal(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n")
        handle.flush(); os.fsync(handle.fileno())


async def evaluate_key(
    key: str,
    trigger: BookUpdate,
    universe: HotUniverse,
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
    active: dict[str, ActiveSignal],
    stats: HotStats,
    *,
    ws_book_max_age_ms: int,
    gemini_max_age_ms: int,
    journal_path: str | None,
) -> None:
    rows = await priced_quotes_for_key(
        key, universe, poly, kalshi, gemini,
        ws_book_max_age_ms=ws_book_max_age_ms,
        gemini_max_age_ms=gemini_max_age_ms,
    )
    stats.evaluations += 1
    if len({row.quote.venue for row in rows}) >= 2:
        stats.comparable += 1
    opps = _dedupe(find_cross_venue_locks(
        [row.quote for row in rows],
        min_unit_profit=D("0"),
        max_quote_skew_ms=None,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    now_ms = int(time.time() * 1000)
    prior = active.get(key)
    if not opps:
        if prior is not None:
            stats.closes += 1
            payload = {
                **prior.payload,
                "event":"CLOSE","closed_ms":now_ms,
                "lifetime_ms":now_ms-prior.opened_ms,
                "close_reason":"NO_FEE_INCLUSIVE_Q1_LOCK_IN_CURRENT_HOT_BOOKS",
                "trigger_venue":trigger.venue,"trigger_kind":trigger.kind,
                "read_only":True,"order_entry":False,
            }
            logger.info("THREE_VENUE_MLB_HOT_CLOSE %s", json.dumps(payload,separators=(",",":"),default=str))
            _append_journal(journal_path, payload)
            active.pop(key, None)
        return

    stats.positive += 1
    opps = opps[:10]
    ages = [max(0, now_ms-row.quote.received_ms) for row in rows]
    payload = {
        "event":"OPEN" if prior is None else "MOVE",
        "ts_ms":now_ms,
        "settlement_key":key,
        "venues":sorted({row.quote.venue for row in rows}),
        "quotes":[row.as_dict() for row in rows],
        "locks":[row.as_dict() for row in opps],
        "best_unit_profit":str(opps[0].unit_profit),
        "max_book_age_ms":max(ages) if ages else None,
        "trigger_venue":trigger.venue,"trigger_kind":trigger.kind,
        "rule_gate":"FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible":False,"read_only":True,"order_entry":False,
    }
    fingerprint = _signal_fingerprint(opps)
    if prior is not None and prior.fingerprint == fingerprint:
        return
    if prior is None:
        stats.opens += 1
        active[key] = ActiveSignal(now_ms, fingerprint, payload)
    else:
        stats.moves += 1
        prior.fingerprint = fingerprint
        prior.payload = payload
    logger.info("THREE_VENUE_MLB_HOT_%s %s", payload["event"], json.dumps(payload,separators=(",",":"),default=str))
    _append_journal(journal_path, payload)


async def _next_update(
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
) -> BookUpdate:
    tasks: list[asyncio.Task[BookUpdate]] = [asyncio.create_task(gemini.updates.get())]
    if poly is not None:
        tasks.append(asyncio.create_task(poly.updates.get()))
    if kalshi is not None:
        tasks.append(asyncio.create_task(kalshi.updates.get()))
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def _gemini_loop(store: GeminiHotStore, interval_s: float) -> None:
    backoff = interval_s
    async with httpx.AsyncClient(timeout=5.0) as http:
        while True:
            started = time.monotonic()
            try:
                events = await fetch_gemini_mlb(http)
                store.ingest(events, notify=True)
                store.last_refresh_error = ""
                backoff = interval_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                store.refresh_errors += 1
                store.last_refresh_error = f"{type(exc).__name__}:{str(exc)[:240]}"
                backoff = min(max(backoff * 2, interval_s), 10.0)
                logger.warning("THREE_VENUE_MLB_GEMINI_REFRESH_ERROR %s", store.last_refresh_error)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.05, backoff - elapsed))


async def _heartbeat(
    universe: HotUniverse,
    stats: HotStats,
    active: dict[str, ActiveSignal],
    poly: EventingPolyBookStore | None,
    kalshi: EventingKalshiCrossBooks | None,
    gemini: GeminiHotStore,
) -> None:
    while True:
        await asyncio.sleep(10)
        payload = {
            **universe.diagnostics,
            **stats.payload(),
            "active_signals":len(active),
            "poly_connected":None if poly is None else poly.connected,
            "poly_stored_books":0 if poly is None else poly.quote_count,
            "poly_ws_updates":0 if poly is None else poly.ws_updates,
            "poly_raw_ws_messages":0 if poly is None else poly.raw_ws_messages,
            "poly_dropped_updates":0 if poly is None else poly.dropped_updates,
            "kalshi_initialized_books":0 if kalshi is None else kalshi.initialized_count,
            "kalshi_dropped_updates":0 if kalshi is None else kalshi.dropped_updates,
            "gemini_cached_quotes":len(gemini.quotes),
            "gemini_refreshes":gemini.refreshes,
            "gemini_refresh_errors":gemini.refresh_errors,
            "gemini_last_refresh_age_ms":None if not gemini.last_refresh_ms else max(0,int(time.time()*1000)-gemini.last_refresh_ms),
            "gemini_last_refresh_error":gemini.last_refresh_error,
            "read_only":True,"order_entry":False,
        }
        logger.info("THREE_VENUE_MLB_HOT_SUMMARY %s", json.dumps(payload,separators=(",",":"),default=str))


async def run_hot_session(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        universe = await discover_hot_universe(http)
    if not universe.contracts:
        logger.warning("THREE_VENUE_MLB_HOT_EMPTY %s", json.dumps(universe.diagnostics,separators=(",",":")))
        await asyncio.sleep(min(args.discovery_refresh_seconds, 60.0))
        return

    if universe.poly_slugs:
        if not os.getenv("POLYMARKET_KEY_ID", "").strip() or not os.getenv("POLYMARKET_SECRET_KEY", "").strip():
            raise RuntimeError("Polymarket market-data WebSocket credentials are required")
    if universe.kalshi_tickers:
        if not os.getenv("KALSHI_API_KEY_ID", "").strip() or not (os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip() or os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()):
            raise RuntimeError("Kalshi market-data WebSocket credentials are required")

    poly: EventingPolyBookStore | None = EventingPolyBookStore() if universe.poly_slugs else None
    kalshi: EventingKalshiCrossBooks | None = EventingKalshiCrossBooks(universe.kalshi_tickers) if universe.kalshi_tickers else None
    gemini = GeminiHotStore(universe)
    gemini.ingest(universe.initial_gemini_events, notify=False)

    poly_client: PolymarketUS | None = None
    tasks: list[asyncio.Task[Any]] = []
    if poly is not None:
        async with httpx.AsyncClient(timeout=8.0) as seed_http:
            sem = asyncio.Semaphore(20)
            poly.notifications_enabled = False
            try:
                await asyncio.gather(*(_fetch_poly_book(seed_http, poly, slug, sem) for slug in universe.poly_slugs))
            finally:
                poly.notifications_enabled = True
        poly_client = PolymarketUS(
            key_id=os.environ["POLYMARKET_KEY_ID"],
            secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=10.0,
        )
        tasks.append(asyncio.create_task(run_poly_ws(poly_client, poly, universe.poly_slugs), name="poly_ws"))
    if kalshi is not None:
        tasks.append(asyncio.create_task(kalshi.run(), name="kalshi_ws"))
    gemini_task = asyncio.create_task(_gemini_loop(gemini, args.gemini_poll_seconds), name="gemini_poll")
    tasks.append(gemini_task)

    deadline = time.monotonic() + args.startup_timeout_seconds
    while time.monotonic() < deadline:
        feed_failure = next((task for task in tasks if task.done()), None)
        if feed_failure is not None:
            break
        poly_ready = poly is None or poly.connected
        kalshi_ready = kalshi is None or kalshi.initialized_count > 0
        if poly_ready and kalshi_ready:
            break
        await asyncio.sleep(0.025)

    for task in tasks:
        if task.done():
            exc = task.exception()
            if exc is not None:
                raise RuntimeError(f"hot feed task failed during startup: {exc}")
    if (poly is not None and not poly.connected) or (kalshi is not None and kalshi.initialized_count == 0):
        raise RuntimeError(
            f"hot startup timeout poly_connected={None if poly is None else poly.connected} "
            f"kalshi_initialized={0 if kalshi is None else kalshi.initialized_count}"
        )

    stats = HotStats()
    active: dict[str, ActiveSignal] = {}
    heartbeat = asyncio.create_task(_heartbeat(universe, stats, active, poly, kalshi, gemini), name="heartbeat")
    tasks.append(heartbeat)
    logger.info("THREE_VENUE_MLB_HOT_READY %s", json.dumps({
        **universe.diagnostics,
        "poly_seeded_books":0 if poly is None else poly.quote_count,
        "kalshi_initialized_books":0 if kalshi is None else kalshi.initialized_count,
        "gemini_cached_quotes":len(gemini.quotes),
        "gemini_poll_seconds":args.gemini_poll_seconds,
        "discovery_refresh_seconds":args.discovery_refresh_seconds,
        "read_only":True,"order_entry":False,
    },separators=(",",":"),default=str))

    # Evaluate every exact key once from the initialized in-memory state.
    initial_trigger = BookUpdate("startup","startup",int(time.time()*1000),time.perf_counter_ns(),"startup")
    for key in universe.contracts:
        await evaluate_key(
            key, initial_trigger, universe, poly, kalshi, gemini, active, stats,
            ws_book_max_age_ms=args.ws_book_max_age_ms,
            gemini_max_age_ms=args.gemini_max_age_ms,
            journal_path=args.journal_path or None,
        )

    end_at = time.monotonic() + args.discovery_refresh_seconds
    try:
        while time.monotonic() < end_at:
            for task in tasks:
                if task.done() and task is not heartbeat:
                    exc = task.exception()
                    raise RuntimeError(f"hot feed task exited: {exc or task.get_name()}")
            try:
                trigger = await asyncio.wait_for(_next_update(poly, kalshi, gemini), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            stats.triggers += 1
            keys = universe.market_to_keys.get((trigger.venue, trigger.market_id), ())
            for key in keys:
                await evaluate_key(
                    key, trigger, universe, poly, kalshi, gemini, active, stats,
                    ws_book_max_age_ms=args.ws_book_max_age_ms,
                    gemini_max_age_ms=args.gemini_max_age_ms,
                    journal_path=args.journal_path or None,
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
            await run_hot_session(args)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("THREE_VENUE_MLB_HOT_SESSION_ERROR %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--gemini-poll-seconds", type=float, default=1.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--ws-book-max-age-ms", type=int, default=60000)
    parser.add_argument("--gemini-max-age-ms", type=int, default=2500)
    parser.add_argument("--journal-path", default="")
    args = parser.parse_args()
    if args.discovery_refresh_seconds < 60:
        raise SystemExit("discovery refresh must be >= 60 seconds")
    if args.gemini_poll_seconds < 0.25:
        raise SystemExit("gemini poll must be >= 0.25 seconds")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervise(args))


if __name__ == "__main__":
    main()
