"""Reconcile the single settled ETH5 V3 probe into the AWS-local ledger.

This performs no venue mutation. It first proves the Gemini short-crypto account is flat,
then records the already-observed one-sided settled loss and releases the stale local lock.
"""
from __future__ import annotations

import asyncio
import json

from src import gemini_short_crypto_local_budget as local

TRADE = "crypto-gem-eth5m-dyn-dcq1-dcq1-eth5v3_20260-"
SYMBOL = "GEMI-ETH05M2608290140-UP"
TICKER = "ETH05M2608290140"


async def main() -> None:
    state = local.local_open_state()
    assert state is not None
    assert state.get("trade_id") == TRADE, state
    assert state.get("symbol") == SYMBOL, state

    before = await local.account_exposure_snapshot()
    assert before["active_short_crypto_orders"] == 0, before
    assert before["active_short_crypto_positions"] == 0, before

    await local.result(
        symbol=SYMBOL,
        event_ticker=TICKER,
        trade_id=TRADE,
        status="SETTLED_ONE_SIDED_YES_LOSS_RECONCILED",
        legs_filled=1,
        metadata={
            "yes_fill": "1",
            "yes_price": "0.45",
            "no_fill": "0",
            "no_price": "0.43",
            "available_usd_before": "61.8455",
            "available_usd_after": "61.3855",
            "account_cash_delta": "-0.4600",
            "reconciliation_basis": "EXACT_ORDER_HISTORY_PLUS_ACCOUNT_CASH_DELTA",
            "manual_reconciliation": True,
        },
    )
    await local.release(
        symbol=SYMBOL,
        event_ticker=TICKER,
        trade_id=TRADE,
        reason="POST_CRASH_SETTLED_ONE_SIDED_LOSS_RECONCILED",
    )

    after_state = local.local_open_state()
    after = await local.account_exposure_snapshot()
    assert after_state is None, after_state
    assert after["active_short_crypto_orders"] == 0, after
    assert after["active_short_crypto_positions"] == 0, after
    print(json.dumps({"local_open_state": after_state, **after}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
