"""Disarmed-by-default q=1 live execution probe for Gemini Predictions maker fillability.

Purpose: measure real maker queue/fill behavior for the incentive-aware two-sided
strategy without pretending the q=1 probe itself captures q=10 liquidity rewards.

Hard bounds:
- BTC 5m only via the already-proven inventory-maker child;
- exactly q=1 maximum per outcome;
- one campaign, no automatic restart;
- initial maker quotes must both be inside the 20c-80c incentive band;
- initial fee-adjusted matched-pair economics may be at most 1c negative;
- one-sided inventory may settle directionally, so this is NOT arbitrage;
- durable reservation, cancel/reconcile, and authoritative settlement are reused
  from the proven q=1 child/reconciler;
- maker rebates/rewards are never counted as realized trading P&L;
- dual-key environment arming is mandatory.

This module is safe to import and safe to execute while disarmed: it performs no
venue calls before the wrapper dual-key is satisfied.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import os
import time
from typing import Any, Callable

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
from src import gemini_short_crypto_local_budget as local_base

LIVE_ENV = "GEMINI_INCENTIVE_MM_LIVE_PROBE_Q1"
PROBE_ID_ENV = "GEMINI_INCENTIVE_MM_PROBE_ID"
PROBE_ARM_ENV = "GEMINI_INCENTIVE_MM_PROBE_ARM"

MIN_REBATE_BAND_PRICE = D("0.20")
MAX_REBATE_BAND_PRICE = D("0.80")
MAX_INITIAL_PAIR_LOSS = D("0.01")
PROBE_ENTRY_PAIR_EDGE = -MAX_INITIAL_PAIR_LOSS
SETTLEMENT_TIMEOUT_S = 12 * 60
SETTLEMENT_POLL_S = 5.0


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_INCENTIVE_MM_LIVE_Q1 "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    probe_id = os.getenv(PROBE_ID_ENV, "").strip()
    return bool(
        _enabled(LIVE_ENV)
        and probe_id
        and os.getenv(PROBE_ARM_ENV, "").strip() == probe_id
    )


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "probe"


def quotes_in_incentive_band(model: dict[str, Any]) -> bool:
    try:
        yes_quote = D(str(model["yes_quote"]))
        no_quote = D(str(model["no_quote"]))
    except Exception:
        return False
    return (
        MIN_REBATE_BAND_PRICE <= yes_quote <= MAX_REBATE_BAND_PRICE
        and MIN_REBATE_BAND_PRICE <= no_quote <= MAX_REBATE_BAND_PRICE
    )


def patched_pair_model(original: Callable[[D, D], dict[str, D]]) -> Callable[[D, D], dict[str, D]]:
    """Keep the proven quote model but reject books outside the incentive band.

    The child itself enforces ``inv.ENTRY_PAIR_EDGE``. During this probe that
    floor is temporarily changed from +1c to -1c. Poisoning modeled_edge here
    guarantees the child's fresh-book revalidation also enforces the 20c-80c
    band immediately before order entry.
    """
    def model(bid: D, ask: D) -> dict[str, D]:
        out = dict(original(D(str(bid)), D(str(ask))))
        if not quotes_in_incentive_band(out):
            out["modeled_edge"] = D("-1")
        return out
    return model


async def _wait_for_settlement() -> dict[str, Any]:
    deadline = time.monotonic() + SETTLEMENT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return await settle.reconcile()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if not any(
                marker in msg
                for marker in (
                    "settlement not posted yet",
                    "missing exact canary order history",
                    "open reservation has zero authoritative fills",
                )
            ):
                raise
            emit(
                "WAITING_FOR_SETTLEMENT",
                seconds_remaining=max(0, int(deadline - time.monotonic())),
                order_entry=False,
            )
            await asyncio.sleep(SETTLEMENT_POLL_S)
    raise RuntimeError("incentive-mm q1 probe settlement timeout with unresolved reservation")


async def run() -> None:
    # Critical safety property: no venue/account calls while wrapper is disarmed.
    if not live_armed():
        emit(
            "DISARMED",
            q_per_outcome=1,
            max_campaigns=1,
            max_initial_pair_loss=str(MAX_INITIAL_PAIR_LOSS),
            order_entry=False,
            realized_pnl="0",
        )
        return

    if local_base.local_open_state() is not None:
        raise RuntimeError("incentive-mm q1 probe blocked by unresolved local reservation")

    probe_id = _clean(os.environ[PROBE_ID_ENV], 18)
    child_id = f"immq1-{probe_id}"[:36]
    original_floor = inv.ENTRY_PAIR_EDGE
    original_pair_model = base.pair_model

    emit(
        "START",
        probe_id=probe_id,
        child_canary_id=child_id,
        q_per_outcome=1,
        max_campaigns=1,
        max_initial_pair_loss=str(MAX_INITIAL_PAIR_LOSS),
        incentive_price_band=f"{MIN_REBATE_BAND_PRICE}-{MAX_REBATE_BAND_PRICE}",
        liquidity_rewards_eligible_at_q1=False,
        maker_rewards_counted_as_realized=False,
        classification="LIVE_EXECUTION_PROBE_NOT_ARBITRAGE",
        order_entry=True,
    )

    try:
        inv.ENTRY_PAIR_EDGE = PROBE_ENTRY_PAIR_EDGE
        base.pair_model = patched_pair_model(original_pair_model)
        os.environ[inv.LIVE_ENV] = "YES"
        os.environ[inv.CANARY_ID_ENV] = child_id
        os.environ[inv.CANARY_ARM_ENV] = child_id
        await inv.run()
    finally:
        inv.ENTRY_PAIR_EDGE = original_floor
        base.pair_model = original_pair_model

    local = local_base.local_open_state()
    if local is None:
        emit(
            "COMPLETE_NO_OPEN_INVENTORY",
            probe_id=probe_id,
            order_entry=False,
            realized_pnl="0",
        )
        return

    result = await _wait_for_settlement()
    pnl = D(str(result.get("net_profit") or "0"))
    emit(
        "SETTLED",
        probe_id=probe_id,
        trade_id=result.get("trade_id"),
        yes_fill=result.get("yes_fill"),
        no_fill=result.get("no_fill"),
        matched_quantity=result.get("matched_quantity"),
        residual_quantity=result.get("residual_quantity"),
        resolution_side=result.get("resolution_side"),
        maker_fees=result.get("maker_fees"),
        realized_trading_pnl=str(pnl),
        maker_rewards_included=False,
        classification="SETTLED_LIVE_PROBE_REALIZED_TRADING_PNL",
        order_entry=False,
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
