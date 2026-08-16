"""Rate-safe MLB prop scanner with explicit $1 and $5 paper-probe economics.

Read-only research. This module reuses v3 discovery/rate-limit handling and reports
best integer-quantity paired plans under $1 and $5 all-in modeled capital caps.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src import strict_mlb_prop_snapshot_v3 as v3

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v4")
base._fetch_poly_mlb = _fetch_poly_mlb_search


def best_capped_plan(
    poly: base.TopBook,
    kalshi: base.TopBook,
    *,
    kalshi_fee_multiplier: Decimal,
    capital_cap: Decimal,
) -> dict[str, Any] | None:
    """Best positive paired plan whose modeled all-in cost is <= capital_cap."""
    directions = (
        ("POLY_YES_KALSHI_NO", poly.yes_ask, poly.yes_depth, kalshi.no_ask, kalshi.no_depth),
        ("KALSHI_YES_POLY_NO", kalshi.yes_ask, kalshi.yes_depth, poly.no_ask, poly.no_depth),
    )
    best: dict[str, Any] | None = None
    for direction, first_px, first_depth, second_px, second_depth in directions:
        max_depth_q = min(int(first_depth), int(second_depth), 100)
        for q in range(1, max_depth_q + 1):
            if direction == "POLY_YES_KALSHI_NO":
                poly_px, kalshi_px = first_px, second_px
            else:
                kalshi_px, poly_px = first_px, second_px
            poly_fee = base.polymarket_fee(q, poly_px)
            kalshi_fee = base.conservative_kalshi_fee(q, kalshi_px, kalshi_fee_multiplier)
            cost = Decimal(q) * (poly_px + kalshi_px) + poly_fee + kalshi_fee
            if cost > capital_cap:
                break
            profit = Decimal(q) - cost
            if profit <= 0:
                continue
            row = {
                "direction": direction,
                "quantity": q,
                "poly_price": str(poly_px),
                "kalshi_price": str(kalshi_px),
                "poly_fee": str(poly_fee),
                "kalshi_fee_upper_bound": str(kalshi_fee),
                "total_cost_upper_bound": str(cost),
                "capital_cap": str(capital_cap),
                "conservative_locked_profit": str(profit),
                "conservative_locked_profit_cents": str(profit * Decimal("100")),
                "conservative_roi": str(profit / cost) if cost > 0 else "0",
            }
            if best is None or profit > Decimal(best["conservative_locked_profit"]):
                best = row
    return best


def _empty(candidate: base.StrictCandidate, error: str | None) -> dict[str, Any]:
    assessment = base.assess_rule_equivalence(candidate.family)
    return {
        "family": candidate.family,
        "poly_slug": candidate.poly_slug,
        "kalshi_ticker": candidate.kalshi_ticker,
        "player": candidate.player,
        "threshold": str(candidate.threshold),
        "game_date": candidate.game_date,
        "start_skew_seconds": candidate.start_skew_seconds,
        "rule_status": assessment.status.value,
        "required_participation_gate": assessment.required_gate,
        "settlement_equivalent_now": False,
        "book_comparable": False,
        "positive_conservative_plan": None,
        "probe_1usd": None,
        "probe_5usd": None,
        "fetch_error": error,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(
            base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
        )
        candidates = base.discover_strict_candidates(poly_markets, kalshi_events)

        series_meta: dict[str, dict[str, Any]] = {}
        for series in sorted(set(base.FAMILY_SERIES.values())):
            body, error = await v3._fetch_optional(http, f"{base.KALSHI}/series/{series}")
            if body is not None and error is None:
                series_meta[series] = dict(body.get("series") or {})

        slot = int(time.time() // v3.ROTATION_SECONDS)
        selected, batch_start = v3.select_batch(candidates, slot=slot)
        semaphore = asyncio.Semaphore(v3.PRICE_CONCURRENCY)

        async def price(candidate: base.StrictCandidate) -> dict[str, Any]:
            async with semaphore:
                poly_body, poly_error = await v3._fetch_optional(
                    http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book"
                )
                kalshi_body, kalshi_error = await v3._fetch_optional(
                    http,
                    f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                    params={"depth": 1},
                )
            if poly_body is None or kalshi_body is None:
                error = ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing"
                return _empty(candidate, error)

            pbook = base.poly_top_book(poly_body)
            kbook = base.kalshi_top_book(kalshi_body)
            series = base.FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = base._d(meta.get("fee_multiplier"))
            plan = probe1 = probe5 = None
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and Decimal("0") <= multiplier <= Decimal("100")
            ):
                plan = base.best_conservative_plan(pbook, kbook, kalshi_fee_multiplier=multiplier)
                probe1 = best_capped_plan(
                    pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1")
                )
                probe5 = best_capped_plan(
                    pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5")
                )

            assessment = base.assess_rule_equivalence(candidate.family)
            return {
                "family": candidate.family,
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "player": candidate.player,
                "threshold": str(candidate.threshold),
                "game_date": candidate.game_date,
                "start_skew_seconds": candidate.start_skew_seconds,
                "rule_status": assessment.status.value,
                "required_participation_gate": assessment.required_gate,
                "settlement_equivalent_now": False,
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": str(multiplier) if multiplier is not None else None,
                "book_comparable": pbook is not None and kbook is not None,
                "positive_conservative_plan": plan,
                "probe_1usd": probe1,
                "probe_5usd": probe5,
                "fetch_error": None,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }

        priced = await asyncio.gather(*(price(c) for c in selected)) if selected else []

    positive = [r for r in priced if r.get("positive_conservative_plan")]
    probe1 = [r for r in priced if r.get("probe_1usd")]
    probe5 = [r for r in priced if r.get("probe_5usd")]
    failures = [r for r in priced if r.get("fetch_error")]

    def top(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda r: Decimal(r[field]["conservative_locked_profit"]),
            reverse=True,
        )[:20]

    result = {
        "total_strict_candidates": len(candidates),
        "batch_size": len(selected),
        "batch_start": batch_start,
        "rotation_slot": slot,
        "book_comparable": sum(bool(r["book_comparable"]) for r in priced),
        "fetch_failures": len(failures),
        "conservative_positive_pairs": len(positive),
        "probe_1usd_positive_pairs": len(probe1),
        "probe_5usd_positive_pairs": len(probe5),
        "top_probe_1usd": top(probe1, "probe_1usd"),
        "top_probe_5usd": top(probe5, "probe_5usd"),
        "rule_warning": (
            "Positive pricing remains research-only until the required first-PA/first-pitch participation gate "
            "and independently audited settlement equivalence are satisfied."
        ),
        "probe_warning": (
            "$1/$5 plans are capped paper-probe economics after modeled fees; no authentication/order-entry path exists."
        ),
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
    }
    logger.info("STRICT_MLB_PROP_SNAPSHOT_V4 %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
