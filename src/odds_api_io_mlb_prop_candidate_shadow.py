"""Read-only MLB sportsbook player-prop -> Gemini candidate sieve.

Odds-API.io supplies the single live DraftKings/FanDuel WebSocket. Player Props
rows are matched strictly to Gemini's canonical player_prop identities by event,
player, stat, comparator, and half-unit line. Gemini REST prices are only a cheap
candidate sieve; outputs are explicitly NOT executable until the exact Gemini
bookTicker is revalidated. No order transport exists here.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
import time
from typing import Any, Mapping

import httpx

from src import odds_api_io_mlb_hot_shadow as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.three_venue_mlb_price_scan import _gemini_contracts
from src.universal_three_venue_sports import fetch_gemini_mlb

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_prop_candidate_shadow")
D = Decimal
ONE = D("1")

STAT_MAP = {
    "home runs": "home_runs",
    "home run": "home_runs",
    "hits": "hits",
    "hit": "hits",
    "rbis": "rbis",
    "rbi": "rbis",
    "runs batted in": "rbis",
    "total bases": "total_bases",
    "runs": "runs",
    "run": "runs",
    "strikeouts": "strikeouts",
    "strikeout": "strikeouts",
    "pitcher strikeouts": "strikeouts",
    "pitching strikeouts": "strikeouts",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("’", "'").split())


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _label_parts(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)\s*", text)
    if not match:
        return None
    player = _norm(match.group(1))
    stat = STAT_MAP.get(_norm(match.group(2)))
    if not player or not stat:
        return None
    return player, stat


def _semantic(*, teams: tuple[str, ...], start_ms: int, player: str, stat: str, line: Decimal) -> tuple[Any, ...]:
    # Keep start in the key to fail closed on same-team doubleheaders.
    return (tuple(sorted(teams)), int(start_ms), _norm(player), stat, "GT", format(line.normalize(), "f"))


@dataclass(frozen=True)
class GeminiPropSnapshot:
    symbol: str
    no_cost: Decimal
    no_fee: Decimal
    received_ms: int
    label: str


_gemini_props: dict[tuple[Any, ...], GeminiPropSnapshot] = {}
_gemini_refreshes = 0
_gemini_last_refresh_ms = 0
_retail_store: base.OddsIoStore | None = None
_seen_fingerprints: dict[tuple[Any, ...], tuple[str, str]] = {}
_stats: Counter[str] = Counter()
_orig_init = base.OddsIoStore.__init__
_orig_ingest = base.OddsIoStore.ingest
_orig_run_session = base.run_session


def _store_init(self: base.OddsIoStore, *args: Any, **kwargs: Any) -> None:
    global _retail_store
    _orig_init(self, *args, **kwargs)
    _retail_store = self


async def _refresh_gemini_props() -> None:
    global _gemini_props, _gemini_refreshes, _gemini_last_refresh_ms
    backoff = 1.0
    async with httpx.AsyncClient(timeout=8.0) as http:
        while True:
            started = time.monotonic()
            try:
                events = await fetch_gemini_mlb(http)
                contracts = _gemini_contracts(events)
                now_ms = int(time.time() * 1000)
                next_map: dict[tuple[Any, ...], GeminiPropSnapshot] = {}
                for side in normalize_gemini_mlb(events):
                    ident = side.identity
                    if ident.market_family != "player_prop" or ident.comparator != "GT" or ident.line is None:
                        continue
                    raw = contracts.get(side.venue_market_id, {})
                    prices = raw.get("prices") if isinstance(raw, Mapping) and isinstance(raw.get("prices"), Mapping) else {}
                    buy = prices.get("buy") if isinstance(prices.get("buy"), Mapping) else {}
                    no_cost = _d(buy.get("no"))
                    if no_cost is None or not (D("0") < no_cost <= ONE):
                        continue
                    key = _semantic(
                        teams=ident.event.participants,
                        start_ms=ident.event.scheduled_start_ms,
                        player=ident.subject,
                        stat=ident.stat,
                        line=ident.line,
                    )
                    next_map[key] = GeminiPropSnapshot(
                        symbol=side.venue_market_id,
                        no_cost=no_cost,
                        no_fee=gemini_predictions_taker_fee(no_cost, ONE),
                        received_ms=now_ms,
                        label=side.source_label,
                    )
                _gemini_props = next_map
                _gemini_refreshes += 1
                _gemini_last_refresh_ms = now_ms
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ODDS_API_IO_PROP_GEMINI_REFRESH_ERROR type=%s", type(exc).__name__)
                backoff = min(10.0, backoff * 2.0)
            await asyncio.sleep(max(0.05, backoff - (time.monotonic() - started)))


def _candidate_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    accepted = _orig_ingest(self, message, freshness_ms=freshness_ms)
    if str(message.get("type") or "") not in {"created", "updated"}:
        return accepted
    event_id = str(message.get("id") or "")
    event = self.events.get(event_id)
    if not isinstance(event, Mapping):
        return accepted
    event_ident = base._event_identity(event)
    if event_ident is None:
        return accepted
    book = base._retail_book_name(message.get("bookie") or message.get("bookmaker"))
    if not book:
        return accepted

    received_ms = int(time.time() * 1000)
    markets = message.get("markets")
    if not isinstance(markets, list):
        return accepted
    for market in markets:
        if not isinstance(market, Mapping) or _norm(market.get("name")) != "player props":
            continue
        updated_ms = base._market_updated_ms(market.get("updatedAt"))
        if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
            _stats["stale_prop_markets"] += 1
            continue
        rows = market.get("odds")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            parts = _label_parts(row.get("label"))
            line = _d(row.get("hdp"))
            decimal_odds = _d(row.get("over"))
            if parts is None or line is None or decimal_odds is None or decimal_odds <= ONE:
                _stats["unsupported_prop_rows"] += 1
                continue
            player, stat = parts
            key = _semantic(
                teams=event_ident.participants,
                start_ms=event_ident.scheduled_start_ms,
                player=player,
                stat=stat,
                line=line,
            )
            _stats["supported_prop_rows"] += 1
            gem = _gemini_props.get(key)
            if gem is None:
                _stats["unmatched_prop_rows"] += 1
                continue
            _stats["matched_prop_rows"] += 1
            sportsbook_cost = ONE / decimal_odds
            profit = ONE - sportsbook_cost - gem.no_cost - gem.no_fee
            if profit <= D("0"):
                continue
            _stats["positive_snapshot_candidates"] += 1
            fingerprint_key = (key, book)
            fingerprint = (str(decimal_odds), str(gem.no_cost))
            if _seen_fingerprints.get(fingerprint_key) == fingerprint:
                continue
            _seen_fingerprints[fingerprint_key] = fingerprint
            logger.info(
                "ODDS_API_IO_PROP_CANDIDATE %s",
                json.dumps({
                    "event_id": event_id,
                    "book": book,
                    "player": player,
                    "stat": stat,
                    "line": str(line),
                    "sportsbook_over_decimal": str(decimal_odds),
                    "sportsbook_cost_per_1_payout": str(sportsbook_cost),
                    "gemini_symbol": gem.symbol,
                    "gemini_label": gem.label,
                    "gemini_no_snapshot_cost": str(gem.no_cost),
                    "gemini_no_fee": str(gem.no_fee),
                    "snapshot_locked_profit": str(profit),
                    "sportsbook_market_age_ms": max(0, received_ms - updated_ms),
                    "gemini_snapshot_age_ms": max(0, received_ms - gem.received_ms),
                    "candidate_only": True,
                    "needs_gemini_bookticker_revalidation": True,
                    "settlement_rule_audit_required": True,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"), default=str),
            )
    return accepted


async def _prop_heartbeat() -> None:
    while True:
        await asyncio.sleep(15.0)
        now_ms = int(time.time() * 1000)
        logger.info("ODDS_API_IO_PROP_SUMMARY %s", json.dumps({
            "gemini_prop_snapshot_contracts": len(_gemini_props),
            "gemini_refreshes": _gemini_refreshes,
            "gemini_snapshot_age_ms": None if not _gemini_last_refresh_ms else max(0, now_ms - _gemini_last_refresh_ms),
            "stats": dict(_stats),
            "candidate_only": True,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


async def _run_with_prop_sieve(args: Any) -> None:
    prop_refresh = asyncio.create_task(_refresh_gemini_props(), name="gemini_prop_refresh")
    prop_hb = asyncio.create_task(_prop_heartbeat(), name="prop_heartbeat")
    try:
        await _orig_run_session(args)
    finally:
        prop_refresh.cancel(); prop_hb.cancel()
        await asyncio.gather(prop_refresh, prop_hb, return_exceptions=True)


base.OddsIoStore.__init__ = _store_init
base.OddsIoStore.ingest = _candidate_ingest
base.run_session = _run_with_prop_sieve

if __name__ == "__main__":
    base.main()
