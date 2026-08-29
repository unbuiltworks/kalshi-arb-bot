"""Running two-sided inventory for one market window.

Cost accounting is FIFO-free weighted average: a BUY raises the side's cost
basis, a SELL removes shares at the basis prevailing *before* that sell and
books the cash separately. That keeps `avg_price` meaning "what the shares
still held cost", which is what a matched-pair acquisition cost needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

EPS = 1e-9

UP = "UP"
DOWN = "DOWN"
BALANCED = "BALANCED"


@dataclass
class SideState:
    """One outcome's running position."""

    qty: float = 0.0             # net shares held
    cost: float = 0.0            # cost basis of the shares still held
    buy_qty: float = 0.0         # gross shares bought
    buy_spend: float = 0.0       # gross dollars spent buying
    sell_qty: float = 0.0        # gross shares sold
    sell_proceeds: float = 0.0   # gross dollars received selling
    oversold: bool = False       # a SELL exceeded the held quantity

    @property
    def avg_price(self) -> float | None:
        """Weighted average cost of shares currently held."""
        if self.qty > EPS:
            return self.cost / self.qty
        return None

    @property
    def gross_avg_buy_price(self) -> float | None:
        """Weighted average of every buy, ignoring later sells."""
        if self.buy_qty > EPS:
            return self.buy_spend / self.buy_qty
        return None

    def buy(self, shares: float, price: float, dollars: float) -> None:
        self.qty += shares
        self.cost += dollars
        self.buy_qty += shares
        self.buy_spend += dollars

    def sell(self, shares: float, price: float, dollars: float) -> None:
        basis = self.avg_price
        closing = min(shares, max(self.qty, 0.0))
        if basis is not None and closing > 0:
            self.cost -= closing * basis
        if shares > self.qty + EPS:
            # Net short. Public data alone cannot distinguish this from a
            # fill we failed to see, so it is flagged rather than smoothed.
            self.oversold = True
            self.cost = 0.0
        self.qty -= shares
        if self.qty <= EPS and not self.oversold:
            self.cost = 0.0
        self.sell_qty += shares
        self.sell_proceeds += dollars


@dataclass
class Snapshot:
    """Cumulative state immediately after one trade."""

    trade_seq: int
    up_qty: float
    up_spend: float
    up_avg_price: float | None
    down_qty: float
    down_spend: float
    down_avg_price: float | None
    up_buy_qty: float
    up_buy_spend: float
    down_buy_qty: float
    down_buy_spend: float
    up_sell_qty: float
    down_sell_qty: float
    matched_qty: float
    matched_pair_cost_per_share: float | None
    matched_pair_cost_total: float | None
    excess_qty: float            # signed: positive = long UP
    excess_abs: float
    excess_side: str             # UP | DOWN | BALANCED
    dominant_side: str           # UP | DOWN | BALANCED
    cum_trades: int
    cum_dollars_bought: float
    cum_dollars_sold: float
    cum_net_cash_out: float      # bought - sold
    oversold: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Inventory:
    up: SideState = field(default_factory=SideState)
    down: SideState = field(default_factory=SideState)
    trades: int = 0

    def side(self, outcome: str) -> SideState:
        if outcome == UP:
            return self.up
        if outcome == DOWN:
            return self.down
        raise ValueError(f"unknown outcome {outcome!r}")

    def apply(self, outcome: str, action: str, shares: float, price: float, dollars: float) -> Snapshot:
        state = self.side(outcome)
        if action == "BUY":
            state.buy(shares, price, dollars)
        elif action == "SELL":
            state.sell(shares, price, dollars)
        else:
            raise ValueError(f"unknown side {action!r}")
        self.trades += 1
        return self.snapshot()

    def snapshot(self) -> Snapshot:
        up_avg = self.up.avg_price
        down_avg = self.down.avg_price
        matched = min(max(self.up.qty, 0.0), max(self.down.qty, 0.0))

        if up_avg is not None and down_avg is not None:
            per_share: float | None = up_avg + down_avg
            total: float | None = matched * per_share
        else:
            per_share = None
            total = 0.0 if matched <= EPS else None

        excess = self.up.qty - self.down.qty
        if excess > EPS:
            excess_side = UP
        elif excess < -EPS:
            excess_side = DOWN
        else:
            excess_side = BALANCED

        bought = self.up.buy_spend + self.down.buy_spend
        sold = self.up.sell_proceeds + self.down.sell_proceeds

        return Snapshot(
            trade_seq=self.trades,
            up_qty=self.up.qty,
            up_spend=self.up.cost,
            up_avg_price=up_avg,
            down_qty=self.down.qty,
            down_spend=self.down.cost,
            down_avg_price=down_avg,
            up_buy_qty=self.up.buy_qty,
            up_buy_spend=self.up.buy_spend,
            down_buy_qty=self.down.buy_qty,
            down_buy_spend=self.down.buy_spend,
            up_sell_qty=self.up.sell_qty,
            down_sell_qty=self.down.sell_qty,
            matched_qty=matched,
            matched_pair_cost_per_share=per_share,
            matched_pair_cost_total=total,
            excess_qty=excess,
            excess_abs=abs(excess),
            excess_side=excess_side,
            dominant_side=excess_side,
            cum_trades=self.trades,
            cum_dollars_bought=bought,
            cum_dollars_sold=sold,
            cum_net_cash_out=bought - sold,
            oversold=self.up.oversold or self.down.oversold,
        )
