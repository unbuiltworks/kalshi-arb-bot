"""Discover and normalize the best current nested candidate for a one-shot probe.

Read-only discovery only. This module converts the aggressive maker scanner's row
into the explicit proven lower/higher strike specification required by the live
probe guard. The FIRST live probe intentionally rejects boundary prices, long-dated
events, empty maker bids, and large jumps away from the displayed maker bid.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from src.kalshi_nested_live_probe_guard import (
    D,
    MIN_OUTCOME_COST,
    MAX_OUTCOME_COST,
    NestedLiveProbeSpec,
    validate_spec,
)
from src.kalshi_nested_maker_aggressive_shadow import scan_aggressive_series
from src.kalshi_nested_rolling_shadow import parse_event_close

NY = ZoneInfo("America/New_York")
MAX_FIRST_PROBE_MAKER_IMPROVEMENT = D("0.02")


def row_to_spec(
    row: dict[str, Any],
    *,
    fee_type: str,
    fee_multiplier: Decimal,
    scheduled_fee_changes: tuple[dict, ...],
    target_profit: Decimal,
    maker_rest_seconds: int,
) -> NestedLiveProbeSpec:
    event_ticker = str(row.get("event_ticker") or "")
    close = parse_event_close(event_ticker)
    if close is None:
        raise ValueError("cannot parse event close")
    strike_type = str(row.get("strike_type") or "").lower().strip()
    maker_ticker = str(row.get("maker_ticker") or "")
    maker_side = str(row.get("maker_side") or "").lower().strip()
    hedge_ticker = str(row.get("hedge_ticker") or "")
    hedge_side = str(row.get("hedge_side") or "").lower().strip()
    lower_strike = D(str(row["lower_strike"]))
    higher_strike = D(str(row["higher_strike"]))

    if strike_type == "greater":
        if maker_side == "yes" and hedge_side == "no":
            lower_ticker, higher_ticker = maker_ticker, hedge_ticker
        elif maker_side == "no" and hedge_side == "yes":
            lower_ticker, higher_ticker = hedge_ticker, maker_ticker
        else:
            raise ValueError("greater row has impossible maker/hedge sides")
    elif strike_type == "less":
        if maker_side == "no" and hedge_side == "yes":
            lower_ticker, higher_ticker = maker_ticker, hedge_ticker
        elif maker_side == "yes" and hedge_side == "no":
            lower_ticker, higher_ticker = hedge_ticker, maker_ticker
        else:
            raise ValueError("less row has impossible maker/hedge sides")
    else:
        raise ValueError("unsupported strike type")

    return NestedLiveProbeSpec(
        event_ticker=event_ticker,
        event_close=close,
        strike_type=strike_type,
        lower_ticker=lower_ticker,
        lower_strike=lower_strike,
        higher_ticker=higher_ticker,
        higher_strike=higher_strike,
        maker_ticker=maker_ticker,
        maker_side=maker_side,
        maker_cost=D(str(row["shadow_quote"])),
        hedge_ticker=hedge_ticker,
        hedge_side=hedge_side,
        hedge_cost=D(str(row["hedge_top_ask"])),
        fee_type=fee_type,
        fee_multiplier=D(fee_multiplier),
        scheduled_fee_changes=scheduled_fee_changes,
        target_profit=D(target_profit),
        maker_rest_seconds=int(maker_rest_seconds),
    )


def first_probe_row_is_fillable_enough(row: dict[str, Any]) -> bool:
    """Hard first-probe liquidity gate; do not manufacture a fill with a huge jump."""
    bid = D(str(row.get("current_best_bid") or "0"))
    quote = D(str(row.get("shadow_quote") or "-1"))
    improvement = D(str(row.get("quote_improvement") or "1"))
    if bid <= D("0"):
        return False
    if quote <= bid:
        return False
    if improvement <= D("0") or improvement > MAX_FIRST_PROBE_MAKER_IMPROVEMENT:
        return False
    # Recompute rather than trusting telemetry alone.
    if quote - bid != improvement:
        return False
    return True


def candidate_rank(row: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    """Prefer fillability: smallest bid improvement, near ask, then cheap hedge."""
    improvement = D(str(row.get("quote_improvement") or "1"))
    distance = D(str(row.get("distance_below_ask") or "1"))
    hedge = D(str(row.get("hedge_top_ask") or "1"))
    return improvement, distance, hedge


async def discover_best_spec(
    *,
    series: list[str],
    target_profit: Decimal = D("0.03"),
    maker_rest_seconds: int = 10,
    now: datetime | None = None,
) -> tuple[NestedLiveProbeSpec, dict[str, Any]] | None:
    current = now or datetime.now(NY)
    eligible: list[tuple[NestedLiveProbeSpec, dict[str, Any]]] = []
    for series_ticker in series:
        summary = await scan_aggressive_series(
            series_ticker.upper(),
            target_profit=D(target_profit),
            top_n=1000,
        )
        meta = summary.get("fee_metadata") or {}
        fee_type = str(meta.get("fee_type") or "")
        fee_multiplier = D(str(meta.get("fee_multiplier") or "-1"))
        changes = tuple(meta.get("scheduled_changes") or [])
        for row in summary.get("top_quotes") or []:
            maker_cost = D(str(row.get("shadow_quote") or "-1"))
            hedge_cost = D(str(row.get("hedge_top_ask") or "-1"))
            if not first_probe_row_is_fillable_enough(row):
                continue
            if not (MIN_OUTCOME_COST <= maker_cost <= MAX_OUTCOME_COST):
                continue
            if not (MIN_OUTCOME_COST <= hedge_cost <= MAX_OUTCOME_COST):
                continue
            try:
                spec = row_to_spec(
                    row,
                    fee_type=fee_type,
                    fee_multiplier=fee_multiplier,
                    scheduled_fee_changes=changes,
                    target_profit=D(target_profit),
                    maker_rest_seconds=maker_rest_seconds,
                )
                validate_spec(spec, now=current)
            except (ValueError, KeyError):
                continue
            eligible.append((spec, row))

    if not eligible:
        return None
    eligible.sort(key=lambda item: (
        item[0].event_close,
        *candidate_rank(item[1]),
    ))
    return eligible[0]
