"""Hot read-only DraftKings/FanDuel <-> Kalshi MLB player-prop shadow.

Odds-API.io supplies fresh Player Props over one websocket. Kalshi supplies exact
KXMLBHIT/KXMLBHRR/KXMLBTB/KXMLBKS books over its authenticated market-data websocket.
The join is fail-closed on event, player, stat family, threshold, scheduled start,
quote freshness, quote skew, depth, fee metadata, and a $5 capital ceiling.

This module intentionally omits Home Runs until KXMLBHR is registered in the shared
rule profile. Positive packets remain rule-pending research: no alerts or order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import json
import logging
import os
import re
import signal
import time
from typing import Any, Mapping

import httpx

from src import odds_api_io_mlb_hot_shadow as retail
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.strict_mlb_prop_adapters import FAMILY_SERIES, _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc
from src.strict_mlb_prop_snapshot import KALSHI, _d, _fetch_json, _fetch_kalshi_events, conservative_kalshi_fee

logger = logging.getLogger("kalshi_arb.odds_api_io_kalshi_mlb_prop_hot_shadow")
D = Decimal
ONE = D("1")
MAX_START_SKEW_MS = 5 * 60 * 1000

FAMILY_TO_STAT = {
    "baseball_player_hits": "hits",
    "baseball_player_hits_runs_rbis": "hits_runs_rbis",
    "baseball_player_total_bases": "total_bases",
    "baseball_player_strikeouts": "strikeouts",
}
STAT_TEXT = {
    "hits": "hits",
    "hit": "hits",
    "hits + runs + rbis": "hits_runs_rbis",
    "hits+runs+rbis": "hits_runs_rbis",
    "hits runs rbis": "hits_runs_rbis",
    "total bases": "total_bases",
    "strikeouts": "strikeouts",
    "strikeout": "strikeouts",
    "pitcher strikeouts": "strikeouts",
    "pitching strikeouts": "strikeouts",
}
SUPPORTED_SERIES = tuple(FAMILY_SERIES[f] for f in FAMILY_TO_STAT)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("’", "'").split())


def _decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _player_stat(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)\s*", text)
    if match is None:
        return None
    player = _norm(match.group(1))
    stat = STAT_TEXT.get(_norm(match.group(2)))
    if not player or stat is None:
        return None
    return player, stat


def sportsbook_threshold(hdp: Any) -> Decimal | None:
    """Convert an O/U half-line to the equivalent binary integer threshold.

    Example: Over 5.5 strikeouts == YES on 6+ strikeouts; Under 5.5 == NO on 6+.
    Non-half lines fail closed because push semantics would not be binary-equivalent.
    """
    line = _decimal(hdp)
    if line is None or line < D("0"):
        return None
    twice = line * D("2")
    if twice != twice.to_integral_value() or int(twice) % 2 != 1:
        return None
    return line.to_integral_value(rounding=ROUND_FLOOR) + ONE


def _decimal_cost(value: Any) -> Decimal | None:
    odds = _decimal(value)
    if odds is None or odds <= ONE:
        return None
    return ONE / odds


@dataclass(frozen=True)
class KalshiProp:
    event_id: str
    family: str
    stat: str
    player: str
    player_key: str
    threshold: Decimal
    ticker: str
    start_ms: int

    @property
    def lookup_key(self) -> tuple[str, str, str, str]:
        return (self.event_id, self.player_key, self.stat, format(self.threshold.normalize(), "f"))


@dataclass(frozen=True)
class RetailPropQuote:
    event_id: str
    book: str
    player: str
    player_key: str
    stat: str
    threshold: Decimal
    yes_cost: Decimal | None
    no_cost: Decimal | None
    received_ms: int
    source_updated_ms: int

    @property
    def lookup_key(self) -> tuple[str, str, str, str]:
        return (self.event_id, self.player_key, self.stat, format(self.threshold.normalize(), "f"))


def _event_matches_kalshi(event: Mapping[str, Any], event_ticker: str, k_start_ms: int) -> bool:
    ident = retail._event_identity(event)
    if ident is None or abs(ident.scheduled_start_ms - k_start_ms) > MAX_START_SKEW_MS:
        return False
    a, b = ident.participants
    suffix = str(event_ticker or "").upper()
    return suffix.endswith(a + b) or suffix.endswith(b + a)


def discover_kalshi_props(
    odds_events: list[Mapping[str, Any]],
    kalshi_events: list[Mapping[str, Any]],
) -> list[KalshiProp]:
    out: list[KalshiProp] = []
    seen: set[str] = set()
    for event in kalshi_events:
        family = next((f for f, s in FAMILY_SERIES.items() if s == str(event.get("series_ticker") or "") and f in FAMILY_TO_STAT), None)
        if family is None:
            continue
        event_ticker = str(event.get("event_ticker") or "")
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ident = _kalshi_identity(event, market, family)
            if ident is None:
                continue
            player, threshold = ident
            start = kalshi_event_start_utc(event, market)
            ticker = str(market.get("ticker") or "")
            if start is None or not ticker or ticker in seen:
                continue
            start_ms = int(start.timestamp() * 1000)
            matches = [str(row.get("id")) for row in odds_events if row.get("id") is not None and _event_matches_kalshi(row, event_ticker, start_ms)]
            if len(matches) != 1:
                continue
            seen.add(ticker)
            out.append(KalshiProp(
                event_id=matches[0], family=family, stat=FAMILY_TO_STAT[family],
                player=player, player_key=_name(player), threshold=threshold,
                ticker=ticker, start_ms=start_ms,
            ))
    return out


class PropOddsIoStore(retail.OddsIoStore):
    def __init__(self, eligible: Mapping[tuple[str, str, str, str], KalshiProp], *, max_queue: int = 50000) -> None:
        super().__init__(set(), max_queue=max_queue)
        self.eligible_props = dict(eligible)
        self.prop_quotes: dict[str, dict[str, RetailPropQuote]] = defaultdict(dict)
        self.prop_supported_rows = 0
        self.prop_matched_rows = 0
        self.prop_stale_markets = 0

    def ingest(self, message: Mapping[str, Any], *, freshness_ms: int) -> int:
        accepted = super().ingest(message, freshness_ms=freshness_ms)
        if str(message.get("type") or "") not in {"created", "updated"}:
            return accepted
        event_id = str(message.get("id") or "")
        event = self.events.get(event_id)
        book = retail._retail_book_name(message.get("bookie") or message.get("bookmaker"))
        if not event_id or not isinstance(event, Mapping) or not book:
            return accepted
        received_ms = int(time.time() * 1000)
        markets = message.get("markets")
        if not isinstance(markets, list):
            return accepted
        for market in markets:
            if not isinstance(market, Mapping) or _norm(market.get("name")) != "player props":
                continue
            updated_ms = retail._market_updated_ms(market.get("updatedAt"))
            if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
                self.prop_stale_markets += 1
                continue
            rows = market.get("odds")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                parts = _player_stat(row.get("label"))
                threshold = sportsbook_threshold(row.get("hdp"))
                if parts is None or threshold is None:
                    continue
                player, stat = parts
                self.prop_supported_rows += 1
                key = (event_id, _name(player), stat, format(threshold.normalize(), "f"))
                kprop = self.eligible_props.get(key)
                if kprop is None:
                    continue
                yes_cost = _decimal_cost(row.get("over"))
                no_cost = _decimal_cost(row.get("under"))
                if yes_cost is None and no_cost is None:
                    continue
                self.prop_matched_rows += 1
                quote = RetailPropQuote(
                    event_id=event_id, book=book, player=player, player_key=_name(player), stat=stat,
                    threshold=threshold, yes_cost=yes_cost, no_cost=no_cost,
                    received_ms=received_ms, source_updated_ms=updated_ms,
                )
                prior = self.prop_quotes[kprop.ticker].get(book)
                self.prop_quotes[kprop.ticker][book] = quote
                signature = None if prior is None else (prior.yes_cost, prior.no_cost, prior.source_updated_ms)
                next_signature = (quote.yes_cost, quote.no_cost, quote.source_updated_ms)
                if signature != next_signature:
                    self._notify(kprop.ticker, book, received_ms)
                accepted += 1
        return accepted

    def fresh_for_ticker(self, ticker: str, *, max_age_ms: int) -> list[RetailPropQuote]:
        now = int(time.time() * 1000)
        return [q for q in self.prop_quotes.get(ticker, {}).values() if now - q.received_ms <= max_age_ms]


def best_plan(
    retail_quote: RetailPropQuote,
    kalshi_quote: Any,
    *,
    kalshi_fee_multiplier: Decimal,
    max_quantity: int,
    max_capital: Decimal,
) -> dict[str, Any] | None:
    directions = []
    if retail_quote.yes_cost is not None:
        directions.append(("SPORTSBOOK_YES_KALSHI_NO", retail_quote.yes_cost, D(str(kalshi_quote.no_ask)), D(str(kalshi_quote.no_depth))))
    if retail_quote.no_cost is not None:
        directions.append(("SPORTSBOOK_NO_KALSHI_YES", retail_quote.no_cost, D(str(kalshi_quote.yes_ask)), D(str(kalshi_quote.yes_depth))))
    best = None
    for direction, sb_cost, k_price, k_depth in directions:
        qmax = min(max_quantity, int(k_depth))
        for q in range(1, qmax + 1):
            sportsbook_stake = D(q) * sb_cost
            if sportsbook_stake < D("0.10"):
                continue
            k_fee = conservative_kalshi_fee(q, k_price, kalshi_fee_multiplier)
            k_cost = D(q) * k_price + k_fee
            capital = sportsbook_stake + k_cost
            if capital > max_capital:
                continue
            profit = D(q) - capital
            row = {
                "direction": direction, "quantity": q,
                "sportsbook": retail_quote.book,
                "sportsbook_stake": str(sportsbook_stake),
                "sportsbook_cost_per_1_payout": str(sb_cost),
                "kalshi_price": str(k_price), "kalshi_fee_upper_bound": str(k_fee),
                "kalshi_cost": str(k_cost), "total_capital": str(capital),
                "conservative_locked_profit": str(profit),
                "conservative_roi": str(profit / capital) if capital > 0 else "0",
            }
            if best is None or D(row["conservative_locked_profit"]) > D(best["conservative_locked_profit"]):
                best = row
    return best


async def run(args: argparse.Namespace) -> None:
    api_key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ODDS_API_IO_API_KEY required")

    # First get current retail event identities, then independently discover exact Kalshi props.
    bootstrap_store = retail.OddsIoStore(set())
    async with httpx.AsyncClient(timeout=20.0) as http:
        odds_events = await retail.fetch_mlb_events(http, api_key=api_key, league_slug=args.league_slug, store=bootstrap_store)
        kalshi_events = await _fetch_kalshi_events(http)
        series_meta: dict[str, dict[str, Any]] = {}
        for series in SUPPORTED_SERIES:
            body = await _fetch_json(http, f"{KALSHI}/series/{series}")
            series_meta[series] = dict(body.get("series") or {})

    props = discover_kalshi_props(odds_events, kalshi_events)
    if not props:
        raise SystemExit("no exact Odds-API.io event <-> Kalshi player-prop inventory")
    eligible = {p.lookup_key: p for p in props}
    by_ticker = {p.ticker: p for p in props}
    store = PropOddsIoStore(eligible)
    store.set_events(odds_events)
    kstore = EventingKalshiCrossBooks(list(by_ticker), max_queue=50000)

    fee_multiplier: dict[str, Decimal] = {}
    for family, series in FAMILY_SERIES.items():
        if family not in FAMILY_TO_STAT:
            continue
        meta = series_meta.get(series) or {}
        fee_type = str(meta.get("fee_type") or "")
        mult = _d(meta.get("fee_multiplier"))
        if fee_type in {"quadratic", "quadratic_with_maker_fees"} and mult is not None:
            fee_multiplier[family] = mult

    logger.info("ODDS_IO_KALSHI_PROP_HOT_START %s", json.dumps({
        "kalshi_props": len(props),
        "by_family": dict(Counter(p.family for p in props)),
        "retail_events": len(odds_events),
        "max_book_age_ms": args.max_book_age_ms,
        "max_quote_skew_ms": args.max_quote_skew_ms,
        "max_quantity": args.max_quantity,
        "max_capital": str(args.max_capital),
        "rule_status": "BOOKMAKER_RULES_PENDING",
        "alert_eligible": False, "execution_eligible": False,
        "read_only": True, "order_entry": False,
    }, separators=(",", ":")))

    odds_task = asyncio.create_task(retail.run_odds_io_ws(
        store, api_key=api_key, league_slug=args.league_slug,
        markets="Player Props", freshness_ms=args.max_book_age_ms,
    ))
    kalshi_task = asyncio.create_task(kstore.run())
    stop_task = asyncio.create_task(_wait_for_stop())

    evaluations = comparable = positives = preferred = 0
    last_summary = time.monotonic()
    fingerprints: dict[tuple[str, str], tuple[Any, ...]] = {}

    async def evaluate(ticker: str, trigger: str) -> None:
        nonlocal evaluations, comparable, positives, preferred
        prop = by_ticker.get(ticker)
        if prop is None:
            return
        kq = await kstore.quote(ticker, max_age_ms=args.max_book_age_ms)
        if kq is None:
            return
        mult = fee_multiplier.get(prop.family)
        if mult is None:
            return
        for rq in store.fresh_for_ticker(ticker, max_age_ms=args.max_book_age_ms):
            evaluations += 1
            skew = abs(rq.received_ms - int(kq.received_ms))
            if skew > args.max_quote_skew_ms:
                continue
            comparable += 1
            plan = best_plan(rq, kq, kalshi_fee_multiplier=mult, max_quantity=args.max_quantity, max_capital=args.max_capital)
            if plan is None or D(plan["conservative_locked_profit"]) <= 0:
                continue
            positives += 1
            net = D(plan["conservative_locked_profit"])
            is_preferred = net >= args.preferred_profit
            preferred += int(is_preferred)
            fp = (str(plan["direction"]), str(plan["quantity"]), str(plan["sportsbook_cost_per_1_payout"]), str(plan["kalshi_price"]))
            fkey = (ticker, rq.book)
            if fingerprints.get(fkey) == fp and not is_preferred:
                continue
            fingerprints[fkey] = fp
            logger.warning("ODDS_IO_KALSHI_PROP_HOT_PACKET %s", json.dumps({
                "trigger": trigger, "observed_at_ms": int(time.time() * 1000),
                "event_id": prop.event_id, "family": prop.family, "player": prop.player,
                "threshold": str(prop.threshold), "kalshi_ticker": ticker,
                "sportsbook": rq.book, "retail_source_age_ms": max(0, rq.received_ms - rq.source_updated_ms),
                "retail_book_age_ms": max(0, int(time.time() * 1000) - rq.received_ms),
                "kalshi_book_age_ms": max(0, int(time.time() * 1000) - int(kq.received_ms)),
                "quote_skew_ms": skew, "plan": plan, "preferred": is_preferred,
                "settlement_status": "NOT_EXECUTABLE_BOOKMAKER_RULE_CERTIFICATION_REQUIRED",
                "alert_eligible": False, "execution_eligible": False,
                "realized_pnl": "0", "read_only": True, "order_entry": False,
            }, separators=(",", ":"), default=str))

    try:
        while True:
            if stop_task.done():
                break
            rtask = asyncio.create_task(store.updates.get())
            ktask = asyncio.create_task(kstore.updates.get())
            done, pending = await asyncio.wait({rtask, ktask, stop_task}, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                if task is not stop_task:
                    task.cancel()
            if stop_task in done:
                break
            for task in done:
                if task is stop_task:
                    continue
                update: BookUpdate = task.result()
                await evaluate(update.market_id, f"{update.venue}:{update.kind}")

            now = time.monotonic()
            if now - last_summary >= args.summary_seconds:
                logger.info("ODDS_IO_KALSHI_PROP_HOT_SUMMARY %s", json.dumps({
                    "kalshi_props": len(props), "odds_io_connected": store.connected,
                    "sportsbook_ws_messages": store.ws_messages, "sportsbook_ws_updates": store.ws_updates,
                    "sportsbook_by_book": dict(store.by_book), "supported_prop_rows": store.prop_supported_rows,
                    "matched_prop_rows": store.prop_matched_rows, "stale_prop_markets": store.prop_stale_markets,
                    "kalshi_initialized": kstore.initialized_count,
                    "evaluations": evaluations, "comparable_evaluations": comparable,
                    "positive_evaluations": positives, "preferred_evaluations": preferred,
                    "latency": store.latency_payload(),
                    "rule_status": "BOOKMAKER_RULES_PENDING",
                    "alert_eligible": False, "execution_eligible": False,
                    "read_only": True, "order_entry": False,
                }, separators=(",", ":"), default=str))
                last_summary = now
    finally:
        odds_task.cancel(); kalshi_task.cancel(); stop_task.cancel()
        await kstore.close()
        await asyncio.gather(odds_task, kalshi_task, stop_task, return_exceptions=True)


async def _wait_for_stop() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only odds-api.io/Kalshi MLB prop hot shadow")
    p.add_argument("--league-slug", default="usa-mlb")
    p.add_argument("--max-book-age-ms", type=int, default=3000)
    p.add_argument("--max-quote-skew-ms", type=int, default=3000)
    p.add_argument("--max-quantity", type=int, default=5)
    p.add_argument("--max-capital", type=D, default=D("5"))
    p.add_argument("--preferred-profit", type=D, default=D("0.03"))
    p.add_argument("--summary-seconds", type=float, default=10.0)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
