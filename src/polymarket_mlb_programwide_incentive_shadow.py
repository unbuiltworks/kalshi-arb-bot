"""Read-only Polymarket US MLB moneyline program-wide incentive denominator.

This is instrumentation only. It follows the documented Polymarket US model:
* Target Size eligibility uses raw resting size per side.
* Each qualifying book side is independently normalized to 1.0 per snapshot.
* A program reward pool is shared across the program's markets.

Therefore a market's discounted score is a LOCAL competition denominator, not
Target coverage, and the program-wide instantaneous denominator is the count of
qualifying normalized book sides in that program phase. No reward estimate here
is realized P&L and no order transport exists.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.mlb_game_side_normalization import normalize_polymarket_mlb
from src.sports_maker_incentive_telemetry import (
    POLY_GATEWAY,
    POLY_INCENTIVES,
    _dec,
    _expected_poly_period,
    _poly_level,
    _poly_period_name,
    _unwrap_poly_book,
    polymarket_side_score,
)
from src.universal_three_venue_sports import fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.poly_mlb_programwide_incentives")
D = Decimal
ZERO = D("0")
SCENARIOS = (1, 10, 100, 500)


async def _book(http: httpx.AsyncClient, slug: str, sem: asyncio.Semaphore) -> tuple[str, dict[str, Any] | None]:
    async with sem:
        try:
            r = await http.get(f"{POLY_GATEWAY}/v1/markets/{slug}/book")
            r.raise_for_status()
            return slug, _unwrap_poly_book(r.json())
        except Exception:
            return slug, None


async def snapshot() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await fetch_polymarket_mlb(http)
        moneyline = [
            row for row in normalize_polymarket_mlb(events)
            if row.identity.market_family == "moneyline"
        ]
        start_by_slug: dict[str, int] = {}
        for row in moneyline:
            start_by_slug[row.venue_market_id] = int(row.identity.event.scheduled_start_ms)
        slugs = sorted(start_by_slug)

        params: list[tuple[str, str]] = [("pageSize", "500"), ("statuses", "active")]
        params.extend(("symbols", slug) for slug in slugs)
        r = await http.get(POLY_INCENTIVES, params=params)
        r.raise_for_status()
        body = r.json()
        programs = [x for x in (body.get("programs") or []) if isinstance(x, dict)] if isinstance(body, dict) else []

        selected: list[tuple[str, dict[str, Any], str]] = []
        for row in programs:
            slug = str(row.get("marketSlug") or "")
            start_ms = start_by_slug.get(slug)
            if not slug or start_ms is None:
                continue
            expected = _expected_poly_period(now_ms, start_ms)
            for period in row.get("timePeriods") or []:
                if not isinstance(period, dict):
                    continue
                period_name = _poly_period_name(period.get("period"))
                if period_name == "dayof":
                    period_name = "day_of"
                if period_name != expected:
                    continue
                selected.append((slug, period, expected))
                break

        sem = asyncio.Semaphore(8)
        books = dict(await asyncio.gather(*(_book(http, slug, sem) for slug, _, _ in selected))) if selected else {}

    groups: dict[str, list[dict[str, Any]]] = {}
    for slug, period, phase in selected:
        book = books.get(slug) or {}
        bids = [x for x in (_poly_level(v) for v in (book.get("bids") or [])) if x is not None]
        asks = [x for x in (_poly_level(v) for v in (book.get("offers") or book.get("asks") or [])) if x is not None]
        target = _dec(period.get("targetSize"))
        discount = _dec(period.get("discountFactor"))
        bid = polymarket_side_score(bids, target=target, discount=discount, ascending=False)
        ask = polymarket_side_score(asks, target=target, discount=discount, ascending=True)
        program_id = str(period.get("programId") or f"unknown:{phase}")
        reward = _dec(period.get("rewardPool"))
        row = {
            "market": slug,
            "phase": phase,
            "program_id": program_id,
            "reward_pool_usd": str(reward),
            "target_size": str(target),
            "bid_raw_coverage": str((bid.total_raw_size / target) if target > 0 else ZERO),
            "ask_raw_coverage": str((ask.total_raw_size / target) if target > 0 else ZERO),
            "bid_target_met": bid.target_met,
            "ask_target_met": ask.target_met,
            "bid_local_discounted_score": str(bid.raw_score),
            "ask_local_discounted_score": str(ask.raw_score),
            "time_to_game_minutes": str((D(start_by_slug[slug]) - D(now_ms)) / D("60000")),
        }
        groups.setdefault(program_id, []).append(row)

    summaries: list[dict[str, Any]] = []
    for program_id, rows in groups.items():
        qualifying_sides = sum(int(r["bid_target_met"]) + int(r["ask_target_met"]) for r in rows)
        reward = _dec(rows[0].get("reward_pool_usd")) if rows else ZERO
        ranked: list[dict[str, Any]] = []
        for row in rows:
            for side in ("bid", "ask"):
                if not row[f"{side}_target_met"]:
                    continue
                local_score = _dec(row[f"{side}_local_discounted_score"])
                scenarios: dict[str, Any] = {}
                for size in SCENARIOS:
                    own_score = D(size)  # hypothetical at current best price, 0 ticks
                    local_share = own_score / (local_score + own_score) if local_score + own_score > 0 else ZERO
                    program_snapshot_share = local_share / D(qualifying_sides) if qualifying_sides > 0 else ZERO
                    scenarios[str(size)] = {
                        "local_side_share_if_at_best": str(local_share),
                        "program_snapshot_share_if_at_best": str(program_snapshot_share),
                        "pool_dollars_equivalent_if_share_held_for_entire_program_weight": str(reward * program_snapshot_share),
                    }
                ranked.append({
                    "market": row["market"],
                    "side": side,
                    "phase": row["phase"],
                    "time_to_game_minutes": row["time_to_game_minutes"],
                    "raw_target_coverage": row[f"{side}_raw_coverage"],
                    "local_discounted_score": str(local_score),
                    "scenarios_at_best": scenarios,
                })
        ranked.sort(key=lambda x: (_dec(x["local_discounted_score"]), x["market"], x["side"]))
        summaries.append({
            "program_id": program_id,
            "phase": rows[0]["phase"] if rows else None,
            "reward_pool_usd": str(reward),
            "markets_in_current_phase": len(rows),
            "qualifying_normalized_book_sides": qualifying_sides,
            "under_raw_target_sides": sum((not r["bid_target_met"]) + (not r["ask_target_met"]) for r in rows),
            "lowest_local_competition": ranked[:10],
        })

    summaries.sort(key=lambda x: (-_dec(x["reward_pool_usd"]), str(x["program_id"])))
    result = {
        "classification": "READ_ONLY_PROGRAMWIDE_INCENTIVE_DENOMINATOR_NOT_REWARD_CREDIT",
        "mlb_moneyline_markets_discovered": len(slugs),
        "market_program_rows_selected": len(selected),
        "programs": summaries,
        "allocation_rule": "PROGRAM_POOL_SHARED; EACH_TARGET_QUALIFIED_BOOK_SIDE_NORMALIZED_TO_ONE_PER_SNAPSHOT",
        "target_rule": "RAW_SIZE_NOT_DISCOUNTED_SCORE",
        "daily_yield_status": "PENDING_TIME_WEIGHTING_AND_PICKOFF_COST",
        "capacity_credit_usd_per_day": 0,
        "read_only": True,
        "order_entry": False,
        "ts_ms": now_ms,
    }
    logger.warning("KPMM_PROGRAMWIDE_INCENTIVE %s", json.dumps(result, separators=(",", ":")))
    return result


async def run_forever(interval_s: float = 60.0) -> None:
    while True:
        started = time.monotonic()
        try:
            await snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("KPMM_PROGRAMWIDE_INCENTIVE_ERROR")
        await asyncio.sleep(max(1.0, interval_s - (time.monotonic() - started)))
