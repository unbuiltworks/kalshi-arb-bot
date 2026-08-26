"""Read-only liquidity-incentive telemetry for sports maker shadows.

No order entry. This module periodically reads the venues' public incentive
program APIs and public/streaming books, then estimates instantaneous reward
shares for hypothetical resting liquidity. All reward values are shadow
estimates until the venue actually credits them.

Kalshi model follows the currently documented Liquidity Incentive Program:
* each side must reach Target Size or the snapshot is excluded;
* Reference Price is the level where cumulative size reaches Target/5;
* orders helping reach Target Size score size * discount^ticks_below_reference;
* yes/no sides are normalized separately, so one-sided share is divided by 2
  when both sides qualify.

Polymarket US telemetry records active program parameters and per-market
qualifying book size/score. Program pools may be shared across many markets, so
this module does not invent a complete Polymarket reward denominator unless all
program markets are observed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import logging
import time
from typing import Any, Iterable

import httpx

logger = logging.getLogger("kalshi_arb.sports_maker_incentives")
D = Decimal
ZERO = D("0")
ONE = D("1")
KALSHI_INCENTIVES = "https://external-api.kalshi.com/trade-api/v2/incentive_programs"
POLY_INCENTIVES = "https://api.prod.polymarketexchange.com/v1/incentives"
POLY_GATEWAY = "https://gateway.polymarket.us"
SCENARIOS = (1, 10, 100, 500, 2500)
SIX_HOURS_MS = 6 * 60 * 60 * 1000


def _dec(value: Any, default: Decimal = ZERO) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return default


def _parse_ts(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _period_seconds(start: Any, end: Any) -> float | None:
    a = _parse_ts(start)
    b = _parse_ts(end)
    if a is None or b is None or b <= a:
        return None
    return b - a


def _ticks(diff: Decimal, tick: Decimal = D("0.01")) -> int:
    if diff <= 0:
        return 0
    return max(0, int((diff / tick).to_integral_value()))


def _poly_period_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _expected_poly_period(now_ms: int, event_start_ms: int) -> str:
    if now_ms < event_start_ms - SIX_HOURS_MS:
        return "early"
    if now_ms < event_start_ms:
        return "day_of"
    return "live"


def _unwrap_poly_book(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("marketData"), dict):
        return body["marketData"]
    if isinstance(body.get("market_data"), dict):
        return body["market_data"]
    return body


@dataclass(frozen=True)
class SideScore:
    total_raw_size: Decimal
    qualifying_raw_size: Decimal
    raw_score: Decimal
    reference_price: Decimal | None
    target_met: bool
    own_score: Decimal = ZERO


def kalshi_side_score(
    levels: Iterable[tuple[Decimal, Decimal]],
    *,
    target: Decimal,
    discount: Decimal,
    own_price: Decimal | None = None,
    own_size: Decimal = ZERO,
) -> SideScore:
    """Aggregate-book approximation of Kalshi snapshot score for one bid side."""
    merged: dict[Decimal, Decimal] = {}
    for price, size in levels:
        if size > 0:
            merged[price] = merged.get(price, ZERO) + size
    if own_price is not None and own_size > 0:
        merged[own_price] = merged.get(own_price, ZERO) + own_size
    rows = sorted(merged.items(), key=lambda x: x[0], reverse=True)
    total_raw = sum((size for _, size in rows), ZERO)
    if target <= 0 or total_raw < target:
        return SideScore(total_raw, ZERO, ZERO, None, False, ZERO)

    reference_goal = target / D("5")
    cumulative = ZERO
    reference: Decimal | None = None
    for price, size in rows:
        cumulative += size
        if cumulative >= reference_goal:
            reference = price
            break
    if reference is None:
        return SideScore(total_raw, ZERO, ZERO, None, False, ZERO)

    cumulative = ZERO
    score = ZERO
    qualifying = ZERO
    own_score = ZERO
    own_level_counted = False
    for price, size in rows:
        if cumulative >= target:
            break
        cumulative += size
        qualifying += size
        distance = _ticks(reference - price)
        multiplier = ONE if price >= reference else discount ** distance
        score += size * multiplier
        if own_price is not None and price == own_price and own_size > 0:
            own_score = own_size * multiplier
            own_level_counted = True
    if not own_level_counted:
        own_score = ZERO
    return SideScore(total_raw, qualifying, score, reference, True, own_score)


def polymarket_side_score(
    levels: Iterable[tuple[Decimal, Decimal]],
    *,
    target: Decimal,
    discount: Decimal,
    ascending: bool,
) -> SideScore:
    """Polymarket side score: best outward until raw Target Size is reached."""
    rows = sorted(
        [(p, s) for p, s in levels if s > 0],
        key=lambda x: x[0],
        reverse=not ascending,
    )
    total_raw = sum((size for _, size in rows), ZERO)
    if not rows or target <= 0 or total_raw < target:
        return SideScore(total_raw, ZERO, ZERO, rows[0][0] if rows else None, False, ZERO)
    best = rows[0][0]
    cumulative = ZERO
    score = ZERO
    qualifying = ZERO
    for price, size in rows:
        if cumulative >= target:
            break
        cumulative += size
        qualifying += size
        distance = _ticks((price - best) if ascending else (best - price))
        score += size * (discount ** distance)
    return SideScore(total_raw, qualifying, score, best, True, ZERO)


def _poly_level(level: Any) -> tuple[Decimal, Decimal] | None:
    if not isinstance(level, dict):
        return None
    px = level.get("px")
    if isinstance(px, dict):
        px = px.get("value")
    qty = level.get("qty", level.get("quantity"))
    try:
        p = D(str(px))
        q = D(str(qty))
    except Exception:
        return None
    if q <= 0:
        return None
    return p, q


class IncentiveTelemetry:
    def __init__(self, shadow: Any, *, interval_s: float = 60.0) -> None:
        self.shadow = shadow
        self.interval_s = interval_s
        self.last_snapshot: dict[str, Any] = {
            "kalshi_active_programs": 0,
            "poly_active_programs": 0,
            "kalshi_estimated_q1_incentive_daily_run_rate": 0.0,
            "kalshi_estimated_q2500_incentive_daily_run_rate": 0.0,
            "kalshi_snapshot_eligible_markets": 0,
            "polymarket_denominator_complete": False,
            "updated_ms": None,
        }

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.snapshot()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("KPMM_INCENTIVE_TELEMETRY_ERROR")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, self.interval_s - elapsed))

    async def _kalshi_programs(self, http: httpx.AsyncClient) -> list[dict[str, Any]]:
        response = await http.get(
            KALSHI_INCENTIVES,
            params={"status": "active", "type": "liquidity", "limit": 10000},
        )
        response.raise_for_status()
        body = response.json()
        rows = body.get("incentive_programs") if isinstance(body, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict)]

    async def _poly_programs(self, http: httpx.AsyncClient, slugs: list[str]) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [("pageSize", "500"), ("statuses", "active")]
        params.extend(("symbols", slug) for slug in slugs)
        response = await http.get(POLY_INCENTIVES, params=params)
        response.raise_for_status()
        body = response.json()
        rows = body.get("programs") if isinstance(body, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict)]

    async def _kalshi_levels(self, ticker: str) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        # The read-only book stream owns these aggregate level maps. Copy under
        # its lock so incentive math cannot race order-book deltas.
        async with self.shadow.kalshi._lock:  # noqa: SLF001 - intentional instrumentation access
            yes = list((self.shadow.kalshi._yes_bids.get(ticker) or {}).items())  # noqa: SLF001
            no = list((self.shadow.kalshi._no_bids.get(ticker) or {}).items())  # noqa: SLF001
        return yes, no

    async def _poly_book(self, http: httpx.AsyncClient, slug: str) -> dict[str, Any] | None:
        try:
            response = await http.get(f"{POLY_GATEWAY}/v1/markets/{slug}/book")
            response.raise_for_status()
            return _unwrap_poly_book(response.json())
        except Exception:
            return None

    async def snapshot(self) -> None:
        tickers = sorted({p.kalshi.venue_market_id for p in self.shadow.pairs})
        slugs = sorted({p.poly.venue_market_id for p in self.shadow.pairs})
        poly_start_ms: dict[str, int] = {}
        for pair in self.shadow.pairs:
            try:
                poly_start_ms[pair.poly.venue_market_id] = int(pair.poly.identity.event.scheduled_start_ms)
            except Exception:
                continue
        active_by_ticker = {q.ticker: q for q in self.shadow.active.values()}
        now_ms = int(time.time() * 1000)

        async with httpx.AsyncClient(timeout=10.0) as http:
            kalshi_rows, poly_rows = await asyncio.gather(
                self._kalshi_programs(http),
                self._poly_programs(http, slugs),
            )

            k_programs = [r for r in kalshi_rows if str(r.get("market_ticker") or "") in tickers]
            by_kticker: dict[str, list[dict[str, Any]]] = {}
            for row in k_programs:
                by_kticker.setdefault(str(row.get("market_ticker")), []).append(row)

            q1_daily_total = ZERO
            q2500_daily_total = ZERO
            eligible_markets = 0
            for ticker, programs in sorted(by_kticker.items()):
                yes_levels, no_levels = await self._kalshi_levels(ticker)
                q = active_by_ticker.get(ticker)
                for program in programs:
                    target = _dec(program.get("target_size_fp"))
                    discount = _dec(program.get("discount_factor_bps")) / D("10000")
                    reward = _dec(program.get("period_reward"))
                    yes_base = kalshi_side_score(yes_levels, target=target, discount=discount)
                    no_base = kalshi_side_score(no_levels, target=target, discount=discount)
                    snapshot_eligible = yes_base.target_met and no_base.target_met
                    if snapshot_eligible:
                        eligible_markets += 1
                    duration_s = _period_seconds(program.get("start_date"), program.get("end_date"))
                    scenarios: dict[str, Any] = {}
                    if q is not None:
                        base_levels = yes_levels if q.maker_side == "YES" else no_levels
                        other = no_base if q.maker_side == "YES" else yes_base
                        for size in SCENARIOS:
                            side = kalshi_side_score(
                                base_levels,
                                target=target,
                                discount=discount,
                                own_price=q.maker_price,
                                own_size=D(size),
                            )
                            valid = side.target_met and other.target_met
                            side_share = (side.own_score / side.raw_score) if valid and side.raw_score > 0 else ZERO
                            reward_share = side_share / D("2") if valid else ZERO
                            daily_run_rate = ZERO
                            if duration_s and duration_s > 0:
                                daily_run_rate = reward * reward_share * D(str(86400.0 / duration_s))
                            scenarios[str(size)] = {
                                "valid_snapshot": valid,
                                "own_side_share": str(side_share),
                                "reward_share": str(reward_share),
                                "estimated_reward_daily_run_rate_usd": str(daily_run_rate),
                                "estimated_reward_per_contract_daily_run_rate_usd": str(daily_run_rate / D(size)),
                            }
                            if size == 1:
                                q1_daily_total += daily_run_rate
                            if size == 2500:
                                q2500_daily_total += daily_run_rate
                    payload = {
                        "venue": "KALSHI",
                        "market": ticker,
                        "program_id": str(program.get("id") or ""),
                        "description": str(program.get("incentive_description") or ""),
                        "period_reward_usd": str(reward),
                        "period_start": program.get("start_date"),
                        "period_end": program.get("end_date"),
                        "discount_factor": str(discount),
                        "target_size": str(target),
                        "yes_total_raw_size": str(yes_base.total_raw_size),
                        "no_total_raw_size": str(no_base.total_raw_size),
                        "yes_qualifying_raw_size": str(yes_base.qualifying_raw_size),
                        "no_qualifying_raw_size": str(no_base.qualifying_raw_size),
                        "yes_qualifying_score": str(yes_base.raw_score),
                        "no_qualifying_score": str(no_base.raw_score),
                        "yes_reference_price": str(yes_base.reference_price) if yes_base.reference_price is not None else None,
                        "no_reference_price": str(no_base.reference_price) if no_base.reference_price is not None else None,
                        "snapshot_two_sided_target_met": snapshot_eligible,
                        "active_shadow_quote": q is not None,
                        "shadow_maker_side": q.maker_side if q is not None else None,
                        "shadow_maker_price": str(q.maker_price) if q is not None else None,
                        "size_scenarios": scenarios,
                        "estimate_status": "PUBLIC_AGGREGATE_BOOK_APPROXIMATION_NOT_VENUE_CREDIT",
                        "ts_ms": now_ms,
                    }
                    logger.warning("KPMM_INCENTIVE_MARKET %s", json.dumps(payload, separators=(",", ":")))

            # Polymarket: select only the incentive phase actually applicable to
            # each event right now. The API can return early/day-of/live rows in
            # one payload even when only one phase is currently earnable.
            poly_program_periods: list[tuple[str, dict[str, Any], str]] = []
            for row in poly_rows:
                slug = str(row.get("marketSlug") or "")
                start_ms = poly_start_ms.get(slug)
                if not slug or start_ms is None:
                    continue
                expected = _expected_poly_period(now_ms, start_ms)
                for period in row.get("timePeriods") or []:
                    if not isinstance(period, dict):
                        continue
                    explicit_status = str(period.get("status") or "").strip().lower()
                    if explicit_status and explicit_status != "active":
                        continue
                    period_name = _poly_period_name(period.get("period"))
                    if period_name == "dayof":
                        period_name = "day_of"
                    if period_name != expected:
                        continue
                    poly_program_periods.append((slug, period, expected))

            poly_book_sem = asyncio.Semaphore(8)

            async def fetch_one(slug: str) -> tuple[str, dict[str, Any] | None]:
                async with poly_book_sem:
                    return slug, await self._poly_book(http, slug)

            poly_books = dict(
                await asyncio.gather(
                    *(fetch_one(slug) for slug in sorted({s for s, _, _ in poly_program_periods}))
                )
            ) if poly_program_periods else {}

            for slug, period, expected in poly_program_periods:
                book = poly_books.get(slug) or {}
                bids = [x for x in (_poly_level(v) for v in (book.get("bids") or [])) if x is not None]
                asks = [x for x in (_poly_level(v) for v in (book.get("offers") or book.get("asks") or [])) if x is not None]
                target = _dec(period.get("targetSize"))
                discount = _dec(period.get("discountFactor"))
                bid_score = polymarket_side_score(bids, target=target, discount=discount, ascending=False)
                ask_score = polymarket_side_score(asks, target=target, discount=discount, ascending=True)
                logger.warning("KPMM_INCENTIVE_MARKET %s", json.dumps({
                    "venue": "POLYMARKET_US",
                    "market": slug,
                    "program_id": str(period.get("programId") or ""),
                    "period": period.get("period"),
                    "applicable_period_by_event_clock": expected,
                    "event_start_ms": poly_start_ms.get(slug),
                    "period_reward_usd": str(_dec(period.get("rewardPool"))),
                    "period_start": period.get("start"),
                    "period_end": period.get("end"),
                    "discount_factor": str(discount),
                    "target_size": str(target),
                    "bid_total_raw_size": str(bid_score.total_raw_size),
                    "ask_total_raw_size": str(ask_score.total_raw_size),
                    "bid_qualifying_raw_size": str(bid_score.qualifying_raw_size),
                    "ask_qualifying_raw_size": str(ask_score.qualifying_raw_size),
                    "bid_qualifying_score": str(bid_score.raw_score),
                    "ask_qualifying_score": str(ask_score.raw_score),
                    "bid_target_met": bid_score.target_met,
                    "ask_target_met": ask_score.target_met,
                    "program_denominator_complete": False,
                    "estimate_status": "BOOK_SCORE_ONLY_PROGRAM_POOL_SHARE_NOT_ESTIMATED",
                    "ts_ms": now_ms,
                }, separators=(",", ":")))

        self.last_snapshot = {
            "kalshi_active_programs": len(k_programs),
            "poly_active_programs": len(poly_program_periods),
            "kalshi_estimated_q1_incentive_daily_run_rate": float(q1_daily_total),
            "kalshi_estimated_q2500_incentive_daily_run_rate": float(q2500_daily_total),
            "kalshi_snapshot_eligible_markets": eligible_markets,
            "polymarket_denominator_complete": False,
            "updated_ms": now_ms,
        }
        logger.warning("KPMM_INCENTIVE_SUMMARY %s", json.dumps({
            **self.last_snapshot,
            "reward_accounting": "SHADOW_ESTIMATE_NOT_REALIZED",
            "combined_capacity_credit_usd_per_day": 0,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
