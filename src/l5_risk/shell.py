"""Venue-neutral risk arithmetic, and a shell any venue can be bounded by.

WHY THIS IS SHARED. The risk logic the Polymarket executor uses -- guaranteed
settlement floor, combined loss, an inclusive budget boundary -- is not
Polymarket-specific. It is arithmetic on inventory and cash. Writing a second
copy for Gemini is how two venues end up with two subtly different definitions
of "how much can we lose", which is the one number that must mean the same
thing everywhere.

These functions are therefore a SINGLE definition, and
tests/test_l5_risk_shell.py asserts they agree numerically with the live PMUS
implementation. If either moves, that test fails. The PMUS executor is NOT
refactored to import this -- changing a running money path to share code is a
production change with no safety benefit, and the equivalence test gives the
same protection without touching it.

NOTHING HERE MARKS TO MARKET. The floor reads contract counts and cash actually
paid; no price, no book, no valuation model. That is what lets it sit in a risk
stop without violating "never infer P&L from a mark".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Iterable, Optional, Sequence

ZERO = D("0")


def guaranteed_floor(up: Any, dn: Any, spent: Any) -> D:
    """min(up, dn) - cash paid: what this market settles to in its WORST branch.

    Exactly one side pays 1.00 per contract, so the poorer side is the floor.
    """
    return min(D(str(up)), D(str(dn))) - D(str(spent))


def unsettled_floor_total(floors: Iterable[Any]) -> D:
    """Sum of the NEGATIVE floors only.

    Negatives only, so one favourable open position cannot subsidise another
    market's guaranteed loss against the budget."""
    return sum((min(ZERO, D(str(f))) for f in floors), ZERO)


def combined_loss(realized: Any, floors: Iterable[Any]) -> D:
    """realized + the negative unsettled floors.

    One budget for settled losses AND guaranteed-but-unsettled ones, so the two
    cannot independently approach the stop."""
    return D(str(realized)) + unsettled_floor_total(floors)


def budget_tripped(combined: Any, stop: Any) -> bool:
    """INCLUSIVE: at exactly the stop, the stop HAS fired."""
    return D(str(combined)) <= D(str(stop))


@dataclass
class RiskVerdict:
    allowed: bool
    reason: str
    combined_loss: str
    stop: str
    detail: dict = field(default_factory=dict)


@dataclass
class RiskShell:
    """Per-venue bounds. Every field must be set explicitly.

    There is no default stop and no default inventory bound on purpose: a risk
    shell that works without being configured is one that can be deployed
    without anyone deciding what the limits are.
    """
    venue: str
    daily_loss_stop: str
    max_inventory_shares: str
    max_market_spend: str
    max_concurrent_markets: int
    deadman_s: float

    def __post_init__(self) -> None:
        if D(self.daily_loss_stop) >= 0:
            raise ValueError("daily_loss_stop must be negative: a stop at or "
                             "above zero never fires")
        for f in ("max_inventory_shares", "max_market_spend"):
            if D(getattr(self, f)) <= 0:
                raise ValueError(f"{f} must be positive")
        if self.max_concurrent_markets < 1:
            raise ValueError("max_concurrent_markets must be at least 1")
        if self.deadman_s <= 0:
            raise ValueError("deadman_s must be positive: a deadman that never "
                             "expires is not a deadman")

    def check(self, *, realized: Any, floors: Sequence[Any],
              intent, current_inventory: Any = 0, market_spent: Any = 0,
              open_markets: int = 0,
              last_heartbeat_age_s: Optional[float] = None) -> RiskVerdict:
        """May this intent acquire more inventory on this venue?

        Every refusal names the bound it hit. A verdict that just says "no"
        cannot be argued with or audited.
        """
        combined = combined_loss(realized, floors)
        det = {"combined_loss": str(combined), "stop": self.daily_loss_stop}

        if last_heartbeat_age_s is not None and \
                last_heartbeat_age_s > self.deadman_s:
            return RiskVerdict(False, (
                f"DEADMAN: last heartbeat {last_heartbeat_age_s:.0f}s ago "
                f"exceeds {self.deadman_s:.0f}s; state is stale so no new "
                f"exposure"), str(combined), self.daily_loss_stop, det)

        if budget_tripped(combined, self.daily_loss_stop):
            return RiskVerdict(False, (
                f"DAILY STOP: combined loss {combined} has reached "
                f"{self.daily_loss_stop} (inclusive)"),
                str(combined), self.daily_loss_stop, det)

        after = D(str(current_inventory)) + D(str(intent.incremental_qty))
        if after > D(self.max_inventory_shares):
            return RiskVerdict(False, (
                f"INVENTORY CAP: {after} would exceed "
                f"{self.max_inventory_shares} shares"),
                str(combined), self.daily_loss_stop, det)

        # priced at the intent's own ceiling: the worst price we would accept
        # is the right one for a spend bound, not the quote we hope to get.
        worst = D(str(market_spent)) + (D(str(intent.incremental_qty))
                                        * D(intent.max_acceptable_price))
        if worst > D(self.max_market_spend):
            return RiskVerdict(False, (
                f"MARKET SPEND CAP: {worst} at the intent ceiling would exceed "
                f"{self.max_market_spend}"),
                str(combined), self.daily_loss_stop, det)

        if open_markets >= self.max_concurrent_markets:
            return RiskVerdict(False, (
                f"CONCURRENCY: {open_markets} markets already open, limit "
                f"{self.max_concurrent_markets}"),
                str(combined), self.daily_loss_stop, det)

        return RiskVerdict(True, "within every configured bound",
                           str(combined), self.daily_loss_stop, det)


# Gemini is NOT live and these are NOT an authorisation to trade. They are the
# bounds a first bounded probe would run under, written down so the shell
# cannot be deployed with nobody having chosen them. Tiny on purpose, and the
# measured Gemini book -- ~1 contract deep at the touch, 6-8c spreads -- is the
# reason the inventory bound is small rather than the reason to widen it.
GEMINI_PROBE_SHELL = RiskShell(
    venue="GEMINI", daily_loss_stop="-5.00", max_inventory_shares="10",
    max_market_spend="5.00", max_concurrent_markets=1, deadman_s=300.0)
