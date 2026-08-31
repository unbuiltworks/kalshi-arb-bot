"""Gemini paired-maker engine: the user's four-state game plan as a pure core.

States (per 5m/15m window):
  SUBSIDY      dead flow + market in an active Liquidity Rewards pool:
               rest wide two-sided quotes (>= SUBSIDY_MIN_SIZE) for uptime.
  IDLE         dead flow, no reward pool: quote nothing. No reason to sit in
               a dead window without subsidy.
  ACTIVE_MAKER flow arriving: tighten toward fair, let takers hit us.
  (lean)       imbalanced inventory: the missing side's bid leans up toward
               the pair-cost ceiling; the held side's bid backs off.

Hard rules (v1):
  - Maker-only quoting. post-only semantics assumed by the wrapper.
  - PAIR CEILING: never bid the missing side above
    (100c - avg cost of held side): every completed pair costs <= $1.00.
    <100c is the target; =100c is acceptable (settlement breakeven, incentives
    on top); >100c is forbidden - no dependence on uncredited rewards.
  - Taker is EMERGENCY ONLY: allowed when it reduces worst-settlement loss and
    the crossing premium is under RESCUE_MAX_PREMIUM per contract, in the
    rescue zone only. Never to farm volume.
  - Incentives NEVER enter quoting decisions or P&L here. The wrapper tracks
    credited rebates/rewards separately (incentive_pending -> realized only
    when credited).

Objective this engine is scored on (shadow + live alike):
  real net = paired trading P&L + ACTUAL credited rebates/rewards
             - rescue losses - taker fees/slippage
Volume, fills, and theoretical rebates are diagnostics, not objectives.

All constants are v1 PROPOSALS to be validated on hot-window shadow tape,
then frozen before any live test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# --- Flow regime thresholds (trailing-60s traded dollars in this window) ----
WARM_FLOW_60S = 10.0      # any real flow
HOT_FLOW_60S = 100.0      # burst underway

# --- Quoting ----------------------------------------------------------------
SUBSIDY_MIN_SIZE = 10     # contracts per side in subsidy mode
SUBSIDY_BACKOFF = 0.04    # rest 4c behind best bid: qualify, don't get picked off
ACTIVE_SIZE = 5           # contracts per quote in active mode
WARM_JOIN_BACKOFF = 0.01  # warm: 1c behind best bid
HOT_JOIN_BACKOFF = 0.0    # hot: join best bid

# --- Pair construction ------------------------------------------------------
PAIR_CEILING = 1.00       # hard: held avg cost + missing-side bid <= this
LEAN_BACKOFF_HELD = 0.03  # holding inventory: held side's bid drops 3c

# --- Envelope / risk --------------------------------------------------------
STOP_QUOTING_BEFORE_EXPIRY_S = 30.0
RESCUE_ZONE_BEFORE_EXPIRY_S = 60.0
RESCUE_MAX_PREMIUM = 0.03      # cross only within 3c of the pair ceiling
MAX_SPEND_PER_WINDOW = 25.0    # dollars
MAX_WORST_LOSS_PER_WINDOW = 5.0
MIN_PRICE = 0.05               # never quote either side below/above 5c/95c
MAX_PRICE = 0.95


@dataclass
class PairInventory:
    yes_qty: float = 0.0
    yes_cost: float = 0.0
    no_qty: float = 0.0
    no_cost: float = 0.0

    def apply_fill(self, side: str, qty: float, price: float) -> None:
        if side == "yes":
            self.yes_qty += qty
            self.yes_cost += qty * price
        elif side == "no":
            self.no_qty += qty
            self.no_cost += qty * price

    @property
    def spend(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def residual(self) -> float:
        """+ = long yes unpaired, - = long no unpaired."""
        return self.yes_qty - self.no_qty

    def held_avg_cost(self) -> Optional[float]:
        """Average cost of the residual (unpaired) side, None if flat."""
        if self.residual > 1e-9 and self.yes_qty > 0:
            return self.yes_cost / self.yes_qty
        if self.residual < -1e-9 and self.no_qty > 0:
            return self.no_cost / self.no_qty
        return None

    def worst_settlement_loss(self) -> float:
        yes_wins = self.yes_qty - self.spend
        no_wins = self.no_qty - self.spend
        return max(0.0, -min(yes_wins, no_wins))

    def pairs_completed(self) -> float:
        return min(self.yes_qty, self.no_qty)

    def avg_pair_cost(self) -> Optional[float]:
        """Average combined cost per completed pair (yes avg + no avg)."""
        if self.yes_qty < 1e-9 or self.no_qty < 1e-9:
            return None
        return self.yes_cost / self.yes_qty + self.no_cost / self.no_qty


@dataclass(frozen=True)
class PairBook:
    """Top of book in each side's own price scale (yes price + no price ~ 1)."""
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]


@dataclass(frozen=True)
class Quote:
    side: str      # "yes" | "no"
    price: float
    size: float
    reason: str


@dataclass(frozen=True)
class EngineDecision:
    state: str     # IDLE | SUBSIDY | WARM | HOT | EXPIRY_STOP | HALTED | CAPPED
    quotes: List[Quote]
    rescue: Optional[Quote]   # taker intent, emergency only
    cancel_all: bool
    reason: str


def _clamp(price: float) -> Optional[float]:
    if price < MIN_PRICE or price > MAX_PRICE:
        return None
    return round(price, 2)


def _flow_state(flow_60s: float) -> str:
    if flow_60s >= HOT_FLOW_60S:
        return "HOT"
    if flow_60s >= WARM_FLOW_60S:
        return "WARM"
    return "DEAD"


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
    """One evaluation of the paired-maker rule. Deterministic, no I/O."""
    if halted:
        return EngineDecision("HALTED", [], None, True, "HALTED_BY_WRAPPER")
    if seconds_to_expiry is None:
        return EngineDecision("HALTED", [], None, True, "NO_CLOCK")

    rescue = _rescue_intent(inv, book, seconds_to_expiry)

    if seconds_to_expiry <= STOP_QUOTING_BEFORE_EXPIRY_S:
        return EngineDecision("EXPIRY_STOP", [], rescue, True, "EXPIRY_STOP")
    if inv.spend >= max_spend:
        return EngineDecision("CAPPED", [], rescue, True, "SPEND_CAP")

    state = _flow_state(flow_60s)

    if state == "DEAD":
        if not in_reward_pool:
            return EngineDecision("IDLE", [], rescue, True, "DEAD_NO_POOL")
        quotes = []
        for side, bid in (("yes", book.yes_bid), ("no", book.no_bid)):
            if bid is None:
                continue
            price = _clamp(bid - SUBSIDY_BACKOFF)
            if price is not None:
                quotes.append(Quote(side, price, SUBSIDY_MIN_SIZE, "SUBSIDY_REST"))
        if len(quotes) < 2:
            return EngineDecision("IDLE", [], rescue, True, "SUBSIDY_NO_BOOK")
        return EngineDecision("SUBSIDY", quotes, rescue, False, "SUBSIDY_TWO_SIDED")

    # WARM / HOT: active maker. Missing side leans toward the pair ceiling;
    # held side backs off; every quote respects the pair ceiling and risk caps.
    backoff = WARM_JOIN_BACKOFF if state == "WARM" else HOT_JOIN_BACKOFF
    residual = inv.residual
    held_avg = inv.held_avg_cost()
    quotes: List[Quote] = []
    for side, bid in (("yes", book.yes_bid), ("no", book.no_bid)):
        if bid is None:
            continue
        is_missing = (residual > 1e-9 and side == "no") or (residual < -1e-9 and side == "yes")
        is_held = (residual > 1e-9 and side == "yes") or (residual < -1e-9 and side == "no")
        price = bid - backoff
        if is_held:
            price = bid - LEAN_BACKOFF_HELD
        if is_missing and held_avg is not None:
            # Lean up to the ceiling, but never cross the ask (stay maker).
            ceiling = PAIR_CEILING - held_avg
            ask = book.no_ask if side == "no" else book.yes_ask
            lean = min(ceiling, (ask - 0.01) if ask is not None else ceiling)
            price = max(price, min(lean, ceiling))
            if price > ceiling + 1e-9:
                continue  # ceiling unreachable at a maker price -> don't chase
        price = _clamp(price)
        if price is None:
            continue
        size = min(ACTIVE_SIZE, max(0.0, (max_spend - inv.spend) / max(price, 0.01)))
        size = float(int(size))
        if size < 1.0:
            continue
        probe = PairInventory(inv.yes_qty, inv.yes_cost, inv.no_qty, inv.no_cost)
        probe.apply_fill(side, size, price)
        while size >= 1.0 and probe.worst_settlement_loss() > max_worst_loss:
            size -= 1.0
            probe = PairInventory(inv.yes_qty, inv.yes_cost, inv.no_qty, inv.no_cost)
            probe.apply_fill(side, size, price)
        if size < 1.0:
            continue
        reason = "LEAN_COMPLETE_PAIR" if is_missing else ("HELD_BACKOFF" if is_held else f"{state}_JOIN")
        quotes.append(Quote(side, price, size, reason))
    if not quotes:
        return EngineDecision(state, [], rescue, True, "NO_QUOTABLE_SIDE")
    return EngineDecision(state, quotes, rescue, False, f"{state}_QUOTING")


def _rescue_intent(
    inv: PairInventory,
    book: PairBook,
    seconds_to_expiry: float,
) -> Optional[Quote]:
    """Emergency taker completion: only in the rescue zone, only if it reduces
    worst-settlement loss, only within RESCUE_MAX_PREMIUM of the pair ceiling."""
    if seconds_to_expiry > RESCUE_ZONE_BEFORE_EXPIRY_S or seconds_to_expiry <= 0:
        return None
    residual = inv.residual
    if abs(residual) < 1.0:
        return None
    held_avg = inv.held_avg_cost()
    if held_avg is None:
        return None
    missing = "no" if residual > 0 else "yes"
    ask = book.no_ask if missing == "no" else book.yes_ask
    if ask is None:
        return None
    ceiling = PAIR_CEILING - held_avg
    if ask > ceiling + RESCUE_MAX_PREMIUM:
        return None  # too expensive: eat the directional risk, capped at $5
    qty = float(int(abs(residual)))
    probe = PairInventory(inv.yes_qty, inv.yes_cost, inv.no_qty, inv.no_cost)
    probe.apply_fill(missing, qty, ask)
    if probe.worst_settlement_loss() >= inv.worst_settlement_loss():
        return None
    return Quote(missing, ask, qty, "RESCUE_TAKER")
