"""BOUNDED, DEADLINE-AWARE PACING FOR THE E1 DECISION LOOP.

WHY THIS FILE EXISTS. On 2026-10-08 the fresh cohort armed, opened window
`cpc-btc-updown-15m-2026-10-08-0100z`, and spent all 960 of its iterations in
about ONE SECOND. Every iteration refused with `E9_PRICE_ABOVE_CEILING:0.62`:
the same-side best bid was 0.62 and the frozen first-leg ceiling is $0.60, so
the bound did its job and no order was ever priced. But `run_cohort`'s refusal
branch was a bare `continue` with no pacing, so instead of waiting through the
window's fifteen minutes for the price to come to us, the loop burned its whole
iteration budget, consumed one of twelve authorized windows, and exited.

XR2W Attempt 6 -- the only run of ours that has ever filled -- paced itself:
1,230 polls over 6,150.1 s. This module gives E1 the same patience, which is
what `L5_OPERATING_MODEL` puts first: PATIENCE -> FREQUENCY -> SIZE ->
CONCURRENCY.

IT CHANGES NO BOUND. Not the $0.60 first-leg ceiling, not the $0.96
fee-inclusive pair ceiling, not the 0.13-share quantity, not 20 placements per
window, not $1.56 of window exposure, not the $5.00 cohort-wide forward-looking
ceiling. It decides ONE thing: after a refusal, whether to wait and look at a
fresher book, or to stop.

FAIL CLOSED BY CONSTRUCTION. Only refusals on the explicit allowlist below are
paced. EVERY other refusal -- including one this file has never seen -- is
terminal and stops the cohort. A new safety refusal therefore stops the run by
default rather than being spun on, which is the direction a mistake here has to
fail.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the wait between re-observations after a paced refusal. Bounded and small
#: enough to catch a 15-minute window's price moves, large enough that 900
#: seconds of observation costs ~900 book reads rather than millions.
#: THE PRODUCTION PACE, as a name of its own. The suite shrinks `PACE_S` so
#: tests do not really sleep, and the iteration guard below must NOT shrink
#: with it: deriving the guard from a 0.001 s test pace gave a cap of ten
#: MILLION and hung the suite. So the guard is computed from this literal,
#: which nothing patches, and `PACE_S` is only how long we actually wait.
PRODUCTION_PACE_S = 1.0

PACE_S = PRODUCTION_PACE_S

#: no paced iteration EVER sleeps less than this. This is what makes "no busy
#: loop" a property of the code rather than a hope: every paced iteration
#: yields the CPU for a measurable interval, including the iterations right at
#: a deadline boundary.
MIN_PACE_S = 0.05

#: a transient empty snapshot (a momentary book gap) must not end the cohort,
#: but a permanently broken feed must not be retried forever either. 30 x
#: PACE_S = ~30 s of patience, then a distinct terminal reason.
MAX_CONSECUTIVE_EMPTY_SNAPSHOTS = 30

#: PACED: a refusal that a FRESHER BOOK IN THE SAME WINDOW could satisfy, or
#: that resolves by waiting out the window so the provider rolls over.
PACED_EXACT = frozenset({
    # our own order or residual is still live. Waiting is exactly right: a
    # maker order rests and then fills or is cancelled.
    "E3_OUR_ORDER_IS_OUTSTANDING",
    "E3_OUR_RESIDUAL_IS_OPEN",
    # the window is not tradeable now. We wait rather than stop, because the
    # provider rolls to the next window once window_end passes.
    "E4_WINDOW_NOT_OPEN",
    "E4_PAST_HANDOVER_TTE",
    # this window's twenty attempt slots are gone. The COHORT is not over;
    # wait out the window and the provider opens the next one.
    "E11_WINDOW_LATCH_EXHAUSTED",
})

#: prefixes of paced refusals. E7 feed freshness, E8 book executability, E9
#: price/tick/ceiling/cross, E10 complement feasibility are all statements
#: about THE BOOK RIGHT NOW, and the book moves.
PACED_PREFIXES = (
    "E7_",
    "E8_",
    "E9_",
    "E10_",
    # this window's $1.56 is spent. Window-scoped, not cohort-scoped.
    "PROJECTED_WINDOW_EXPOSURE_",
    # THE SAME TWO E3 STATEMENTS AS ABOVE, now that the provider derives
    # them from authoritative venue state and reports the count and the
    # residual alongside the code ("E3_OUR_ORDER_IS_OUTSTANDING:3"). They
    # were exact matches while the field was a hard-coded 0; a detailed
    # code must not silently become terminal just because it says more.
    "E3_OUR_ORDER_IS_OUTSTANDING",
    "E3_OUR_RESIDUAL_IS_OPEN",
    # order state could not be ESTABLISHED this instant -- an unreadable
    # GET, an unparsable body, two endpoints mid-cancellation. Nothing was
    # placed and nothing was risked, and the next read may well succeed, so
    # this is observed patiently rather than ending the cohort. It is
    # recorded on every iteration it occurs, so a persistent failure is
    # visible in the refusal tape rather than silent.
    #
    # NOTE what is deliberately NOT here: E3_AGGREGATE_VENUE_EXPOSURE_*.
    # An aggregate exposure that would breach the cohort ceiling, or that
    # cannot be made finite, is a RISK LIMIT, and a risk limit is terminal.
    "E3_ORDER_STATE_NOT_ESTABLISHED",
)


def _is_window_attempt_cap(code: str) -> bool:
    """`WINDOW_<n>_ATTEMPT_<k>_REFUSED_MAX_20` -- window-scoped, so paced."""
    return code.startswith("WINDOW_") and "_ATTEMPT_" in code


def is_paced(code: Any) -> bool:
    """True only for refusals on the explicit allowlist. Anything else,
    including an unrecognised code, is terminal."""
    if not isinstance(code, str):
        return False
    if code in PACED_EXACT:
        return True
    if _is_window_attempt_cap(code):
        return True
    return code.startswith(PACED_PREFIXES)


def classify(refusals: Optional[Sequence[Any]]) -> Dict[str, Any]:
    """Split a refusal list into the paced ones and the terminal ones.

    `all_paced` is True only when there is at least one refusal and EVERY
    refusal is paced. An empty list is not "all paced": nothing was refused,
    so there is nothing to pace.
    """
    codes = list(refusals or [])
    paced = [c for c in codes if is_paced(c)]
    terminal = [c for c in codes if not is_paced(c)]
    return {"paced": paced, "terminal": terminal,
            "all_paced": bool(codes) and not terminal,
            "n": len(codes)}


def pace_seconds(*, now: float, trading_cutoff_unix: Any = None,
                 window_end_unix: Any = None,
                 pace_s: float = PACE_S) -> float:
    """How long to wait before looking at the book again. DEADLINE-AWARE.

    Two different deadlines matter and they are not the same instant:

      * `trading_cutoff_unix` is the last moment a placement is permitted
        (the handover TTE). While we are still BEFORE it, we must never sleep
        PAST it, or we would sleep through the last tradeable instant of the
        window and discover the cutoff instead of using it.
      * `window_end_unix` is when the window dies. Once the cutoff has gone
        by, there is nothing left to place, so we simply wait out the rest of
        the window and let the provider roll to the next one.

    The return value is never below `MIN_PACE_S`, so no caller can be handed a
    zero-length wait and turn this into a busy loop.
    """
    horizon: Any = None
    if trading_cutoff_unix is not None and now < float(trading_cutoff_unix):
        horizon = float(trading_cutoff_unix)
    elif window_end_unix is not None:
        horizon = float(window_end_unix)
    if horizon is None:
        return max(MIN_PACE_S, float(pace_s))
    remaining = horizon - float(now)
    if remaining <= 0:
        # past the horizon. Still a real wait, never a spin: the provider
        # rolls the window over on the next call.
        return max(MIN_PACE_S, float(pace_s))
    return max(MIN_PACE_S, min(float(pace_s), remaining))


def pace_for(snapshot: Optional[Mapping[str, Any]], refusals:
             Optional[Sequence[Any]], *, now: float,
             pace_s: float = PACE_S) -> Dict[str, Any]:
    """The decision, as one reportable record: wait, or stop."""
    cls = classify(refusals)
    snap = snapshot or {}
    cutoff = snap.get("trading_cutoff_unix")
    end = snap.get("window_end_unix")
    if not cls["all_paced"]:
        return {"action": "STOP", "sleep_s": 0.0, **cls,
                "why": "a refusal outside the paced allowlist is terminal"}
    return {"action": "PACE",
            "sleep_s": pace_seconds(now=now, trading_cutoff_unix=cutoff,
                                    window_end_unix=end, pace_s=pace_s),
            "trading_cutoff_unix": cutoff, "window_end_unix": end,
            **cls,
            "why": "every refusal is a statement about a book that moves"}


def iteration_cap(*, windows: int, window_len_s: float,
                  max_placements: int,
                  pace_s: float = PRODUCTION_PACE_S) -> int:
    """A SAFETY NET, NOT A BUDGET -- and this is the distinction that the
    960-iteration cap got wrong.

    The old cap was `MAX_PLACEMENTS * 4` = 960, which is smaller than the
    number of times a PACED loop legitimately looks at the book while
    observing twelve 15-minute windows. So the cap, not the market, ended the
    cohort. The real budget is windows, placements and the trading deadline;
    iterations are only a guard against a genuine runaway, so the cap is
    derived from how many paced observations a complete cohort can contain
    and reaching it is an ANOMALY rather than a normal ending.
    """
    observations = int((windows * float(window_len_s)) / max(pace_s, 1e-9))
    return observations + max_placements * 4
