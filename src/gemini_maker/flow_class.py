"""Pairable/toxic flow classification (strategy brief section 3).

Replaces raw-volume HOT/WARM tiers as the entry gate. A window is classified
from the character of its flow, not its size:

  DEAD            no meaningful flow
  HEALTHY_PASSIVE some flow, no red flags, not yet clearly two-sided
  PAIRABLE        two-sided, alternating, depth on both sides: makeable
  TOXIC           one-way information burst: do not stand in front of it
  DISLOCATED      book disagrees materially with reference fair value

The first live canary's failure mode (volume spike -> aggressive quote ->
one-sided 25c fill -> loss) classifies as TOXIC here: dominated one-side
share, burst concentration, reference moving the same direction.

All thresholds are v1 proposals pending lag-study/hot-tape validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# v1 thresholds
DEAD_FLOW_60S = 10.0            # dollars traded in trailing 60s
PAIRABLE_MIN_PRINTS = 8         # prints in trailing 60s
PAIRABLE_MIN_ALTERNATION = 0.30  # share of sign flips between consecutive prints
TOXIC_ONE_SIDE_SHARE = 0.80     # share-weighted dominance of one direction
TOXIC_LARGEST_PRINT_SHARE = 0.50
PAIRABLE_MAX_ONE_SIDE = 0.65
DISLOCATION_CENTS = 0.10        # |fair - mid| beyond this = DISLOCATED
MIN_TWO_SIDED_DEPTH = 10.0      # contracts on BOTH best bid and best ask


@dataclass(frozen=True)
class FlowInputs:
    prints: Sequence[Tuple[float, str, float]]  # (t, "buy"|"sell" of YES, dollars)
    bid_depth: Optional[float]
    ask_depth: Optional[float]
    mid: Optional[float]
    fair_p_yes: Optional[float]
    ref_velocity_sign: int  # -1/0/+1: recent reference-price direction


@dataclass(frozen=True)
class FlowClass:
    label: str
    flow_60s: float
    one_side_share: float
    alternation: float
    largest_print_share: float
    reason: str


def classify(inp: FlowInputs) -> FlowClass:
    rows = list(inp.prints)
    total = sum(d for _, _, d in rows)
    buys = sum(d for _, s, d in rows if s == "buy")
    one_side = max(buys, total - buys) / total if total > 0 else 0.0
    dominant_dir = +1 if buys >= total - buys else -1
    largest = max((d for _, _, d in rows), default=0.0)
    largest_share = largest / total if total > 0 else 0.0
    flips = sum(1 for (_, s0, _), (_, s1, _) in zip(rows, rows[1:]) if s0 != s1)
    alternation = flips / (len(rows) - 1) if len(rows) > 1 else 0.0

    # DISLOCATED beats everything: the book is simply wrong vs fair value.
    if (inp.mid is not None and inp.fair_p_yes is not None
            and abs(inp.fair_p_yes - inp.mid) >= DISLOCATION_CENTS):
        return FlowClass("DISLOCATED", total, one_side, alternation,
                         largest_share, "FAIR_VS_MID_GAP")

    if total < DEAD_FLOW_60S:
        return FlowClass("DEAD", total, one_side, alternation, largest_share,
                         "NO_FLOW")

    toxic = (one_side >= TOXIC_ONE_SIDE_SHARE
             and largest_share >= TOXIC_LARGEST_PRINT_SHARE)
    if toxic or (one_side >= TOXIC_ONE_SIDE_SHARE
                 and inp.ref_velocity_sign == dominant_dir
                 and inp.ref_velocity_sign != 0):
        return FlowClass("TOXIC", total, one_side, alternation, largest_share,
                         "ONE_WAY_BURST")

    two_sided_depth = (inp.bid_depth is not None and inp.ask_depth is not None
                       and inp.bid_depth >= MIN_TWO_SIDED_DEPTH
                       and inp.ask_depth >= MIN_TWO_SIDED_DEPTH)
    if (len(rows) >= PAIRABLE_MIN_PRINTS
            and alternation >= PAIRABLE_MIN_ALTERNATION
            and one_side <= PAIRABLE_MAX_ONE_SIDE
            and two_sided_depth):
        return FlowClass("PAIRABLE", total, one_side, alternation,
                         largest_share, "TWO_SIDED_FLOW")

    return FlowClass("HEALTHY_PASSIVE", total, one_side, alternation,
                     largest_share, "NO_RED_FLAGS")
