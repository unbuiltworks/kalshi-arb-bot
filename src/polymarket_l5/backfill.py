"""Backfill the May-Aug hole in the L5 activity history.

The original harvest used the plain ASC offset walk, which the venue caps at
~5000 rows, plus a budget-limited windowed walk from the newest edge — leaving
a gap (2026-04-21 .. 2026-08-28, ~129 days). This module fills exactly that
gap with the timestamp-windowed DESC walk (`fetch_activity_windowed`).

Volume control: mid-summer density is ~8-12k activity rows/day, so the gap is
~1.1-1.5M rows. Caching full payloads would cost ~1GB+, so the opener slims
each activity item to the fields the accounting ledger needs before it is
cached, under the separate tag ``activity_gap`` (the original raw cache is
never touched). The slim rows still carry the transaction hash, so dedup
against the original harvest's overlap seconds works downstream.

Read-only: GET only, public endpoints, no credentials — enforced by
http_client. Research only; nothing here executes trades anywhere.

    python -m src.polymarket_l5.backfill            # fill the largest gap
    python -m src.polymarket_l5.backfill --positions  # also refresh positions
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .cache import RawCache, coerce_items
from .fetch import fetch_positions
from .http_client import _urlopen_json
from .timeutil import now_iso, to_epoch_s

GAP_TAG = "activity_gap"
REPORT_PATH = config.OUTPUT_DIR / "l5_backfill_report.json"

# Everything the ledger, window reconstruction, and dedup need — nothing else.
SLIM_FIELDS = (
    "timestamp", "type", "side", "size", "price", "usdcSize",
    "conditionId", "outcome", "outcomeIndex", "transactionHash",
    "slug", "eventSlug",
)


def slim_opener(full_url: str, timeout: float) -> Tuple[int, Any, str]:
    status, payload, error = _urlopen_json(full_url, timeout)
    if status == 200 and isinstance(payload, list):
        payload = [
            {k: item.get(k) for k in SLIM_FIELDS if item.get(k) is not None}
            for item in payload
            if isinstance(item, dict)
        ]
    return status, payload, error


def cached_timestamps(cache: RawCache, tags=("activity", GAP_TAG)) -> List[int]:
    tss: List[int] = []
    for tag in tags:
        for record in cache.records(tag):
            if record.get("status") != 200:
                continue
            for item in coerce_items(record.get("payload")):
                ts = to_epoch_s(item.get("timestamp"))
                if ts is not None:
                    tss.append(int(ts))
    tss.sort()
    return tss


def largest_gap(tss: List[int], min_gap_s: int = 86400) -> Optional[Tuple[int, int]]:
    """(lo, hi) of the largest hole bigger than min_gap_s, else None."""
    best: Optional[Tuple[int, int]] = None
    prev = None
    for ts in tss:
        if prev is not None and ts - prev > min_gap_s:
            if best is None or (ts - prev) > (best[1] - best[0]):
                best = (prev, ts)
        prev = ts
    return best


def run_backfill(
    *,
    cache: Optional[RawCache] = None,
    max_total_pages: int = 4000,
    refresh_positions: bool = False,
) -> Dict[str, Any]:
    cache = cache or RawCache(config.RAW_DIR)
    tss = cached_timestamps(cache)
    report: Dict[str, Any] = {
        "started_at": now_iso(),
        "address": config.TARGET_ADDRESS,
        "cached_rows_before": len(tss),
    }
    gap = largest_gap(tss)
    if gap is None:
        report["result"] = "no_gap_found"
        return report
    lo, hi = gap
    report["gap"] = {"lo_ts": lo, "hi_ts": hi, "days": round((hi - lo) / 86400, 1)}
    # Walk DESC from just above the gap's top edge down to its bottom edge.
    # +/-1s overlap at both boundaries is deliberate; dedup removes it.
    report["activity_gap"] = _windowed_between(cache, lo, hi, max_total_pages)
    if refresh_positions:
        for tag in ("positions_all", "positions_redeemable", "positions_closed"):
            cache.reset(tag)
        report["positions"] = fetch_positions(config.TARGET_ADDRESS, cache=cache)
    report["finished_at"] = now_iso()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1, default=str))
    return report


def _windowed_between(cache: RawCache, lo: int, hi: int, max_total_pages: int) -> Dict[str, Any]:
    """fetch_activity_windowed, but starting from `hi` instead of from now."""
    # fetch_activity_windowed starts with end=None (i.e. newest). Seed the walk
    # at the gap's top edge by passing end via extra_params on the first window:
    # simplest correct approach is a thin re-implementation of its loop with a
    # seeded `end`, reusing the same primitives.
    from .http_client import get_json

    report: Dict[str, Any] = {
        "url": f"{config.DATA_API}/activity",
        "tag": GAP_TAG,
        "mode": "windowed_desc_between",
        "lo_ts": lo,
        "hi_ts": hi,
        "windows": 0,
        "pages": 0,
        "items": 0,
        "oldest_ts": None,
        "newest_ts": None,
        "stopped_because": "unknown",
    }
    end = hi + 1
    while report["pages"] < max_total_pages:
        window_min: Optional[int] = None
        window_rows = 0
        offset = 0
        for _page in range(9):  # 9 * 500 = 4500 < the venue's ~5000 offset cap
            params = {
                "user": config.TARGET_ADDRESS,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "limit": config.PAGE_LIMIT,
                "offset": offset,
                "end": int(end),
            }
            status, payload, error = get_json(
                f"{config.DATA_API}/activity", params,
                cache=cache, tag=GAP_TAG, opener=slim_opener,
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
                ts = int(ts)
                if window_min is None or ts < window_min:
                    window_min = ts
                if report["newest_ts"] is None or ts > report["newest_ts"]:
                    report["newest_ts"] = ts
                if report["oldest_ts"] is None or ts < report["oldest_ts"]:
                    report["oldest_ts"] = ts
            if len(items) < config.PAGE_LIMIT:
                break
            offset += config.PAGE_LIMIT
            if report["pages"] >= max_total_pages:
                break
        report["windows"] += 1
        if report["oldest_ts"] is not None and report["oldest_ts"] <= lo:
            report["stopped_because"] = "reached_gap_floor"
            return report
        if window_rows == 0:
            report["stopped_because"] = "history_exhausted"
            return report
        if window_min is None or window_min >= int(end):
            report["stopped_because"] = (
                "history_exhausted" if window_rows < config.PAGE_LIMIT else "cursor_stalled"
            )
            return report
        end = window_min
    report["stopped_because"] = "max_total_pages"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=4000)
    ap.add_argument("--positions", action="store_true",
                    help="also refresh the three positions caches")
    args = ap.parse_args()
    report = run_backfill(max_total_pages=args.max_pages,
                          refresh_positions=args.positions)
    gap_rep = report.get("activity_gap", {})
    print(json.dumps({k: gap_rep.get(k) for k in
                      ("items", "pages", "windows", "oldest_ts", "newest_ts",
                       "stopped_because")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
