"""Frozen shadow arms for the causal simulator.

An arm is FROZEN when its flags are recorded here. Freezing is what makes
"no tuning after seeing results" checkable: an arm's behaviour is a named
constant in source, so a later commit that changes it is visible in the diff
rather than hidden in a script invocation.

`PRE_REGISTERED` arms are declared BEFORE their economics are run, which is
the whole point of pre-registration: the hypothesis is on the record before
the number that would tempt us to adjust it exists.

Nothing here runs anything, and nothing here is permitted to place a live
order. These are shadow definitions only.
"""
from __future__ import annotations

from typing import Dict

#: arm name -> CausalSim keyword flags
ARMS: Dict[str, dict] = {
    # ---------------------------------------------------------------- frozen
    "CONTROL": dict(
        persistent_maker=True, reactive_take=False, basis_extend=False,
        hold_the_level=False, hold_open_extend=False,
    ),
    # Built, run, measured: a NO-OP against CONTROL. A complement's desired
    # price is theta - basis(held), which moves only on a fill, so CONTROL
    # never repriced a complement either. Kept frozen as the record.
    # See analysis/HOLD_THE_LEVEL_ARM_RESULT.md.
    "HOLD_THE_LEVEL": dict(
        persistent_maker=True, reactive_take=False, basis_extend=False,
        hold_the_level=True, hold_open_extend=False,
    ),
    # The measured churn is entirely on the OPEN/EXTEND leg (54/54 requotes).
    # This arm holds that leg instead.
    "HOLD_OPEN_EXTEND": dict(
        persistent_maker=True, reactive_take=False, basis_extend=False,
        hold_the_level=True, hold_open_extend=True,
    ),

    # -------------------------------------------------------- pre-registered
    # Declared before any economics are computed for it. Hypothesis: holding
    # the opening leg accumulates inventory that the basis-priced complement
    # cannot always clear as a maker, so residual is cleared by crossing
    # displayed fee-safe depth. Adds no new threshold: the take is gated by
    # the same fee-safe pair bound.
    "HOLD_OPEN_EXTEND_REACTIVE_TAKE": dict(
        persistent_maker=True, reactive_take=True, basis_extend=False,
        hold_the_level=True, hold_open_extend=True,
    ),
}

#: arms whose economics have NOT yet been run against a settled window
PRE_REGISTERED = ("HOLD_OPEN_EXTEND_REACTIVE_TAKE",)

#: mechanics may be reported from a partial slice; economics may not
ECONOMICS_REQUIRES_SETTLED_WINDOW = True
