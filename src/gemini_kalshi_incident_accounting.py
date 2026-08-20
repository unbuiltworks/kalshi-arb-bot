"""Ledger accounting for confirmed one-leg fills that are emergency-flattened.

The live campaign already hard-halts on every unsafe execution. Historically its
ledger helper recorded the original filled leg and then raised, but did not record a
confirmed emergency flatten or the resulting realized round-trip P&L. This wrapper
adds only that accounting. It does not change execution, flattening, risk gates,
authorization, or halt behavior.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import gemini_kalshi_crypto_live_campaign as base

D = Decimal
_ORIGINAL_EMIT_EXECUTION_EVENTS = base._emit_execution_events
_INSTALLED = False


def confirmed_flattened_one_leg_pnl(record: dict[str, Any]) -> Decimal | None:
    result = record.get("result") or {}
    gemini = result.get("gemini") or {}
    kalshi = result.get("kalshi") or {}
    flatten = result.get("flatten") or {}
    if str(result.get("outcome") or "") != "ONE_LEG_FILLED":
        return None
    if str(gemini.get("status") or "").upper() != "FILLED":
        return None
    if D(str(kalshi.get("filled_quantity") or "0")) != D("0"):
        return None
    if str(flatten.get("status") or "").upper() != "FILLED":
        return None

    opened_qty = D(str(gemini.get("filled_quantity") or "0"))
    flattened_qty = D(str(flatten.get("filled_quantity") or "0"))
    if opened_qty <= 0 or flattened_qty != opened_qty:
        return None

    open_price = D(str(gemini.get("average_fill_price")))
    open_fee = D(str(gemini.get("fee_paid") or "0"))
    exit_price = D(str(flatten.get("average_fill_price")))
    exit_fee = D(str(flatten.get("fee_paid") or "0"))
    return (exit_price * flattened_qty - exit_fee) - (open_price * opened_qty + open_fee)


async def _record_confirmed_flatten(http, run_id: str, record: dict[str, Any], pnl: Decimal) -> None:
    result = record["result"]
    gp = record.get("gemini_payload") or {}
    kp = record.get("kalshi_payload") or record.get("kalshi_payload_original") or {}
    gemini = result["gemini"]
    flatten = result["flatten"]
    symbol = str(gp.get("symbol") or "")
    event_ticker = base._event_ticker_from_symbol(symbol)
    asset = base._asset_from_event_ticker(event_ticker)
    kalshi_ticker = str(kp.get("ticker") or "")
    trade_id = f"gk15m-{run_id}"
    event_ts = str(record.get("completed_at_utc") or base._iso())
    qty = D(str(flatten["filled_quantity"]))
    exit_price = D(str(flatten["average_fill_price"]))
    exit_fee = D(str(flatten.get("fee_paid") or "0"))
    net_exit_proceeds = exit_price * qty - exit_fee

    await base._post_event(http, {
        "event_id": f"{trade_id}-flatten-gemini",
        "trade_id": trade_id,
        "event_type": "FILL",
        "event_ts": event_ts,
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": f"{event_ticker}|{kalshi_ticker}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "side": "Gemini EMERGENCY_FLATTEN",
        "status": "FILLED",
        "quantity": str(qty),
        "price": str(exit_price),
        "amount": str(net_exit_proceeds),
        "fee": str(exit_fee),
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            "run_id": run_id,
            "client_order_id": flatten.get("client_order_id"),
            "original_gemini_client_order_id": gemini.get("client_order_id"),
            "flatten_confirmed": True,
            "position_flat_after_event": True,
        },
    })
    await base._post_event(http, {
        "event_id": f"{trade_id}-realized-flatten",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": event_ts,
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": f"{event_ticker}|{kalshi_ticker}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "side": "ONE_LEG_FILLED_THEN_FLATTENED",
        "status": "REALIZED_FLATTENED_ONE_LEG",
        "quantity": str(qty),
        "fee": "0",
        "gross_pnl": str(pnl),
        "net_pnl": str(pnl),
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            "run_id": run_id,
            "execution_outcome": result.get("outcome"),
            "hard_halt_required": bool(result.get("hard_halt_required")),
            "flatten_confirmed": True,
            "position_flat_after_event": True,
            "kalshi_filled_quantity": str((result.get("kalshi") or {}).get("filled_quantity") or "0"),
            "realization_source": "CONFIRMED_OPEN_AND_EMERGENCY_FLATTEN_FILLS",
        },
    })


async def emit_execution_events_with_incident_accounting(http, run_id: str, record: dict[str, Any]):
    try:
        return await _ORIGINAL_EMIT_EXECUTION_EVENTS(http, run_id, record)
    except RuntimeError:
        pnl = confirmed_flattened_one_leg_pnl(record)
        if pnl is not None:
            await _record_confirmed_flatten(http, run_id, record, pnl)
        raise


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base._emit_execution_events = emit_execution_events_with_incident_accounting
    _INSTALLED = True
