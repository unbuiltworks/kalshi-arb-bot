"""Rate-safe read-only MLB prop pricing scan.

Discovery and strict identity come from v2/base.  This wrapper exists because a full
slate can contain >1k exact counterparts and pricing every book at once trips public
API rate limits.  It rotates deterministic 60-pair batches across the exact universe,
uses low concurrency, retries 429s with backoff, and never lets one failed book abort
the scan.

Research only: no authentication or order-entry path is added.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v3")
BATCH_SIZE = 60
PRICE_CONCURRENCY = 2
REQUEST_PACE_SECONDS = 0.12
ROTATION_SECONDS = 30
RETRY_DELAYS = (0.75, 1.5, 3.0)

# Keep the corrected Polymarket Search discovery from v2.
base._fetch_poly_mlb = _fetch_poly_mlb_search


def _stable_order(candidates: list[base.StrictCandidate]) -> list[base.StrictCandidate]:
    return sorted(
        candidates,
        key=lambda c: hashlib.sha256(c.key.encode("utf-8")).hexdigest(),
    )


def select_batch(
    candidates: list[base.StrictCandidate],
    *,
    slot: int,
    batch_size: int = BATCH_SIZE,
) -> tuple[list[base.StrictCandidate], int]:
    """Select one deterministic rotating slice, wrapping at the end."""
    ordered = _stable_order(candidates)
    total = len(ordered)
    if total == 0:
        return [], 0
    size = min(batch_size, total)
    start = (slot * size) % total
    if start + size <= total:
        return ordered[start : start + size], start
    tail = ordered[start:]
    head = ordered[: size - len(tail)]
    return tail + head, start


async def _fetch_optional(
    http: httpx.AsyncClient,
    url: str,
    **kwargs: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch JSON with bounded 429 retry; return errors instead of raising."""
    attempts = 1 + len(RETRY_DELAYS)
    for attempt in range(attempts):
        try:
            response = await http.get(url, **kwargs)
        except httpx.HTTPError as exc:
            if attempt == attempts - 1:
                return None, f"transport:{type(exc).__name__}"
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
            continue

        if response.status_code == 429:
            if attempt == attempts - 1:
                return None, "http:429"
            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            except ValueError:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            await asyncio.sleep(max(delay, RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]))
            continue

        if response.status_code in {404, 410}:
            return None, f"http:{response.status_code}"
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return None, f"http_or_json:{type(exc).__name__}"
        await asyncio.sleep(REQUEST_PACE_SECONDS)
        return body, None

    return None, "retry_exhausted"


def _empty_row(candidate: base.StrictCandidate, *, error: str | None) -> dict[str, Any]:
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
        "kalshi_fee_type": None,
        "kalshi_fee_multiplier": None,
        "book_comparable": False,
        "positive_conservative_plan": None,
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
            body, error = await _fetch_optional(http, f"{base.KALSHI}/series/{series}")
            if body is not None and error is None:
                series_meta[series] = dict(body.get("series") or {})

        slot = int(time.time() // ROTATION_SECONDS)
        selected, batch_start = select_batch(candidates, slot=slot)
        semaphore = asyncio.Semaphore(PRICE_CONCURRENCY)

        async def price(candidate: base.StrictCandidate) -> dict[str, Any]:
            async with semaphore:
                poly_body, poly_error = await _fetch_optional(
                    http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book"
                )
                kalshi_body, kalshi_error = await _fetch_optional(
                    http,
                    f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                    params={"depth": 1},
                )

            if poly_body is None or kalshi_body is None:
                error = ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing"
                return _empty_row(candidate, error=error)

            pbook = base.poly_top_book(poly_body)
            kbook = base.kalshi_top_book(kalshi_body)
            series = base.FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = base._d(meta.get("fee_multiplier"))
            plan = None
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and Decimal("0") <= multiplier <= Decimal("100")
            ):
                plan = base.best_conservative_plan(
                    pbook,
                    kbook,
                    kalshi_fee_multiplier=multiplier,
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
                "fetch_error": None,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }

        priced = await asyncio.gather(*(price(candidate) for candidate in selected)) if selected else []

    positive = [row for row in priced if row.get("positive_conservative_plan")]
    failures = [row for row in priced if row.get("fetch_error")]
    by_family: dict[str, dict[str, int]] = {}
    for family in base.FAMILY_SERIES:
        family_rows = [row for row in priced if row["family"] == family]
        by_family[family] = {
            "priced_in_batch": len(family_rows),
            "book_comparable": sum(bool(row["book_comparable"]) for row in family_rows),
            "conservative_positive": sum(bool(row["positive_conservative_plan"]) for row in family_rows),
        }

    result = {
        "total_strict_candidates": len(candidates),
        "batch_size": len(selected),
        "batch_start": batch_start,
        "rotation_slot": slot,
        "book_comparable": sum(bool(row["book_comparable"]) for row in priced),
        "fetch_failures": len(failures),
        "fetch_failure_examples": [
            {"poly_slug": row["poly_slug"], "kalshi_ticker": row["kalshi_ticker"], "error": row["fetch_error"]}
            for row in failures[:5]
        ],
        "conservative_positive_pairs": len(positive),
        "families": by_family,
        "top_positive": sorted(
            positive,
            key=lambda row: Decimal(row["positive_conservative_plan"]["conservative_locked_profit"]),
            reverse=True,
        )[:20],
        "rule_warning": (
            "MLB player props remain participation-conditional across venues. Positive pricing is research-only "
            "until the required first-PA/first-pitch gate is independently observed."
        ),
        "batch_warning": (
            "This pass prices a rotating rate-safe subset of the exact identity universe; zero positives in one "
            "batch does not imply zero positives across the full slate."
        ),
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
    }
    logger.info("STRICT_MLB_PROP_SNAPSHOT_V3 %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
