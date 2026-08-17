"""Bounded-risk seed-first execution for the one-shot nested Kalshi probe.

Why seed first:
- the selected complementary taker leg costs only ~1c;
- if that IOC does not fill, stop with no further exposure;
- if it fills, only then rest the expensive maker leg;
- because the cheap nested leg is already owned, there is no maker-fill -> hedge
  latency race;
- if the maker never fills, the seeded leg's purchase+fee is the primary maximum
  cash-at-risk for this experiment.

This function requires the explicit live arm gate and consumes its one-use token
immediately before the first order. Nothing calls it from Railway by default.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import re
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
from src.kalshi_nested_probe_transport import (
    AmbiguousOrderSubmission,
    KalshiV2RestTransport,
    OrderState,
)

MAX_SEED_CASH_RISK = D("0.02")
CLIENT_ROOT_RE = re.compile(r"^[A-Za-z0-9_-]{8,48}$")


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
    """Worst modeled cash outlay if only the cheap taker leg fills (1 contract)."""
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
    """Conservative settlement floor using actual filled quantities/cash costs."""
    maker_fill = maker.fill_count if maker is not None else D("0")
    maker_cost = maker.total_cash_cost if maker is not None else D("0")
    minimum_payout = min(seed.fill_count, maker_fill)
    return minimum_payout - seed.total_cash_cost - maker_cost


def apply_client_order_root(
    maker_payload: dict[str, Any],
    seed_payload: dict[str, Any],
    client_order_root: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Optionally replace random IDs with deterministic one-shot session IDs."""
    if client_order_root is None:
        return maker_payload, seed_payload
    root = str(client_order_root).strip()
    if not CLIENT_ROOT_RE.fullmatch(root):
        raise ValueError("client_order_root must be 8..48 safe characters")
    maker_payload = dict(maker_payload)
    seed_payload = dict(seed_payload)
    maker_payload["client_order_id"] = root + "-maker"
    seed_payload["client_order_id"] = root + "-seed"
    return maker_payload, seed_payload


async def _final_state(transport: KalshiV2RestTransport, order_id: str, *, delay: float = 0.05) -> OrderState:
    if delay > 0:
        await asyncio.sleep(delay)
    return await transport.get(order_id)


async def _reconcile_late(
    transport: KalshiV2RestTransport,
    *,
    payload: dict[str, Any],
    delay: float = 0.25,
) -> OrderState | None:
    if delay > 0:
        await asyncio.sleep(delay)
    return await transport.reconcile_by_client_id(
        ticker=str(payload["ticker"]),
        client_order_id=str(payload["client_order_id"]),
    )


async def execute_seed_first(
    spec: NestedLiveProbeSpec,
    *,
    cli_live: bool,
    transport: KalshiV2RestTransport | None = None,
    client_order_root: str | None = None,
) -> SeedFirstResult:
    """Execute at most one seed IOC and one short-lived post-only maker order."""
    risk = validate_seed_first(spec)
    if not live_probe_authorized(cli_live, consume_token=False):
        raise RuntimeError("nested live probe is not armed")

    maker_payload, seed_payload = build_v2_payloads(spec)
    maker_payload, seed_payload = apply_client_order_root(
        maker_payload, seed_payload, client_order_root
    )
    own_transport = transport is None
    tx = transport or KalshiV2RestTransport()
    try:
        if not live_probe_authorized(cli_live, consume_token=True):
            raise RuntimeError("nested live arm token disappeared before send")

        try:
            seed_ack = await tx.create(seed_payload)
            seed_state = await _final_state(tx, seed_ack.order_id)
        except AmbiguousOrderSubmission:
            seed_state = await _reconcile_late(tx, payload=seed_payload)
            if seed_state is None:
                return SeedFirstResult(
                    status="SEED_SUBMISSION_AMBIGUOUS_STOP",
                    seed_state=None,
                    maker_state=None,
                    modeled_seed_cash_risk=risk,
                    actual_worst_case_pnl=None,
                    detail="seed submission state remained unknown; maker was never submitted",
                )
        except RuntimeError as exc:
            return SeedFirstResult(
                status="SEED_REJECTED_STOP",
                seed_state=None,
                maker_state=None,
                modeled_seed_cash_risk=risk,
                actual_worst_case_pnl=D("0"),
                detail=str(exc)[:300],
            )

        if seed_state.fill_count != D("1"):
            if seed_state.remaining_count > 0:
                await tx.cancel(seed_state.order_id)
                seed_state = await _final_state(tx, seed_state.order_id)
            return SeedFirstResult(
                status="SEED_NOT_FULL_STOP",
                seed_state=seed_state,
                maker_state=None,
                modeled_seed_cash_risk=risk,
                actual_worst_case_pnl=-seed_state.total_cash_cost,
                detail="maker was not submitted because seed IOC did not fill exactly 1.00",
            )

        maker_payload["expiration_time"] = int(time.time()) + int(spec.maker_rest_seconds)

        try:
            maker_ack = await tx.create(maker_payload)
            maker_state = await _final_state(tx, maker_ack.order_id)
        except AmbiguousOrderSubmission:
            maker_state = await _reconcile_late(tx, payload=maker_payload)
            if maker_state is None:
                return SeedFirstResult(
                    status="MAKER_SUBMISSION_AMBIGUOUS",
                    seed_state=seed_state,
                    maker_state=None,
                    modeled_seed_cash_risk=risk,
                    actual_worst_case_pnl=None,
                    detail=(
                        "maker submission state remained unknown; seed was already owned and "
                        "the outbound maker was the validated complementary nested leg"
                    ),
                )
        except RuntimeError as exc:
            return SeedFirstResult(
                status="SEED_ONLY_MAKER_REJECTED",
                seed_state=seed_state,
                maker_state=None,
                modeled_seed_cash_risk=risk,
                actual_worst_case_pnl=-seed_state.total_cash_cost,
                detail=str(exc)[:300],
            )

        await asyncio.sleep(int(spec.maker_rest_seconds) + 0.25)
        maker_state = await tx.get(maker_state.order_id)
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
