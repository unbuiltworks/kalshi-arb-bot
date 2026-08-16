"""Scheduled-time guard for strict read-only MLB prop counterpart matching.

The base strict prop adapter validates family/player/threshold/team identity and rule
review evidence. This guard adds the missing game-instance check: Polymarket's
structured game start must agree with Kalshi's explicit occurrence timestamp when
available. The event-ticker clock is only a last-resort fallback because live Kalshi
sports tickers can encode a display/local clock that is not UTC or Eastern time.

No order-entry code exists here. A successful match remains shadow-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.strict_mlb_prop_adapters import RuleAuditEvidence, StrictMLBPropPair, strict_match

ET = ZoneInfo("America/New_York")
MAX_START_SKEW_SECONDS = 5 * 60

# Last-resort fallback only. Do not cross-check this clock against an explicit
# occurrence_datetime: current live sports tickers may encode a venue/display local
# clock rather than ET. Structured occurrence_datetime is authoritative when present.
KALSHI_EVENT_TIME_RE = re.compile(
    r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<hh>\d{2})(?P<mm>\d{2})(?P<teams>[A-Z]+)$",
    re.I,
)
MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _iso_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def polymarket_start_utc(poly: Mapping[str, Any]) -> datetime | None:
    """Extract an explicit scheduled start from the Polymarket market payload."""
    for key in ("gameStartTime", "startTime", "eventStartTime"):
        parsed = _iso_utc(poly.get(key))
        if parsed is not None:
            return parsed
    return None


def _kalshi_ticker_start_utc(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    """Parse legacy ticker clock as ET only when structured occurrence time is absent."""
    ticker = str(event.get("event_ticker") or market.get("event_ticker") or "").upper()
    match = KALSHI_EVENT_TIME_RE.search(ticker)
    if match is None:
        return None
    month = MONTHS.get(match.group("mon").upper())
    if month is None:
        return None
    try:
        local = datetime(
            2000 + int(match.group("yy")), month, int(match.group("dd")),
            int(match.group("hh")), int(match.group("mm")), tzinfo=ET,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def kalshi_event_start_utc(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    """Prefer authoritative structured occurrence_datetime; ticker is fallback only.

    Live 2026-08-16 MLB payloads demonstrated that an event ticker such as a 12:15
    clock can coexist with market occurrence_datetime 19:15Z (15:15 ET). Treating
    the ticker clock as ET falsely rejected every otherwise valid Kalshi prop market.
    """
    explicit = _iso_utc(market.get("occurrence_datetime")) or _iso_utc(event.get("occurrence_datetime"))
    if explicit is not None:
        return explicit
    return _kalshi_ticker_start_utc(event, market)


def strict_scheduled_match(
    poly: Mapping[str, Any],
    kalshi_event: Mapping[str, Any],
    kalshi_market: Mapping[str, Any],
    audit: RuleAuditEvidence | None,
    *,
    max_start_skew_seconds: int = MAX_START_SKEW_SECONDS,
) -> StrictMLBPropPair | None:
    """Return a strict rule-audited shadow pair only for the same game instance."""
    poly_start = polymarket_start_utc(poly)
    kalshi_start = kalshi_event_start_utc(kalshi_event, kalshi_market)
    if poly_start is None or kalshi_start is None:
        return None
    if abs((poly_start - kalshi_start).total_seconds()) > max_start_skew_seconds:
        return None

    pair = strict_match(poly, kalshi_event, kalshi_market, audit)
    if pair is None:
        return None
    if pair.game_date != kalshi_start.astimezone(ET).date().isoformat():
        return None
    return pair
