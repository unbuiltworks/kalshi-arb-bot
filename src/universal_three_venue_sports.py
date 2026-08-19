"""Read-only three-venue MLB game counterpart scanner.

Fetches active Gemini, Polymarket US, and Kalshi game markets, normalizes executable
outcome sides into one canonical identity, and reports exact normal-event overlaps.
It does not authenticate, price order books, or place orders. Rule audits remain
incomplete, so every overlap is research-only until venue settlement rules are
fingerprinted and matched.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import logging
from typing import Any, Mapping

import httpx

from src.mlb_game_side_normalization import (
    KALSHI_SERIES,
    NormalizedSide,
    normalize_gemini_mlb,
    normalize_kalshi_mlb,
    normalize_polymarket_mlb,
)

logger = logging.getLogger("kalshi_arb.universal_three_venue_sports")
GEMINI = "https://api.gemini.com"
POLY = "https://gateway.polymarket.us"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_429_MAX_ATTEMPTS = 4
KALSHI_429_BASE_DELAY_S = 0.25
KALSHI_SERIES_SPACING_S = 0.05


async def _json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _kalshi_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    """Fetch Kalshi public JSON with bounded 429 retry; never substitute stale data."""
    for attempt in range(KALSHI_429_MAX_ATTEMPTS):
        response = await http.get(url, **kwargs)
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else {}

        if attempt + 1 >= KALSHI_429_MAX_ATTEMPTS:
            response.raise_for_status()

        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            server_delay = float(retry_after)
        except (TypeError, ValueError):
            server_delay = 0.0
        delay_s = max(server_delay, KALSHI_429_BASE_DELAY_S * (2 ** attempt))
        delay_s = min(delay_s, 4.0)
        logger.warning(
            "KALSHI_MLB_DISCOVERY_429 attempt=%s/%s retry_after_s=%.3f stale_fallback=false",
            attempt + 1,
            KALSHI_429_MAX_ATTEMPTS,
            delay_s,
        )
        await asyncio.sleep(delay_s)

    raise RuntimeError("unreachable Kalshi 429 retry state")


async def fetch_gemini_mlb(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    limit = 500
    offset = 0
    while True:
        body = await _json(
            http,
            f"{GEMINI}/v1/prediction-markets/events",
            params={"status": "active", "sport": "baseball", "limit": limit, "offset": offset},
        )
        rows = [dict(row) for row in (body.get("data") or body.get("events") or []) if isinstance(row, Mapping)]
        out.extend(rows)
        if len(rows) < limit:
            return out
        offset += limit
        if offset >= 20000:
            logger.warning("Stopping Gemini MLB pagination at defensive 20k ceiling")
            return out


async def fetch_polymarket_mlb(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    limit = 500
    offset = 0
    while True:
        body = await _json(
            http,
            f"{POLY}/v2/leagues/mlb/events",
            params={"limit": limit, "offset": offset, "type": "sport", "section": "general"},
        )
        rows = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
        out.extend(rows)
        if len(rows) < limit:
            return out
        offset += limit
        if offset >= 10000:
            logger.warning("Stopping Polymarket MLB pagination at defensive 10k ceiling")
            return out


async def fetch_kalshi_mlb(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    async def one(series: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(10):
            params: dict[str, Any] = {
                "limit": 200,
                "status": "open",
                "series_ticker": series,
                "with_nested_markets": "true",
            }
            if cursor:
                params["cursor"] = cursor
            body = await _kalshi_json(http, f"{KALSHI}/events", params=params)
            rows = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
            out.extend(rows)
            cursor = str(body.get("cursor") or "")
            if not cursor:
                break
        return out

    # Avoid bursting all MLB series at Kalshi simultaneously. This path is discovery,
    # not price-trigger execution, so a few milliseconds of spacing is a safe tradeoff.
    groups: list[list[dict[str, Any]]] = []
    for index, series in enumerate(KALSHI_SERIES):
        groups.append(await one(series))
        if index + 1 < len(KALSHI_SERIES):
            await asyncio.sleep(KALSHI_SERIES_SPACING_S)
    return [row for group in groups for row in group]


def counterpart_clusters(rows: list[NormalizedSide]) -> list[dict[str, Any]]:
    grouped: dict[str, list[NormalizedSide]] = defaultdict(list)
    for row in rows:
        grouped[row.contract_key].append(row)

    out: list[dict[str, Any]] = []
    for key, matches in grouped.items():
        venues = sorted({row.identity.venue for row in matches})
        if len(venues) < 2:
            continue
        # The contract key intentionally excludes scheduled start; enforce a strict
        # five-minute cross-venue start envelope before reporting a counterpart.
        starts = [row.identity.event.scheduled_start_ms for row in matches]
        start_skew_ms = max(starts) - min(starts)
        if start_skew_ms > 5 * 60 * 1000:
            continue
        representative = matches[0].identity
        out.append({
            "contract_key": key,
            "sport": representative.event.sport,
            "league": representative.event.league,
            "market_family": representative.market_family,
            "subject": representative.subject,
            "stat": representative.stat,
            "comparator": representative.comparator,
            "line": str(representative.line) if representative.line is not None else None,
            "venues": venues,
            "venue_count": len(venues),
            "start_skew_ms": start_skew_ms,
            "members": [
                {
                    "venue": row.identity.venue,
                    "market_id": row.venue_market_id,
                    "side": row.side,
                    "source_label": row.source_label,
                    "scheduled_start_ms": row.identity.event.scheduled_start_ms,
                    "fully_rule_audited": row.identity.fully_rule_audited,
                }
                for row in sorted(matches, key=lambda item: (item.identity.venue, item.venue_market_id, item.side))
            ],
            "execution_eligible": False,
            "status": "NORMAL_EVENT_IDENTITY_MATCH_NEEDS_RULE_AUDIT",
        })
    out.sort(key=lambda row: (row["venue_count"], row["market_family"], row["contract_key"]), reverse=True)
    return out


def summarize(gemini: list[dict[str, Any]], poly: list[dict[str, Any]], kalshi: list[dict[str, Any]]) -> dict[str, Any]:
    g = normalize_gemini_mlb(gemini)
    p = normalize_polymarket_mlb(poly)
    k = normalize_kalshi_mlb(kalshi)
    all_rows = g + p + k
    clusters = counterpart_clusters(all_rows)
    by_family = Counter(row["market_family"] for row in clusters)
    venue_sets = Counter("+".join(row["venues"]) for row in clusters)
    three = [row for row in clusters if row["venue_count"] == 3]
    return {
        "status": "THREE_VENUE_MLB_NORMAL_EVENT_SCAN",
        "raw_events": {"gemini": len(gemini), "polymarket": len(poly), "kalshi": len(kalshi)},
        "normalized_sides": {"gemini": len(g), "polymarket": len(p), "kalshi": len(k)},
        "counterpart_clusters": len(clusters),
        "three_venue_clusters": len(three),
        "clusters_by_family": dict(by_family),
        "clusters_by_venue_set": dict(venue_sets),
        "top_three_venue": three[:30],
        "top_counterparts": clusters[:50],
        "execution_eligible_clusters": 0,
        "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "read_only": True,
        "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, poly, kalshi = await asyncio.gather(
            fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
        )
    result = summarize(gemini, poly, kalshi)
    logger.info("THREE_VENUE_MLB_SCAN %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(run_once()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
