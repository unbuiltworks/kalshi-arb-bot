"""Exact safety cleanup for an interrupted Gemini Sports L5 q1 canary.

Cancels only the two deterministic client order IDs belonging to the durable
Sports reservation, reconciles authoritative fills, and retains any filled
inventory for normal Gemini settlement. It never broad-cancels the account and
never sells a normal strategy residual.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src.gemini_short_crypto_btc5m_inventory_maker_canary_q1 import inventory_status
from src import gemini_short_crypto_local_budget as local
from src import gemini_sports_l5_inventory_local_budget as budget


async def run() -> dict:
    state = local.local_open_state()
    if state is None:
        return {
            "type": "SPORTS_INTERRUPT_RECONCILE",
            "status": "NO_LOCAL_RESERVATION",
            "order_entry": False,
        }
    if str(state.get("strategy") or "") != budget.STRATEGY:
        raise RuntimeError(f"unexpected reservation strategy={state.get('strategy')!r}")
    trade_id = str(state.get("trade_id") or "")
    symbol = str(state.get("symbol") or "")
    event_ticker = str(state.get("event_ticker") or "")
    if not trade_id or not symbol or not event_ticker:
        raise RuntimeError("incomplete Sports reservation identity")

    yes_id, no_id = maker_client_ids(trade_id)
    maker = base.GeminiMakerSession()
    try:
        await maker.connect()
        yes_row = await base._order_by_client(yes_id, symbol)
        no_row = await base._order_by_client(no_id, symbol)
        if yes_row is None or no_row is None:
            raise RuntimeError("exact canary order history not available")
        yes_cancel = await base._cancel_maker(maker, row=yes_row)
        no_cancel = await base._cancel_maker(maker, row=no_row)
        await asyncio.sleep(0.25)
        _, _, yes_fill, no_fill = await base._reconcile_pair(yes_id, no_id, symbol)
    finally:
        await maker.close()

    if not (D("0") <= yes_fill <= D("1") and D("0") <= no_fill <= D("1")):
        raise RuntimeError("q1 fill bounds violated")
    status = inventory_status(yes_fill, no_fill)
    if yes_fill == 0 and no_fill == 0:
        if not yes_cancel.get("ok") or not no_cancel.get("ok"):
            raise RuntimeError("zero-fill canary cancel not authoritative")
        await budget.release(
            symbol=symbol,
            event_ticker=event_ticker,
            trade_id=trade_id,
            reason="INTERRUPTED_CANARY_ZERO_FILL_CANCELLED",
        )
        released = True
    else:
        await budget.result(
            symbol=symbol,
            event_ticker=event_ticker,
            trade_id=trade_id,
            status=status,
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            metadata={
                "interrupt_reconciled": True,
                "yes_cancel": yes_cancel,
                "no_cancel": no_cancel,
                "settlement_required": True,
                "normal_sell_exit": False,
            },
        )
        released = False

    return {
        "type": "SPORTS_INTERRUPT_RECONCILE",
        "status": status,
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "yes_client_id": yes_id,
        "no_client_id": no_id,
        "yes_fill": str(yes_fill),
        "no_fill": str(no_fill),
        "yes_cancel_ok": bool(yes_cancel.get("ok")),
        "no_cancel_ok": bool(no_cancel.get("ok")),
        "reservation_released": released,
        "settlement_required": not released,
        "realized": False,
        "order_entry": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
