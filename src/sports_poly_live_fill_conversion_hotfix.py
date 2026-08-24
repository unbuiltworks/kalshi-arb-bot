"""Production fill-conversion hotfix for the live Polymarket MLB maker lane.

This module changes throughput, not economics or risk:
- harmless no-mutation persistence/certification skips are safe-continue states;
- q=1 makers keep queue position for 180s instead of being churned every 60s;
- initial MLB candidate ranking prefers contracts with observed traded activity,
  while preserving the exact >=3c maker->FOK economics, participation gate,
  one-maker exposure, durable reservation, flatten paths, and $5 rule-risk ceiling.

No settlement or modeled edge is counted as realized P&L here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping

import httpx

import src.sports_poly_direct_mlb_campaign_q1_v3 as mlb_v3
import src.sports_poly_structural_live_supervisor as supervisor
import src.sports_poly_structural_live_supervisor_recovered as recovered

D = Decimal
BASE = "https://gateway.polymarket.us"
ACTIVITY_SAMPLE_ROWS = 16
ACTIVITY_CONCURRENCY = 6
ACTIVITY_TIMEOUT_SECONDS = 4.0
STICKY_REST_SECONDS = 180.0
SAFE_NO_MUTATION_STATUSES = {
    "SKIP_MLB_NEAR_TOUCH_NOT_PERSISTENT",
    "SKIP_MLB_CERT_LOST",
}

_ORIGINAL_FRESH_RANKED = mlb_v3._fresh_ranked


def _d(value: Any) -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return D("0")


def _feed_activity(market: Mapping[str, Any] | None) -> D:
    if not isinstance(market, Mapping):
        return D("0")
    stats = market.get("stats") if isinstance(market.get("stats"), Mapping) else {}
    values = [
        market.get("sharesTraded"),
        market.get("shares_traded"),
        market.get("volume"),
        market.get("volume24hr"),
        market.get("volume24h"),
        market.get("notionalTraded"),
        market.get("notional_traded"),
        stats.get("sharesTraded"),
        stats.get("notionalTraded"),
    ]
    return max((_d(v) for v in values), default=D("0"))


def _activity_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    maker = _d(row.get("activity_maker_shares"))
    hedge = _d(row.get("activity_hedge_shares"))
    combined = maker + hedge
    both = min(maker, hedge)
    return (
        -both,
        -combined,
        not bool(row.get("improves_bid")),
        _d(row.get("maker_gap_to_ask") or "1"),
        -_d(row.get("hedge_depth")),
        -_d(row.get("quote_locked_profit")),
    )


async def _book_activity(slug: str, http: httpx.AsyncClient, sem: asyncio.Semaphore) -> tuple[str, D, D, D]:
    async with sem:
        try:
            response = await http.get(f"{BASE}/v1/markets/{slug}/book")
            if response.status_code != 200:
                return slug, D("0"), D("0"), D("0")
            body = response.json() if response.content else {}
            market_data = body.get("marketData") if isinstance(body, Mapping) and isinstance(body.get("marketData"), Mapping) else body
            stats = market_data.get("stats") if isinstance(market_data, Mapping) and isinstance(market_data.get("stats"), Mapping) else {}
            return (
                slug,
                _d(stats.get("sharesTraded")),
                _d(stats.get("notionalTraded")),
                _d(stats.get("lastTradePx")),
            )
        except Exception:
            return slug, D("0"), D("0"), D("0")


async def _activity_ranked(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
    *,
    require_near_touch: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, health = await _ORIGINAL_FRESH_RANKED(feed, pairs, require_near_touch=require_near_touch)
    if len(rows) <= 1:
        return rows, health

    # Free first-pass activity signal from the event feed, if present.
    prepared: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        maker_slug = str(row.get("maker_slug") or "")
        hedge_slug = str(row.get("hedge_slug") or "")
        row["activity_maker_shares"] = _feed_activity(feed.get(maker_slug))
        row["activity_hedge_shares"] = _feed_activity(feed.get(hedge_slug))
        prepared.append(row)
    prepared.sort(key=_activity_sort_key)

    # Enrich only the best economic/queue candidates with authoritative public
    # book stats. This is ranking telemetry only; it cannot admit an unsafe row.
    sample = prepared[:ACTIVITY_SAMPLE_ROWS]
    slugs = sorted({
        str(row.get(key) or "")
        for row in sample
        for key in ("maker_slug", "hedge_slug")
        if str(row.get(key) or "")
    })
    sem = asyncio.Semaphore(ACTIVITY_CONCURRENCY)
    activity: dict[str, tuple[D, D, D]] = {}
    async with httpx.AsyncClient(timeout=ACTIVITY_TIMEOUT_SECONDS) as http:
        results = await asyncio.gather(*(_book_activity(slug, http, sem) for slug in slugs))
    for slug, shares, notional, last_px in results:
        activity[slug] = (shares, notional, last_px)

    enriched = 0
    for row in sample:
        maker_slug = str(row.get("maker_slug") or "")
        hedge_slug = str(row.get("hedge_slug") or "")
        ms, mn, ml = activity.get(maker_slug, (D("0"), D("0"), D("0")))
        hs, hn, hl = activity.get(hedge_slug, (D("0"), D("0"), D("0")))
        if ms > 0 or hs > 0:
            enriched += 1
        row["activity_maker_shares"] = max(_d(row.get("activity_maker_shares")), ms)
        row["activity_hedge_shares"] = max(_d(row.get("activity_hedge_shares")), hs)
        row["activity_maker_notional"] = mn
        row["activity_hedge_notional"] = hn
        row["activity_maker_last_px"] = ml
        row["activity_hedge_last_px"] = hl

    prepared.sort(key=_activity_sort_key)
    health = dict(health)
    health["activity_sample_rows"] = len(sample)
    health["activity_book_slugs"] = len(slugs)
    health["activity_rows_with_trades"] = enriched
    if prepared:
        top = prepared[0]
        print(
            "SPORTS_MLB_ACTIVITY_RANK "
            + json.dumps(
                {
                    "maker_slug": top.get("maker_slug"),
                    "hedge_slug": top.get("hedge_slug"),
                    "maker_shares_traded": str(top.get("activity_maker_shares") or "0"),
                    "hedge_shares_traded": str(top.get("activity_hedge_shares") or "0"),
                    "maker_gap_to_ask": str(top.get("maker_gap_to_ask") or ""),
                    "hedge_depth": str(top.get("hedge_depth") or ""),
                    "locked_profit": str(top.get("quote_locked_profit") or ""),
                    "order_entry": False,
                    "realized": False,
                },
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )
    return prepared, health


def configure() -> None:
    supervisor.SAFE_CONTINUE_STATUSES.update(SAFE_NO_MUTATION_STATUSES)
    mlb_v3.STICKY_REST_SECONDS = STICKY_REST_SECONDS
    mlb_v3._fresh_ranked = _activity_ranked


async def main() -> None:
    configure()
    await recovered.main()


if __name__ == "__main__":
    asyncio.run(main())
