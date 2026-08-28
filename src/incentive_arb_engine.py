"""Venue-neutral economics for incentive-aware binary market making and arbitrage.

Pure accounting/policy only: no network, credentials, balances, or order entry.

The core invariant is intentionally strict:
- an arbitrage must be profitable from the trade/settlement cash flows after known fees;
- maker rebates, liquidity rewards, volume rewards, or other incentives NEVER rescue a
  negative trade into the arbitrage bucket;
- only actually paid rewards may be added to realized accounting.

Polymarket and Gemini can be modeled as complementary YES/NO inventory with a $1
binary payout for equal quantities. Kalshi economically nets opposing positions, so
we label the same positive round-trip math as a locked roundtrip rather than a
complete-set holding.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from typing import Iterable

ONE = D("1")
ZERO = D("0")
SUPPORTED_VENUES = {"POLYMARKET", "GEMINI", "KALSHI"}


def dec(value: D | str | int | float) -> D:
    return value if isinstance(value, D) else D(str(value))


@dataclass(frozen=True)
class PairEconomics:
    venue: str
    quantity: D
    yes_price: D
    no_price: D
    yes_fee: D
    no_fee: D
    trading_cost: D
    guaranteed_payout: D
    locked_edge: D
    locked_edge_per_pair: D
    classification: str
    is_arb: bool
    reward_credit_used_for_arb: D = ZERO


@dataclass(frozen=True)
class InventoryRisk:
    yes_qty: D
    no_qty: D
    yes_vwap: D | None
    no_vwap: D | None
    matched_qty: D
    paired_acquisition_cost: D
    residual_side: str | None
    residual_qty: D
    residual_acquisition_cost: D


@dataclass(frozen=True)
class RewardPayment:
    kind: str
    amount: D
    status: str = "PAID"


@dataclass(frozen=True)
class AccountingSummary:
    trade_settlement_realized: D
    paid_rewards: D
    projected_rewards: D
    realized_net: D
    classification: str


@dataclass(frozen=True)
class SizeDecision:
    mode: str
    allowed_quantity: D
    reason: str


def venue_pair_label(venue: str) -> str:
    v = venue.upper()
    if v not in SUPPORTED_VENUES:
        raise ValueError(f"unsupported venue: {venue}")
    if v == "KALSHI":
        return "LOCKED_ROUNDTRIP"
    return "COMPLETE_SET"


def evaluate_pair(
    *,
    venue: str,
    yes_price: D | str | float,
    no_price: D | str | float,
    quantity: D | str | float = D("1"),
    yes_fee: D | str | float = ZERO,
    no_fee: D | str | float = ZERO,
    min_locked_edge: D | str | float = ZERO,
) -> PairEconomics:
    """Evaluate equal YES/NO quantities without giving credit for rewards.

    Fees are absolute cash amounts for the proposed quantity, not percentages.
    A positive rebate must NOT be passed as a negative fee; rebates belong in the
    separate realized-reward ledger after they are actually paid.
    """
    v = venue.upper()
    if v not in SUPPORTED_VENUES:
        raise ValueError(f"unsupported venue: {venue}")
    yp, np = dec(yes_price), dec(no_price)
    q, yf, nf = dec(quantity), dec(yes_fee), dec(no_fee)
    floor = dec(min_locked_edge)
    if q <= ZERO:
        raise ValueError("quantity must be positive")
    if not (ZERO <= yp <= ONE and ZERO <= np <= ONE):
        raise ValueError("prices must be in [0, 1]")
    if yf < ZERO or nf < ZERO:
        raise ValueError("fees must be non-negative; rewards are separate")
    if floor < ZERO:
        raise ValueError("min_locked_edge must be non-negative")

    cost = (yp + np) * q + yf + nf
    payout = q
    edge = payout - cost
    edge_per_pair = edge / q
    is_arb = edge >= floor and edge > ZERO
    if is_arb:
        classification = f"{venue_pair_label(v)}_ARB"
    elif edge == ZERO:
        classification = f"{venue_pair_label(v)}_BREAKEVEN"
    else:
        classification = f"{venue_pair_label(v)}_NEGATIVE"
    return PairEconomics(
        venue=v,
        quantity=q,
        yes_price=yp,
        no_price=np,
        yes_fee=yf,
        no_fee=nf,
        trading_cost=cost,
        guaranteed_payout=payout,
        locked_edge=edge,
        locked_edge_per_pair=edge_per_pair,
        classification=classification,
        is_arb=is_arb,
    )


def inventory_risk(
    *,
    yes_qty: D | str | float,
    no_qty: D | str | float,
    yes_vwap: D | str | float | None,
    no_vwap: D | str | float | None,
) -> InventoryRisk:
    """Separate paired acquisition capital from naked residual acquisition capital."""
    yq, nq = dec(yes_qty), dec(no_qty)
    if yq < ZERO or nq < ZERO:
        raise ValueError("inventory cannot be negative")
    yv = None if yes_vwap is None else dec(yes_vwap)
    nv = None if no_vwap is None else dec(no_vwap)
    if yq > ZERO and yv is None:
        raise ValueError("yes_vwap required when yes_qty > 0")
    if nq > ZERO and nv is None:
        raise ValueError("no_vwap required when no_qty > 0")
    for value in (yv, nv):
        if value is not None and not (ZERO <= value <= ONE):
            raise ValueError("VWAP must be in [0, 1]")

    matched = min(yq, nq)
    paired_cost = matched * ((yv or ZERO) + (nv or ZERO))
    residual_side: str | None = None
    residual_qty = ZERO
    residual_cost = ZERO
    if yq > nq:
        residual_side = "YES"
        residual_qty = yq - nq
        residual_cost = residual_qty * (yv or ZERO)
    elif nq > yq:
        residual_side = "NO"
        residual_qty = nq - yq
        residual_cost = residual_qty * (nv or ZERO)

    return InventoryRisk(
        yes_qty=yq,
        no_qty=nq,
        yes_vwap=yv,
        no_vwap=nv,
        matched_qty=matched,
        paired_acquisition_cost=paired_cost,
        residual_side=residual_side,
        residual_qty=residual_qty,
        residual_acquisition_cost=residual_cost,
    )


def accounting_summary(
    *,
    trade_settlement_realized: D | str | float,
    rewards: Iterable[RewardPayment] = (),
) -> AccountingSummary:
    """Add only PAID rewards to realized P&L; retain estimates separately."""
    trade = dec(trade_settlement_realized)
    paid = ZERO
    projected = ZERO
    for reward in rewards:
        amount = dec(reward.amount)
        status = reward.status.upper()
        if status == "PAID":
            paid += amount
        else:
            projected += amount
    return AccountingSummary(
        trade_settlement_realized=trade,
        paid_rewards=paid,
        projected_rewards=projected,
        realized_net=trade + paid,
        classification="REALIZED_ONLY_PAID_REWARDS_INCLUDED",
    )


def sizing_decision(
    *,
    candidate_quantity: D | str | float,
    candidate_unit_cost: D | str | float,
    completes_residual: bool,
    locked_edge_per_pair: D | str | float | None,
    pair_floor: D | str | float,
    current_residual_qty: D | str | float,
    residual_unit_cap: D | str | float,
    paired_capital_remaining: D | str | float,
) -> SizeDecision:
    """Prioritize profitable completion while tightly bounding new naked inventory.

    This is not an execution instruction. It is a pure sizing gate for shadow/replay.
    """
    q = dec(candidate_quantity)
    unit_cost = dec(candidate_unit_cost)
    residual = dec(current_residual_qty)
    residual_cap = dec(residual_unit_cap)
    paired_cash = dec(paired_capital_remaining)
    floor = dec(pair_floor)
    if min(q, unit_cost, residual, residual_cap, paired_cash, floor) < ZERO:
        raise ValueError("inputs must be non-negative")
    if q == ZERO:
        return SizeDecision("SKIP", ZERO, "Candidate quantity is zero.")

    if completes_residual:
        if locked_edge_per_pair is None:
            return SizeDecision("WAIT", ZERO, "Completion edge is unknown.")
        edge = dec(locked_edge_per_pair)
        if edge < floor or edge <= ZERO:
            return SizeDecision("WAIT", ZERO, "Complement does not meet the trade-only locked-edge floor.")
        affordable = paired_cash / unit_cost if unit_cost > ZERO else q
        allowed = min(q, residual, affordable)
        if allowed <= ZERO:
            return SizeDecision("WAIT", ZERO, "No residual quantity or paired-capital budget remains.")
        return SizeDecision("PAIR_COMPLETE", allowed, "Risk-reducing profitable completion has priority.")

    headroom = max(ZERO, residual_cap - residual)
    allowed = min(q, headroom)
    if allowed <= ZERO:
        return SizeDecision("RESIDUAL_CAP", ZERO, "Fresh directional inventory would exceed the residual cap.")
    return SizeDecision("ACCUMULATE_BOUNDED", allowed, "Fresh inventory allowed only within the naked residual cap.")
