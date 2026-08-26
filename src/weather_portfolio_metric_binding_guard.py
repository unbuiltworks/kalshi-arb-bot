"""Fail-closed binding for weather portfolio ranking metrics.

The central portfolio score consumes ``net_edge_per_dollar``, ``executable_probability``
and ``depth_dollars``. Those summary scalars must not be trusted merely because the
underlying weather settlement/book/fee/model audit passed: a malformed producer could
attach inflated ranking inputs to otherwise valid SHADOW evidence.

This guard independently derives the edge/depth terms from the selected executable side
and independently derives empirical execution probability from raw eligible/total sample
counts in a verified observation window. The probability denominator must represent the
complete scanner-snapshot window (including zero-opportunity snapshots), duplicate
snapshot counting must be explicitly excluded, and the sample cohort must be bound to
the exact Kalshi market and scored side being ranked. It is read-only and cannot
authorize order entry or a live probe.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

D = Decimal

PORTFOLIO_METRICS_UNVERIFIED = "WEATHER_PORTFOLIO_METRICS_UNVERIFIED"
EXECUTION_PROBABILITY_UNVERIFIED = "WEATHER_EXECUTION_PROBABILITY_UNVERIFIED"
EXECUTION_SAMPLE_BASIS_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_BASIS_UNVERIFIED"
EXECUTION_SAMPLE_SCOPE_UNVERIFIED = "WEATHER_EXECUTION_SAMPLE_SCOPE_UNVERIFIED"
COMPLETE_SCANNER_SAMPLE_BASIS = "COMPLETE_SCANNER_SNAPSHOT_WINDOW"
EXACT_MARKET_SIDE_SAMPLE_SCOPE = "EXACT_MARKET_SAME_SIDE_EXECUTABLE_SHADOW"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _selected_record(row: dict[str, Any]) -> dict[str, Any] | None:
    side = str(row.get("best_shadow_side") or "").strip().upper()
    records = row.get("side_economics")
    if side not in {"YES", "NO"} or not isinstance(records, list) or len(records) != 2:
        return None
    matches = [r for r in records if isinstance(r, dict) and str(r.get("side") or "").strip().upper() == side]
    return matches[0] if len(matches) == 1 else None


def _price_quantity_fee(record: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal] | None:
    # Fixed-point fields are authoritative when present.
    fixed_present = "ask_dollars" in record or "quantity" in record
    if fixed_present:
        price = _decimal(record.get("ask_dollars"))
        qty = _decimal(record.get("quantity"))
        fee = _decimal(record.get("modeled_taker_fee_dollars"))
        if fee is None:
            fee_cents = _decimal(record.get("modeled_taker_fee_cents"))
            fee = None if fee_cents is None else fee_cents / D("100")
    else:
        ask_cents = _decimal(record.get("ask_cents"))
        qty = _decimal(record.get("available_contracts"))
        fee_cents = _decimal(record.get("modeled_taker_fee_cents"))
        price = None if ask_cents is None else ask_cents / D("100")
        fee = None if fee_cents is None else fee_cents / D("100")

    if price is None or qty is None or fee is None:
        return None
    if not (D("0") < price < D("1")) or qty <= 0 or fee < 0:
        return None
    return price, qty, fee


def _empirical_execution_probability(row: dict[str, Any]) -> tuple[Decimal | None, int | None, int | None]:
    """Derive execution probability from raw verified observation counts."""
    eligible = row.get("executable_probability_success_count")
    total = row.get("executable_probability_sample_count")
    if (
        not isinstance(eligible, int)
        or isinstance(eligible, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or eligible < 0
        or eligible > total
    ):
        return None, None, None
    return D(eligible) / D(total), eligible, total


def _sample_basis_verified(row: dict[str, Any]) -> bool:
    """Require probability counts to come from a complete, non-duplicated snapshot basis."""
    return (
        row.get("executable_probability_sample_basis") == COMPLETE_SCANNER_SAMPLE_BASIS
        and row.get("executable_probability_complete_window_verified") is True
        and row.get("executable_probability_includes_zero_opportunity_snapshots") is True
        and row.get("executable_probability_duplicate_snapshot_counting_excluded") is True
    )


def _sample_scope_verified(row: dict[str, Any]) -> bool:
    """Bind empirical execution counts to the exact market and side being ranked.

    A portfolio row must not borrow an execution rate measured on another city/date,
    another bracket, or the opposite side. Aggregate weather-wide success counts remain
    useful research diagnostics but cannot certify a specific market's capacity score.
    """
    market_ticker = str(row.get("market_ticker") or row.get("kalshi_ticker") or "").strip()
    selected_side = str(row.get("best_shadow_side") or "").strip().upper()
    sample_market_ticker = str(row.get("executable_probability_market_ticker") or "").strip()
    sample_side = str(row.get("executable_probability_side") or "").strip().upper()
    return (
        bool(market_ticker)
        and selected_side in {"YES", "NO"}
        and row.get("executable_probability_scope_verified") is True
        and row.get("executable_probability_sample_scope") == EXACT_MARKET_SIDE_SAMPLE_SCOPE
        and sample_market_ticker == market_ticker
        and sample_side == selected_side
    )


def audit_weather_portfolio_metric_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Verify weather ranking inputs against raw selected-side SHADOW economics."""
    blockers: list[str] = []
    record = _selected_record(row)
    derived_edge: Decimal | None = None
    derived_depth: Decimal | None = None

    if record is None:
        blockers.append(PORTFOLIO_METRICS_UNVERIFIED)
    else:
        parsed = _price_quantity_fee(record)
        ev_cents = _decimal(record.get("modeled_after_fee_ev_cents"))
        if parsed is None or ev_cents is None:
            blockers.append(PORTFOLIO_METRICS_UNVERIFIED)
        else:
            price, qty, fee = parsed
            deployed_capital = price * qty + fee
            if deployed_capital <= 0:
                blockers.append(PORTFOLIO_METRICS_UNVERIFIED)
            else:
                derived_depth = price * qty
                derived_edge = (ev_cents / D("100")) / deployed_capital

    producer_probability = _decimal(row.get("executable_probability"))
    derived_probability, success_count, sample_count = _empirical_execution_probability(row)
    sample_basis_verified = _sample_basis_verified(row)
    sample_scope_verified = _sample_scope_verified(row)
    if not sample_basis_verified:
        blockers.append(EXECUTION_SAMPLE_BASIS_UNVERIFIED)
    if not sample_scope_verified:
        blockers.append(EXECUTION_SAMPLE_SCOPE_UNVERIFIED)

    probability_verified = (
        row.get("executable_probability_verified") is True
        and row.get("executable_probability_window_verified") is True
        and sample_basis_verified
        and sample_scope_verified
        and derived_probability is not None
        and producer_probability is not None
        and producer_probability == derived_probability
        and D("0") <= derived_probability <= D("1")
    )
    if not probability_verified:
        blockers.append(EXECUTION_PROBABILITY_UNVERIFIED)

    producer_edge = _decimal(row.get("net_edge_per_dollar"))
    producer_depth = _decimal(row.get("depth_dollars"))
    metric_binding_verified = (
        derived_edge is not None
        and derived_depth is not None
        and producer_edge is not None
        and producer_depth is not None
        and producer_edge == derived_edge
        and producer_depth == derived_depth
        and probability_verified
    )
    if not metric_binding_verified and PORTFOLIO_METRICS_UNVERIFIED not in blockers:
        blockers.append(PORTFOLIO_METRICS_UNVERIFIED)

    return {
        "weather_portfolio_metric_binding_verified": metric_binding_verified,
        "weather_execution_probability_verified": probability_verified,
        "weather_execution_sample_basis_verified": sample_basis_verified,
        "weather_execution_sample_scope_verified": sample_scope_verified,
        "execution_probability_sample_basis": row.get("executable_probability_sample_basis"),
        "execution_probability_sample_scope": row.get("executable_probability_sample_scope"),
        "execution_probability_market_ticker": row.get("executable_probability_market_ticker"),
        "execution_probability_side": row.get("executable_probability_side"),
        "derived_net_edge_per_dollar": None if derived_edge is None else str(derived_edge),
        "derived_depth_dollars": None if derived_depth is None else str(derived_depth),
        "verified_executable_probability": None if not probability_verified else str(derived_probability),
        "executable_probability_success_count": success_count,
        "executable_probability_sample_count": sample_count,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
