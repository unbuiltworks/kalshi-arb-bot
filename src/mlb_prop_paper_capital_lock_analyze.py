"""Settlement-based capital-lock analysis for MLB synthetic paper probes.

Research-only. Quote-opportunity lifetime is NOT capital lock time after a synthetic
fill. For an actually paper-filled pair, capital remains tied up until both venue
settlements have been observed. This analyzer therefore derives dollar-hours only
from exact simultaneous synthetic fills joined to exact venue-specific settlement
events.

Any missing/ambiguous/nonpositive settlement timestamp fails closed. Incomplete
trade groups are right-censored and prevent an aggregate conservative
profit-per-dollar-hour figure from being reported.

Paper micro-probes record modeled fees as separate ``fee`` ledger events. Legacy
fixtures/producers may embed a fee on the fill itself. Fee accounting therefore
prefers exact market-matched fee events and falls back to embedded fill fees only
when no fee events exist; having both representations nonzero is treated as
ambiguous and fails closed rather than risking double counting or omission.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable, Any

from src.cross_venue_ledger import LedgerEvent

D = Decimal
VENUES = ("polymarket_us", "kalshi")
VALID_SIDES = {"YES", "NO"}


def analyze_paper_capital_lock(events: Iterable[LedgerEvent]) -> dict[str, Any]:
    fills: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))
    fees: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))
    settlements: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))

    for event in events:
        if event.venue not in VENUES:
            continue
        if event.event_type == "fill":
            fills[event.trade_group_id][event.venue].append(event)
        elif event.event_type == "fee":
            fees[event.trade_group_id][event.venue].append(event)
        elif event.event_type == "settlement":
            settlements[event.trade_group_id][event.venue].append(event)

    rows: list[dict[str, Any]] = []
    total_profit = D("0")
    total_capital_hours = D("0")
    verified_groups = 0
    right_censored_groups = 0
    invalid_groups = 0

    for group_id in sorted(fills):
        group_fills = fills[group_id]
        if any(len(group_fills.get(v, [])) != 1 for v in VENUES):
            invalid_groups += 1
            rows.append({"trade_group_id": group_id, "status": "AMBIGUOUS_OR_MISSING_FILL_FAIL_CLOSED"})
            continue

        poly = group_fills["polymarket_us"][0]
        kalshi = group_fills["kalshi"][0]
        structurally_valid = (
            poly.ts_ms == kalshi.ts_ms
            and poly.settlement_key == kalshi.settlement_key
            and poly.category == kalshi.category
            and poly.quantity > 0
            and poly.quantity == kalshi.quantity
            and poly.side in VALID_SIDES
            and kalshi.side in VALID_SIDES
            and poly.side != kalshi.side
        )
        if not structurally_valid:
            invalid_groups += 1
            rows.append({"trade_group_id": group_id, "status": "MALFORMED_SYNTHETIC_FILL_GROUP_FAIL_CLOSED"})
            continue

        group_fees = fees.get(group_id, {})
        fee_by_venue: dict[str, Decimal] = {}
        fee_invalid = False
        for venue, fill in (("polymarket_us", poly), ("kalshi", kalshi)):
            fee_events = group_fees.get(venue, [])
            if any(
                event.market_id != fill.market_id
                or event.settlement_key != fill.settlement_key
                or event.category != fill.category
                for event in fee_events
            ):
                fee_invalid = True
                break
            explicit_fee = sum((event.fee for event in fee_events), D("0"))
            if explicit_fee > 0 and fill.fee > 0:
                fee_invalid = True
                break
            fee_by_venue[venue] = explicit_fee if fee_events else fill.fee

        if fee_invalid:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED",
            })
            continue

        fill_ts = poly.ts_ms
        group_settlements = settlements.get(group_id, {})
        if any(len(group_settlements.get(v, [])) != 1 for v in VENUES):
            right_censored_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "fill_ts_ms": fill_ts,
                "status": "RIGHT_CENSORED_AWAITING_BOTH_SETTLEMENTS",
                "capital_lock_hours": None,
                "realized_profit": None,
            })
            continue

        poly_settle = group_settlements["polymarket_us"][0]
        kalshi_settle = group_settlements["kalshi"][0]

        def settlement_matches_fill(settlement: LedgerEvent, fill: LedgerEvent) -> bool:
            return (
                settlement.market_id == fill.market_id
                and settlement.settlement_key == fill.settlement_key
                and settlement.category == fill.category
                and settlement.ts_ms > fill_ts
                and (settlement.side is None or settlement.side == fill.side)
                and settlement.payout >= 0
                and settlement.payout <= fill.quantity
            )

        exact_leg_match = (
            settlement_matches_fill(poly_settle, poly)
            and settlement_matches_fill(kalshi_settle, kalshi)
        )
        if not exact_leg_match:
            invalid_groups += 1
            rows.append({
                "trade_group_id": group_id,
                "status": "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED",
            })
            continue

        poly_fee = fee_by_venue["polymarket_us"]
        kalshi_fee = fee_by_venue["kalshi"]
        total_fee = poly_fee + kalshi_fee
        capital = (
            poly.quantity * poly.price
            + kalshi.quantity * kalshi.price
            + total_fee
        )
        if capital <= 0:
            invalid_groups += 1
            rows.append({"trade_group_id": group_id, "status": "NONPOSITIVE_CAPITAL_FAIL_CLOSED"})
            continue

        lock_end_ms = max(poly_settle.ts_ms, kalshi_settle.ts_ms)
        lock_ms = lock_end_ms - fill_ts
        capital_hours = capital * D(lock_ms) / D("3600000")
        realized_profit = poly_settle.payout + kalshi_settle.payout - capital
        efficiency = realized_profit / capital_hours if capital_hours > 0 else None

        verified_groups += 1
        total_profit += realized_profit
        total_capital_hours += capital_hours
        rows.append({
            "trade_group_id": group_id,
            "fill_ts_ms": fill_ts,
            "polymarket_us_settled_ts_ms": poly_settle.ts_ms,
            "kalshi_settled_ts_ms": kalshi_settle.ts_ms,
            "capital_lock_end_ms": lock_end_ms,
            "capital_lock_ms": lock_ms,
            "poly_fee": str(poly_fee),
            "kalshi_fee": str(kalshi_fee),
            "total_fee": str(total_fee),
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
        "capital_lock_semantics": "FILL_TO_LATER_OF_TWO_OBSERVED_VENUE_SETTLEMENTS",
        "fee_semantics": "EXACT_FEE_EVENTS_PREFERRED_LEGACY_FILL_FEE_FALLBACK_FAIL_CLOSED_ON_AMBIGUITY",
        "settlement_validation": "EXACT_KEY_CATEGORY_MARKET_OPTIONAL_SIDE_AND_PAYOUT_BOUNDED_BY_FILLED_QUANTITY",
        "opportunity_duration_is_capital_lock": False,
        "warning": (
            "Do not substitute OPEN/MOVE/CLOSE quote lifetime for post-fill capital lock. "
            "Aggregate efficiency is withheld while any filled group is unresolved or invalid."
        ),
        "groups": rows,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
