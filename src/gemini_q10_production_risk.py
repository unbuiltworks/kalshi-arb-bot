"""Pure safety policy for the Gemini q=10 production candidate.

No credentials and no order entry.  This module centralizes the decisions that
must stay deterministic/testable before the live executor is allowed to mutate a
venue: dynamic complement spending, residual bounds, and a proactive short-horizon
spot-momentum darkness gate.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Deque

from src import gemini_liquidity_reward_dynamic_completion_q10 as completion

Q = D("10")
ZERO = D("0")
MAX_RESIDUAL = D("10")
DEFAULT_TOXICITY_WINDOW_S = 1.0
DEFAULT_TOXICITY_BPS = D("4.0")


def residual_qty(yes_fill: D, no_fill: D) -> D:
    return abs(D(str(yes_fill)) - D(str(no_fill)))


def residual_safe(yes_fill: D, no_fill: D, *, cap: D = MAX_RESIDUAL) -> bool:
    yes = D(str(yes_fill)); no = D(str(no_fill)); cap = D(str(cap))
    return ZERO <= yes <= Q and ZERO <= no <= Q and residual_qty(yes, no) <= cap


def completion_decision(*, filled_outcome: str, filled_price: D,
                        current_complement_price: D, yes_bid: D, yes_ask: D,
                        seconds_to_expiry: float, increment: D = D("0.01")) -> dict:
    """Delegate to the tested q10 completion policy, never counting incentives."""
    return completion.completion_plan(
        filled_outcome=filled_outcome,
        filled_price=D(str(filled_price)),
        current_complement_price=D(str(current_complement_price)),
        yes_bid=D(str(yes_bid)), yes_ask=D(str(yes_ask)),
        seconds_to_expiry=seconds_to_expiry, increment=D(str(increment)),
    )


@dataclass
class SpotMomentumGuard:
    """Small rolling guard used to go dark before a stale prediction quote is hit.

    ``observe`` returns True when the absolute move from the oldest retained spot
    to the newest reaches ``threshold_bps`` inside ``window_seconds``.  This is a
    protection signal, not a prediction model.
    """
    window_seconds: float = DEFAULT_TOXICITY_WINDOW_S
    threshold_bps: D = DEFAULT_TOXICITY_BPS
    samples: Deque[tuple[float, D]] = field(default_factory=deque)

    def observe(self, ts: float, spot: D) -> bool:
        ts = float(ts); spot = D(str(spot))
        if spot <= ZERO:
            return True
        self.samples.append((ts, spot))
        cutoff = ts - float(self.window_seconds)
        while len(self.samples) > 1 and self.samples[0][0] < cutoff:
            self.samples.popleft()
        if len(self.samples) < 2:
            return False
        old = self.samples[0][1]
        move_bps = abs(spot / old - D("1")) * D("10000")
        return move_bps >= D(str(self.threshold_bps))

    def current_move_bps(self) -> D:
        if len(self.samples) < 2:
            return ZERO
        old = self.samples[0][1]; new = self.samples[-1][1]
        return abs(new / old - D("1")) * D("10000")
