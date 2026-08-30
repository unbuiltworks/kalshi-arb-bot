"""Fetch orchestration: pull everything the public APIs will give us, cache raw.

Endpoint availability and parameter support drift over time, so every call site
degrades gracefully: a 404/400 records the failure in the raw cache and in the
fetch report instead of aborting the run. Nothing here infers or synthesises
data -- if an endpoint is unavailable, the downstream analysis says so.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from . import config
from .cache import RawCache, coerce_items
from .http_client import get_json
from .timeutil import now_iso


def _fingerprint(item: Dict[str, Any]) -> str:
    keys = ("transactionHash", "timestamp", "asset", "side", "size", "price", "conditionId", "id")
    return "|".join(str(item.get(key, "")) for key in keys)


def paginate(
    url: str,
    base_params: Dict[str, Any],
    *,
    cache: RawCache,
    tag: str,
    limit: int = config.PAGE_LIMIT,
    max_pages: int = config.MAX_PAGES,
    opener: Any = None,
) -> Dict[str, Any]:
    """Offset-paginate a list endpoint until a short page, a repeat, or an error.

    Returns a report dict; the items themselves live in the raw cache.
    """
    report = {
        "url": url,
        "tag": tag,
        "params": dict(base_params),
        "pages": 0,
        "items": 0,
        "stopped_because": "unknown",
        "statuses": [],
    }
    seen_first: set = set()
    offset = 0
    for page in range(max_pages):
        params = dict(base_params)
        params.update({"limit": limit, "offset": offset})
        status, payload, error = get_json(
            url, params, cache=cache, tag=tag, opener=opener
        )
        report["pages"] += 1
        report["statuses"].append(status)
        if status != 200:
            report["stopped_because"] = f"http_{status}: {error[:200]}"
            break
        items = coerce_items(payload)
        if not items:
            report["stopped_because"] = "empty_page"
            break
        # Guard against an endpoint that silently ignores `offset`.
        head = _fingerprint(items[0])
        if head in seen_first:
            report["stopped_because"] = "offset_ignored_repeat_page"
            break
        seen_first.add(head)
        report["items"] += len(items)
        if len(items) < limit:
            report["stopped_because"] = "short_page"
            break
        offset += limit
    else:
        report["stopped_because"] = "max_pages_reached"
    return report


def fetch_activity(address: str, *, cache: RawCache, opener: Any = None, **kw) -> Dict[str, Any]:
    """User activity feed -- the primary source of executed fills."""
    return paginate(
        f"{config.DATA_API}/activity",
        {
            "user": address,
            "sortBy": "TIMESTAMP",
            "sortDirection": "ASC",
        },
        cache=cache,
        tag="activity",
        opener=opener,
        **kw,
    )


def fetch_activity_windowed(
    address: str,
    *,
    cache: RawCache,
    opener: Any = None,
    limit: int = config.PAGE_LIMIT,
    max_total_pages: int = 100,
    window_page_cap: int = 9,
) -> Dict[str, Any]:
    """Walk /activity newest-first past its ~5000 offset cap.

    The endpoint rejects offsets beyond ~5000, but accepts unix-second
    ``start``/``end`` filters. So: page DESC inside a window, then slide ``end``
    down to the oldest timestamp seen and reset the offset. The one-second
    overlap at each boundary is intentional -- downstream dedup removes it.
    ``window_page_cap`` stays under the venue's offset cap (9 * 500 = 4500).
    """
    report: Dict[str, Any] = {
        "url": f"{config.DATA_API}/activity",
        "tag": "activity",
        "mode": "windowed_desc",
        "windows": 0,
        "pages": 0,
        "items": 0,
        "oldest_ts": None,
        "newest_ts": None,
        "stopped_because": "unknown",
    }
    from .timeutil import to_epoch_s

    end: Any = None
    while report["pages"] < max_total_pages:
        window_min: Any = None
        window_rows = 0
        offset = 0
        for _page in range(window_page_cap):
            params: Dict[str, Any] = {
                "user": address,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "limit": limit,
                "offset": offset,
            }
            if end is not None:
                params["end"] = int(end)
            status, payload, error = get_json(
                f"{config.DATA_API}/activity", params, cache=cache, tag="activity", opener=opener
            )
            report["pages"] += 1
            if status != 200:
                report["stopped_because"] = f"http_{status}: {error[:200]}"
                return report
            items = coerce_items(payload)
            window_rows += len(items)
            report["items"] += len(items)
            for item in items:
                ts = to_epoch_s(item.get("timestamp"))
                if ts is None:
                    continue
                if window_min is None or ts < window_min:
                    window_min = ts
                if report["newest_ts"] is None or ts > report["newest_ts"]:
                    report["newest_ts"] = ts
                if report["oldest_ts"] is None or ts < report["oldest_ts"]:
                    report["oldest_ts"] = ts
            if len(items) < limit:
                break
            offset += limit
            if report["pages"] >= max_total_pages:
                break
        report["windows"] += 1
        if window_rows == 0:
            report["stopped_because"] = "history_exhausted"
            return report
        if window_min is None or (end is not None and window_min >= int(end)):
            # No progress. A short window means we just re-read the boundary
            # second at the very start of history: that is exhaustion. A *full*
            # window with no progress would mean one second holds more rows
            # than a whole window can page -- stop rather than loop.
            report["stopped_because"] = (
                "history_exhausted" if window_rows < limit else "cursor_stalled"
            )
            return report
        end = window_min
    report["stopped_because"] = "max_total_pages"
    return report


def fetch_trades(address: str, *, cache: RawCache, opener: Any = None, **kw) -> Dict[str, Any]:
    """Secondary fill source; used for cross-checking activity coverage."""
    return paginate(
        f"{config.DATA_API}/trades",
        {"user": address, "takerOnly": "false"},
        cache=cache,
        tag="trades",
        opener=opener,
        **kw,
    )


def fetch_positions(address: str, *, cache: RawCache, opener: Any = None, **kw) -> List[Dict[str, Any]]:
    """Position rows, including realized P&L where Polymarket exposes it."""
    reports = []
    for label, extra in (
        ("positions_all", {"sizeThreshold": 0}),
        ("positions_redeemable", {"sizeThreshold": 0, "redeemable": "true"}),
        ("positions_closed", {"sizeThreshold": 0, "closed": "true"}),
    ):
        params = {"user": address}
        params.update(extra)
        reports.append(
            paginate(
                f"{config.DATA_API}/positions",
                params,
                cache=cache,
                tag=label,
                opener=opener,
                **kw,
            )
        )
    return reports


def fetch_markets_by_condition(
    condition_ids: Iterable[str], *, cache: RawCache, chunk: int = 20, opener: Any = None
) -> Dict[str, Any]:
    """Market metadata from Gamma, batched by condition id, with a per-id fallback."""
    ids = [cid for cid in dict.fromkeys(condition_ids) if cid]
    report = {"requested": len(ids), "chunks_ok": 0, "chunks_failed": 0, "fallback_ok": 0, "fallback_failed": 0}
    # Gamma's /markets excludes closed markets unless closed=true is passed, and a
    # historical study is almost entirely closed markets -- so query each chunk in
    # both states. The market index dedups by condition id downstream.
    variants = ({"closed": "true"}, {})
    for start in range(0, len(ids), chunk):
        batch = ids[start : start + chunk]
        chunk_ok = True
        for extra in variants:
            params = {"condition_ids": batch, "limit": len(batch)}
            params.update(extra)
            status, _payload, _error = get_json(
                f"{config.GAMMA_API}/markets",
                params,
                cache=cache,
                tag="markets",
                opener=opener,
            )
            if status != 200:
                chunk_ok = False
        if chunk_ok:
            report["chunks_ok"] += 1
            continue
        report["chunks_failed"] += 1
        for cid in batch:
            single, _p, _e = get_json(
                f"{config.GAMMA_API}/markets",
                {"condition_ids": cid, "closed": "true"},
                cache=cache,
                tag="markets",
                opener=opener,
            )
            if single == 200:
                report["fallback_ok"] += 1
            else:
                report["fallback_failed"] += 1
                get_json(
                    f"{config.CLOB_API}/markets/{cid}",
                    None,
                    cache=cache,
                    tag="markets_clob",
                    opener=opener,
                )
    return report


def fetch_events_by_slug(
    slugs: Iterable[str], *, cache: RawCache, opener: Any = None, max_slugs: Optional[int] = None
) -> Dict[str, Any]:
    """Event metadata -- gives series/window grouping independent of the market title."""
    unique = [slug for slug in dict.fromkeys(slugs) if slug]
    if max_slugs is not None:
        unique = unique[:max_slugs]
    report = {"requested": len(unique), "ok": 0, "failed": 0}
    for slug in unique:
        status, _payload, _error = get_json(
            f"{config.GAMMA_API}/events",
            {"slug": slug},
            cache=cache,
            tag="events",
            opener=opener,
        )
        if status == 200:
            report["ok"] += 1
        else:
            report["failed"] += 1
    return report


def run_fetch(
    address: str = config.TARGET_ADDRESS,
    *,
    cache: Optional[RawCache] = None,
    opener: Any = None,
    fresh: bool = True,
    max_event_slugs: Optional[int] = None,
) -> Dict[str, Any]:
    """Full read-only harvest. Returns a fetch report suitable for the manifest."""
    cache = cache or RawCache(config.RAW_DIR)
    if fresh:
        for tag in (
            "activity",
            "trades",
            "positions_all",
            "positions_redeemable",
            "positions_closed",
            "markets",
            "markets_clob",
            "events",
        ):
            cache.reset(tag)

    report: Dict[str, Any] = {
        "started_at": now_iso(),
        "address": address,
        "profile_slug": config.TARGET_PROFILE_SLUG,
    }
    report["activity"] = fetch_activity(address, cache=cache, opener=opener)
    report["trades"] = fetch_trades(address, cache=cache, opener=opener)
    report["positions"] = fetch_positions(address, cache=cache, opener=opener)

    condition_ids: List[str] = []
    event_slugs: List[str] = []
    for tag in ("activity", "trades", "positions_all", "positions_redeemable", "positions_closed"):
        for batch in cache.payload_batches(tag):
            for item in batch:
                cid = item.get("conditionId") or item.get("condition_id")
                if cid:
                    condition_ids.append(cid)
                slug = item.get("eventSlug") or item.get("event_slug")
                if slug:
                    event_slugs.append(slug)

    report["markets"] = fetch_markets_by_condition(condition_ids, cache=cache, opener=opener)
    report["events"] = fetch_events_by_slug(
        event_slugs, cache=cache, opener=opener, max_slugs=max_event_slugs
    )
    report["finished_at"] = now_iso()
    return report
