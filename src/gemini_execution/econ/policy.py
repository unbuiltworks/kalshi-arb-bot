"""BTC5m AND BTC15m ARE DIFFERENT POLICIES, NOT ONE POLICY WITH TWO CLOCKS.

The lineage already learned this once and paid for it.
`L5_ADAPTED_GEMINI_CRYPTO` carried `TTE_policy = [0, 300]`, where 300 is the
LENGTH OF A BTC5m CONTRACT -- an OURS_ADAPTED number chosen for the 5-minute
cohort and then inherited unchanged when the same lane was bound to BTC15m
(`TTE_PROVENANCE_1.md`). Under it the clone was present only for the last third
of a 900s window, which is the 3.2% tail in which L5 has already stopped
opening. `L5_ADAPTED_GEMINI_BTC15M_V2` corrected exactly that one field to
[0, 900] and nothing else, on evidence: 89.5% of L5's BTC15m OPEN fills land at
tte 600-900, and a fee-positive complement is price-observable in 28 of 96
contracts under [300, 900] against 12 under [0, 300] (sign test p=0.0037).

THAT CORRECTION IS PRESERVED AND COMPARED AGAINST, NOT REINVENTED. This module
takes [0, 900] as given for BTC15m and adds the thing the timing correction
does not address: WHAT THE LANE SHOULD BE DOING at each point inside those 900
seconds. A band says when we may quote. A mode says what we are quoting FOR.

THE MODES, AND THE ONE THE BRIEF EXPLICITLY RULES OUT.

    ENTRY        seek a first fill; inventory work if any arises
    MIXED        both, with entry progressively discouraged
    INVENTORY    no new first fills; work what is held
    WITHDRAW     nothing rests

The brief says: "do not mechanically wait until the last five minutes to
participate." So BTC15m ENTRY opens at the TOP of the window (900s) rather than
at 300s, and the 900->300 region is where the challenger is designed to do most
of its entering -- it is the region the V2 correction opened and the region the
old [0,300] band could not see at all.

WHY BTC5m IS STRICTER RATHER THAN FASTER. A 300s window leaves almost no time
to recover a complement after a first fill, so the cost of a bad entry is
carried to settlement rather than worked out of. BTC5m therefore tightens the
ENTRY gates (more alternation required, a nearer complement) and tolerates less
residual, instead of merely running the same policy on a shorter clock.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional, Tuple

from src.gemini_execution.econ import entry as EN

SCHEMA = "gemini_econ_cohort_policy/v1"

ENTRY, MIXED, INVENTORY, WITHDRAW = "ENTRY", "MIXED", "INVENTORY", "WITHDRAW"
MODES = (ENTRY, MIXED, INVENTORY, WITHDRAW)


class CohortPolicy:
    """Everything that differs between the two cohorts, in one place.

    Declared per cohort rather than derived from window length, so a value
    that is genuinely instrument-specific cannot be inherited by arithmetic
    the way `[0, 300]` was.
    """

    __slots__ = ("cohort", "window_s", "quote_band_s", "entry_until_tte_s",
                 "mixed_until_tte_s", "withdraw_below_tte_s",
                 "max_residual", "toxicity_horizon_s", "rescue_below_tte_s",
                 "thresholds", "notes")

    def __init__(self, *, cohort, window_s, quote_band_s, entry_until_tte_s,
                 mixed_until_tte_s, withdraw_below_tte_s, max_residual,
                 toxicity_horizon_s, rescue_below_tte_s, thresholds, notes):
        self.cohort = cohort
        self.window_s = window_s
        self.quote_band_s = quote_band_s
        self.entry_until_tte_s = entry_until_tte_s
        self.mixed_until_tte_s = mixed_until_tte_s
        self.withdraw_below_tte_s = withdraw_below_tte_s
        self.max_residual = D(str(max_residual))
        self.toxicity_horizon_s = toxicity_horizon_s
        self.rescue_below_tte_s = rescue_below_tte_s
        self.thresholds = thresholds
        self.notes = notes

    def mode(self, tte_s: Any) -> Dict[str, Any]:
        """Which mode this instant is in, and why.

        Read as "time remaining", so the bounds count DOWN: entry stops first,
        then mixed, then everything withdraws.
        """
        if tte_s is None:
            return {"mode": WITHDRAW, "why": "TTE unreadable; UNREADABLE is "
                    "never treated as time remaining"}
        t = float(tte_s)
        lo, hi = self.quote_band_s
        if not (lo <= t <= hi):
            return {"mode": WITHDRAW, "tte_s": t,
                    "why": "outside the quote band %s" % (self.quote_band_s,)}
        if t < self.withdraw_below_tte_s:
            return {"mode": WITHDRAW, "tte_s": t,
                    "why": "below the withdrawal floor %ss"
                           % self.withdraw_below_tte_s}
        if t >= self.entry_until_tte_s:
            return {"mode": ENTRY, "tte_s": t,
                    "why": "full entry region"}
        if t >= self.mixed_until_tte_s:
            return {"mode": MIXED, "tte_s": t,
                    "why": "entry progressively discouraged; inventory work "
                           "takes precedence"}
        return {"mode": INVENTORY, "tte_s": t,
                "why": "no new first fills; only what is already held"}

    def may_open(self, tte_s: Any) -> bool:
        return self.mode(tte_s)["mode"] in (ENTRY, MIXED)

    def may_rescue(self, tte_s: Any) -> bool:
        """Bounded aggressive exit is permitted only near the end, where
        carrying an unknown is worse than taking a known loss."""
        if tte_s is None:
            return False
        return float(tte_s) <= self.rescue_below_tte_s

    def report(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA, "cohort": self.cohort,
            "window_s": self.window_s, "quote_band_s": list(self.quote_band_s),
            "entry_until_tte_s": self.entry_until_tte_s,
            "mixed_until_tte_s": self.mixed_until_tte_s,
            "withdraw_below_tte_s": self.withdraw_below_tte_s,
            "max_residual": str(self.max_residual),
            "toxicity_horizon_s": self.toxicity_horizon_s,
            "rescue_below_tte_s": self.rescue_below_tte_s,
            "thresholds": self.thresholds.report(),
            "notes": self.notes,
        }


#: BTC5m -- 300s. Stricter, not merely faster.
BTC5M = CohortPolicy(
    cohort="BTC5m", window_s=300, quote_band_s=(0.0, 300.0),
    entry_until_tte_s=120.0,      # stop seeking first fills with 2 min left
    mixed_until_tte_s=60.0,
    withdraw_below_tte_s=30.0,    # matches continuous_probe.WITHDRAW_ALL_TTE_S
    max_residual="0.02",          # half the frozen 0.04: less time to work it
    toxicity_horizon_s=20.0,      # post-fill assessment must be fast here
    rescue_below_tte_s=60.0,
    thresholds=EN.Thresholds(
        cohort="BTC5m",
        min_alternation=0.35,            # stricter: reversibility matters more
        max_largest_print_share=0.45,
        max_required_gap_ticks=2,        # the market must already be near
        max_reference_disagreement="0.04",
        min_prints=5,
        hazard_threshold=0.40,
        min_edge="0.005",
        max_spread="0.08"),
    notes=("300s leaves almost no room to recover a complement, so a bad "
           "entry is carried to settlement rather than worked out. The "
           "response is tighter ADMISSION and less residual, not a faster "
           "loop over the same rules."))

#: BTC15m -- 900s. The full lifecycle, entering early rather than late.
BTC15M = CohortPolicy(
    cohort="BTC15m", window_s=900, quote_band_s=(0.0, 900.0),
    entry_until_tte_s=300.0,      # ENTRY across the whole 900->300 region
    mixed_until_tte_s=120.0,
    withdraw_below_tte_s=30.0,
    max_residual="0.04",          # the frozen envelope; there is time to work it
    toxicity_horizon_s=60.0,
    rescue_below_tte_s=180.0,
    thresholds=EN.Thresholds(
        cohort="BTC15m",
        min_alternation=0.30,
        max_largest_print_share=0.50,
        max_required_gap_ticks=4,        # a longer horizon can wait a little
        max_reference_disagreement="0.05",
        min_prints=4,
        hazard_threshold=0.35,
        min_edge="0.005",
        max_spread="0.10"),
    notes=("quote_band_s [0,900] is L5_ADAPTED_GEMINI_BTC15M_V2's correction, "
           "preserved and not re-derived. ENTRY runs 900->300 because the "
           "brief rules out mechanically waiting for the last five minutes, "
           "and because 89.5% of L5's BTC15m OPEN fills land at tte 600-900."))

COHORTS: Dict[str, CohortPolicy] = {"BTC5m": BTC5M, "BTC15m": BTC15M}


def policy_for(cohort: Any) -> Optional[CohortPolicy]:
    """The policy for a cohort, or None. NEVER a default.

    A cohort we have not written a policy for must not silently inherit
    another one's -- that inheritance is the exact defect `TTE_PROVENANCE_1.md`
    records.
    """
    name = getattr(cohort, "name", None) or str(cohort or "")
    return COHORTS.get(name)
