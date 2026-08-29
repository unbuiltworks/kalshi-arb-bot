"""Asset and window classification for Polymarket crypto Up/Down markets.

The rule this module exists to enforce: a market's window length is decided by
its *timestamps*, not by its title. Titles and slugs are parsed too, but only
as a cross-check -- a disagreement is recorded as a conflict rather than
silently resolved in favour of the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from src.l5.config import WINDOW_DURATION_BANDS_S

# --- timestamp parsing -------------------------------------------------------

_TRAILING_Z = re.compile(r"[zZ]$")


def parse_ts(value: Any) -> int | None:
    """Parse a Polymarket timestamp into unix seconds, or None.

    Accepts ISO-8601 strings (with or without a trailing Z / offset), unix
    seconds, and unix milliseconds. Returns None for anything unparseable so
    callers can fall through to the next candidate field.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n <= 0:
            return None
        # Anything past year ~2286 in seconds is really milliseconds.
        if n > 1e11:
            n /= 1000.0
        return int(n)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit() or (s.lstrip("-").isdigit()):
            return parse_ts(int(s))
        iso = _TRAILING_Z.sub("+00:00", s)
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def iso(ts: int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# --- asset classification ----------------------------------------------------

_ASSET_PATTERNS = {
    "BTC": re.compile(r"(?<![a-z0-9])(btc|xbt|bitcoin)(?![a-z0-9])", re.I),
    "ETH": re.compile(r"(?<![a-z0-9])(eth|ether|ethereum)(?![a-z0-9])", re.I),
}

# Assets that must never be mistaken for BTC/ETH. Presence of one of these and
# no BTC/ETH token means the market is out of scope; presence alongside a
# BTC/ETH token means the text is ambiguous.
_OTHER_ASSET_PATTERN = re.compile(
    r"(?<![a-z0-9])(sol|solana|xrp|ripple|doge|dogecoin|bnb|ada|cardano|"
    r"hype|shib|link|ltc|litecoin|avax|dot|matic|trx)(?![a-z0-9])",
    re.I,
)


def _text_of(*parts: Any) -> str:
    return " ".join(str(p) for p in parts if p)


def classify_asset(*text_parts: Any) -> tuple[str | None, bool]:
    """Return (asset, ambiguous). asset is 'BTC', 'ETH' or None."""
    text = _text_of(*text_parts).replace("-", " ").replace("_", " ")
    hits = [name for name, pat in _ASSET_PATTERNS.items() if pat.search(text)]
    other = bool(_OTHER_ASSET_PATTERN.search(text))
    if len(hits) != 1:
        return None, len(hits) > 1
    if other:
        # e.g. a "BTC vs SOL" style market -- in scope for neither.
        return None, True
    return hits[0], False


# --- window length from text (cross-check only) ------------------------------

_TEXT_WINDOW_PATTERNS = (
    (5, re.compile(r"(?<![a-z0-9])(5\s*m(?:in(?:ute)?s?)?|five\s*minutes?)(?![a-z0-9])", re.I)),
    (15, re.compile(r"(?<![a-z0-9])(15\s*m(?:in(?:ute)?s?)?|fifteen\s*minutes?)(?![a-z0-9])", re.I)),
)


def window_minutes_from_text(*text_parts: Any) -> int | None:
    text = _text_of(*text_parts).replace("-", " ").replace("_", " ")
    found = {minutes for minutes, pat in _TEXT_WINDOW_PATTERNS if pat.search(text)}
    if len(found) == 1:
        return found.pop()
    return None


_TIME_RANGE = re.compile(
    r"(?<![a-z0-9:])(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?"
    r"\s*[-\u2012\u2013\u2014\u2015]\s*"
    r"(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?",
)


def _to_minutes_of_day(hour: int, minute: int, meridiem: str) -> int:
    hour = hour % 12
    if meridiem.lower() == "p":
        hour += 12
    return hour * 60 + minute


def window_minutes_from_time_range(*text_parts: Any) -> int | None:
    """Read the window length out of an explicit clock range in the title.

    Polymarket names these markets by their span -- "Bitcoin Up or Down -
    August 29, 5:00PM-5:15PM ET" -- so this is a *derived duration*, not a
    text label, and it is trusted well above a bare "5m" token. It is still
    weaker than market metadata, which is timezone-unambiguous.
    """
    text = _text_of(*text_parts)
    match = _TIME_RANGE.search(text)
    if not match:
        return None
    h1, m1, ap1, h2, m2, ap2 = match.groups()
    start = _to_minutes_of_day(int(h1), int(m1), ap1)
    end = _to_minutes_of_day(int(h2), int(m2), ap2)
    delta = end - start
    if delta < 0:  # range crosses midnight
        delta += 24 * 60
    if delta <= 0:
        return None
    return window_minutes_from_duration(delta * 60)


def window_minutes_from_duration(duration_s: int | None) -> int | None:
    """Map an observed market duration onto a 5m/15m bucket, or None."""
    if duration_s is None or duration_s <= 0:
        return None
    for minutes, (lo, hi) in WINDOW_DURATION_BANDS_S.items():
        if lo <= duration_s <= hi:
            return minutes
    return None


# --- market classification ---------------------------------------------------

# Candidate fields, most specific first. Polymarket is inconsistent across
# series about which of these is populated, so we record which one we used.
_START_FIELDS = ("gameStartTime", "eventStartTime", "startDate", "startTime")
_END_FIELDS = ("endDate", "endTime", "resolutionTime")


@dataclass(frozen=True)
class MarketClassification:
    condition_id: str
    market_id: str
    slug: str
    question: str
    event_slug: str
    event_title: str
    asset: str | None
    asset_ambiguous: bool
    window_start_ts: int | None
    window_end_ts: int | None
    duration_s: int | None
    window_minutes: int | None
    window_minutes_from_metadata: int | None
    window_minutes_from_time_range: int | None
    window_minutes_from_text: int | None
    window_source: str          # "metadata" | "text_fallback" | "unclassified"
    window_conflict: bool       # metadata and text disagree
    bounds_reconstructed: bool  # start inferred from end - window length
    start_field: str
    end_field: str
    outcomes: tuple[str, ...]
    token_ids: tuple[str, ...]
    closed: bool

    @property
    def in_scope(self) -> bool:
        return (
            self.asset in ("BTC", "ETH")
            and self.window_minutes in (5, 15)
            and self.window_start_ts is not None
            and self.window_end_ts is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_ts(market: dict, fields: Iterable[str], nested: dict | None) -> tuple[int | None, str]:
    for field in fields:
        ts = parse_ts(market.get(field))
        if ts is not None:
            return ts, field
    if nested:
        for field in fields:
            ts = parse_ts(nested.get(field))
            if ts is not None:
                return ts, f"event.{field}"
    return None, ""


def _json_list(value: Any) -> tuple[str, ...]:
    """Gamma returns `outcomes` / `clobTokenIds` as JSON-encoded strings."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return ()
        if isinstance(parsed, list):
            return tuple(str(v) for v in parsed)
    return ()


def classify_market(market: dict) -> MarketClassification:
    """Classify one gamma `/markets` record."""
    events = market.get("events") or []
    event = events[0] if isinstance(events, list) and events else {}
    if not isinstance(event, dict):
        event = {}

    slug = str(market.get("slug") or "")
    question = str(market.get("question") or market.get("title") or "")
    event_slug = str(event.get("slug") or market.get("eventSlug") or "")
    event_title = str(event.get("title") or "")

    asset, ambiguous = classify_asset(slug, question, event_slug, event_title)

    start_ts, start_field = _first_ts(market, _START_FIELDS, event)
    end_ts, end_field = _first_ts(market, _END_FIELDS, event)

    duration_s = (end_ts - start_ts) if (start_ts is not None and end_ts is not None) else None
    from_meta = window_minutes_from_duration(duration_s)
    from_range = window_minutes_from_time_range(question, event_title, slug, event_slug)
    from_text = window_minutes_from_text(slug, question, event_slug, event_title)

    bounds_reconstructed = False
    if from_meta is not None:
        window_minutes = from_meta
        source = "metadata"
    elif from_range is not None and end_ts is not None:
        # The title names an explicit clock span. Trust the span, keep the
        # metadata end, and derive the start from it.
        window_minutes = from_range
        source = "title_timerange"
        start_ts = end_ts - window_minutes * 60
        duration_s = window_minutes * 60
        bounds_reconstructed = True
    elif from_text is not None and end_ts is not None:
        # No usable duration (a missing or bogus start field is common on some
        # series). Fall back to the text, and reconstruct the start from the
        # end so trade offsets are still computable -- flagged, so any row
        # depending on it can be excluded from a strict analysis.
        window_minutes = from_text
        source = "text_fallback"
        start_ts = end_ts - window_minutes * 60
        duration_s = window_minutes * 60
        bounds_reconstructed = True
    else:
        window_minutes = None
        source = "unclassified"

    claims = {c for c in (from_meta, from_range, from_text) if c is not None}
    conflict = len(claims) > 1

    return MarketClassification(
        condition_id=str(market.get("conditionId") or ""),
        market_id=str(market.get("id") or ""),
        slug=slug,
        question=question,
        event_slug=event_slug,
        event_title=event_title,
        asset=asset,
        asset_ambiguous=ambiguous,
        window_start_ts=start_ts,
        window_end_ts=end_ts,
        duration_s=duration_s,
        window_minutes=window_minutes,
        window_minutes_from_metadata=from_meta,
        window_minutes_from_time_range=from_range,
        window_minutes_from_text=from_text,
        window_source=source,
        window_conflict=conflict,
        bounds_reconstructed=bounds_reconstructed,
        start_field=start_field,
        end_field=end_field,
        outcomes=_json_list(market.get("outcomes")),
        token_ids=_json_list(market.get("clobTokenIds")),
        closed=bool(market.get("closed")),
    )
