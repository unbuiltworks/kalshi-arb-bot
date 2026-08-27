"""Rate-safe MLB prop scanner with explicit $1 and $5 paper-probe economics.

Read-only research. This module reuses v3 discovery/rate-limit handling and reports
best integer-quantity paired plans under $1 and $5 all-in modeled capital caps.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src import strict_mlb_prop_snapshot_v3 as v3
from src.mlb_kalshi_book_guard import audit_kalshi_binary_book
from src.mlb_kalshi_fee_guard import audit_current_series_fee

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v4")
base._fetch_poly_mlb = _fetch_poly_mlb_search

# Receive-time skew is not exchange quote age, but it is the strongest timing guard
# available from these public REST book endpoints. A wider gap can manufacture a
# transient cross-venue lock when one venue moves between requests, so fail closed.
MAX_BOOK_RECEIVE_SKEW_MS = 500


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


def _quote_time_iso(*received_ms: int) -> str:
    """Return an aware UTC quote observation timestamp from local receive evidence."""
    latest_ms = max(received_ms)
    return datetime.fromtimestamp(latest_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _verified_series_fee(
    *,
    series: str,
    series_meta: dict[str, Any] | None,
    fee_changes_body: dict[str, Any] | None,
    observed_at: str,
) -> tuple[Decimal | None, dict[str, Any]]:
    """Return a usable multiplier only when quote-time Series fee evidence verifies."""
    audit = audit_current_series_fee(
        series_ticker=series,
        series_meta=series_meta,
        fee_changes_body=fee_changes_body,
        observed_at=observed_at,
    )
    if audit.get("verified") is not True:
        return None, audit
    raw = audit.get("fee_multiplier")
    try:
        multiplier = Decimal(str(raw))
    except Exception:
        return None, {**audit, "verified": False, "blockers": sorted(set([*audit.get("blockers", []), "FEE_MULTIPLIER_PARSE_FAILED"]))}
    if not multiplier.is_finite() or multiplier < 0 or multiplier > Decimal("100"):
        return None, {**audit, "verified": False, "blockers": sorted(set([*audit.get("blockers", []), "FEE_MULTIPLIER_INVALID_OR_MISSING"]))}
    return multiplier, audit


async def _fetch_optional_timed(
    http: httpx.AsyncClient,
    url: str,
    **kwargs: Any,
) -> tuple[dict[str, Any] | None, str | None, int, int]:
    """Fetch through v3's rate-safe helper and record local receive timing."""
    started_ns = time.monotonic_ns()
    body, error = await v3._fetch_optional(http, url, **kwargs)
    received_ms = time.time_ns() // 1_000_000
    latency_ms = max(0, (time.monotonic_ns() - started_ns) // 1_000_000)
    return body, error, received_ms, latency_ms


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
        "synchronized_book_pair": False,
        "quote_receive_skew_ms": None,
        "kalshi_book_integrity_verified": False,
        "kalshi_book_integrity_blocker": error,
        "kalshi_fee_verified": False,
        "kalshi_fee_blockers": [error] if error else [],
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
        series_fee_changes: dict[str, dict[str, Any] | None] = {}
        series_fee_fetch_errors: dict[str, str | None] = {}
        for series in sorted(set(base.FAMILY_SERIES.values())):
            meta_result, changes_result = await asyncio.gather(
                v3._fetch_optional(http, f"{base.KALSHI}/series/{series}"),
                v3._fetch_optional(
                    http,
                    f"{base.KALSHI}/series/fee_changes",
                    params={"series_ticker": series, "show_historical": "true"},
                ),
            )
            meta_body, meta_error = meta_result
            changes_body, changes_error = changes_result
            if meta_body is not None and meta_error is None:
                series_meta[series] = dict(meta_body.get("series") or {})
            series_fee_changes[series] = changes_body if changes_error is None else None
            series_fee_fetch_errors[series] = ";".join(x for x in (meta_error, changes_error) if x) or None

        slot = int(time.time() // v3.ROTATION_SECONDS)
        selected, batch_start = v3.select_batch(candidates, slot=slot)
        semaphore = asyncio.Semaphore(v3.PRICE_CONCURRENCY)

        async def price(candidate: base.StrictCandidate) -> dict[str, Any]:
            # Fetch both venue books concurrently. Sequential REST reads can create a
            # false cross-venue lock when one book moves between requests.
            async with semaphore:
                poly_result, kalshi_result = await asyncio.gather(
                    _fetch_optional_timed(
                        http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book"
                    ),
                    _fetch_optional_timed(
                        http,
                        f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                        params={"depth": 1},
                    ),
                )
            poly_body, poly_error, poly_received_ms, poly_latency_ms = poly_result
            kalshi_body, kalshi_error, kalshi_received_ms, kalshi_latency_ms = kalshi_result
            receive_skew_ms = abs(poly_received_ms - kalshi_received_ms)
            synchronized = receive_skew_ms <= MAX_BOOK_RECEIVE_SKEW_MS

            if poly_body is None or kalshi_body is None:
                error = ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing"
                row = _empty(candidate, error)
                row.update(
                    {
                        "poly_received_ms": poly_received_ms,
                        "kalshi_received_ms": kalshi_received_ms,
                        "poly_fetch_latency_ms": poly_latency_ms,
                        "kalshi_fetch_latency_ms": kalshi_latency_ms,
                        "quote_receive_skew_ms": receive_skew_ms,
                    }
                )
                return row

            pbook = base.poly_top_book(poly_body)
            kalshi_book_audit = audit_kalshi_binary_book(kalshi_body)
            kbook = base.kalshi_top_book(kalshi_body) if kalshi_book_audit.get("verified") is True else None
            series = base.FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            quote_observed_at = _quote_time_iso(poly_received_ms, kalshi_received_ms)
            multiplier, fee_audit = _verified_series_fee(
                series=series,
                series_meta=meta,
                fee_changes_body=series_fee_changes.get(series),
                observed_at=quote_observed_at,
            )
            if series_fee_fetch_errors.get(series):
                fee_audit = {
                    **fee_audit,
                    "verified": False,
                    "blockers": sorted(set([*fee_audit.get("blockers", []), "FEE_SOURCE_FETCH_ERROR"])),
                }
                multiplier = None
            fee_type = fee_audit.get("fee_type")
            plan = probe1 = probe5 = None
            if (
                synchronized
                and pbook is not None
                and kbook is not None
                and kalshi_book_audit.get("verified") is True
                and fee_audit.get("verified") is True
                and multiplier is not None
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
                "kalshi_fee_verified": fee_audit.get("verified") is True,
                "kalshi_fee_blockers": list(fee_audit.get("blockers") or []),
                "kalshi_fee_source": fee_audit.get("fee_source"),
                "kalshi_fee_effective_change_id": fee_audit.get("effective_change_id"),
                "kalshi_fee_next_change_scheduled_ts": fee_audit.get("next_change_scheduled_ts"),
                "quote_observed_at": quote_observed_at,
                "book_comparable": pbook is not None and kbook is not None,
                "kalshi_book_integrity_verified": kalshi_book_audit.get("verified") is True,
                "kalshi_book_integrity_blocker": kalshi_book_audit.get("blocker"),
                "synchronized_book_pair": synchronized,
                "poly_received_ms": poly_received_ms,
                "kalshi_received_ms": kalshi_received_ms,
                "poly_fetch_latency_ms": poly_latency_ms,
                "kalshi_fetch_latency_ms": kalshi_latency_ms,
                "quote_receive_skew_ms": receive_skew_ms,
                "max_quote_receive_skew_ms": MAX_BOOK_RECEIVE_SKEW_MS,
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
        "kalshi_book_integrity_rejected_pairs": sum(
            bool(r.get("kalshi_book_integrity_verified") is False) for r in priced
        ),
        "kalshi_fee_verified_pairs": sum(bool(r.get("kalshi_fee_verified") is True) for r in priced),
        "kalshi_fee_rejected_pairs": sum(bool(r.get("kalshi_fee_verified") is False) for r in priced),
        "synchronized_book_pairs": sum(bool(r.get("synchronized_book_pair")) for r in priced),
        "receive_skew_rejected_pairs": sum(
            bool(r.get("book_comparable")) and not bool(r.get("synchronized_book_pair")) for r in priced
        ),
        "max_quote_receive_skew_ms": MAX_BOOK_RECEIVE_SKEW_MS,
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
            "$1/$5 plans are capped paper-probe economics after modeled fees; malformed/crossed Kalshi books, "
            "receive-time skew >500ms, and unverified quote-time Series fee evidence are rejected; no "
            "authentication/order-entry path exists."
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
