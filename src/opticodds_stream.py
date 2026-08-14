"""OpticOdds (OddsJam API) real-time sportsbook totals ingestion.

OddsJam's developer documentation currently redirects to OpticOdds v3. We seed
current state from `/fixtures/odds`, then consume the SSE odds stream. Seeding is
important because the stream is an update feed; waiting for every line to move
before the scanner knows current state would miss opportunities after startup.

No wager is ever placed by this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import AsyncIterator, Iterable

import httpx

from src.cross_market import SportsbookOverQuote

logger = logging.getLogger("kalshi_arb.opticodds")

BASE_URL = "https://api.opticodds.com/api/v3"


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: str
    event_id: str = ""


class OpticOddsStream:
    """Reconnectable OpticOdds v3 SSE odds stream."""

    def __init__(
        self,
        api_key: str,
        *,
        sport: str,
        sportsbooks: Iterable[str],
        leagues: Iterable[str] = (),
        markets: Iterable[str] = (),
        fixture_ids: Iterable[str] = (),
        live_only: bool | None = True,
        include_deep_link: bool = True,
        reconnect_delay_s: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpticOdds API key is required")
        books = tuple(sportsbooks)
        if not books:
            raise ValueError("at least one sportsbook is required")
        if len(books) > 5:
            raise ValueError("OpticOdds supports at most 5 sportsbooks per stream")
        self.api_key = api_key
        self.sport = sport
        self.sportsbooks = books
        self.leagues = tuple(leagues)
        self.markets = tuple(markets)
        self.fixture_ids = tuple(fixture_ids)
        self.live_only = live_only
        self.include_deep_link = include_deep_link
        self.reconnect_delay_s = reconnect_delay_s
        self.last_entry_id: str | None = None

    def _params(self) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = []
        for book in self.sportsbooks:
            params.append(("sportsbook", book))
        for league in self.leagues:
            params.append(("league", league))
        for market in self.markets:
            params.append(("market", market))
        for fixture_id in self.fixture_ids:
            params.append(("fixture_id", fixture_id))
        if self.live_only is not None:
            params.append(("is_live", "true" if self.live_only else "false"))
        if self.include_deep_link:
            params.append(("include_deep_link", "true"))
        params.append(("odds_format", "AMERICAN"))
        if self.last_entry_id:
            params.append(("last_entry_id", self.last_entry_id))
        return params

    async def events(self) -> AsyncIterator[SSEEvent]:
        """Yield SSE events forever, reconnecting and resuming by entry id."""
        url = f"{BASE_URL}/stream/odds/{self.sport}"
        headers = {"X-Api-Key": self.api_key, "Accept": "text/event-stream"}

        while True:
            try:
                timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "GET", url, headers=headers, params=self._params()
                    ) as response:
                        response.raise_for_status()
                        async for event in _iter_sse(response):
                            if event.event in {"odds", "locked-odds"}:
                                try:
                                    body = json.loads(event.data)
                                    entry_id = body.get("entry_id")
                                    if entry_id:
                                        self.last_entry_id = str(entry_id)
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("OpticOdds stream disconnected: %s", exc)
                await asyncio.sleep(self.reconnect_delay_s)


async def _iter_sse(response: httpx.Response) -> AsyncIterator[SSEEvent]:
    event_name = "message"
    event_id = ""
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield SSEEvent(event=event_name, data="\n".join(data_lines), event_id=event_id)
            event_name = "message"
            event_id = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue

        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)


def _provider_limit_cents(odd: dict) -> int | None:
    limits = odd.get("limits")
    if not isinstance(limits, dict):
        return None
    raw = limits.get("max_stake", limits.get("max"))
    if raw is None:
        return None
    try:
        return max(0, int(Decimal(str(raw)) * 100))
    except Exception:
        return None


def normalize_over_quote(
    odd: dict,
    *,
    event_key: str,
    configured_stake_cap_cents: int,
) -> SportsbookOverQuote | None:
    """Convert one OpticOdds odd into the optimizer's normalized OVER quote."""
    if str(odd.get("selection_line", "")).lower() != "over":
        return None

    points = odd.get("points", odd.get("selection_points"))
    price = odd.get("price")
    sportsbook = odd.get("sportsbook")
    if points is None or price is None or not sportsbook:
        return None

    try:
        line = Decimal(str(points))
        american_odds = int(price)
    except Exception:
        return None

    doubled = line * 2
    if doubled != doubled.to_integral_value() or int(doubled) % 2 != 1:
        return None

    provider_cap = _provider_limit_cents(odd)
    if provider_cap is None:
        if configured_stake_cap_cents <= 0:
            return None
        max_stake = configured_stake_cap_cents
    elif configured_stake_cap_cents > 0:
        max_stake = min(provider_cap, configured_stake_cap_cents)
    else:
        max_stake = provider_cap

    if max_stake <= 0:
        return None

    deep_link = odd.get("deep_link") or {}
    if isinstance(deep_link, dict):
        deeplink = str(
            deep_link.get("ios") or deep_link.get("desktop") or deep_link.get("android") or ""
        )
    else:
        deeplink = str(deep_link)

    timestamp = odd.get("timestamp")
    try:
        observed_ms = int(float(timestamp) * 1000)
    except (TypeError, ValueError):
        # Unknown source timestamp should not bypass the staleness gate.
        observed_ms = int(time.time() * 1000)

    return SportsbookOverQuote(
        book=str(sportsbook),
        event_key=event_key,
        line=line,
        american_odds=american_odds,
        max_stake_cents=max_stake,
        deeplink=deeplink,
        observed_ms=observed_ms,
        source_id=str(odd.get("id", "")),
    )


class SportsbookQuoteStore:
    """Latest active sportsbook OVER quote by OpticOdds odd id."""

    def __init__(self) -> None:
        self._quotes: dict[str, SportsbookOverQuote] = {}

    def _apply_odd(
        self,
        odd: dict,
        *,
        fixture_id: str,
        fixture_to_event: dict[str, str],
        configured_stake_cap_cents: int,
    ) -> str | None:
        event_key = fixture_to_event.get(fixture_id)
        if not event_key:
            return None
        quote = normalize_over_quote(
            odd,
            event_key=event_key,
            configured_stake_cap_cents=configured_stake_cap_cents,
        )
        if quote is None or not quote.source_id:
            return None

        # REST seeding and SSE updates can race. Never let an older snapshot
        # overwrite a newer stream update for the same normalized odd id.
        previous = self._quotes.get(quote.source_id)
        if previous is not None and previous.observed_ms > quote.observed_ms:
            return None

        self._quotes[quote.source_id] = quote
        return event_key

    def apply_odds_payload(
        self,
        payload: dict,
        *,
        fixture_to_event: dict[str, str],
        configured_stake_cap_cents: int,
    ) -> set[str]:
        affected: set[str] = set()
        for odd in payload.get("data", []):
            fixture_id = str(odd.get("fixture_id", ""))
            event_key = self._apply_odd(
                odd,
                fixture_id=fixture_id,
                fixture_to_event=fixture_to_event,
                configured_stake_cap_cents=configured_stake_cap_cents,
            )
            if event_key:
                affected.add(event_key)
        return affected

    def apply_snapshot_payload(
        self,
        payload: dict,
        *,
        fixture_to_event: dict[str, str],
        configured_stake_cap_cents: int,
    ) -> set[str]:
        """Apply `/fixtures/odds` response whose odds are nested under fixtures."""
        affected: set[str] = set()
        for fixture in payload.get("data", []):
            fixture_id = str(fixture.get("id", ""))
            for odd in fixture.get("odds", []):
                event_key = self._apply_odd(
                    odd,
                    fixture_id=fixture_id,
                    fixture_to_event=fixture_to_event,
                    configured_stake_cap_cents=configured_stake_cap_cents,
                )
                if event_key:
                    affected.add(event_key)
        return affected

    def apply_locked_payload(self, payload: dict) -> set[str]:
        affected: set[str] = set()
        for odd in payload.get("data", []):
            odd_id = str(odd.get("id", ""))
            old = self._quotes.pop(odd_id, None)
            if old:
                affected.add(old.event_key)
        return affected

    def for_event(self, event_key: str) -> tuple[SportsbookOverQuote, ...]:
        return tuple(q for q in self._quotes.values() if q.event_key == event_key)


async def seed_current_odds(
    api_key: str,
    *,
    fixture_ids: Iterable[str],
    sportsbooks: Iterable[str],
    markets: Iterable[str],
    fixture_to_event: dict[str, str],
    store: SportsbookQuoteStore,
    configured_stake_cap_cents: int,
) -> None:
    """Seed current sportsbook state from REST before/while listening for updates.

    OpticOdds documents max 5 fixture IDs and max 5 sportsbooks per request, so
    both dimensions are chunked. Timestamp ordering in the store prevents an
    older REST row from replacing a newer SSE update.
    """
    fixture_ids = tuple(dict.fromkeys(fixture_ids))
    sportsbooks = tuple(dict.fromkeys(sportsbooks))
    markets = tuple(dict.fromkeys(markets))
    if not fixture_ids or not sportsbooks:
        return

    headers = {"X-Api-Key": api_key}
    timeout = httpx.Timeout(connect=10.0, read=10.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for fixture_chunk in _chunks(fixture_ids, 5):
            for book_chunk in _chunks(sportsbooks, 5):
                params: list[tuple[str, str]] = [("odds_format", "AMERICAN")]
                params.extend(("fixture_id", x) for x in fixture_chunk)
                params.extend(("sportsbook", x) for x in book_chunk)
                params.extend(("market", x) for x in markets)
                response = await client.get(
                    f"{BASE_URL}/fixtures/odds", headers=headers, params=params
                )
                response.raise_for_status()
                store.apply_snapshot_payload(
                    response.json(),
                    fixture_to_event=fixture_to_event,
                    configured_stake_cap_cents=configured_stake_cap_cents,
                )


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]
