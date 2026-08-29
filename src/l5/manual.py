"""Ingest screenshot transcriptions of the profile activity feed.

This exists because the public API is unreachable from some environments. It
converts hand-transcribed feed rows into exactly the record shape the API
returns, so `normalize`, `windows`, `analysis` and `report` run unchanged and
screenshot-derived rows stay distinguishable from fetched ones.

Three things about the feed drive the design:

* **Prices shown in cents are rounded.** A row reading "Up 92c, 30.5 shares,
  $28.17" is really 0.9236. `dollars / shares` is authoritative; the displayed
  cents are kept alongside for audit.
* **Times are relative and coarse.** "4m ago" against a clock legible only to
  the minute bounds the fill to a two-minute interval, not an instant. Seeing
  the same row in two screenshots narrows that interval by intersection.
* **Feed order is exact even when timestamps tie.** The feed is strictly
  newest-first, so ordering comes from position, never from the derived
  timestamp.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET"
_TITLE = re.compile(
    r"^(?P<asset>.+?)\s+Up or Down\s*[-‒–—]\s*"
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*"
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<ap1>[AaPp])\.?[Mm]\.?"
    r"\s*[-‒–—]\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<ap2>[AaPp])\.?[Mm]\.?"
    r"(?:\s*(?P<tz>[A-Z]{2,4}))?\s*$"
)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1
    )
}

_ASSET_NAMES = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
}

# Feed verbs -> the activity `type` the API would have used.
_ACTION_TYPES = {
    "buy": ("TRADE", "BUY"),
    "sell": ("TRADE", "SELL"),
    "redeem": ("REDEEM", None),
    "merge": ("MERGE", None),
    "split": ("SPLIT", None),
    "convert": ("CONVERSION", None),
    "reward": ("REWARD", None),
}


class ManualParseError(ValueError):
    pass


@dataclass
class MarketRef:
    asset: str
    condition_id: str
    slug: str
    title: str
    start_ts: int
    end_ts: int

    @property
    def window_minutes(self) -> int:
        return round((self.end_ts - self.start_ts) / 60)


def _hour24(hour: int, meridiem: str) -> int:
    hour = hour % 12
    return hour + 12 if meridiem.lower() == "p" else hour


def parse_market_title(title: str, year: int, utc_offset_hours: float) -> MarketRef:
    """Turn a feed market title into a window with exact bounds.

    The title names the span explicitly, so the window length is derived, not
    guessed. `utc_offset_hours` comes from the batch file rather than a
    timezone database, so the transcription states its own offset instead of
    this code inferring one.
    """
    match = _TITLE.match(title.strip())
    if not match:
        raise ManualParseError(f"unrecognised market title: {title!r}")

    asset_key = match.group("asset").strip().lower()
    asset = _ASSET_NAMES.get(asset_key)
    if asset is None:
        raise ManualParseError(f"unrecognised asset {match.group('asset')!r} in {title!r}")

    month = _MONTHS.get(match.group("month").lower())
    if month is None:
        raise ManualParseError(f"unrecognised month in {title!r}")

    tzinfo = timezone(timedelta(hours=utc_offset_hours))
    day = int(match.group("day"))
    start = datetime(
        year, month, day, _hour24(int(match.group("h1")), match.group("ap1")),
        int(match.group("m1")), tzinfo=tzinfo,
    )
    end = datetime(
        year, month, day, _hour24(int(match.group("h2")), match.group("ap2")),
        int(match.group("m2")), tzinfo=tzinfo,
    )
    if end <= start:  # the span crossed midnight
        end += timedelta(days=1)

    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
    stamp = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
    minutes = round((end_ts - start_ts) / 60)
    slug = f"{asset.lower()}-up-or-down-{minutes}m-{stamp}z"
    # Deterministic: the same window transcribed twice yields the same ID, so
    # batches can be merged without coordinating.
    condition_id = f"screenshot:{asset}:{start_ts}:{end_ts}"
    return MarketRef(asset, condition_id, slug, title.strip(), start_ts, end_ts)


# --- seam handling -----------------------------------------------------------


def _display_key(row: dict) -> tuple:
    """Identity of a feed row by what is actually printed on screen.

    Excludes the relative time: two screenshots a minute apart show the same
    fill as "4m ago" and "5m ago".
    """
    return (
        str(row.get("market_title") or ""),
        str(row.get("action") or "").lower(),
        str(row.get("outcome") or "").lower(),
        row.get("price_cents_displayed"),
        round(float(row.get("shares") or 0.0), 6),
        round(float(row.get("dollars") or 0.0), 6),
    )


def dedupe_seam(pages: list[dict]) -> tuple[list[dict], int]:
    """Stitch sequential screenshots by their overlapping seam.

    Scroll captures repeat a few rows at the boundary. Matching the longest
    suffix of one page against the prefix of the next removes exactly that
    overlap -- and, unlike a global dedup, preserves a fill that genuinely
    repeats twice in a row (which does happen: identical price and size,
    two separate cards).
    """
    stitched: list[dict] = []
    dropped = 0

    for page_index, page in enumerate(pages):
        rows = []
        for row_index, row in enumerate(page.get("rows", [])):
            enriched = dict(row)
            enriched["_page_image"] = page.get("image")
            enriched["_screenshot_clock"] = page.get("screenshot_clock_local")
            enriched["_row_in_page"] = row_index
            rows.append(enriched)

        if not stitched:
            stitched.extend(rows)
            continue

        overlap = 0
        limit = min(len(stitched), len(rows))
        for size in range(limit, 0, -1):
            tail = [_display_key(r) for r in stitched[-size:]]
            head = [_display_key(r) for r in rows[:size]]
            if tail == head:
                overlap = size
                break
        if overlap == 0 and page_index > 0:
            # Not fatal, but the caller should know the pages may not be
            # contiguous -- a gap means missed fills, not duplicates.
            rows[0] = dict(rows[0], _seam_gap=True)

        # Merge the overlapping observations into the rows we already kept, so
        # their timestamp intervals can be intersected later.
        for offset in range(overlap):
            kept = stitched[len(stitched) - overlap + offset]
            kept.setdefault("_observations", []).append(rows[offset])
        dropped += overlap
        stitched.extend(rows[overlap:])

    return stitched, dropped


# --- timestamps --------------------------------------------------------------


def _clock_ts(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def timestamp_interval(clock_ts: int, relative_minutes: int) -> tuple[int, int]:
    """Bound a fill's time from a relative label and a minute-resolution clock.

    The clock reads `C`, so true clock time is in [C, C+60). The label says
    "N minutes ago", so elapsed is in [N*60, (N+1)*60). Therefore the fill
    happened in (C - (N+1)*60, C - (N-1)*60) -- a two-minute window, not a
    point.
    """
    lo = clock_ts - (relative_minutes + 1) * 60
    hi = clock_ts - (relative_minutes - 1) * 60
    return lo, hi


def _resolve_timestamp(row: dict) -> tuple[int, int]:
    """Intersect every observation of a row. Returns (midpoint, +/- seconds)."""
    observations = [row] + list(row.get("_observations", []))
    lo, hi = None, None
    for observation in observations:
        clock = observation.get("_screenshot_clock") or row.get("_screenshot_clock")
        relative = observation.get("relative_minutes", row.get("relative_minutes"))
        if clock is None or relative is None:
            continue
        o_lo, o_hi = timestamp_interval(_clock_ts(clock), int(relative))
        lo = o_lo if lo is None else max(lo, o_lo)
        hi = o_hi if hi is None else min(hi, o_hi)
    if lo is None or hi is None:
        raise ManualParseError(f"row has no usable clock/relative time: {row}")
    if lo >= hi:
        # Observations disagree beyond their stated precision -- fall back to
        # the first one alone rather than inventing an empty interval.
        clock = _clock_ts(row["_screenshot_clock"])
        lo, hi = timestamp_interval(clock, int(row["relative_minutes"]))
    return (lo + hi) // 2, max((hi - lo) // 2, 1)


# --- conversion --------------------------------------------------------------


@dataclass
class ManualStats:
    pages: int = 0
    rows_read: int = 0
    seam_duplicates_dropped: int = 0
    seam_gaps: int = 0
    trades: int = 0
    non_trades: int = 0
    markets: int = 0
    price_rounding_corrections: int = 0
    unparsed_titles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "rows_read": self.rows_read,
            "seam_duplicates_dropped": self.seam_duplicates_dropped,
            "seam_gaps": self.seam_gaps,
            "trades": self.trades,
            "non_trades": self.non_trades,
            "markets": self.markets,
            "price_rounding_corrections": self.price_rounding_corrections,
            "unparsed_titles": self.unparsed_titles,
        }


def convert_batch(batch: dict) -> tuple[list[dict], list[dict], ManualStats]:
    """Screenshot batch -> (activity records, market records, stats)."""
    stats = ManualStats()
    pages = batch.get("pages", [])
    stats.pages = len(pages)

    rows, dropped = dedupe_seam(pages)
    stats.rows_read = sum(len(p.get("rows", [])) for p in pages)
    stats.seam_duplicates_dropped = dropped
    stats.seam_gaps = sum(1 for r in rows if r.get("_seam_gap"))

    header = batch.get("profile_header", {})
    reference_clock = header.get("screenshot_clock_local") or (
        pages[0].get("screenshot_clock_local") if pages else None
    )
    if reference_clock is None:
        raise ManualParseError("batch has no screenshot_clock_local to anchor times")
    anchor = datetime.fromisoformat(reference_clock)
    year = anchor.year
    offset = (anchor.utcoffset() or timedelta(0)).total_seconds() / 3600.0

    markets: dict[str, MarketRef] = {}
    activity: list[dict] = []

    # The feed is newest-first; iterate reversed so feed_seq counts forward in
    # time and can act as the tiebreaker for equal timestamps.
    for feed_seq, row in enumerate(reversed(rows)):
        title = str(row.get("market_title") or "")
        try:
            market = parse_market_title(title, year, offset)
        except ManualParseError:
            stats.unparsed_titles.append(title)
            continue
        markets.setdefault(market.condition_id, market)

        action = str(row.get("action") or "").strip().lower()
        if action not in _ACTION_TYPES:
            stats.unparsed_titles.append(f"unknown action {action!r} on {title!r}")
            continue
        activity_type, side = _ACTION_TYPES[action]

        timestamp, uncertainty = _resolve_timestamp(row)
        shares = float(row.get("shares") or 0.0)
        dollars = float(row.get("dollars") or 0.0)
        displayed = row.get("price_cents_displayed")

        # Recover the true price from the dollar figure; the cents badge is a
        # rounded display value and disagrees by up to half a cent.
        price = (dollars / shares) if shares > 0 else None
        if price is not None and displayed is not None:
            if abs(price - float(displayed) / 100.0) > 0.0005:
                stats.price_rounding_corrections += 1

        outcome = row.get("outcome")
        record: dict[str, Any] = {
            "type": activity_type,
            "conditionId": market.condition_id,
            "timestamp": timestamp,
            "size": shares,
            "usdcSize": dollars,
            "transactionHash": "",
            "slug": market.slug,
            "eventSlug": market.slug,
            "title": market.title,
            # Provenance, carried through to the CSVs.
            "feedSeq": feed_seq,
            "tsUncertaintyS": uncertainty,
            "priceCentsDisplayed": displayed,
            "screenshotImage": row.get("_page_image"),
        }
        if side is not None:
            record["side"] = side
        if outcome:
            record["outcome"] = str(outcome).title()
            record["outcomeIndex"] = 0 if str(outcome).lower() == "up" else 1
            record["asset"] = f"{market.condition_id}:{str(outcome).upper()}"
        if price is not None:
            record["price"] = price

        activity.append(record)
        if activity_type == "TRADE":
            stats.trades += 1
        else:
            stats.non_trades += 1

    market_records = [
        {
            "id": market.condition_id,
            "conditionId": market.condition_id,
            "slug": market.slug,
            "question": market.title,
            "gameStartTime": datetime.fromtimestamp(market.start_ts, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "endDate": datetime.fromtimestamp(market.end_ts, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "outcomes": json.dumps(["Up", "Down"]),
            "clobTokenIds": json.dumps(
                [f"{market.condition_id}:UP", f"{market.condition_id}:DOWN"]
            ),
            "closed": True,
            "events": [{"slug": market.slug, "title": market.title}],
        }
        for market in markets.values()
    ]
    stats.markets = len(market_records)
    return activity, market_records, stats


def load_batches(paths: Iterable[str]) -> tuple[list[dict], list[dict], list[ManualStats]]:
    activity: list[dict] = []
    markets: dict[str, dict] = {}
    all_stats: list[ManualStats] = []
    for path in sorted(paths):
        with open(path, encoding="utf-8") as handle:
            batch = json.load(handle)
        batch_activity, batch_markets, stats = convert_batch(batch)
        activity.extend(batch_activity)
        for market in batch_markets:
            markets.setdefault(market["conditionId"], market)
        all_stats.append(stats)
    return activity, list(markets.values()), all_stats
