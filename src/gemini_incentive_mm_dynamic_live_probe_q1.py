"""Disarmed-by-default ETH5 q=1 dynamic-complement execution probe wrapper.

This wrapper is a separate generation from the prior static/striking-distance q1
probe. It performs no venue/account calls unless its own dual key is armed. When
armed, it temporarily substitutes ETH5 discovery into the bounded dynamic maker
child, then restores all process state before authoritative settlement polling.

No live trigger or launcher is created by this module.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
import time
from typing import Any

from src import gemini_short_crypto_dynamic_complement_q1 as dyn
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
from src import gemini_short_crypto_local_budget as local_base
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m

LIVE_ENV = "GEMINI_DYNAMIC_COMPLEMENT_LIVE_PROBE_Q1"
PROBE_ID_ENV = "GEMINI_DYNAMIC_COMPLEMENT_PROBE_ID"
PROBE_ARM_ENV = "GEMINI_DYNAMIC_COMPLEMENT_PROBE_ARM"
TARGET_ASSET = "ETH"
TARGET_HORIZON = "5m"
SETTLEMENT_TIMEOUT_S = 12 * 60
SETTLEMENT_POLL_S = 5.0


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_DYNAMIC_COMPLEMENT_LIVE_Q1 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"), default=str,
        ),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    probe_id = os.getenv(PROBE_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and probe_id and os.getenv(PROBE_ARM_ENV, "").strip() == probe_id)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "probe"


def _restore_env(saved: dict[str, str | None]) -> None:
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


async def _wait_for_settlement() -> dict[str, Any]:
    deadline = time.monotonic() + SETTLEMENT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return await settle.reconcile()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if not any(marker in msg for marker in (
                "settlement not posted yet",
                "missing exact canary order history",
                "open reservation has zero authoritative fills",
            )):
                raise
            emit(
                "WAITING_FOR_SETTLEMENT",
                target_asset=TARGET_ASSET,
                target_horizon=TARGET_HORIZON,
                seconds_remaining=max(0, int(deadline - time.monotonic())),
                order_entry=False,
            )
            await asyncio.sleep(SETTLEMENT_POLL_S)
    raise RuntimeError("dynamic-complement q1 settlement timeout with unresolved reservation")


async def run() -> None:
    # Strong safety property: wrapper disarm is checked before local account state,
    # discovery, credentials, or any venue call.
    if not live_armed():
        emit(
            "DISARMED",
            target_asset=TARGET_ASSET,
            target_horizon=TARGET_HORIZON,
            q_per_outcome=1,
            entry_pair_edge_floor=str(dyn.ENTRY_PAIR_EDGE),
            final_pair_edge_floor=str(dyn.FINAL_PAIR_EDGE),
            initial_wait_seconds=dyn.INITIAL_WAIT_SECONDS,
            rescue_seconds=dyn.RESCUE_SECONDS,
            max_reprices=dyn.MAX_REPRICES,
            taker_rescue_enabled=False,
            order_entry=False,
            credentials_read=False,
            realized_pnl=None,
            classification="DISARMED_NO_VENUE_CALLS",
        )
        return

    if local_base.local_open_state() is not None:
        raise RuntimeError("dynamic-complement q1 probe blocked by unresolved local reservation")

    probe_id = _clean(os.environ[PROBE_ID_ENV], 18)
    child_id = f"dcq1-{probe_id}"[:36]
    original_discover = base.discover_btc5m
    child_env_names = (dyn.LIVE_ENV, dyn.CANARY_ID_ENV, dyn.CANARY_ARM_ENV)
    saved_child_env = {name: os.environ.get(name) for name in child_env_names}

    emit(
        "START",
        probe_id=probe_id,
        child_canary_id=child_id,
        target_asset=TARGET_ASSET,
        target_horizon=TARGET_HORIZON,
        q_per_outcome=1,
        max_campaigns=1,
        entry_pair_edge_floor=str(dyn.ENTRY_PAIR_EDGE),
        final_pair_edge_floor=str(dyn.FINAL_PAIR_EDGE),
        initial_wait_seconds=dyn.INITIAL_WAIT_SECONDS,
        rescue_seconds=dyn.RESCUE_SECONDS,
        reprice_interval_seconds=dyn.REPRICE_INTERVAL_SECONDS,
        max_reprices=dyn.MAX_REPRICES,
        quote_policy="RAW_ENTRY_EDGE_RESERVED_THEN_DYNAMIC_MAKER_COMPLEMENT",
        taker_rescue_enabled=False,
        rewards_counted_as_realized=False,
        classification="LIVE_EXECUTION_PROBE_NOT_ARBITRAGE",
        order_entry=True,
    )

    try:
        base.discover_btc5m = discover_eth5m
        os.environ[dyn.LIVE_ENV] = "YES"
        os.environ[dyn.CANARY_ID_ENV] = child_id
        os.environ[dyn.CANARY_ARM_ENV] = child_id
        await dyn.run()
    finally:
        base.discover_btc5m = original_discover
        _restore_env(saved_child_env)

    local = local_base.local_open_state()
    if local is None:
        emit(
            "COMPLETE_NO_OPEN_RESERVATION",
            probe_id=probe_id,
            target_asset=TARGET_ASSET,
            target_horizon=TARGET_HORIZON,
            order_entry=False,
            realized_pnl=None,
            classification="LIVE_PROBE_EXECUTION_ENDED_NO_AUTHORITATIVE_SETTLEMENT_TO_REPORT",
        )
        return

    result = await _wait_for_settlement()
    pnl = D(str(result.get("net_profit") or "0"))
    emit(
        "SETTLED",
        probe_id=probe_id,
        target_asset=TARGET_ASSET,
        target_horizon=TARGET_HORIZON,
        trade_id=result.get("trade_id"),
        symbol=result.get("symbol"),
        yes_fill=result.get("yes_fill"),
        no_fill=result.get("no_fill"),
        yes_client_order_ids=result.get("yes_client_order_ids"),
        no_client_order_ids=result.get("no_client_order_ids"),
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
