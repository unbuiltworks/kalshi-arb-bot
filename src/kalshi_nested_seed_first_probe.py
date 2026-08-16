"""Bounded-risk seed-first execution for the one-shot nested Kalshi probe.

Why seed first:
- the selected complementary taker leg costs only ~1c;
- if that IOC does not fill, stop with no exposure;
- if it fills, only then rest the expensive maker leg;
- because the cheap nested leg is already owned, there is no maker-fill -> hedge
  latency race;
- if the maker never fills, the seeded leg's purchase+fee is the primary maximum
  cash-at-risk for this experiment.

This function STILL requires the explicit live arm gate and consumes its one-use
token immediately before the first order.  Nothing calls it from Railway by default.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import time
from typing import Any

from src.kalshi_nested_live_probe_guard import (
    D,
    NestedLiveProbeSpec,
    build_v2_payloads,
    live_probe_authorized,
    validate_spec,
)
from src.kalshi_nested_maker_shadow import maker_then_taker_profit
from src.kalshi_nested_probe_transport import KalshiV2RestTransport, OrderState

MAX_SEED_CASH_RISK = D("0.02")


@dataclass(frozen=True)
class SeedFirstResult:
    status: str
    seed_state: OrderState | None
    maker_state: OrderState | None
    modeled_seed_cash_risk: Decimal
    actual_worst_case_pnl: Decimal | None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "seed_state": self.seed_state.as_dict() if self.seed_state else None,
            "maker_state": self.maker_state.as_dict() if self.maker_state else None,
            "modeled_seed_cash_risk": str(self.modeled_seed_cash_risk),
            "actual_worst_case_pnl": str(self.actual_worst_case_pnl) if self.actual_worst_case_pnl is not None else None,
            "detail": self.detail,
        }


def modeled_seed_cash_risk(spec: NestedLiveProbeSpec) -> Decimal:
    """Worst cash outlay if only the cheap taker leg fills (1 contract)."""
    _, taker_fee = maker_then_taker_profit(
        maker_price=spec.maker_cost,
        hedge_price=spec.hedge_cost,
        fee_multiplier=spec.fee_multiplier,
        quantity=D("1"),
    )
    return D(spec.hedge_cost) + taker_fee


def validate_seed_first(spec: NestedLiveProbeSpec, *, now=None) -> Decimal:
    validate_spec(spec, now=now)
    risk = modeled_seed_cash_risk(spec)
    if risk > MAX_SEED_CASH_RISK:
        raise ValueError(f"seed-first probe cash risk {risk} exceeds {MAX_SEED_CASH_RISK}")
    return risk


def actual_nested_worst_case_pnl(seed: OrderState, maker: OrderState | None) -> Decimal:
    """Conservative settlement floor using actual filled quantities/cash costs.

    For the proven nested pair, the minimum payout across settlement regions is
    min(filled quantity on each leg).  With no maker fill that minimum is zero.
    """
    maker_fill = maker.fill_count if maker is not None else D("0")
    maker_cost = maker.total_cash_cost if maker is not None else D("0")
    minimum_payout = min(seed.fill_count, maker_fill)
    return minimum_payout - seed.total_cash_cost - maker_cost


async def _final_state(transport: KalshiV2RestTransport, order_id: str, *, delay: float = 0.05) -> OrderState:
    if delay > 0:
        await asyncio.sleep(delay)
    return await transport.get(order_id)


async def execute_seed_first(
    spec: NestedLiveProbeSpec,
    *,
    cli_live: bool,
    transport: KalshiV2RestTransport | None = None,
) -> SeedFirstResult:
    """Execute at most one seed IOC and one short-lived post-only maker order."""
    risk = validate_seed_first(spec)
    if not live_probe_authorized(cli_live, consume_token=False):
        raise RuntimeError("nested live probe is not armed")

    maker_payload, seed_payload = build_v2_payloads(spec)
    own_transport = transport is None
    tx = transport or KalshiV2RestTransport()
    try:
        # Consume the single-use arm immediately before the first live request.
        if not live_probe_authorized(cli_live, consume_token=True):
            raise RuntimeError("nested live arm token disappeared before send")

        seed_ack = await tx.create(seed_payload)
        seed_state = await _final_state(tx, seed_ack.order_id)
        if seed_state.fill_count != D("1"):
            # IOC should have no resting remainder, but cancel defensively if API
            # state says otherwise. Never place the maker unless the seed is whole.
            if seed_state.remaining_count > 0:
                await tx.cancel(seed_state.order_id)
            return SeedFirstResult(
                status="SEED_NOT_FULL_STOP",
                seed_state=seed_state,
                maker_state=None,
                modeled_seed_cash_risk=risk,
                actual_worst_case_pnl=-seed_state.total_cash_cost,
                detail="maker was not submitted because seed IOC did not fill exactly 1.00",
            )

        # Refresh maker expiry AFTER the seed is confirmed so the intended resting
        # window is not shortened by discovery/seed network time.
        maker_payload["expiration_time"] = int(time.time()) + int(spec.maker_rest_seconds)
        maker_ack = await tx.create(maker_payload)
        await asyncio.sleep(int(spec.maker_rest_seconds) + 0.25)
        maker_state = await tx.get(maker_ack.order_id)
        if maker_state.remaining_count > 0:
            try:
                await tx.cancel(maker_state.order_id)
            finally:
                maker_state = await _final_state(tx, maker_state.order_id, delay=0.05)

        worst = actual_nested_worst_case_pnl(seed_state, maker_state)
        status = "NESTED_PAIR_FULL" if maker_state.fill_count >= D("1") else (
            "NESTED_PAIR_PARTIAL" if maker_state.fill_count > 0 else "SEED_ONLY_MAKER_NO_FILL"
        )
        return SeedFirstResult(
            status=status,
            seed_state=seed_state,
            maker_state=maker_state,
            modeled_seed_cash_risk=risk,
            actual_worst_case_pnl=worst,
        )
    finally:
        if own_transport:
            await tx.close()
