"""STAGE-6-S6P bounds, as Decimals with their dollar values written out.

Every number here is a BOUND, not a tuned parameter. None was fitted to any
backtest, grid, or replay. Each is justified by an arithmetic worst case or
by a rule already frozen elsewhere in the repository, and the justification
travels with the number in `BOUND_PROVENANCE` so a reviewer never has to
take a constant on trust.

THE WORST CASE THIS PROBE CAN LOSE. Both legs are BUYs (the venue has a
single instrument; "sell NO" is "buy YES" and vice versa, per
`src.l5_order_body.STATIC_CONTRACT`). So cash out is bounded by what we pay,
and the loss-maximising outcome is NOT a completed pair -- a pair completed
at a combined cost at or below $0.96 locks a non-negative gross outcome,
since one side of a binary pays $1.00 per contract. The worst case is the
FIRST LEG FILLING AND THE COMPLEMENT NEVER FILLING: we hold an unmatched
directional residual which settles to zero.

    MAX_POSSIBLE_LOSS_USD
      = FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE
      = 1.00 shares * $0.60
      = $0.60

and that is the whole session's loss bound, because the probe places at most
one first leg and at most one complement, in one market, once.

WHY 1.00 SHARE AND NOT THE VENUE MINIMUM. XR2W traded 0.01 shares, which was
right for a one-leg wire test. It is the WRONG size for this experiment, and
not because bigger is better: the PMUS maker rebate has a hard cent floor
(round_half_even of 0.0125*C*p*(1-p) to $0.01 is $0.00 below 1.62 shares at
p=0.45), so at 0.01 shares every rebate figure is structurally $0.00 and a
partial fill cannot be distinguished from a full one at the venue's 0.01
quantity grid. 1.00 share gives 100 distinguishable partial-fill states on
that grid while keeping the loss bound under a dollar. It is still BELOW the
rebate cent floor, which is deliberate: see `REBATE_IS_ASSUMED_ZERO`.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict

# ---------------------------------------------------------------- venue grid
#: the venue's own 0.01 grid and minimum, inherited from the proven spec
VENUE_TICK = D("0.01")
VENUE_MIN_QTY = D("0.01")
VENUE_MIN_PRICE = D("0.01")
VENUE_MAX_PRICE = D("0.99")

# ------------------------------------------------------------- the first leg
#: shares of the FIRST (passive, post-only) leg
FIRST_LEG_QTY = D("1.00")

#: the first leg may not be priced above this. A ceiling, not a target: it
#: exists so the loss bound below is arithmetic rather than hopeful.
MAX_FIRST_LEG_PRICE = D("0.60")

#: $ -- the most the first leg can cost if it fills in full
MAX_FIRST_LEG_NOTIONAL_USD = (FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE)

# --------------------------------------------------------------- the pair
#: Spring discipline: materially cheaper pair completion. CLAUDE.md pins
#: this at <= 0.96 where the venue implementation uses that target, and
#: records that near-$1 pair tolerance is LOCKED later-incentive-era
#: behaviour. Not tuned here; copied.
PAIR_COST_CEILING = D("0.96")

#: $ per matched contract -- the locked gross if a pair completes AT the
#: ceiling. One side of a binary pays $1.00.
MIN_LOCKED_PAIR_GROSS_USD = (D("1.00") - PAIR_COST_CEILING)

# ------------------------------------------------------------- the residual
#: shares. The residual cannot exceed the first leg, because the complement
#: is sized to the first leg's ACTUAL filled quantity and never above it.
MAX_RESIDUAL_SHARES = FIRST_LEG_QTY

#: $ -- residual directional exposure bound. Equal to the first-leg notional
#: bound by construction, and asserted equal at import.
MAX_RESIDUAL_NOTIONAL_USD = MAX_FIRST_LEG_NOTIONAL_USD

#: shares -- |long_shares - short_shares|. Same bound: with one leg per side
#: and the complement capped at the first leg's fill, imbalance can never
#: exceed one first leg.
MAX_INVENTORY_IMBALANCE_SHARES = FIRST_LEG_QTY

# ------------------------------------------------------------------ the loss
#: $ -- the arithmetic worst case for the whole session. See the module
#: docstring for the derivation.
MAX_POSSIBLE_LOSS_USD = MAX_FIRST_LEG_NOTIONAL_USD

#: $ -- daily realized loss bound. One probe per authorization, no automatic
#: repeat (CLAUDE.md: "no automatic repeat after a completed/crashed live
#: probe"), so the daily bound IS the session bound.
MAX_DAILY_REALIZED_LOSS_USD = MAX_POSSIBLE_LOSS_USD

#: $ -- total strategy exposure bound while this probe is the only live
#: money-moving path.
MAX_TOTAL_STRATEGY_EXPOSURE_USD = MAX_POSSIBLE_LOSS_USD

# ------------------------------------------------------------------- counting
#: concurrent markets. One market at a time (CLAUDE.md).
MAX_CONCURRENT_MARKETS = 1

#: outstanding orders AT ANY INSTANT. The legs are strictly sequential:
#: the complement is not placed until the first leg is cancelled or
#: terminal, so "two submissions" never means "two resting orders".
MAX_OUTSTANDING_ORDERS = 1

#: total POST /v1/orders calls this session may make, ever. Two durable
#: latch slots, one per leg.
MAX_SUBMISSIONS_PER_SESSION = 2

#: first legs. Exactly one; a second first leg is a new experiment.
MAX_FIRST_LEGS = 1

#: complement submissions. Exactly one attempt. A complement that does not
#: fill is NOT re-quoted, re-priced or re-sent in this build -- patience is
#: expressed as WAITING at one price, not as working an order. Re-quoting is
#: the next rung and is deliberately out of scope.
MAX_COMPLEMENT_SUBMISSIONS = 1

# ---------------------------------------------------------------- the clock
#: s -- btc-updown-15m window length, inherited from live_exec2.timing
WINDOW_LEN_S = 900.0

#: s -- no placement at or below this TTE; resting exposure comes off here.
TTE_CUTOFF_S = 120.0

#: s -- the TTE at which the first leg stops resting and the complement's
#: turn begins.
#:
#: WHY THIS IS NOT AN ARBITRARY TIMER. CLAUDE.md forbids forcing
#: rebalancing on an arbitrary timer, and the complement is accordingly
#: never forced -- it is quoted once and waits. But the first leg and the
#: complement compete for the SINGLE outstanding-order slot, so something
#: has to end the first leg's turn, and "whenever it happens to fill" would
#: leave the complement no time at all in the common case.
#:
#: So the split is derived from the window's own geometry rather than
#: chosen: the placeable stretch is WINDOW_LEN_S - TTE_CUTOFF_S = 780 s, and
#: it is divided EQUALLY, 390 s to each leg. The first leg rests over TTE
#: 900 -> 510; the complement rests over TTE 510 -> 120. Neither leg gets
#: more patience than the other, and the number falls out of 900 and 120,
#: both of which are inherited. It was not fitted to anything.
COMPLEMENT_HANDOVER_TTE_S = TTE_CUTOFF_S + (WINDOW_LEN_S - TTE_CUTOFF_S) / 2

#: s -- the patience each leg is given, equal by construction
PER_LEG_PATIENCE_S = (WINDOW_LEN_S - TTE_CUTOFF_S) / 2

# ---------------------------------------------------------------- the rebate
#: The PMUS maker rebate is round_half_even(0.0125*C*p*(1-p), $0.01) with a
#: HARD CENT FLOOR: below 1.62 shares (at p=0.45) it rounds to $0.00. At
#: FIRST_LEG_QTY = 1.00 share it is STRUCTURALLY $0.00. So no bound, gate or
#: economic test in this protocol may depend on it, and the pair-cost gate
#: counts the rebate as exactly zero.
REBATE_IS_ASSUMED_ZERO = True
REBATE_CENT_FLOOR_SHARES = D("1.62")


BOUND_PROVENANCE: Dict[str, Dict[str, Any]] = {
    "FIRST_LEG_QTY": {
        "usd_or_unit": "1.00 shares",
        "why": ("100 distinguishable partial-fill states on the venue's "
                "0.01 quantity grid, with a sub-dollar loss bound. Chosen "
                "for observability of partial fills, not for expectancy."),
        "fitted": False,
    },
    "MAX_FIRST_LEG_PRICE": {
        "usd_or_unit": "$0.60",
        "why": ("a ceiling that makes MAX_POSSIBLE_LOSS_USD arithmetic. "
                "Also leaves room under PAIR_COST_CEILING for a fee-safe "
                "complement: 0.96 - 0.60 = 0.36 before fees."),
        "fitted": False,
    },
    "PAIR_COST_CEILING": {
        "usd_or_unit": "$0.96 per matched contract, fees included",
        "why": ("CLAUDE.md spring discipline, copied verbatim. Near-$1 "
                "pair tolerance is LOCKED later-incentive-era behaviour."),
        "fitted": False,
    },
    "MAX_RESIDUAL_NOTIONAL_USD": {
        "usd_or_unit": "$0.60",
        "why": ("equal to the first-leg notional bound by construction: "
                "the complement is sized to the first leg's ACTUAL fill "
                "and never above it, so the residual cannot exceed the "
                "first leg."),
        "fitted": False,
    },
    "MAX_INVENTORY_IMBALANCE_SHARES": {
        "usd_or_unit": "1.00 shares",
        "why": "same construction as the residual bound.",
        "fitted": False,
    },
    "MAX_POSSIBLE_LOSS_USD": {
        "usd_or_unit": "$0.60",
        "why": ("first leg fills in full at the ceiling, complement never "
                "fills, residual settles to zero. Both legs are BUYs, so "
                "cash out is bounded by what we pay."),
        "fitted": False,
    },
    "MAX_DAILY_REALIZED_LOSS_USD": {
        "usd_or_unit": "$0.60",
        "why": ("one probe per authorization with no automatic repeat, so "
                "the daily bound is the session bound."),
        "fitted": False,
    },
    "COMPLEMENT_HANDOVER_TTE_S": {
        "usd_or_unit": "510.0 s TTE",
        "why": ("an EQUAL division of the placeable stretch "
                "(900 - 120 = 780 s) between the two legs, 390 s each. "
                "Derived from the inherited window length and TTE cutoff, "
                "not chosen and not fitted. It ends the first leg's turn "
                "because the two legs compete for the single outstanding-"
                "order slot; it never forces the complement, which is "
                "quoted once and waits."),
        "fitted": False,
    },
    "TTE_CUTOFF_S": {
        "usd_or_unit": "120.0 s",
        "why": ("inherited unchanged from analysis.l5.live_exec2.timing; "
                "unknown TTE counts as past the boundary."),
        "fitted": False,
    },
    "MAX_SUBMISSIONS_PER_SESSION": {
        "usd_or_unit": "2 POSTs",
        "why": ("one first leg and one complement, each behind its own "
                "O_CREAT|O_EXCL durable latch slot."),
        "fitted": False,
    },
}


def assert_bounds_coherent() -> None:
    """The bounds must not merely be small; they must be CONSISTENT.

    Asserted at import and again at every boot, because a bound that
    contradicts another bound is how a loss envelope quietly widens.
    """
    bad = []
    if MAX_FIRST_LEG_NOTIONAL_USD != FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE:
        bad.append("first-leg notional is not qty * price ceiling")
    if MAX_RESIDUAL_NOTIONAL_USD != MAX_FIRST_LEG_NOTIONAL_USD:
        bad.append("residual bound != first-leg notional bound")
    if MAX_RESIDUAL_SHARES != FIRST_LEG_QTY:
        bad.append("residual share bound != first-leg qty")
    if MAX_INVENTORY_IMBALANCE_SHARES != FIRST_LEG_QTY:
        bad.append("imbalance bound != first-leg qty")
    if MAX_POSSIBLE_LOSS_USD != MAX_FIRST_LEG_NOTIONAL_USD:
        bad.append("loss bound != first-leg notional bound")
    if MAX_DAILY_REALIZED_LOSS_USD < MAX_POSSIBLE_LOSS_USD:
        bad.append("daily loss bound is below the single-session bound")
    if PAIR_COST_CEILING >= D("1.00"):
        bad.append("pair cost ceiling is at or above par: near-$1 "
                   "tolerance is LOCKED")
    if MAX_FIRST_LEG_PRICE >= PAIR_COST_CEILING:
        bad.append("first-leg ceiling leaves no room for a complement "
                   "under the pair cost ceiling")
    if MAX_SUBMISSIONS_PER_SESSION != MAX_FIRST_LEGS + \
            MAX_COMPLEMENT_SUBMISSIONS:
        bad.append("submission count does not equal first legs + "
                   "complements")
    if COMPLEMENT_HANDOVER_TTE_S <= TTE_CUTOFF_S:
        bad.append("the handover TTE is at or below the withdrawal cutoff, "
                   "so the complement would get no patience at all")
    if COMPLEMENT_HANDOVER_TTE_S >= WINDOW_LEN_S:
        bad.append("the handover TTE is at or above the window length, so "
                   "the first leg would get no patience at all")
    if PER_LEG_PATIENCE_S * 2 != WINDOW_LEN_S - TTE_CUTOFF_S:
        bad.append("the per-leg patience split is not an equal division of "
                   "the placeable stretch")
    if MAX_OUTSTANDING_ORDERS != 1:
        bad.append("more than one outstanding order")
    if MAX_CONCURRENT_MARKETS != 1:
        bad.append("more than one concurrent market")
    if FIRST_LEG_QTY >= REBATE_CENT_FLOOR_SHARES and REBATE_IS_ASSUMED_ZERO:
        # not an error, but the reason for the assumption would have changed
        bad.append("first-leg qty is at or above the rebate cent floor, so "
                   "REBATE_IS_ASSUMED_ZERO no longer states a structural "
                   "fact and must be re-justified")
    if bad:
        raise SystemExit("STAGE-6-S6P bounds are incoherent: "
                         + "; ".join(bad))


def summary() -> Dict[str, Any]:
    """Every bound with its value, for the record and the protocol doc."""
    return {
        "first_leg_qty_shares": str(FIRST_LEG_QTY),
        "max_first_leg_price_usd": str(MAX_FIRST_LEG_PRICE),
        "max_first_leg_notional_usd": str(MAX_FIRST_LEG_NOTIONAL_USD),
        "pair_cost_ceiling_usd": str(PAIR_COST_CEILING),
        "min_locked_pair_gross_usd": str(MIN_LOCKED_PAIR_GROSS_USD),
        "max_residual_shares": str(MAX_RESIDUAL_SHARES),
        "max_residual_notional_usd": str(MAX_RESIDUAL_NOTIONAL_USD),
        "max_inventory_imbalance_shares":
            str(MAX_INVENTORY_IMBALANCE_SHARES),
        "max_possible_loss_usd": str(MAX_POSSIBLE_LOSS_USD),
        "max_daily_realized_loss_usd": str(MAX_DAILY_REALIZED_LOSS_USD),
        "max_total_strategy_exposure_usd":
            str(MAX_TOTAL_STRATEGY_EXPOSURE_USD),
        "max_concurrent_markets": MAX_CONCURRENT_MARKETS,
        "max_outstanding_orders": MAX_OUTSTANDING_ORDERS,
        "max_submissions_per_session": MAX_SUBMISSIONS_PER_SESSION,
        "max_first_legs": MAX_FIRST_LEGS,
        "max_complement_submissions": MAX_COMPLEMENT_SUBMISSIONS,
        "window_len_s": WINDOW_LEN_S,
        "tte_cutoff_s": TTE_CUTOFF_S,
        "complement_handover_tte_s": COMPLEMENT_HANDOVER_TTE_S,
        "per_leg_patience_s": PER_LEG_PATIENCE_S,
        "venue_tick": str(VENUE_TICK),
        "venue_min_qty": str(VENUE_MIN_QTY),
        "rebate_is_assumed_zero": REBATE_IS_ASSUMED_ZERO,
        "rebate_cent_floor_shares": str(REBATE_CENT_FLOOR_SHARES),
        "provenance": BOUND_PROVENANCE,
    }


assert_bounds_coherent()
