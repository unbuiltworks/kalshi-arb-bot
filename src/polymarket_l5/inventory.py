"""Cumulative inventory math for one trading window.

Accounting conventions (stated explicitly because they change the numbers):

* Cost basis is **average cost**. A BUY adds ``price * shares`` to the side's
  basis; a SELL removes ``wavg_price * shares``, and the difference between
  proceeds and that removed basis is booked as realized trading P&L.
* ``matched_qty = min(up_qty, down_qty)`` -- the number of Up/Down pairs held.
  A pair pays exactly $1 at resolution regardless of outcome, so the pair's
  *acquisition cost* ``wavg_up + wavg_down`` is the whole game: below $1.00 it
  is locked profit before fees/rebates, above $1.00 it is a locked loss.
* ``excess_qty = up_qty - down_qty`` is signed directional inventory: positive
  means long Up, negative means long Down.
* Nothing here models fees, maker rebates, or liquidity incentives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from . import config

EPS = config.QTY_EPS


@dataclass
class SideState:
    qty: float = 0.0
    basis: float = 0.0  # dollars of remaining cost basis
    buy_qty: float = 0.0
    buy_dollars: float = 0.0
    sell_qty: float = 0.0
    sell_dollars: float = 0.0
    max_buy_price: Optional[float] = None
    first_buy_ts: Optional[int] = None

    @property
    def wavg_price(self) -> float:
        return self.basis / self.qty if self.qty > EPS else 0.0

    @property
    def wavg_buy_price(self) -> float:
        return self.buy_dollars / self.buy_qty if self.buy_qty > EPS else 0.0


@dataclass
class InventoryState:
    up: SideState = field(default_factory=SideState)
    down: SideState = field(default_factory=SideState)
    realized_pnl_trading: float = 0.0
    trade_count: int = 0
    gross_dollars_bought: float = 0.0
    anomalies: List[str] = field(default_factory=list)

    def side(self, outcome: str) -> Optional[SideState]:
        if outcome == "Up":
            return self.up
        if outcome == "Down":
            return self.down
        return None

    @property
    def matched_qty(self) -> float:
        return min(self.up.qty, self.down.qty)

    @property
    def matched_pair_unit_cost(self) -> Optional[float]:
        """Cost of acquiring one Up+Down pair at current average prices."""
        if self.matched_qty <= EPS:
            return None
        return self.up.wavg_price + self.down.wavg_price

    @property
    def matched_pair_cost(self) -> float:
        unit = self.matched_pair_unit_cost
        return 0.0 if unit is None else unit * self.matched_qty

    @property
    def excess_qty(self) -> float:
        return self.up.qty - self.down.qty

    @property
    def dominant_side(self) -> str:
        excess = self.excess_qty
        if excess > EPS:
            return "Up"
        if excess < -EPS:
            return "Down"
        return "BALANCED"

    @property
    def excess_cost(self) -> float:
        """Cost basis attributable to the unmatched (directional) leg."""
        excess = self.excess_qty
        if abs(excess) <= EPS:
            return 0.0
        side = self.up if excess > 0 else self.down
        return abs(excess) * side.wavg_price


def apply_trade(state: InventoryState, trade: Dict[str, Any]) -> InventoryState:
    """Mutate ``state`` with one fill and return it."""
    outcome = trade.get("outcome") or ""
    side_state = state.side(outcome)
    if side_state is None:
        state.anomalies.append("unmapped_outcome")
        return state

    shares = float(trade.get("shares") or 0.0)
    price = trade.get("price")
    price = float(price) if price is not None else 0.0
    dollars = trade.get("dollars")
    dollars = float(dollars) if dollars is not None else price * shares
    action = (trade.get("side") or "").upper()
    state.trade_count += 1

    if action == "BUY":
        side_state.qty += shares
        side_state.basis += dollars
        side_state.buy_qty += shares
        side_state.buy_dollars += dollars
        state.gross_dollars_bought += dollars
        if side_state.max_buy_price is None or price > side_state.max_buy_price:
            side_state.max_buy_price = price
        if side_state.first_buy_ts is None:
            side_state.first_buy_ts = trade.get("timestamp")
    elif action == "SELL":
        sellable = min(shares, side_state.qty)
        if shares - sellable > EPS:
            state.anomalies.append("sell_exceeds_held_qty")
        wavg = side_state.wavg_price
        removed_basis = wavg * sellable
        side_state.qty -= sellable
        side_state.basis -= removed_basis
        if side_state.qty <= EPS:
            side_state.qty = 0.0
            side_state.basis = 0.0
        side_state.sell_qty += shares
        side_state.sell_dollars += dollars
        proceeds = dollars * (sellable / shares) if shares > EPS else 0.0
        state.realized_pnl_trading += proceeds - removed_basis
    else:
        state.anomalies.append(f"unknown_action:{action or 'blank'}")
    return state


def running_states(trades: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return one cumulative snapshot dict per trade, in the given order."""
    state = InventoryState()
    rows: List[Dict[str, Any]] = []
    previous_ts: Optional[int] = None
    for index, trade in enumerate(trades, start=1):
        apply_trade(state, trade)
        timestamp = trade.get("timestamp")
        rows.append(
            {
                "trade_seq": index,
                "seconds_since_prev_trade": (
                    timestamp - previous_ts if (timestamp and previous_ts) else None
                ),
                "cum_up_qty": _r(state.up.qty),
                "cum_up_spend": _r(state.up.basis),
                "cum_down_qty": _r(state.down.qty),
                "cum_down_spend": _r(state.down.basis),
                "wavg_up_price": _r(state.up.wavg_price, 6),
                "wavg_down_price": _r(state.down.wavg_price, 6),
                "matched_qty": _r(state.matched_qty),
                "matched_pair_unit_cost": _r(state.matched_pair_unit_cost, 6),
                "matched_pair_cost": _r(state.matched_pair_cost),
                "excess_qty": _r(state.excess_qty),
                "excess_abs_qty": _r(abs(state.excess_qty)),
                "excess_cost": _r(state.excess_cost),
                "dominant_side": state.dominant_side,
                "cum_gross_dollars_bought": _r(state.gross_dollars_bought),
                "cum_realized_pnl_trading": _r(state.realized_pnl_trading),
                "cum_trade_count": state.trade_count,
                "anomalies": ";".join(sorted(set(state.anomalies))),
            }
        )
        if timestamp:
            previous_ts = timestamp
    return rows


def final_state(trades: Iterable[Dict[str, Any]]) -> InventoryState:
    state = InventoryState()
    for trade in trades:
        apply_trade(state, trade)
    return state


def state_as_dict(state: InventoryState) -> Dict[str, Any]:
    payload = asdict(state)
    payload.update(
        {
            "matched_qty": _r(state.matched_qty),
            "matched_pair_unit_cost": _r(state.matched_pair_unit_cost, 6),
            "matched_pair_cost": _r(state.matched_pair_cost),
            "excess_qty": _r(state.excess_qty),
            "dominant_side": state.dominant_side,
            "wavg_up_price": _r(state.up.wavg_price, 6),
            "wavg_down_price": _r(state.down.wavg_price, 6),
        }
    )
    return payload


def _r(value: Optional[float], places: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), places)
