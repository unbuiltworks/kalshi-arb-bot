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

#: the 15-minute family. THE BAND IS SET BY MODEL A'S OWN QUOTING WINDOW,
#: not by symmetry with the 5-minute one.
#:
#: The frozen surface carries TTE_policy quote_between_tte_s = [0, 300]: a
#: OURS_ADAPTED parameter -- NOT a measurement of L5 -- and part of Model A,
#: so it is not touched here. L5's own measured TTE_policy is [0, 900]; the
#: 300 is the length of a Gemini BTC5m contract, chosen for that cohort and
#: then inherited by BTC15m. See TTE_PROVENANCE_1.md. On a
#: 5-minute contract TTE never exceeds 300 and it never binds. On a
#: 15-minute contract it means Model A quotes ONLY the final five minutes.
#:
#: Entering at 240-280 followed from that: inside the quoting window, so the
#: lane was live from its first cycle. The band was correct GIVEN [0, 300].
#:
#: [0, 300] IS NOW KNOWN TO BE WRONG FOR THIS INSTRUMENT, and the band has to
#: follow it back. L5_ADAPTED_GEMINI_BTC15M_V2 quotes [0, 900]; entering at
#: 240-280 would leave the process unattached to the contract for the entire
#: interval that correction is about, so the corrected arm could produce no
#: evidence whatever and its silence would look like a finding. Entering at
#: the top of the window is what makes the comparison possible at all.
#:
#: THE CONTROL'S DECISIONS ARE UNCHANGED BY THIS. Above tte 300 the frozen
#: band withholds its surface, which is what it did before and what it does
#: now -- the difference is that the withholding is RECORDED cycle by cycle
#: instead of being inferred from the lane's absence. The earlier note that
#: 840-880 "produced nine minutes in which nothing could happen" was right
#: about the mechanism and wrong about the remedy: the cure for a band that
#: withholds for nine minutes is not to stop watching those nine minutes.
#:
#: BTC5m IS UNTOUCHED. Its window is 300s, [0, 300] never binds on it, and
#: its band is the separate constant above.
BTC15M = Cohort(name="BTC15m", prefix="BTC15M", minutes=15,
                entry_band=(840.0, 880.0))

COHORTS: Dict[str, Cohort] = {c.name.lower(): c for c in (BTC5M, BTC15M)}
COHORTS.update({c.prefix.lower(): c for c in (BTC5M, BTC15M)})

DEFAULT = BTC5M


def for_symbol(symbol: str) -> "Optional[Cohort]":
    """Which family a venue symbol belongs to, or None.

    NONE, NEVER A DEFAULT. A symbol nobody recognises must not be labelled
    BTC5m: that is how `state_from_venue` stamped "BTC5m" onto every BTC15m
    market state it built, which then travelled into the engine's Surface
    record and into the evidence.
    """
    s = str(symbol or "")
    for c in (BTC5M, BTC15M):
        if s.startswith("GEMI-" + c.prefix):
            return c
    return None


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
