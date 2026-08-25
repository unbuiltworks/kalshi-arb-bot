"""Pure read-only primitives for single-venue crypto fair value and maker shadow math.

Nothing in this module can place, cancel, or modify an order.  It exists so the
spot-derived fair-value model, maker fee accounting, and pessimistic fill rules
can be tested independently from venue transports.

The probability model is intentionally simple and explicitly UNVALIDATED:
underlying log returns are modeled as zero-drift Gaussian increments with a
realized one-second volatility estimate.  For a binary paying YES when spot is
above a fixed strike at expiry:

    P(YES) = Phi(log(spot / strike) / (sigma_1s * sqrt(seconds_to_expiry)))

This model must not be treated as trading edge until settlement calibration says
that its probabilities are calibrated.  It deliberately does not use the
prediction-market midpoint as an input, preventing circular "fair value".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import statistics
from typing import Iterable

D = Decimal
TICK = D("0.01")
CENT = D("0.01")
MODEL_NAME = "ZERO_DRIFT_LOGNORMAL_REALIZED_VOL_V0_UNVALIDATED"


def fair_probability_yes(
    *,
    spot: float,
    strike: float,
    seconds_to_expiry: float,
    sigma_per_sqrt_second: float,
) -> float | None:
    """Return a model YES probability without consulting the market price."""
    if not (spot > 0 and strike > 0 and seconds_to_expiry > 0):
        return None
    if not math.isfinite(sigma_per_sqrt_second) or sigma_per_sqrt_second <= 0:
        return None
    denom = sigma_per_sqrt_second * math.sqrt(seconds_to_expiry)
    if denom <= 0 or not math.isfinite(denom):
        return None
    z = math.log(spot / strike) / denom
    # Normal CDF using erf; cap only to avoid exact 0/1 numerical endpoints.
    p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return min(0.999999, max(0.000001, p))


def realized_sigma_1s(prices: Iterable[float], *, min_returns: int = 30) -> float | None:
    """Estimate per-sqrt-second log-return volatility from ~1s samples."""
    vals = [float(x) for x in prices if x is not None and float(x) > 0]
    if len(vals) < min_returns + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(vals, vals[1:]) if a > 0 and b > 0]
    if len(rets) < min_returns:
        return None
    sigma = statistics.pstdev(rets)
    if not math.isfinite(sigma) or sigma <= 1e-9:
        return None
    return sigma


def floor_tick(value: float | Decimal, tick: Decimal = TICK) -> Decimal:
    x = D(str(value))
    return (x / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def ceil_tick(value: float | Decimal, tick: Decimal = TICK) -> Decimal:
    x = D(str(value))
    return (x / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _ceil_cent(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("fee cannot be negative")
    return (value / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def kalshi_maker_fee(
    price: Decimal,
    quantity: Decimal,
    *,
    fee_type: str,
    fee_multiplier: Decimal,
) -> Decimal:
    """Conservative maker fee from live Kalshi series metadata.

    Normal ``quadratic`` markets charge no maker fee.  Markets explicitly marked
    ``quadratic_with_maker_fees`` use Kalshi's published 0.0175 curve and are
    rounded up to the next cent at order level.  Unknown/flat types fail closed.
    """
    if quantity < 0 or price < 0 or price > 1 or fee_multiplier < 0:
        raise ValueError("invalid maker fee inputs")
    kind = str(fee_type or "").strip().lower()
    if kind == "quadratic":
        return D("0")
    if kind != "quadratic_with_maker_fees":
        raise ValueError(f"unsupported maker fee_type={fee_type!r}")
    raw = D("0.0175") * fee_multiplier * quantity * price * (D("1") - price)
    return _ceil_cent(raw)


@dataclass
class PessimisticQuote:
    """One hypothetical passive q=1 bid on YES or NO."""

    outcome: str
    price: Decimal
    queue_ahead: Decimal
    quantity: Decimal = D("1")
    cumulative_consumption: Decimal = D("0")
    saw_trade_through: bool = False

    def __post_init__(self) -> None:
        self.outcome = self.outcome.upper()
        if self.outcome not in {"YES", "NO"}:
            raise ValueError("outcome must be YES or NO")
        if self.price <= 0 or self.price >= 1:
            raise ValueError("quote price must be strictly between 0 and 1")
        if self.queue_ahead < 0 or self.quantity <= 0:
            raise ValueError("invalid queue/quantity")

    def apply_trade(
        self,
        *,
        trade_yes_price: Decimal,
        trade_no_price: Decimal,
        trade_quantity: Decimal,
        taker_side: str,
    ) -> Decimal:
        """Fill only after strict trade-through AND conservative queue exhaustion.

        Touches at our exact price never count.  For a YES bid, only a NO taker
        (selling YES) can consume us.  For a NO bid, only a YES taker can consume
        us.  Eligible trade volume at our level-or-worse is accumulated, but a
        fill is withheld until a strict lower-price print proves trade-through.
        """
        if trade_quantity <= 0:
            return D("0")
        taker = str(taker_side or "").lower()
        if self.outcome == "YES":
            if taker != "no":
                return D("0")
            trade_price = trade_yes_price
        else:
            if taker != "yes":
                return D("0")
            trade_price = trade_no_price

        # Higher bid prices are consumed before lower ones.  A trade at or below
        # our bid contributes to conservative consumption, but equality alone is
        # never accepted as a fill.
        if trade_price > self.price:
            return D("0")
        self.cumulative_consumption += trade_quantity
        if trade_price < self.price:
            self.saw_trade_through = True

        required = self.queue_ahead + self.quantity
        if self.saw_trade_through and self.cumulative_consumption >= required:
            fill = self.quantity
            self.quantity = D("0")
            return fill
        return D("0")


def settlement_pnl(
    *,
    outcome: str,
    price: Decimal,
    quantity: Decimal,
    result_yes: bool,
    maker_fee: Decimal,
) -> Decimal:
    """Final P&L for a hypothetical passive BUY YES or BUY NO fill."""
    side = outcome.upper()
    if side not in {"YES", "NO"}:
        raise ValueError("outcome must be YES or NO")
    wins = result_yes if side == "YES" else not result_yes
    gross = quantity * (D("1") - price) if wins else -quantity * price
    return gross - maker_fee
