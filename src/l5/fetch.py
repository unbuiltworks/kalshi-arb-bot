"""Read-only retrieval of one trader's public Polymarket history.

Endpoints used (all public, all GET, no credentials):

  data-api  /activity   -- the fullest history; supports start/end cursors
  data-api  /trades     -- executed fills; offset paging only
  data-api  /positions  -- per-market position rows incl. realizedPnl
  gamma-api /markets    -- market metadata used to *derive* window geometry

Two limits are inherent to these endpoints and are recorded in the manifest
rather than papered over:

  * only executed fills are public -- resting and cancelled orders are not,
  * `offset` stops being honoured past a few thousand rows, so /activity is
    walked backwards with an `end` cursor and /trades is marked truncated if
    it hits the cap.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from src.l5 import config
from src.l5.http import FetchError, HttpJson, RawCache, _iter_records


@dataclass
class FetchStats:
    name: str
    endpoint: str
    pages: int = 0
    records: int = 0
    truncated: bool = False
    truncation_reason: str = ""
    oldest_ts: int | None = None
    newest_ts: int | None = None
    errors: list[str] = field(default_factory=list)


def _timestamps(records: Iterable[dict]) -> list[int]:
    out = []
    for record in records:
        raw = record.get("timestamp")
        if isinstance(raw, (int, float)) and raw > 0:
            out.append(int(raw))
        elif isinstance(raw, str) and raw.isdigit():
            out.append(int(raw))
    return out


def _is_offset_cap_error(exc: FetchError, offset: int) -> bool:
    """Treat Polymarket's 400/422 at deep offsets as an endpoint cap.

    The data API currently rejects deep offsets before our conservative static
    MAX_OFFSET is reached. That is not a fatal fetch error: /activity can step
    backwards with an `end` cursor, while offset-only endpoints should mark the
    older tail truncated rather than crash the entire read-only pipeline.
    """
    if offset <= 0:
        return False
    text = str(exc)
    return "-> HTTP 400" in text or "-> HTTP 422" in text


def _page_sweep(
    http: HttpJson,
    cache: RawCache,
    stats: FetchStats,
    base_params: dict[str, Any],
    max_offset: int,
) -> tuple[bool, int | None]:
    """Walk `offset` until a short page or the endpoint's effective cap.

    Returns (exhausted, oldest_timestamp_seen). `exhausted` is True when a
    short page proved we reached the end of this slice. A deep-offset 400/422
    is treated as the venue's effective offset cap and returns exhausted=False.
    """
    offset = 0
    oldest: int | None = None
    while True:
        params = dict(base_params)
        params["limit"] = config.PAGE_LIMIT
        params["offset"] = offset
        try:
            payload = http.get(stats.endpoint, params)
        except FetchError as exc:
            if _is_offset_cap_error(exc, offset):
                stats.errors.append(
                    f"effective offset cap at {offset}; switching/truncating as designed: {exc}"
                )
                return False, oldest
            raise
        records = list(_iter_records(payload))
        cache.append(stats.name, stats.endpoint, params, payload)
        stats.pages += 1
        stats.records += len(records)

        for ts in _timestamps(records):
            oldest = ts if oldest is None else min(oldest, ts)
            stats.oldest_ts = ts if stats.oldest_ts is None else min(stats.oldest_ts, ts)
            stats.newest_ts = ts if stats.newest_ts is None else max(stats.newest_ts, ts)

        if len(records) < config.PAGE_LIMIT:
            return True, oldest
        offset += config.PAGE_LIMIT
        if offset > max_offset:
            return False, oldest


def fetch_activity(
    http: HttpJson,
    cache: RawCache,
    wallet: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> FetchStats:
    """Page /activity backwards in time, past the effective offset cap."""
    stats = FetchStats(name=config.RAW_ACTIVITY_JSONL, endpoint=f"{config.DATA_API}/activity")
    cursor_end = end_ts
    guard = 0
    while True:
        guard += 1
        if guard > 500:  # pathological loop guard; never expected to trip
            stats.truncated = True
            stats.truncation_reason = "time-cursor guard tripped after 500 slices"
            return stats
        base: dict[str, Any] = {"user": wallet}
        if start_ts is not None:
            base["start"] = start_ts
        if cursor_end is not None:
            base["end"] = cursor_end
        exhausted, oldest = _page_sweep(http, cache, stats, base, config.MAX_OFFSET)
        if exhausted:
            return stats
        if oldest is None:
            stats.truncated = True
            stats.truncation_reason = "offset cap reached and no timestamps to cursor on"
            return stats
        # Step the cursor back. Overlap of one second is deliberate -- losing a
        # trade is worse than a duplicate, and dedup removes the overlap.
        next_end = oldest
        if cursor_end is not None and next_end >= cursor_end:
            stats.truncated = True
            stats.truncation_reason = "time cursor stopped advancing"
            return stats
        cursor_end = next_end


def fetch_trades(http: HttpJson, cache: RawCache, wallet: str) -> FetchStats:
    """Page /trades by offset.

    `takerOnly=false` is essential: the default hides fills where this wallet
    was the maker, which for a two-sided strategy is most of them.
    """
    stats = FetchStats(name=config.RAW_TRADES_JSONL, endpoint=f"{config.DATA_API}/trades")
    base = {"user": wallet, "takerOnly": False}
    exhausted, _ = _page_sweep(http, cache, stats, base, config.MAX_OFFSET)
    if not exhausted:
        stats.truncated = True
        stats.truncation_reason = (
            f"/trades effective offset cap reached; endpoint has no time cursor. "
            "Older fills come from /activity."
        )
    return stats


def fetch_positions(http: HttpJson, cache: RawCache, wallet: str) -> FetchStats:
    """Page /positions. `sizeThreshold=0` keeps fully-closed rows when exposed.

    Positions are only a fallback P&L source downstream, and only when both
    settled outcome rows are present. Redemption-derived activity accounting is
    preferred precisely because size-sorted positions pages can omit redeemed winners.
    """
    stats = FetchStats(name=config.RAW_POSITIONS_JSONL, endpoint=f"{config.DATA_API}/positions")
    base = {"user": wallet, "sizeThreshold": 0}
    exhausted, _ = _page_sweep(http, cache, stats, base, config.MAX_OFFSET)
    if not exhausted:
        stats.truncated = True
        stats.truncation_reason = "/positions effective offset cap reached"
    return stats


def fetch_markets(
    http: HttpJson, cache: RawCache, condition_ids: Iterable[str], batch_size: int = 20
) -> FetchStats:
    """Look up gamma market metadata for the condition IDs seen in activity."""
    stats = FetchStats(name=config.RAW_MARKETS_JSONL, endpoint=f"{config.GAMMA_API}/markets")
    unique = sorted({cid for cid in condition_ids if cid})
    for index in range(0, len(unique), batch_size):
        batch = unique[index : index + batch_size]
        params = {"condition_ids": batch, "limit": batch_size}
        try:
            payload = http.get(stats.endpoint, params)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            stats.errors.append(f"{batch[0]}..{batch[-1]}: {exc}")
            continue
        records = list(_iter_records(payload))
        cache.append(stats.name, stats.endpoint, params, payload)
        stats.pages += 1
        stats.records += len(records)
    return stats


def write_manifest(cache: RawCache, wallet: str, profile: str, stats: list[FetchStats]) -> dict:
    manifest = {
        "wallet": wallet,
        "profile": profile,
        "generated_at": int(time.time()),
        "read_only": True,
        "endpoints": [asdict(s) for s in stats],
        "caveats": [
            "Public activity exposes executed fills only. Resting and cancelled "
            "orders are not retrievable and are not inferred anywhere downstream.",
            "No historical order-book state is reconstructed.",
            "Settlement-derived P&L prefers REDEEM activity minus net trading spend; "
            "positions are only a both-sides-present fallback. Rebates, incentives and gas are separate.",
        ],
    }
    with open(cache.path(config.FETCH_MANIFEST_JSON), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    return manifest
