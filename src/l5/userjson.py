"""Ingest user-downloaded pages of the public data API into the raw cache.

When the analysis environment cannot reach data-api.polymarket.com, the user
can open the same URLs in a browser and save the JSON. Those files are the
verbatim API payloads, so they go into the same raw cache the fetcher would
have written, and everything downstream runs identically.

The one thing a data-api activity page lacks is gamma market metadata. For
these markets the slug is machine-generated --

    btc-updown-15m-1788039000
    {asset}-updown-{minutes}m-{unix window start}

-- so a gamma-shaped market record is synthesized from it: start from the
slug's timestamp, end = start + minutes. The title's clock span ("5:30PM-
5:45PM ET") remains an independent cross-check via the classifier; a slug
that disagrees with its title is flagged as a conflict, not resolved
silently. Records whose slug does not match the pattern get no synthesized
market and simply fall out as dropped_no_market, visibly.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from src.l5 import config
from src.l5.http import RawCache

_SLUG = re.compile(r"^(?P<asset>btc|eth)-updown-(?P<minutes>\d+)m-(?P<start>\d{9,11})$")

_ASSETS = {"btc": "Bitcoin", "eth": "Ethereum"}


class UserJsonError(ValueError):
    pass


def parse_slug(slug: str) -> tuple[str, int, int] | None:
    """slug -> (asset, window_minutes, start_ts), or None if not this family."""
    match = _SLUG.match(str(slug or "").strip())
    if not match:
        return None
    return (
        match.group("asset").upper(),
        int(match.group("minutes")),
        int(match.group("start")),
    )


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def synthesize_markets(records: Iterable[dict]) -> tuple[list[dict], list[str]]:
    """Build gamma-shaped market records from activity slugs.

    Token IDs are collected from the rows themselves: each row carries its
    ERC-1155 token in `asset` and its `outcomeIndex`, so after enough rows the
    market's clobTokenIds list is exactly what gamma would have said.
    """
    by_condition: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[int, str]] = defaultdict(dict)
    unrecognized: list[str] = []

    for record in records:
        condition_id = str(record.get("conditionId") or "")
        slug = str(record.get("slug") or "")
        if not condition_id:
            continue
        parsed = parse_slug(slug)
        if parsed is None:
            if slug and slug not in unrecognized:
                unrecognized.append(slug)
            continue
        asset, minutes, start_ts = parsed
        index = record.get("outcomeIndex")
        token = str(record.get("asset") or "")
        if isinstance(index, int) and token:
            tokens[condition_id].setdefault(index, token)

        if condition_id in by_condition:
            continue
        end_ts = start_ts + minutes * 60
        by_condition[condition_id] = {
            "id": condition_id,
            "conditionId": condition_id,
            "slug": slug,
            "question": str(record.get("title") or ""),
            "gameStartTime": _iso(start_ts),
            "endDate": _iso(end_ts),
            "outcomes": json.dumps(["Up", "Down"]),
            "closed": True,
            "events": [{"slug": str(record.get("eventSlug") or slug),
                        "title": str(record.get("title") or "")}],
            # Provenance: this record was derived, not served by gamma.
            "metadataSource": "derived_from_slug",
        }

    markets: list[dict] = []
    for condition_id, market in by_condition.items():
        seen = tokens.get(condition_id, {})
        market["clobTokenIds"] = json.dumps(
            [seen.get(0, f"{condition_id}:0"), seen.get(1, f"{condition_id}:1")]
        )
        markets.append(market)
    return markets, unrecognized


@dataclass
class IngestStats:
    files: list[str] = field(default_factory=list)
    records: int = 0
    trades: int = 0
    non_trades: int = 0
    wrong_wallet: int = 0
    markets_synthesized: int = 0
    unrecognized_slugs: list[str] = field(default_factory=list)
    oldest_ts: int | None = None
    newest_ts: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "records": self.records,
            "trades": self.trades,
            "non_trades": self.non_trades,
            "wrong_wallet": self.wrong_wallet,
            "markets_synthesized": self.markets_synthesized,
            "unrecognized_slugs": self.unrecognized_slugs,
            "oldest_ts": self.oldest_ts,
            "newest_ts": self.newest_ts,
        }


def _load_records(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        for key in ("data", "activity", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            raise UserJsonError(f"{path}: expected a JSON array of activity records")
    if not isinstance(payload, list):
        raise UserJsonError(f"{path}: expected a JSON array of activity records")
    return [r for r in payload if isinstance(r, dict)]


def ingest_files(
    paths: Iterable[str],
    cache_dir: str,
    wallet: str = config.TARGET_WALLET,
) -> IngestStats:
    """Append user-downloaded activity pages to the raw cache.

    Rows for any other wallet are refused rather than silently mixed in --
    a mispasted URL must not corrupt the dataset. Byte-identical rows across
    overlapping pages are handled downstream by the within-source dedup.
    """
    cache = RawCache(cache_dir)
    stats = IngestStats()
    all_records: list[dict] = []

    for path in paths:
        records = _load_records(path)
        kept: list[dict] = []
        for record in records:
            record_wallet = str(record.get("proxyWallet") or "").lower()
            if record_wallet and record_wallet != wallet.lower():
                stats.wrong_wallet += 1
                continue
            kept.append(record)
            ts = record.get("timestamp")
            if isinstance(ts, (int, float)):
                ts = int(ts)
                stats.oldest_ts = ts if stats.oldest_ts is None else min(stats.oldest_ts, ts)
                stats.newest_ts = ts if stats.newest_ts is None else max(stats.newest_ts, ts)
            if str(record.get("type") or "").upper() == "TRADE":
                stats.trades += 1
            else:
                stats.non_trades += 1
        cache.append(
            config.RAW_ACTIVITY_JSONL,
            f"user-upload:{path}",
            {"user": wallet},
            kept,
        )
        stats.files.append(path)
        stats.records += len(kept)
        all_records.extend(kept)

    markets, unrecognized = synthesize_markets(all_records)
    if markets:
        cache.append(
            config.RAW_MARKETS_JSONL,
            "derived:markets-from-activity-slugs",
            {},
            markets,
        )
    stats.markets_synthesized = len(markets)
    stats.unrecognized_slugs = unrecognized
    return stats
