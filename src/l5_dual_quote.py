"""Same-pass two-sided quoting, replenishment, and the L5 opening guidance.

WHAT WAS WRONG. `maybe_controller_open` called `executor.handle` ONCE per
tick, so the two-sided branch placed one leg and the complement waited for the
next tick ~5s later. Combined with the one-order lockout that refused re-entry
while anything rested, the engine could place at most
floor(entry_cutoff / order_ttl) = floor(480/120) = 4 openings in a window.
Campaign E spent OPEN 3/4 and produced three fills. That is arithmetic, not a
market outcome.

WHAT THIS CHANGES, and it is deliberately small. Nothing about pricing,
sizing, side selection or any gate. The controller simply does not stop after
one placement: it calls the SAME `handle` again in the same tick. The second
call re-reads the book, and the two-sided branch sees leg one already CLAIMED,
so `probe_leg_order` hands it the unclaimed leg on its own. The complement's
side is therefore never forced here -- claim state picks it, exactly as it
does today one tick later.

EVERY GATE STILL RUNS ON EVERY PASS. Each placement is a full `handle` call:
the pair precheck, the claim, MAX_LIVE_ORDERS, the cash and exposure
overlays, the residual cap, the opening floor and the pair ceiling all apply
to pass two as they do to pass one. This cannot place a leg that a tick-later
placement would have been refused; it only stops waiting.

WHY THAT IS NOT "QUOTE BOTH SIDES CONTINUOUSLY". The owner's measurement is
explicit that always-on two-sided quoting is worse: it raised lose-side
residual from 76% to 87% and net from -12.84 to -15.12 on the early era. The
difference here is that both legs are placed AS A PRICED PAIR in one pass,
gated on the pair's own economics, rather than as two independent quotes that
each fill on their own adverse tick.

DEFAULT OFF. `enabled()` is the exact token "ON" and nothing else, so an unset
or misspelled environment leaves `max_placements_per_pass()` at 1 -- today's
behaviour exactly, which is what keeps full-suite parity.
"""
from decimal import Decimal as D
import os
from typing import Any, Dict, Optional, Tuple

DUAL_ENV = "L5_DUAL_QUOTE"
REPLENISH_ENV = "L5_DUAL_REPLENISH"
#: the only value that enables either. A typo must not widen live behaviour.
ENABLE_TOKEN = "ON"
#: INTERLEAVED A/B. Same-pass placement on the B windows only, so both arms
#: see the same session, the same volatility and the same book regime. A
#: sequential "4 windows old then 4 windows new" split would confound the arms
#: with time of day, which at four windows each is the larger effect.
AB_TOKEN = "AB"

#: a pass may never place more than this, whatever the environment says. Two
#: is the pair; a third order in one tick is not a pair and has no economics
#: gated for it.
MAX_PLACEMENTS_CEILING = 2

#: Controls this module may never touch. Listed so the separation is testable.
SAFETY: Tuple[str, ...] = (
    "max_total_exposure_usd", "max_cash_deployed_usd", "max_usd_per_market",
    "max_abs_residual", "ABS_RESIDUAL_CAP_CONTRACTS",
    "max_unmatched_contracts", "max_worst_case_deficit_usd",
    "max_cumulative_loss_usd", "daily_realized_loss_halt_usd",
    "campaign_additional_loss_usd", "max_concurrent_markets",
    "max_live_orders", "completion_pair_ceiling", "max_pair_cost",
    "min_first_leg_own_price", "max_first_leg_own_price",
    "shadow_lock", "owner_arm_token", "halt_latch", "L5_LIVE_HALT",
    "reconciliation", "exact_order_cancellation", "watchdog_deadman",
    "authorization_expiry", "campaign_approval", "submission_class_budgets",
    "entry_cutoff_s", "complete_cutoff_s",
)

# ----------------------------------------------------------------- L5 facts
#: L5's MEASURED sequential behaviour over 214 profitable Mar-Apr BTC15m
#: windows. Data, not a target: these are what the audit found, and they are
#: here so the opening policy is guided by evidence rather than by taste.
L5_OBSERVED: Dict[str, Any] = {
    "windows": 214,
    "median_opening_own_px": D("0.490"),
    "opening_in_0_40_to_0_60_frac": D("0.72"),
    "median_matched_pair_cost": D("0.9583"),
    "maker_share": D("0.87"),
    "median_trades_per_window": 24,
    "median_end_residual_contracts": 26,
    "residual_lost_frac": D("0.57"),
    "net_per_dollar_deployed": D("0.01342"),
    "labels": ("measured from l5_canonical_sequence over the PROFITABLE era "
               "only. The August incentive era is excluded on purpose: it "
               "traded 170 times a window and lost $1,507 in four days."),
}


def _on(env: str) -> bool:
    return (os.getenv(env) or "").strip() == ENABLE_TOKEN


def _raw() -> str:
    return (os.getenv(DUAL_ENV) or "").strip()


def ab_mode() -> bool:
    """Interleaved A/B: arm B gets same-pass placement, arm A does not."""
    return _raw() == AB_TOKEN


def arm_for(window_start: Any) -> Optional[str]:
    """"A" or "B" for a window, from its own start minute.

    B is the :15 and :45 window of each hour, A is :00 and :30, so an
    8-window block is 4 and 4 and the arms alternate. Derived from the window
    itself, so the assignment is reproducible from the tape and cannot drift
    with process state.

    None when the window cannot be read. Callers treat that as arm A -- the
    LESS active arm -- because an unreadable window must never buy the extra
    placement.
    """
    try:
        if window_start is None:
            return None
        if hasattr(window_start, "minute"):
            minute = int(window_start.minute)
        else:
            import datetime as _dt
            minute = _dt.datetime.fromtimestamp(
                float(window_start), _dt.timezone.utc).minute
    except BaseException:                                      # noqa: BLE001
        return None
    return "B" if (minute // 15) % 2 == 1 else "A"


def enabled(window_start: Any = None) -> bool:
    """Same-pass two-sided placement for THIS window.

    "ON" enables it everywhere. "AB" enables it on arm B only. Anything else,
    including an unreadable window under "AB", is off.
    """
    if _on(DUAL_ENV):
        return True
    if ab_mode():
        return arm_for(window_start) == "B"
    return False


def replenish_enabled(window_start: Any = None) -> bool:
    """Re-open after a fill or cancellation frees a slot.

    Requires the dual-quote switch too: replenishing into a single-leg
    controller would just place more unpaired first legs, which is the
    behaviour the measurement says loses money.
    """
    return enabled(window_start) and _on(REPLENISH_ENV)


def max_placements_per_pass(window_start: Any = None) -> int:
    """How many placements one controller tick may attempt.

    1 when off -- today's behaviour, and the reason parity holds. Never more
    than MAX_PLACEMENTS_CEILING whatever the environment says.
    """
    return MAX_PLACEMENTS_CEILING if enabled(window_start) else 1


def opening_preference(window_start: Any = None) -> Optional[D]:
    """The own price L5 actually opened at, for ordering candidate legs.

    None when the mode is off, so the existing cheaper-leg-first ordering is
    untouched. When on, it returns L5's measured median opening so a caller
    can prefer the AT-THE-MONEY leg -- 72% of L5's openings sat in 0.40-0.60
    and its median was 0.490, while opening cheap stranded 52% of the time in
    our own replay.

    ADVISORY ONLY. It returns a number; it places nothing, and it can neither
    widen the opening band nor move a price past any ceiling.
    """
    return (L5_OBSERVED["median_opening_own_px"]
            if enabled(window_start) else None)


def describe() -> Dict[str, Any]:
    """Reporting only, for the effective-config telemetry."""
    return {
        "label": "L5_DUAL_QUOTE",
        "enabled": enabled(),
        "ab_mode": ab_mode(),
        "ab_assignment": "B = :15 and :45 windows; A = :00 and :30",
        "replenish": replenish_enabled(),
        "max_placements_per_pass": max_placements_per_pass(),
        "env": {"dual": DUAL_ENV, "replenish": REPLENISH_ENV},
        "ceiling": MAX_PLACEMENTS_CEILING,
        "opening_preference_own_px": (None if opening_preference() is None
                                      else str(opening_preference())),
        "l5_observed": {k: str(v) for k, v in L5_OBSERVED.items()},
        "safety_never_touched_here": list(SAFETY),
        "labels": ("Each placement is a full `handle` call, so every gate "
                   "runs on every pass. This changes WHEN the complement is "
                   "offered, never whether it is allowed."),
    }
