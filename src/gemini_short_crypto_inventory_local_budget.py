"""AWS-local durable ledger for the Gemini BTC5m inventory-maker q=1 canary.

This is intentionally separate from the earlier atomic/two-sided maker canary
ledger. The inventory strategy may legitimately end a window one-sided, so its
classification must not imply locked arbitrage.

No venue mutation is implemented here. Reservation is fsynced before the live
order-entry session connects, and an unresolved reservation blocks later runs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from src import gemini_short_crypto_local_budget as base

D = Decimal
STRATEGY = "crypto_gemini_btc5m_inventory_maker_q1"
CLASSIFICATION = "GEMINI_SHORT_BINARY_INVENTORY_MAKER_Q1_CANARY_LOCAL_LEDGER"
RESERVED_USD = D("2")


def _event_base(*, trade_id: str, symbol: str, event_ticker: str, event_type: str, status: str) -> dict[str, Any]:
    event = base._event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type=event_type,
        status=status,
    )
    event["strategy"] = STRATEGY
    event["side"] = "INVENTORY_MAKER"
    event["metadata"]["classification"] = CLASSIFICATION
    event["metadata"]["strategy_intent"] = "MAKER_FIRST_TEMPORAL_INVENTORY"
    event["metadata"]["paired_arb"] = False
    return event


async def assert_clean_account() -> dict[str, Any]:
    return await base.assert_clean_account()


async def reserve(*, symbol: str, event_ticker: str, trade_id: str,
                  yes_quote: str, no_quote: str, modeled_pair_edge: str) -> None:
    snapshot = await assert_clean_account()
    event = _event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="CAPITAL_RESERVED",
        status="CAPITAL_RESERVED",
    )
    event.update({"amount": str(RESERVED_USD), "legs_required": 0, "legs_filled": 0})
    event["metadata"].update({
        "reserved_usd": str(RESERVED_USD),
        "max_yes_qty": "1",
        "max_no_qty": "1",
        "initial_yes_quote": yes_quote,
        "initial_no_quote": no_quote,
        "initial_modeled_pair_edge": modeled_pair_edge,
        "gemini_account_snapshot_before_reserve": snapshot,
        "guaranteed_profit": False,
        "one_sided_settlement_allowed": True,
    })
    base._append(event)
    base._write_open({
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "reserved_usd": str(RESERVED_USD),
        "reserved_at": event["event_ts"],
        "status": "CAPITAL_RESERVED",
        "strategy": STRATEGY,
    })


async def release(*, symbol: str, event_ticker: str, trade_id: str, reason: str) -> None:
    event = _event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="CAPITAL_RELEASED",
        status="CAPITAL_RELEASED",
    )
    event.update({"amount": "0", "legs_required": 0, "legs_filled": 0})
    event["event_id"] = base._safe_id(f"{trade_id}:inventory-capital-released:{reason}")
    event["metadata"].update({"reserved_usd": "0", "release_reason": reason})
    base._append(event)
    base._clear_open()


async def result(*, symbol: str, event_ticker: str, trade_id: str,
                 status: str, yes_fill: str, no_fill: str,
                 metadata: Mapping[str, Any]) -> None:
    event = _event_base(
        trade_id=trade_id,
        symbol=symbol,
        event_ticker=event_ticker,
        event_type="EXECUTION_RESULT",
        status=status,
    )
    legs = int(D(str(yes_fill or "0")) > 0) + int(D(str(no_fill or "0")) > 0)
    details = dict(metadata)
    event.update({"quantity": "1", "legs_required": 0, "legs_filled": legs})
    event["event_id"] = base._safe_id(f"{trade_id}:inventory-result:{status}")
    event["metadata"].update({
        "guaranteed_profit": False,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        **details,
    })
    base._append(event)
    current = base.local_open_state() or {
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "strategy": STRATEGY,
    }
    current.update({
        "status": status,
        "updated_at": event["event_ts"],
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "legs_filled": legs,
    })
    # Dynamic-complement campaigns may use several sequential maker client IDs on
    # the missing side. Persist the exact identity chain in the durable open record
    # so authoritative settlement reconciliation never has to infer replacements.
    for key in ("yes_client_order_ids", "no_client_order_ids", "order_execution_modes"):
        value = details.get(key)
        if value is not None:
            current[key] = value
    base._write_open(current)
