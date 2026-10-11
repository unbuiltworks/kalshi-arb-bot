"""The quoting decision core: tape + book + inventory + clock -> desired action.

Encodes exactly what the study validated, nothing more:

- FROZEN selection rule (policy D, 2026-08-30): rest bids only on the side the
  rolling 30-print tape is NOT accumulating, at price >= $0.30.
- Operating envelope (measured from the L5 15m machine): quote from window
  open, tolerate temporary inventory, allow bounded defensive completion late,
  stop quoting STOP_QUOTING_BEFORE_EXPIRY_S before expiry.
- Risk bounds follow the ARB contract: per-window spend cap, per-window
  worst-settlement-loss cap, quote size cap, and a hard no-quote state the
  wrapper can force at any time. Incentives never appear here.

Pure functions + a small state object; no I/O, no venue calls, fully testable.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

# --- FROZEN policy constants (do not tune without a new frozen validation) ---
TAPE_WINDOW = 30
TAPE_LIGHT_THRESHOLD = -0.1
PRICE_FLOOR = 0.30

# --- Operating envelope (measured; conservative defaults) --------------------
STOP_QUOTING_BEFORE_EXPIRY_S = 90.0
COMPLETION_ZONE_BEFORE_EXPIRY_S = 240.0   # bounded defensive completion window
MAX_COMPLETION_PRICE = 0.80

# --- Risk bounds (wrapper may only tighten, never loosen) --------------------
DEFAULT_QUOTE_SIZE = 5
MAX_SPEND_PER_WINDOW = 10.0        # dollars
MAX_WORST_LOSS_PER_WINDOW = 1.0    # dollars, worst-settlement of net inventory


@dataclass
class Tape:
    """Rolling share-weighted direction of observed prints (venue-agnostic)."""

    rows: Deque[Tuple[str, float]] = field(default_factory=lambda: deque(maxlen=TAPE_WINDOW))

    def add_print(self, taker_bought: str, qty: float) -> None:
        if taker_bought in ("Up", "Down") and qty > 0:
            self.rows.append((taker_bought, qty))

    def imbalance_toward(self, side: str) -> float:
        total = sum(q for _, q in self.rows)
        if total <= 1e-12:
            return 0.0
        up = sum(q for s, q in self.rows if s == "Up")
        signed = (2.0 * up - total) / total
        return signed if side == "Up" else -signed

    @property
    def prints_seen(self) -> int:
        return len(self.rows)


@dataclass
class Inventory:
    up_qty: float = 0.0
    up_cost: float = 0.0
    down_qty: float = 0.0
    down_cost: float = 0.0

    def apply_fill(self, side: str, qty: float, price: float) -> None:
        if side == "Up":
            self.up_qty += qty
            self.up_cost += qty * price
        elif side == "Down":
            self.down_qty += qty
            self.down_cost += qty * price

    @property
    def spend(self) -> float:
        return self.up_cost + self.down_cost

    @property
    def residual_qty(self) -> float:
        return self.up_qty - self.down_qty

    def worst_settlement_loss(self) -> float:
        """Loss if the worse side wins, given current holdings (fees excluded)."""
        up_wins = self.up_qty * 1.0 - self.spend
        down_wins = self.down_qty * 1.0 - self.spend
        return max(0.0, -min(up_wins, down_wins))

    def worst_loss_after(self, side: str, qty: float, price: float) -> float:
        probe = Inventory(self.up_qty, self.up_cost, self.down_qty, self.down_cost)
        probe.apply_fill(side, qty, price)
        return probe.worst_settlement_loss()


@dataclass(frozen=True)
class Book:
    """Top of book in Up-price terms: joinable bid per side."""

    up_bid: Optional[float]
    down_bid: Optional[float]   # in the Down side's own price scale


@dataclass(frozen=True)
class QuoteIntent:
    side: str
    price: float
    size: float
    reason: str


@dataclass(frozen=True)
class Decision:
    quotes: List[QuoteIntent]
    cancel_all: bool
    reason: str


def decide(
    tape: Tape,
    book: Book,
    inventory: Inventory,
    seconds_to_expiry: Optional[float],
    *,
    halted: bool = False,
    quote_size: float = DEFAULT_QUOTE_SIZE,
    max_spend: float = MAX_SPEND_PER_WINDOW,
    max_worst_loss: float = MAX_WORST_LOSS_PER_WINDOW,
) -> Decision:
    """One evaluation of the quoting rule. Deterministic; call on every update."""
    if halted:
        return Decision([], True, "HALTED_BY_WRAPPER")
    if seconds_to_expiry is None:
        return Decision([], True, "NO_CLOCK")
    if seconds_to_expiry <= STOP_QUOTING_BEFORE_EXPIRY_S:
        return Decision([], True, "EXPIRY_STOP")
    if inventory.spend >= max_spend:
        return Decision([], True, "SPEND_CAP")
    if tape.prints_seen < TAPE_WINDOW:
        return Decision([], True, "TAPE_WARMUP")

    quotes: List[QuoteIntent] = []
    for side, bid in (("Up", book.up_bid), ("Down", book.down_bid)):
        if bid is None:
            continue
        # FROZEN selection: only the side flow is NOT accumulating, at >= floor.
        if tape.imbalance_toward(side) >= TAPE_LIGHT_THRESHOLD:
            continue
        if bid < PRICE_FLOOR:
            continue
        size = min(quote_size, max(0.0, (max_spend - inventory.spend) / max(bid, 0.01)))
        if size < 1.0:
            continue
        if inventory.worst_loss_after(side, size, bid) > max_worst_loss:
            # shrink to the largest size that respects the worst-loss bound
            lo, hi = 0.0, size
            for _ in range(20):
                mid = (lo + hi) / 2
                if inventory.worst_loss_after(side, mid, bid) > max_worst_loss:
                    hi = mid
                else:
                    lo = mid
            size = float(int(lo))
            if size < 1.0:
                continue
        quotes.append(QuoteIntent(side, bid, size, "TAPE_CONTRA_JOIN"))
    if not quotes:
        return Decision([], True, "NO_ELIGIBLE_SIDE")
    return Decision(quotes, False, "QUOTING")


def completion_action(
    inventory: Inventory,
    book: Book,
    seconds_to_expiry: Optional[float],
    *,
    max_worst_loss: float = MAX_WORST_LOSS_PER_WINDOW,
) -> Optional[QuoteIntent]:
    """Bounded defensive completion: reduce residual late in the window.

    Mirrors the measured L5 envelope (escalating late completion) under the
    hard rule that completing must REDUCE worst-settlement loss and never pay
    above MAX_COMPLETION_PRICE. Returns a taker-style intent or None.
    """
    if seconds_to_expiry is None or seconds_to_expiry > COMPLETION_ZONE_BEFORE_EXPIRY_S:
        return None
    if seconds_to_expiry <= 0:
        return None
    residual = inventory.residual_qty
    if abs(residual) < 1.0:
        return None
    missing = "Down" if residual > 0 else "Up"
    # Cost of the missing side ~ complement of the held side's bid.
    price = None
    if missing == "Down" and book.up_bid is not None:
        price = round(1.0 - book.up_bid, 4)
    elif missing == "Up" and book.down_bid is not None:
        price = round(1.0 - book.down_bid, 4)
    if price is None or price > MAX_COMPLETION_PRICE:
        return None
    qty = float(int(abs(residual)))
    before = inventory.worst_settlement_loss()
    after = inventory.worst_loss_after(missing, qty, price)
    if after >= before or after > max_worst_loss:
        return None
    return QuoteIntent(missing, price, qty, "DEFENSIVE_COMPLETION")
