"""Settlement-based capital-lock analysis for weather synthetic PAPER probes.

Research-only. A weather paper position's capital remains tied up from synthetic fill
until the exact Kalshi market's official settlement is observed. Quote OPEN/MOVE/CLOSE
lifetime is execution evidence, not capital lock time.

This analyzer consumes the shared cross-venue ledger. It requires exactly one Kalshi
fill for a weather trade group, exact market-matched fee accounting, and exactly one
later settlement event for the same market/settlement key. Missing settlement is
right-censored. Ambiguous fills/fees/settlements fail closed. Aggregate realized
profit-per-capital-hour is withheld while any filled group is unresolved or invalid.

No venue client or order-entry path exists here.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent

D = Decimal
VENUE = "kalshi"
VALID_SIDES = {"YES", "NO"}


def _finite_decimal(value: Any) -> bool:
    """Return True only for finite Decimal telemetry; never raise on malformed input."""
    return isinstance(value, Decimal) and value.is_finite()


def analyze_weather_paper_capital_lock(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    fills: dict[str, list[LedgerEvent]] = defaultdict(list)
    fees: dict[str, list[LedgerEvent]] = defaultdict(list)
    settlements: dict[str, list[LedgerEvent]] = defaultdict(list)

    for event in events:
        if event.category != "weather" or event.venue != VENUE:
            continue
        if event.event_type == "fill":
            fills[event.trade_group_id].append(event)
        elif event.event_type == "fee":
            fees[event.trade_group_id].append(event)
        elif event.event_type == "settlement":
            settlements[event.trade_group_id].append(event)

    rows: list[dict[str, Any]] = []
    total_profit = D("0")
    total_capital_hours = D("0")
    verified_groups = 0
    right_censored_groups = 0
    invalid_groups = 0

    for group_id in sorted(fills):
        group_fills = fills[group_id]
        if len(group_fills) != 1:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "AMBIGUOUS_FILL_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        fill = group_fills[0]
        fill_decimals_valid = all(
            _finite_decimal(value) for value in (fill.quantity, fill.price, fill.fee)
        )
        structurally_valid = (
            fill_decimals_valid
            and fill.market_id
            and fill.settlement_key
            and fill.quantity > 0
            and fill.price > 0
            and fill.price < 1
            and fill.fee >= 0
            and fill.side in VALID_SIDES
            and isinstance(fill.ts_ms, int)
            and not isinstance(fill.ts_ms, bool)
            and fill.ts_ms >= 0
        )
        if not structurally_valid:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "MALFORMED_SYNTHETIC_FILL_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        group_fees = fees.get(group_id, [])
        fee_invalid = any(
            fee.market_id != fill.market_id
            or fee.settlement_key != fill.settlement_key
            or fee.category != fill.category
            or fee.side not in {None, fill.side}
            or not _finite_decimal(fee.fee)
            or fee.fee < 0
            for fee in group_fees
        )
        explicit_fee = sum((fee.fee for fee in group_fees), D("0")) if not fee_invalid else D("0")
        # Explicit fee events and an embedded fill fee are alternative ledger
        # representations. Their simultaneous presence is ambiguous even when the
        # explicit event is zero-valued: choosing the explicit representation would
        # silently discard a positive embedded fee and understate deployed capital.
        if group_fees and fill.fee != 0:
            fee_invalid = True
        if fee_invalid:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue
        fee = explicit_fee if group_fees else fill.fee

        group_settlements = settlements.get(group_id, [])
        if len(group_settlements) == 0:
            right_censored_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "fill_ts_ms": fill.ts_ms,
                "status": "RIGHT_CENSORED_AWAITING_OFFICIAL_SETTLEMENT",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue
        if len(group_settlements) != 1:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "AMBIGUOUS_SETTLEMENT_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        settlement = group_settlements[0]
        # Weather bracket markets are binary contracts. A terminal settlement for a
        # filled position can therefore pay exactly zero or exactly the filled
        # quantity. Accepting an arbitrary fractional payout would let malformed or
        # non-terminal telemetry contaminate realized PAPER P/L and capital-efficiency
        # metrics.
        payout_valid = _finite_decimal(settlement.payout)
        binary_terminal_payout = payout_valid and settlement.payout in {D("0"), fill.quantity}
        settlement_valid = (
            settlement.market_id == fill.market_id
            and settlement.settlement_key == fill.settlement_key
            and settlement.category == fill.category
            and settlement.side in {None, fill.side}
            and isinstance(settlement.ts_ms, int)
            and not isinstance(settlement.ts_ms, bool)
            and settlement.ts_ms > fill.ts_ms
            and binary_terminal_payout
        )
        if not settlement_valid:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        capital = fill.quantity * fill.price + fee
        if not _finite_decimal(capital) or capital <= 0:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "NONPOSITIVE_OR_NONFINITE_CAPITAL_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        lock_ms = settlement.ts_ms - fill.ts_ms
        capital_hours = capital * D(lock_ms) / D("3600000")
        realized_profit = settlement.payout - capital
        if not _finite_decimal(capital_hours) or not _finite_decimal(realized_profit):
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "NONFINITE_REALIZED_ACCOUNTING_FAIL_CLOSED",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue
        efficiency = realized_profit / capital_hours if capital_hours > 0 else None

        verified_groups += 1
        total_profit += realized_profit
        total_capital_hours += capital_hours
        rows.append({
            "trade_group_id": group_id,
            "fill_ts_ms": fill.ts_ms,
            "settled_ts_ms": settlement.ts_ms,
            "capital_lock_ms": lock_ms,
            "fee": str(fee),
            "fee_accounting": "EXACT_FEE_EVENTS_OR_LEGACY_EMBEDDED_FILL_FALLBACK",
            "capital": str(capital),
            "capital_lock_hours": str(capital_hours),
            "realized_profit": str(realized_profit),
            "realized_profit_per_capital_hour": str(efficiency) if efficiency is not None else None,
            "status": "VERIFIED_SETTLEMENT_BASED_CAPITAL_LOCK",
        })

    all_filled_groups_resolved = right_censored_groups == 0 and invalid_groups == 0
    aggregate_efficiency = (
        total_profit / total_capital_hours
        if all_filled_groups_resolved and verified_groups > 0 and total_capital_hours > 0
        else None
    )

    return {
        "filled_trade_groups": len(fills),
        "verified_settlement_lock_groups": verified_groups,
        "right_censored_groups": right_censored_groups,
        "invalid_groups": invalid_groups,
        "all_filled_groups_resolved": all_filled_groups_resolved,
        "verified_realized_profit": str(total_profit),
        "verified_capital_hours": str(total_capital_hours),
        "conservative_realized_profit_per_capital_hour": (
            str(aggregate_efficiency) if aggregate_efficiency is not None else None
        ),
        "capital_lock_semantics": "SYNTHETIC_FILL_TO_EXACT_OFFICIAL_KALSHI_SETTLEMENT",
        "fee_semantics": "EXACT_FEE_EVENTS_PREFERRED_LEGACY_FILL_FEE_FALLBACK_FAIL_CLOSED_ON_AMBIGUITY",
        "settlement_validation": "EXACT_KEY_CATEGORY_MARKET_OPTIONAL_SIDE_BINARY_TERMINAL_PAYOUT_AND_FINITE_DECIMALS",
        "opportunity_duration_is_capital_lock": False,
        "warning": (
            "Do not substitute weather OPEN/MOVE/CLOSE quote lifetime for post-fill capital lock. "
            "Aggregate efficiency is withheld while any filled group is unresolved or invalid."
        ),
        "groups": rows,
        "paper_only": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
