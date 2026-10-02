"""Favorite-inventory lane: accounting + execution primitives.

The Step-0 arithmetic findings are ENFORCED HERE, in the path the replay
actually uses -- not merely asserted in a separate test file. If a caller
constructs a matched pair whose cost violates the identity, `Book.pair_cost`
and `Inventory.assert_identities` raise.

VERIFIED VENUE IDENTITY (live data, every sample): buy.no + sell.yes = 1.00
  acquire UP   (taker) = pay ask        acquire UP   (maker) = pay bid
  acquire DOWN (taker) = pay (1 - bid)  acquire DOWN (maker) = pay (1 - ask)
Hence, before fees/rebates:
  taker+taker pair cost = ask + (1-bid) = 1 + spread
  maker+maker pair cost = bid + (1-ask) = 1 - spread
  mixed            pair cost                       = 1 exactly

FEES / REBATE, authoritative from our own credited-account evidence:
  maker fee  ceil(0.0175 * C * P * (1-P))   per fill, to the cent
  taker fee  ceil(0.07   * C * P * (1-P))   per fill, to the cent
  rebate     floor(0.021 * C * P * (1-P))   ONLY if 0.20 <= P <= 0.80
All in Decimal. A float implementation with floor-to-cent breaks the
P <-> 1-P symmetry of the rebate (measured: $4.40 vs $4.41 at C=1000).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from typing import List, Optional

ONE = D("1")
CENT = D("0.01")
REBATE_LO, REBATE_HI = D("0.20"), D("0.80")


def _q(x) -> D:
    return x if isinstance(x, D) else D(str(x))


def maker_fee(C, P) -> D:
    C, P = _q(C), _q(P)
    return (D("0.0175") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_CEILING) * CENT


def taker_fee(C, P) -> D:
    C, P = _q(C), _q(P)
    return (D("0.07") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_CEILING) * CENT


def rebate(C, P) -> D:
    """Maker rebate: floor to the cent, and ZERO outside the 0.20-0.80 band."""
    C, P = _q(C), _q(P)
    if P < REBATE_LO or P > REBATE_HI:
        return D("0")
    return (D("0.021") * C * P * (ONE - P) / CENT).to_integral_value(
        rounding=ROUND_FLOOR) * CENT


@dataclass(frozen=True)
class Book:
    """Executable top of book in UP terms."""
    bid: Optional[D]
    bid_sz: Optional[D]
    ask: Optional[D]
    ask_sz: Optional[D]

    @property
    def two_sided(self) -> bool:
        return self.bid is not None and self.ask is not None

    @property
    def spread(self) -> Optional[D]:
        return (self.ask - self.bid) if self.two_sided else None

    @property
    def mid(self) -> Optional[D]:
        return ((self.ask + self.bid) / 2) if self.two_sided else None

    # ---- executable acquisition prices -------------------------------
    def price_for(self, side: str, mode: str) -> Optional[D]:
        """side in {UP, DOWN}; mode in {taker, maker}. None if unavailable."""
        if side == "UP":
            return self.ask if mode == "taker" else self.bid
        if side == "DOWN":
            if mode == "taker":
                return (ONE - self.bid) if self.bid is not None else None
            return (ONE - self.ask) if self.ask is not None else None
        raise ValueError(side)

    def depth_for(self, side: str, mode: str) -> Optional[D]:
        """Displayed size we could execute against (taker) or join (maker)."""
        if side == "UP":
            return self.ask_sz if mode == "taker" else self.bid_sz
        if side == "DOWN":
            return self.bid_sz if mode == "taker" else self.ask_sz
        raise ValueError(side)

    def pair_cost(self, mode_up: str, mode_down: str) -> D:
        """Cost of one matched contract, and the identity is ENFORCED."""
        if not self.two_sided:
            raise ValueError("pair_cost needs a two-sided book")
        pu = self.price_for("UP", mode_up)
        pd = self.price_for("DOWN", mode_down)
        cost = pu + pd
        s = self.spread
        if mode_up == "taker" and mode_down == "taker":
            expect = ONE + s
        elif mode_up == "maker" and mode_down == "maker":
            expect = ONE - s
        else:
            expect = ONE
        if abs(cost - expect) > D("1e-9"):
            raise AssertionError(
                f"PAIR-COST IDENTITY VIOLATED: {mode_up}/{mode_down} "
                f"cost={cost} expected={expect} (bid={self.bid} ask={self.ask})")
        return cost


@dataclass
class Fill:
    t: float
    side: str            # UP | DOWN
    mode: str            # taker | maker
    price: D
    qty: D
    basis: str           # through_price | same_price_fifo | taker_depth | ...
    fee: D
    rebate: D


@dataclass
class Inventory:
    """Continuous inventory. Nothing is netted into a single number."""
    q_up: D = D("0")
    q_down: D = D("0")
    c_up: D = D("0")
    c_down: D = D("0")
    fees: D = D("0")
    rebates: D = D("0")
    fills: List[Fill] = field(default_factory=list)

    # ---- derived ------------------------------------------------------
    @property
    def matched(self) -> D:
        return min(self.q_up, self.q_down)

    @property
    def residual(self) -> D:
        return abs(self.q_up - self.q_down)

    @property
    def cash(self) -> D:
        return self.c_up + self.c_down

    @property
    def avg_up(self) -> Optional[D]:
        return (self.c_up / self.q_up) if self.q_up else None

    @property
    def avg_down(self) -> Optional[D]:
        return (self.c_down / self.q_down) if self.q_down else None

    def pnl_if(self, outcome: str) -> D:
        """Settlement P&L BEFORE fees/rebates. UP wins -> each UP pays 1."""
        if outcome == "UP":
            return self.q_up - self.cash
        if outcome == "DOWN":
            return self.q_down - self.cash
        raise ValueError(outcome)

    def net_if(self, outcome: str) -> D:
        return self.pnl_if(outcome) - self.fees + self.rebates

    def matched_pair_cost(self) -> Optional[D]:
        """Effective cost of the matched book: avg_up + avg_down."""
        if not (self.q_up and self.q_down):
            return None
        return self.avg_up + self.avg_down

    def matched_pnl(self) -> D:
        """Guaranteed component: matched qty pays exactly 1 whichever way."""
        m = self.matched
        pc = self.matched_pair_cost()
        return (m * (ONE - pc)) if (m and pc is not None) else D("0")

    def residual_pnl_if(self, outcome: str) -> D:
        """Directional component ONLY -- never folded into matched."""
        return self.pnl_if(outcome) - self.matched_pnl()

    # ---- mutation -----------------------------------------------------
    def add(self, f: Fill) -> None:
        if f.qty <= 0:
            raise ValueError("non-positive fill qty")
        if f.side == "UP":
            self.q_up += f.qty
            self.c_up += f.qty * f.price
        elif f.side == "DOWN":
            self.q_down += f.qty
            self.c_down += f.qty * f.price
        else:
            raise ValueError(f.side)
        self.fees += f.fee
        self.rebates += f.rebate
        self.fills.append(f)

    def assert_identities(self) -> None:
        """Invariants that must hold after every mutation."""
        if self.matched != min(self.q_up, self.q_down):
            raise AssertionError("matched != min(q_up, q_down)")
        if self.residual != abs(self.q_up - self.q_down):
            raise AssertionError("residual != |q_up - q_down|")
        if self.cash != self.c_up + self.c_down:
            raise AssertionError("cash != c_up + c_down")
        # matched + residual decomposition must reconstruct total P&L exactly
        for oc in ("UP", "DOWN"):
            lhs = self.pnl_if(oc)
            rhs = self.matched_pnl() + self.residual_pnl_if(oc)
            if abs(lhs - rhs) > D("1e-9"):
                raise AssertionError(
                    f"P&L decomposition broken for {oc}: {lhs} != {rhs}")


def make_fill(t, side, mode, price, qty, basis) -> Fill:
    """Fees/rebates applied at construction so they cannot be forgotten.

    Rebate is credited ONLY on maker fills, and the eligibility band is keyed
    on the UP-equivalent probability (the venue bands on contract price).
    """
    price, qty = _q(price), _q(qty)
    p_for_fee = price if side == "UP" else (ONE - price)
    if mode == "maker":
        fee = maker_fee(qty, p_for_fee)
        reb = rebate(qty, p_for_fee)
    elif mode == "taker":
        fee = taker_fee(qty, p_for_fee)
        reb = D("0")
    else:
        raise ValueError(mode)
    return Fill(t=t, side=side, mode=mode, price=price, qty=qty,
                basis=basis, fee=fee, rebate=reb)
