"""WHICH CONTRACT FAMILY the continuous system is bound to, as one object.

The 5-minute path was written with its grid hard-coded: a `BTC05M` prefix, a
ceil-to-5-minutes clock, a 300-second entry ceiling. Running BTC15m meant
either copying all of that or parameterising it. This is the parameter.

WHAT IS COHORT-DEPENDENT: the ticker prefix, the minute grid, the window
length, and the ENTRY BAND -- which is experimental scheduling and has always
been explicitly outside Model A ("no controller, price, size, envelope or
pair-cost behaviour knows this number exists").

WHAT IS NOT, AND IS NOT SCALED HERE: the frozen policy's TTE bounds.
`NO_NEW_OPEN_EXTEND_TTE_S` (60s) and `WITHDRAW_ALL_TTE_S` (30s) are ABSOLUTE
safety margins measured from expiry, not fractions of a window. On a 15-minute
contract they are a smaller share of the window and the same number of
seconds, which is the point: the margin protects against the same settlement
clock either way. Scaling them would be changing Model A, and Model A is
frozen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Cohort:
    name: str
    prefix: str
    minutes: int
    #: experimental scheduling only -- WHEN a window begins, nothing else
    entry_band: Tuple[float, float]

    @property
    def window_s(self) -> float:
        return float(self.minutes * 60)

    @property
    def max_entry_tte_s(self) -> float:
        """A contract cannot be entered before it exists."""
        return self.window_s

    @property
    def max_entry_wait_s(self) -> float:
        """One full contract cycle plus margin.

        From the worst arrival -- just under the band -- the next band opens
        one window later. The 5-minute family's standing 420s is exactly that
        shape; hard-coding it meant a 15-minute run timed out before its band
        could ever open.
        """
        return self.window_s + 120.0

    def symbol_for(self, ticker: str) -> str:
        return "GEMI-%s-UP" % ticker


#: the 5-minute family the programme has been running. Band unchanged.
BTC5M = Cohort(name="BTC5m", prefix="BTC05M", minutes=5,
               entry_band=(240.0, 280.0))

#: the 15-minute family. The band sits the same distance from the window's
#: OPEN as the 5-minute band does -- 20 to 60 seconds in -- so entry happens
#: early in the contract's life on both, rather than mid-window on one.
BTC15M = Cohort(name="BTC15m", prefix="BTC15M", minutes=15,
                entry_band=(840.0, 880.0))

COHORTS: Dict[str, Cohort] = {c.name.lower(): c for c in (BTC5M, BTC15M)}
COHORTS.update({c.prefix.lower(): c for c in (BTC5M, BTC15M)})

DEFAULT = BTC5M


def resolve(name: Optional[str]) -> Cohort:
    """A cohort by name, or the standing 5-minute default.

    An unknown name RAISES rather than silently falling back: binding a live
    loop to the wrong contract family because of a typo is exactly the kind
    of quiet substitution this codebase refuses everywhere else.
    """
    if name in (None, "", "default"):
        return DEFAULT
    key = str(name).strip().lower()
    if key not in COHORTS:
        raise ValueError(
            "unknown cohort %r; known: %s"
            % (name, sorted({c.name for c in COHORTS.values()})))
    return COHORTS[key]


def of_symbol(symbol: str) -> Optional[Cohort]:
    """Which family a symbol belongs to, from its ticker prefix."""
    parts = str(symbol).split("-")
    if len(parts) < 3:
        return None
    tk = parts[1]
    for c in {id(v): v for v in COHORTS.values()}.values():
        if tk.startswith(c.prefix):
            return c
    return None
