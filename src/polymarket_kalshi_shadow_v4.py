"""Strict event-identity launcher for the read-only cross-exchange scanner.

The earlier shadow matcher intentionally started conservative, but a live audit
found a dangerous edge case: Milwaukee/Los Angeles play on consecutive dates.
A date-only key paired Polymarket's Aug-14 game with Kalshi's Aug-13 game and
created a large *false* arbitrage signal.

This launcher therefore fails closed unless the exchanges agree on:
  * exact MLB participants,
  * exact total threshold,
  * full-game total market type,
  * scheduled first-pitch timestamp (within five minutes), and
  * MLB as the settlement/result source.

It remains read-only. No order-entry code is imported or exposed here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src import polymarket_kalshi_shadow as base
from src import polymarket_kalshi_shadow_v2 as scanner

logger = logging.getLogger("kalshi_arb.poly_kalshi_shadow_v4")
ET = ZoneInfo("America/New_York")
MAX_START_SKEW_SECONDS = 5 * 60

# Current Kalshi baseball-total rule text includes wording such as:
# "originally scheduled for Aug 13, 2026 at 10:10 PM EDT"
_SCHEDULED_RE = re.compile(
    r"originally\s+scheduled\s+for\s+"
    r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})\s+at\s+"
    r"(\d{1,2}:\d{2})\s+(AM|PM)\s+(?:EDT|EST|ET)",
    re.I,
)


def _iso_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _kalshi_scheduled_utc(rules_primary: str) -> datetime | None:
    match = _SCHEDULED_RE.search(rules_primary or "")
    if not match:
        return None
    month, day, year, clock, ampm = match.groups()
    try:
        local = datetime.strptime(
            f"{month} {day} {year} {clock} {ampm}",
            "%b %d %Y %I:%M %p",
        ).replace(tzinfo=ET)
    except Exception:
        return None
    return local.astimezone(timezone.utc)


def _poly_result_source_ok(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(key) or "")
        for key in ("description", "question", "title", "subtitle")
    ).lower()
    return "outcome sourced from mlb" in text or "source" in text and "mlb" in text


def _kalshi_result_source_ok(event: dict[str, Any]) -> bool:
    for source in event.get("settlement_sources") or []:
        joined = " ".join(str(v or "") for v in source.values()).lower()
        if "mlb" in joined:
            return True
    return False


async def _get_with_backoff(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    delay = 5.0
    while True:
        response = await http.get(url, params=params)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("retry-after")
        try:
            server_wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            server_wait = 0.0
        wait_s = max(delay, server_wait)
        logger.warning("Discovery rate-limited at %s; retrying in %.1fs", url, wait_s)
        await asyncio.sleep(wait_s)
        delay = min(delay * 2, 60.0)


async def strict_discover_matches(http: httpx.AsyncClient) -> list[base.Match]:
    teams_task = _get_with_backoff(
        http,
        f"{base.POLY_GATEWAY}/v1/sports/teams",
        params={"limit": 500, "filters.league": "mlb"},
    )
    poly_task = _get_with_backoff(
        http,
        f"{base.POLY_GATEWAY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    kalshi_task = _get_with_backoff(
        http,
        f"{base.KALSHI_REST}/events",
        params={
            "limit": 200,
            "status": "open",
            "series_ticker": base.MLB_SERIES,
            "with_nested_markets": "true",
        },
    )
    teams_resp, poly_resp, kalshi_resp = await asyncio.gather(
        teams_task, poly_task, kalshi_task
    )

    abbreviations = {
        str(row.get("abbreviation", "")).lower()
        for row in teams_resp.json().get("teams", [])
        if row.get("abbreviation")
    }

    # Keep raw structured start timestamps beside the base market object.
    poly_rows: list[tuple[base.PolyMarket, datetime]] = []
    poly_rejected_missing_start = 0
    poly_rejected_source = 0
    for event in poly_resp.json().get("events", []):
        event_title = str(event.get("title") or "")
        event_start = _iso_utc(event.get("startTime"))
        for raw_market in event.get("markets") or []:
            parsed = base._poly_market(event_title, raw_market)
            if parsed is None:
                continue
            start = _iso_utc(raw_market.get("gameStartTime")) or event_start
            if start is None:
                poly_rejected_missing_start += 1
                continue
            if not _poly_result_source_ok(raw_market):
                poly_rejected_source += 1
                continue
            poly_rows.append((parsed, start))

    kalshi_rows: list[tuple[base.KalshiMarket, datetime]] = []
    kalshi_rejected_missing_start = 0
    kalshi_rejected_source = 0
    for event in kalshi_resp.json().get("events", []):
        event_ticker = str(event.get("event_ticker") or "")
        teams = base._ticker_teams(event_ticker, abbreviations)
        if teams is None:
            continue
        if not _kalshi_result_source_ok(event):
            kalshi_rejected_source += len(event.get("markets") or [])
            continue
        event_title = str(event.get("title") or event.get("sub_title") or "")
        for raw_market in event.get("markets") or []:
            line = base._kalshi_line(raw_market)
            if line is None:
                continue
            strike_type = str(raw_market.get("strike_type") or "").lower()
            if strike_type and strike_type not in {"greater", "greater_than"}:
                continue
            rules_primary = str(raw_market.get("rules_primary") or "")
            scheduled = _kalshi_scheduled_utc(rules_primary)
            if scheduled is None:
                kalshi_rejected_missing_start += 1
                continue
            kalshi_rows.append(
                (
                    base.KalshiMarket(
                        ticker=str(raw_market.get("ticker") or ""),
                        event_ticker=event_ticker,
                        event_title=event_title,
                        teams=teams,
                        game_date=scheduled.astimezone(ET).date().isoformat(),
                        line=line,
                        strike_type=strike_type,
                        rules_primary=rules_primary,
                    ),
                    scheduled,
                )
            )

    # Compare exact participants + threshold first, then scheduled timestamp.
    # More than one candidate within the tolerance is treated as ambiguous and rejected.
    matches: list[base.Match] = []
    rejected_start_mismatch = 0
    rejected_ambiguous = 0
    for poly, poly_start in poly_rows:
        candidates: list[tuple[base.KalshiMarket, float]] = []
        for kalshi, kalshi_start in kalshi_rows:
            if poly.teams != kalshi.teams or poly.line != kalshi.line:
                continue
            skew = abs((poly_start - kalshi_start).total_seconds())
            if skew <= MAX_START_SKEW_SECONDS:
                candidates.append((kalshi, skew))
            else:
                rejected_start_mismatch += 1
        if len(candidates) != 1:
            if len(candidates) > 1:
                rejected_ambiguous += 1
            continue
        kalshi, skew = candidates[0]
        matches.append(base.Match(poly=poly, kalshi=kalshi))
        logger.info(
            "PAIR_VALIDATED %s",
            json.dumps(
                {
                    "event": poly.event_title,
                    "line": str(poly.line),
                    "poly_slug": poly.slug,
                    "kalshi_ticker": kalshi.ticker,
                    "poly_start_utc": poly_start.isoformat(),
                    "kalshi_start_utc": _kalshi_scheduled_utc(kalshi.rules_primary).isoformat(),
                    "start_skew_seconds": skew,
                    "result_source": "MLB",
                },
                separators=(",", ":"),
            ),
        )

    logger.info(
        "STRICT_DISCOVERY %s",
        json.dumps(
            {
                "poly_candidates": len(poly_rows),
                "kalshi_candidates": len(kalshi_rows),
                "matched": len(matches),
                "poly_rejected_missing_start": poly_rejected_missing_start,
                "poly_rejected_source": poly_rejected_source,
                "kalshi_rejected_missing_start": kalshi_rejected_missing_start,
                "kalshi_rejected_source": kalshi_rejected_source,
                "rejected_start_mismatch_comparisons": rejected_start_mismatch,
                "rejected_ambiguous": rejected_ambiguous,
            },
            separators=(",", ":"),
        ),
    )
    return matches


# v2's main loop is reused, but its discovery function is replaced before startup.
scanner.discover_matches = strict_discover_matches


if __name__ == "__main__":
    scanner.main()
