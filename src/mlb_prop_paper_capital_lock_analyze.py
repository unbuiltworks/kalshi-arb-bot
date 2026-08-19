"""Settlement-based capital-lock analysis for MLB synthetic paper probes.

Research-only. Quote-opportunity lifetime is NOT capital lock time after a synthetic
fill. For an actually paper-filled pair, capital remains tied up until both venue
settlements have been observed. This analyzer therefore derives dollar-hours only
from exact simultaneous synthetic fills joined to exact venue-specific settlement
events.

Any missing/ambiguous/nonpositive settlement timestamp fails closed. Incomplete
trade groups are right-censored and prevent an aggregate conservative
profit-per-dollar-hour figure from being reported.
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
    settlements: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))

    for event in events:
        if event.venue not in VENUES:
            continue
        if event.event_type == "fill":
            fills[event.trade_group_id][event.venue].append(event)
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
        exact_leg_match = (
            poly_settle.market_id == poly.market_id
            and kalshi_settle.market_id == kalshi.market_id
            and poly_settle.ts_ms > fill_ts
            and kalshi_settle.ts_ms > fill_ts
        )
        if not exact_leg_match:
            invalid_groups += 1
            rows.append({"trade_group_id": group_id, "status": "INVALID_SETTLEMENT_TIMING_OR_MARKET_FAIL_CLOSED"})
            continue

        capital = (
            poly.quantity * poly.price + poly.fee
            + kalshi.quantity * kalshi.price + kalshi.fee
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
