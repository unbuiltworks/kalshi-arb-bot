"""Market metadata index, plus asset and window-length classification.

Classification policy
---------------------
Titles and slugs are *not* trusted on their own. The primary signal is the
market's own measured duration (endDate - startDate), snapped to a known bucket
within a tight tolerance. Text is a secondary signal used only to corroborate,
or -- when duration metadata is missing entirely -- to mark a market as
``text_only``, which is excluded from the primary analysis by default.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import config
from .cache import RawCache
from .timeutil import to_epoch_s

BTC_RE = re.compile(r"(?<![a-z0-9])(btc|xbt|bitcoin)(?![a-z0-9])", re.I)
ETH_RE = re.compile(r"(?<![a-z0-9])(eth|ether|ethereum)(?![a-z0-9])", re.I)

MINUTE_RES = {
    5: re.compile(r"(?<![0-9])5\s*-?\s*(?:min\b|mins\b|minute|m\b)", re.I),
    15: re.compile(r"(?<![0-9])15\s*-?\s*(?:min\b|mins\b|minute|m\b)", re.I),
}
UPDOWN_RE = re.compile(r"up\s*(?:or|/|vs\.?|-)\s*down|updown|up-or-down", re.I)


@dataclass
class MarketMeta:
    condition_id: str
    market_id: str = ""
    slug: str = ""
    title: str = ""
    event_slug: str = ""
    event_title: str = ""
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    duration_s: Optional[int] = None
    asset: str = ""
    asset_source: str = "none"
    window_minutes: Optional[int] = None
    window_source: str = "none"
    is_up_down: bool = False
    closed: Optional[bool] = None
    outcomes: List[str] = field(default_factory=list)
    outcome_prices: List[float] = field(default_factory=list)
    winning_outcome: str = ""
    resolution_source: str = "none"
    flags: List[str] = field(default_factory=list)

    @property
    def in_scope(self) -> bool:
        return (
            self.asset in config.ASSETS
            and self.window_minutes in config.WINDOW_BUCKETS_MIN
            and "window_text_only" not in self.flags
            and "asset_ambiguous" not in self.flags
        )

    def text_blob(self) -> str:
        return " ".join([self.slug, self.title, self.event_slug, self.event_title])


def _load_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def classify_asset(text: str) -> tuple[str, str]:
    """Return ``(asset, source)`` -- ``asset`` is BTC, ETH, '' (none) or 'AMBIGUOUS'."""
    has_btc = bool(BTC_RE.search(text or ""))
    has_eth = bool(ETH_RE.search(text or ""))
    if has_btc and has_eth:
        return "AMBIGUOUS", "text_conflict"
    if has_btc:
        return "BTC", "text"
    if has_eth:
        return "ETH", "text"
    return "", "none"


def window_from_duration(duration_s: Optional[int]) -> Optional[int]:
    """Snap a measured market duration onto a known bucket, or return None."""
    if duration_s is None or duration_s <= 0:
        return None
    for minutes in config.WINDOW_BUCKETS_MIN:
        if abs(duration_s - minutes * 60) <= config.WINDOW_TOLERANCE_S:
            return minutes
    return None


def window_from_text(text: str) -> Optional[int]:
    matches = [minutes for minutes, pattern in MINUTE_RES.items() if pattern.search(text or "")]
    # "15 minute" also contains "5 minute"-ish substrings; prefer the longest match.
    if 15 in matches:
        return 15
    if matches:
        return matches[0]
    return None


def build_market_meta(raw: Dict[str, Any]) -> MarketMeta:
    """Build a classified MarketMeta from one Gamma/CLOB market payload."""
    events = raw.get("events") or []
    event = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}

    start_ts = to_epoch_s(
        raw.get("startDate") or raw.get("gameStartTime") or raw.get("start_date_iso") or event.get("startDate")
    )
    end_ts = to_epoch_s(raw.get("endDate") or raw.get("end_date_iso") or event.get("endDate"))
    duration_s = (end_ts - start_ts) if (start_ts and end_ts and end_ts > start_ts) else None

    meta = MarketMeta(
        condition_id=raw.get("conditionId") or raw.get("condition_id") or "",
        market_id=str(raw.get("id") or ""),
        slug=raw.get("slug") or raw.get("market_slug") or "",
        title=raw.get("question") or raw.get("title") or "",
        event_slug=event.get("slug") or raw.get("eventSlug") or "",
        event_title=event.get("title") or "",
        start_ts=start_ts,
        end_ts=end_ts,
        duration_s=duration_s,
        closed=raw.get("closed") if isinstance(raw.get("closed"), bool) else None,
    )

    blob = meta.text_blob()
    asset, asset_source = classify_asset(blob)
    if asset == "AMBIGUOUS":
        meta.asset, meta.asset_source = "", "text_conflict"
        meta.flags.append("asset_ambiguous")
    else:
        meta.asset, meta.asset_source = asset, asset_source
        if not asset:
            meta.flags.append("asset_unknown")

    by_duration = window_from_duration(duration_s)
    by_text = window_from_text(blob)
    if by_duration is not None and by_text is not None:
        meta.window_minutes = by_duration
        meta.window_source = "both_agree" if by_duration == by_text else "duration_over_text"
        if by_duration != by_text:
            meta.flags.append(f"window_text_conflict:text={by_text}")
    elif by_duration is not None:
        meta.window_minutes, meta.window_source = by_duration, "duration"
    elif by_text is not None:
        meta.window_minutes, meta.window_source = by_text, "text_only"
        meta.flags.append("window_text_only")
    else:
        meta.window_source = "none"
        if duration_s is not None:
            meta.flags.append(f"duration_off_bucket:{duration_s}s")
        else:
            meta.flags.append("no_duration_metadata")

    meta.is_up_down = bool(UPDOWN_RE.search(blob))
    meta.outcomes = [str(value) for value in _load_json_list(raw.get("outcomes"))]
    prices = []
    for value in _load_json_list(raw.get("outcomePrices")):
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            prices.append(float("nan"))
    meta.outcome_prices = prices

    if meta.closed and prices and len(prices) == len(meta.outcomes):
        winners = [
            meta.outcomes[index]
            for index, price in enumerate(prices)
            if price == price and price >= 0.99
        ]
        if len(winners) == 1:
            from .normalize import canonical_outcome

            label, _note = canonical_outcome(winners[0], None, None)
            meta.winning_outcome = label or winners[0]
            meta.resolution_source = "gamma_outcome_prices"
    return meta


def load_market_index(cache: RawCache) -> Dict[str, MarketMeta]:
    """condition_id -> MarketMeta, built from every cached market payload."""
    index: Dict[str, MarketMeta] = {}
    for tag in ("markets", "markets_clob"):
        for batch in cache.payload_batches(tag):
            for raw in batch:
                meta = build_market_meta(raw)
                if not meta.condition_id:
                    continue
                existing = index.get(meta.condition_id)
                if existing is None or _richness(meta) > _richness(existing):
                    index[meta.condition_id] = meta
    _augment_from_events(cache, index)
    return index


def _richness(meta: MarketMeta) -> int:
    score = 0
    score += 4 if meta.duration_s is not None else 0
    score += 2 if meta.asset else 0
    score += 1 if meta.winning_outcome else 0
    return score


def _augment_from_events(cache: RawCache, index: Dict[str, MarketMeta]) -> None:
    """Events carry markets too; use them to fill gaps and to add event titles."""
    for batch in cache.payload_batches("events"):
        for event in batch:
            markets = event.get("markets")
            if not isinstance(markets, list):
                continue
            for raw in markets:
                if not isinstance(raw, dict):
                    continue
                enriched = dict(raw)
                enriched.setdefault("events", [event])
                meta = build_market_meta(enriched)
                if not meta.condition_id:
                    continue
                existing = index.get(meta.condition_id)
                if existing is None or _richness(meta) > _richness(existing):
                    index[meta.condition_id] = meta
                elif existing is not None and not existing.event_title:
                    existing.event_title = meta.event_title
                    existing.event_slug = existing.event_slug or meta.event_slug


def annotate_trades(
    trades: Sequence[Dict[str, Any]], index: Dict[str, MarketMeta]
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Attach market metadata + window timing to each fill.

    Returns ``(in_scope_trades, out_of_scope_trades)``. Trades whose market
    metadata is missing are treated as out of scope and reported, never guessed.
    """
    in_scope: List[Dict[str, Any]] = []
    out_scope: List[Dict[str, Any]] = []
    for trade in trades:
        row = dict(trade)
        meta = index.get(row.get("condition_id", ""))
        if meta is None:
            # Last-resort text classification, recorded but never trusted.
            fallback_asset, _src = classify_asset(f"{row.get('market_slug','')} {row.get('title','')}")
            row.update(
                {
                    "asset": "" if fallback_asset == "AMBIGUOUS" else fallback_asset,
                    "window_minutes": None,
                    "window_source": "no_market_metadata",
                    "window_id": row.get("condition_id", ""),
                    "market_id": "",
                    "window_start_ts": None,
                    "window_end_ts": None,
                    "seconds_from_window_start": None,
                    "seconds_to_expiry": None,
                    "market_flags": "no_market_metadata",
                    "in_scope": False,
                    "exclusion_reason": "no_market_metadata",
                }
            )
            out_scope.append(row)
            continue

        timestamp = row.get("timestamp")
        seconds_from_start = (
            timestamp - meta.start_ts if (timestamp and meta.start_ts) else None
        )
        seconds_to_expiry = (
            meta.end_ts - timestamp if (timestamp and meta.end_ts) else None
        )
        flags = list(meta.flags)
        if (
            seconds_from_start is not None
            and (seconds_from_start < -config.WINDOW_TOLERANCE_S or (meta.duration_s and seconds_from_start > meta.duration_s + config.WINDOW_TOLERANCE_S))
        ):
            flags.append("trade_outside_window_bounds")

        row.update(
            {
                "asset": meta.asset,
                "window_minutes": meta.window_minutes,
                "window_source": meta.window_source,
                "window_id": meta.condition_id,
                "market_id": meta.market_id,
                "market_slug": row.get("market_slug") or meta.slug,
                "event_slug": row.get("event_slug") or meta.event_slug,
                "title": row.get("title") or meta.title,
                "event_title": meta.event_title,
                "window_start_ts": meta.start_ts,
                "window_end_ts": meta.end_ts,
                "seconds_from_window_start": seconds_from_start,
                "seconds_to_expiry": seconds_to_expiry,
                "market_flags": ";".join(flags),
                "is_up_down": meta.is_up_down,
                "winning_outcome": meta.winning_outcome,
            }
        )
        if meta.in_scope:
            row["in_scope"] = True
            row["exclusion_reason"] = ""
            in_scope.append(row)
        else:
            row["in_scope"] = False
            row["exclusion_reason"] = _exclusion_reason(meta)
            out_scope.append(row)
    return in_scope, out_scope


def _exclusion_reason(meta: MarketMeta) -> str:
    if "asset_ambiguous" in meta.flags:
        return "asset_ambiguous"
    if meta.asset not in config.ASSETS:
        return "asset_not_btc_eth"
    if "window_text_only" in meta.flags:
        return "window_text_only_unvalidated"
    if meta.window_minutes not in config.WINDOW_BUCKETS_MIN:
        return f"window_not_5_or_15:{meta.window_source}"
    return "unknown"
