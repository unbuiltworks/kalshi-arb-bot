"""Disarmed-by-default q=1 live execution probe for Gemini Predictions maker fillability.

Purpose: measure real maker queue/fill behavior for the incentive-aware two-sided
strategy without pretending the q=1 probe itself captures q=10 liquidity rewards.

The first target is ETH 5m because both the conservative Gemini shadow fills and
the corrected l5 public market-cohort audit currently favor ETH5 over the other
short-crypto families. The proven BTC5 inventory-maker child is reused by swapping
only its event-discovery function for an ETH5 adapter while armed; all child state
is restored afterward.

This revision moves both resting maker quotes inward before submission, using spare
spread while preserving at least 3c of fee-adjusted matched-pair edge. The intent is
that whichever side fills first, the complement is already within striking distance
without cancel/repost churn or a second client-order identity.

Hard bounds:
- ETH 5m only for this incentive probe;
- exactly q=1 maximum per outcome;
- one campaign, no automatic restart;
- both maker quotes must remain inside the 20c-80c band;
- fee-adjusted matched-pair economics must preserve at least 3c;
- one-sided inventory may settle directionally, so this is NOT guaranteed arbitrage;
- durable reservation, cancel/reconcile, and authoritative settlement are reused
  from the proven q=1 child/reconciler;
- maker rebates/rewards are never counted as realized trading P&L;
- dual-key environment arming is mandatory.

This module is safe to import and safe to execute while disarmed: it performs no
venue calls before the wrapper dual-key is satisfied.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
import re
import time
from typing import Any, Callable, Mapping

import httpx

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
from src import gemini_short_crypto_local_budget as local_base

LIVE_ENV = "GEMINI_INCENTIVE_MM_LIVE_PROBE_Q1"
PROBE_ID_ENV = "GEMINI_INCENTIVE_MM_PROBE_ID"
PROBE_ARM_ENV = "GEMINI_INCENTIVE_MM_PROBE_ARM"

TARGET_ASSET = "ETH"
TARGET_HORIZON = "5m"
ETH5_EVENT_RE = re.compile(r"^ETH05M(\d{10})$")

ONE = D("1")
MIN_REBATE_BAND_PRICE = D("0.20")
MAX_REBATE_BAND_PRICE = D("0.80")
PRICE_TICK = D("0.01")
MIN_LOCKED_PAIR_EDGE = D("0.03")
PROBE_ENTRY_PAIR_EDGE = MIN_LOCKED_PAIR_EDGE
SETTLEMENT_TIMEOUT_S = 12 * 60
SETTLEMENT_POLL_S = 5.0


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_INCENTIVE_MM_LIVE_Q1 "
        + json.dumps(
            {
                "stage": stage,
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                **fields,
            },
            separators=(",", ":"),
            default=str,
        ),
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


def quotes_in_incentive_band(model: Mapping[str, Any]) -> bool:
    try:
        yes_quote = D(str(model["yes_quote"]))
        no_quote = D(str(model["no_quote"]))
    except Exception:
        return False
    return (
        MIN_REBATE_BAND_PRICE <= yes_quote <= MAX_REBATE_BAND_PRICE
        and MIN_REBATE_BAND_PRICE <= no_quote <= MAX_REBATE_BAND_PRICE
    )


def _fee_adjusted_edge(yes_quote: D, no_quote: D) -> D:
    yes_fee = base.gemini_predictions_maker_fee(yes_quote, ONE)
    no_fee = base.gemini_predictions_maker_fee(no_quote, ONE)
    return ONE - yes_quote - no_quote - yes_fee - no_fee


def _price_grid(start: D, cap: D) -> list[D]:
    start = D(str(start)).quantize(PRICE_TICK)
    cap = D(str(cap)).quantize(PRICE_TICK)
    if cap < start:
        return [start]
    out: list[D] = []
    price = start
    while price <= cap:
        out.append(price)
        price += PRICE_TICK
    return out


def aggressive_pair_model(original: Callable[[D, D], dict[str, D]]) -> Callable[[D, D], dict[str, D]]:
    """Spend spare spread to bring both maker quotes inward while keeping >=3c edge.

    For the YES instrument, a passive YES bid must stay below the current YES ask.
    A passive NO bid is equivalent to staying below ``1 - YES bid``. We therefore
    cap each side one tick from crossing and search the cent grid for the most
    balanced inward move that still preserves ``MIN_LOCKED_PAIR_EDGE`` after the
    current Gemini maker-fee formula.
    """
    def model(bid: D, ask: D) -> dict[str, D]:
        bid = D(str(bid))
        ask = D(str(ask))
        raw = dict(original(bid, ask))
        yes0 = D(str(raw["yes_quote"]))
        no0 = D(str(raw["no_quote"]))

        if not quotes_in_incentive_band(raw):
            raw["modeled_edge"] = D("-1")
            return raw

        yes_cap = min(MAX_REBATE_BAND_PRICE, ask - PRICE_TICK)
        no_cap = min(MAX_REBATE_BAND_PRICE, ONE - bid - PRICE_TICK)
        yes_cap = max(yes0, yes_cap)
        no_cap = max(no0, no_cap)

        best_yes = yes0
        best_no = no0
        best_edge = _fee_adjusted_edge(yes0, no0)
        best_score = (D("0"), D("0"))

        for yes_quote in _price_grid(yes0, yes_cap):
            for no_quote in _price_grid(no0, no_cap):
                if not (
                    MIN_REBATE_BAND_PRICE <= yes_quote <= MAX_REBATE_BAND_PRICE
                    and MIN_REBATE_BAND_PRICE <= no_quote <= MAX_REBATE_BAND_PRICE
                ):
                    continue
                edge = _fee_adjusted_edge(yes_quote, no_quote)
                if edge < MIN_LOCKED_PAIR_EDGE:
                    continue
                yes_improve = yes_quote - yes0
                no_improve = no_quote - no0
                score = (min(yes_improve, no_improve), yes_improve + no_improve)
                if score > best_score:
                    best_score = score
                    best_yes = yes_quote
                    best_no = no_quote
                    best_edge = edge

        yes_fee = base.gemini_predictions_maker_fee(best_yes, ONE)
        no_fee = base.gemini_predictions_maker_fee(best_no, ONE)
        raw.update(
            {
                "yes_quote": best_yes,
                "no_quote": best_no,
                "yes_fee": yes_fee,
                "no_fee": no_fee,
                "pair_cost": best_yes + best_no,
                "modeled_edge": best_edge,
                "spread": ask - bid,
                "raw_yes_quote": yes0,
                "raw_no_quote": no0,
                "yes_inward_move": best_yes - yes0,
                "no_inward_move": best_no - no0,
            }
        )
        return raw

    return model


def patched_pair_model(original: Callable[[D, D], dict[str, D]]) -> Callable[[D, D], dict[str, D]]:
    """Apply striking-distance quoting and retain the 20c-80c safety band."""
    aggressive = aggressive_pair_model(original)

    def model(bid: D, ask: D) -> dict[str, D]:
        out = dict(aggressive(D(str(bid)), D(str(ask))))
        if not quotes_in_incentive_band(out):
            out["modeled_edge"] = D("-1")
        return out

    return model


async def discover_eth5m() -> base.Event | None:
    """Return the nearest eligible ETH05M event in the proven child Event shape."""
    found: list[base.Event] = []
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-eth5m-incentive-q1/1.0"},
    ) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{base.REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                if not ETH5_EVENT_RE.match(ticker):
                    continue
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = base._iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None or expiry <= int(time.time() * 1000):
                        continue
                    found.append(
                        base.Event(
                            ticker=ticker,
                            symbol=symbol,
                            expiry_ms=expiry,
                            quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                            quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                            price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                        )
                    )
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    eligible = [event for event in found if event.seconds_to_expiry >= inv.MIN_SECONDS_TO_EXPIRY]
    return min(eligible, key=lambda event: event.expiry_ms) if eligible else None


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
                target_asset=TARGET_ASSET,
                target_horizon=TARGET_HORIZON,
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
            target_asset=TARGET_ASSET,
            target_horizon=TARGET_HORIZON,
            q_per_outcome=1,
            max_campaigns=1,
            min_locked_pair_edge=str(MIN_LOCKED_PAIR_EDGE),
            order_entry=False,
            realized_pnl=None,
            classification="DISARMED_NO_VENUE_CALLS",
        )
        return

    if local_base.local_open_state() is not None:
        raise RuntimeError("incentive-mm q1 probe blocked by unresolved local reservation")

    probe_id = _clean(os.environ[PROBE_ID_ENV], 18)
    child_id = f"immq1-{probe_id}"[:36]
    original_floor = inv.ENTRY_PAIR_EDGE
    original_pair_model = base.pair_model
    original_discover = base.discover_btc5m
    child_env_names = (inv.LIVE_ENV, inv.CANARY_ID_ENV, inv.CANARY_ARM_ENV)
    saved_child_env = {name: os.environ.get(name) for name in child_env_names}

    emit(
        "START",
        probe_id=probe_id,
        child_canary_id=child_id,
        target_asset=TARGET_ASSET,
        target_horizon=TARGET_HORIZON,
        q_per_outcome=1,
        max_campaigns=1,
        min_locked_pair_edge=str(MIN_LOCKED_PAIR_EDGE),
        quote_policy="STRIKING_DISTANCE_TWO_SIDED_MAKER",
        incentive_price_band=f"{MIN_REBATE_BAND_PRICE}-{MAX_REBATE_BAND_PRICE}",
        liquidity_rewards_eligible_at_q1=False,
        maker_rewards_counted_as_realized=False,
        classification="LIVE_EXECUTION_PROBE_NOT_ARBITRAGE",
        order_entry=True,
    )

    try:
        inv.ENTRY_PAIR_EDGE = PROBE_ENTRY_PAIR_EDGE
        base.pair_model = patched_pair_model(original_pair_model)
        base.discover_btc5m = discover_eth5m
        os.environ[inv.LIVE_ENV] = "YES"
        os.environ[inv.CANARY_ID_ENV] = child_id
        os.environ[inv.CANARY_ARM_ENV] = child_id
        await inv.run()
    finally:
        inv.ENTRY_PAIR_EDGE = original_floor
        base.pair_model = original_pair_model
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
