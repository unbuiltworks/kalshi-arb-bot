"""Safety-first Gemini paired-maker decision core.

The engine makes liquidity. Volume only changes maker aggressiveness; it never
switches the normal strategy to taker. Taker exists only as bounded rescue.

Promotion invariant: incentives are not allowed to justify a structurally
losing pair. While inventory is imbalanced, the engine quotes only the missing
side and prices it from the *whole portfolio* so a complete maker fill would
finish at an average pair cost <= $1.00. This avoids the false safety that can
come from looking only at the held side's average fill price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

WARM_FLOW_60S = 10.0
HOT_FLOW_60S = 100.0

SUBSIDY_MIN_SIZE = 10
SUBSIDY_BACKOFF = 0.04
ACTIVE_SIZE = 5
WARM_JOIN_BACKOFF = 0.01
HOT_JOIN_BACKOFF = 0.0

PAIR_CEILING = 1.00

STOP_QUOTING_BEFORE_EXPIRY_S = 30.0
RESCUE_ZONE_BEFORE_EXPIRY_S = 60.0
RESCUE_MAX_PREMIUM = 0.03
MAX_SPEND_PER_WINDOW = 25.0
MAX_WORST_LOSS_PER_WINDOW = 5.0
MIN_PRICE = 0.05
MAX_PRICE = 0.95


@dataclass
class PairInventory:
    yes_qty: float = 0.0
    yes_cost: float = 0.0
    no_qty: float = 0.0
    no_cost: float = 0.0

    def apply_fill(self, side: str, qty: float, price: float) -> None:
        if qty <= 0:
            return
        if side == "yes":
            self.yes_qty += qty
            self.yes_cost += qty * price
        elif side == "no":
            self.no_qty += qty
            self.no_cost += qty * price
        else:
            raise ValueError(f"unknown side: {side}")

    def clone(self) -> "PairInventory":
        return PairInventory(self.yes_qty, self.yes_cost, self.no_qty, self.no_cost)

    @property
    def spend(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def residual(self) -> float:
        return self.yes_qty - self.no_qty

    def worst_settlement_loss(self) -> float:
        yes_wins = self.yes_qty - self.spend
        no_wins = self.no_qty - self.spend
        return max(0.0, -min(yes_wins, no_wins))

    def pairs_completed(self) -> float:
        return min(self.yes_qty, self.no_qty)

    def avg_pair_cost(self) -> Optional[float]:
        """Exact average pair cost only when flat; diagnostic otherwise."""
        if self.yes_qty < 1e-9 or self.no_qty < 1e-9:
            return None
        if abs(self.residual) < 1e-9:
            return self.spend / self.yes_qty
        return self.yes_cost / self.yes_qty + self.no_cost / self.no_qty


@dataclass(frozen=True)
class PairBook:
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]


@dataclass(frozen=True)
class Quote:
    side: str
    price: float
    size: float
    reason: str


@dataclass(frozen=True)
class EngineDecision:
    state: str
    quotes: List[Quote]
    rescue: Optional[Quote]
    cancel_all: bool
    reason: str


def _clamp(price: float) -> Optional[float]:
    if price < MIN_PRICE - 1e-9 or price > MAX_PRICE + 1e-9:
        return None
    return round(price, 2)


def _flow_state(flow_60s: float) -> str:
    if flow_60s >= HOT_FLOW_60S:
        return "HOT"
    if flow_60s >= WARM_FLOW_60S:
        return "WARM"
    return "DEAD"


def _full_completion_ceiling(inv: PairInventory) -> Optional[float]:
    """Max missing-side price that would make the *whole portfolio* flat at
    <= PAIR_CEILING average cost if the entire residual fills now.

    This is portfolio-safe. Example: an old 100c pair plus a new YES fill at
    90c cannot use 1-YES-average as a fake 35c ceiling; the whole-book math
    correctly leaves only 10c for the missing NO.
    """
    qty = abs(inv.residual)
    if qty < 1e-9:
        return None
    final_pairs = max(inv.yes_qty, inv.no_qty)
    budget_left = final_pairs * PAIR_CEILING - inv.spend
    return budget_left / qty


def _safe_size(
    inv: PairInventory,
    side: str,
    price: float,
    requested: float,
    max_spend: float,
    max_worst_loss: float,
) -> float:
    size = float(int(max(0.0, requested)))
    while size >= 1.0:
        probe = inv.clone()
        probe.apply_fill(side, size, price)
        if probe.spend <= max_spend + 1e-9 and probe.worst_settlement_loss() <= max_worst_loss + 1e-9:
            return size
        size -= 1.0
    return 0.0


def _subsidy_quotes(
    inv: PairInventory,
    book: PairBook,
    max_spend: float,
    max_worst_loss: float,
) -> List[Quote]:
    if abs(inv.residual) >= 1e-9:
        return []
    quotes: List[Quote] = []
    prices = {}
    for side, bid in (("yes", book.yes_bid), ("no", book.no_bid)):
        if bid is None:
            return []
        price = _clamp(bid - SUBSIDY_BACKOFF)
        if price is None:
            return []
        prices[side] = price
        if _safe_size(inv, side, price, SUBSIDY_MIN_SIZE, max_spend, max_worst_loss) < SUBSIDY_MIN_SIZE:
            return []
        quotes.append(Quote(side, price, float(SUBSIDY_MIN_SIZE), "SUBSIDY_REST"))
    if prices["yes"] + prices["no"] > PAIR_CEILING + 1e-9:
        return []
    return quotes


def _inventory_completion_quote(
    inv: PairInventory,
    book: PairBook,
    state: str,
    max_spend: float,
    max_worst_loss: float,
) -> Optional[Quote]:
    residual = inv.residual
    if abs(residual) < 1.0:
        return None
    missing = "no" if residual > 0 else "yes"
    bid = book.no_bid if missing == "no" else book.yes_bid
    ask = book.no_ask if missing == "no" else book.yes_ask
    if bid is None:
        return None
    ceiling = _full_completion_ceiling(inv)
    if ceiling is None or ceiling < MIN_PRICE - 1e-9:
        return None
    maker_cap = (ask - 0.01) if ask is not None else ceiling
    price = min(ceiling, maker_cap)
    price = _clamp(price)
    if price is None or price > ceiling + 1e-9:
        return None
    requested = abs(residual)
    size = _safe_size(inv, missing, price, requested, max_spend, max_worst_loss)
    if size < 1.0:
        return None
    if size + 1e-9 < float(int(requested)):
        return None
    return Quote(missing, price, size, "LEAN_COMPLETE_PORTFOLIO")


def decide(
    inv: PairInventory,
    book: PairBook,
    flow_60s: float,
    seconds_to_expiry: Optional[float],
    *,
    in_reward_pool: bool = False,
    halted: bool = False,
    max_spend: float = MAX_SPEND_PER_WINDOW,
    max_worst_loss: float = MAX_WORST_LOSS_PER_WINDOW,
) -> EngineDecision:
    if halted:
        return EngineDecision("HALTED", [], None, True, "HALTED_BY_WRAPPER")
    if seconds_to_expiry is None:
        return EngineDecision("HALTED", [], None, True, "NO_CLOCK")

    rescue = _rescue_intent(inv, book, seconds_to_expiry)

    if seconds_to_expiry <= STOP_QUOTING_BEFORE_EXPIRY_S:
        return EngineDecision("EXPIRY_STOP", [], rescue, True, "EXPIRY_STOP")
    if inv.spend >= max_spend - 1e-9:
        return EngineDecision("CAPPED", [], rescue, True, "SPEND_CAP")

    flow_state = _flow_state(flow_60s)

    if abs(inv.residual) >= 1.0:
        q = _inventory_completion_quote(inv, book, flow_state, max_spend, max_worst_loss)
        state = "INVENTORY_MANAGE" if flow_state == "DEAD" else flow_state
        if q is None:
            return EngineDecision(state, [], rescue, True, "NO_SAFE_COMPLETION_QUOTE")
        return EngineDecision(state, [q], rescue, False, "MISSING_SIDE_ONLY")

    if flow_state == "DEAD":
        if not in_reward_pool:
            return EngineDecision("IDLE", [], rescue, True, "DEAD_NO_POOL")
        quotes = _subsidy_quotes(inv, book, max_spend, max_worst_loss)
        if len(quotes) != 2:
            return EngineDecision("IDLE", [], rescue, True, "SUBSIDY_UNSAFE_OR_UNQUALIFIED")
        return EngineDecision("SUBSIDY", quotes, rescue, False, "SUBSIDY_TWO_SIDED")

    backoff = WARM_JOIN_BACKOFF if flow_state == "WARM" else HOT_JOIN_BACKOFF
    quotes: List[Quote] = []
    for side, bid in (("yes", book.yes_bid), ("no", book.no_bid)):
        if bid is None:
            continue
        price = _clamp(bid - backoff)
        if price is None:
            continue
        size = _safe_size(inv, side, price, ACTIVE_SIZE, max_spend, max_worst_loss)
        if size >= 1.0:
            quotes.append(Quote(side, price, size, f"{flow_state}_JOIN"))
    if not quotes:
        return EngineDecision(flow_state, [], rescue, True, "NO_QUOTABLE_SIDE")
    return EngineDecision(flow_state, quotes, rescue, False, f"{flow_state}_QUOTING")


def _rescue_intent(inv: PairInventory, book: PairBook, seconds_to_expiry: float) -> Optional[Quote]:
    if seconds_to_expiry > RESCUE_ZONE_BEFORE_EXPIRY_S or seconds_to_expiry <= 0:
        return None
    residual = inv.residual
    if abs(residual) < 1.0:
        return None
    missing = "no" if residual > 0 else "yes"
    ask = book.no_ask if missing == "no" else book.yes_ask
    if ask is None:
        return None
    qty = float(int(abs(residual)))
    if qty < 1.0:
        return None
    ceiling = _full_completion_ceiling(inv)
    if ceiling is None or ask > ceiling + RESCUE_MAX_PREMIUM + 1e-9:
        return None
    probe = inv.clone()
    probe.apply_fill(missing, qty, ask)
    if probe.worst_settlement_loss() >= inv.worst_settlement_loss() - 1e-9:
        return None
    return Quote(missing, ask, qty, "RESCUE_TAKER")
