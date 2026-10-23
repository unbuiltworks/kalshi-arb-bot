"""Bounded defensive completion for an unmatched first leg.

TWO VERIFICATIONS THE OWNER REQUIRED BEFORE ANY OF THIS MAY BE ENABLED, AND
WHAT THEY FOUND. Both are reproducible from this repository.

1. THE SPECIFIED GUARD INTERVAL [T+660, T+720) IS UNREACHABLE. A BTC15m
   window has TWO completion clocks and the EARLIER binds:
   `complete_cutoff_s() == 720` and `cancel_cutoff(t_end) == t_end -
   cancel_margin_s()`, which at the deployed `L5_TS_CANCEL_MARGIN_S=300` is
   T+600. `may_complete` therefore returns PAST_TERMINAL at T+600 and
   beyond; production corroborates it (2315z logged PAST_TERMINAL 195
   times). So this module takes BOTH clocks and guards the last
   `DEFENSIVE_ARM_S` seconds before the BINDING one -- [T+540, T+600) at the
   deployed margin -- and cannot be handed the looser clock alone.

2. "BUY THE OPPOSITE SIDE" AND "SELL THE FIRST LEG" ARE THE SAME WIRE ORDER
   ON THIS VENUE, so the choice between them is not an economic choice and
   is not made by implementation convenience. Polymarket US is a
   single-instrument venue -- its own documentation titles the page "buying
   Yes vs selling No" -- and every order it has been observed to accept is
   `ORDER_INTENT_BUY_LONG` or `ORDER_INTENT_BUY_SHORT`; no reduce-only,
   sell-to-close or liquidate intent appears anywhere in its observed
   surface. The venue cashflow proves the identity rather than inferring it:
   Probe 05's eight `ORDER_INTENT_BUY_SHORT` orders CREDITED $1.2432, which
   is exactly their WIRE notional (sum of cumQuantity * avgPx over
   `analysis/pmus/evidence/probe05_orders.json`). A BUY_SHORT is executed as
   a SELL of the YES token and takes cash IN. Unwinding a long UP leg and
   buying DOWN against it are one order, one price, one fee.

   So the comparison this module makes is between the alternatives that DO
   differ here, and `alternatives()` reports all of them with their
   economics: keep working the profitable maker completion, unwind at the
   executable touch, or hold to settlement.

WHAT THIS IS FOR, AND WHAT IT IS NOT. 2315z filled a first leg at own 0.37
for $1.2543 and never obtained its complement; the residual settled
worthless and cost more than both winning pairs of that campaign combined.
This module decides whether to pay to get out of such a leg.

IT IS NOT AN EDGE, AND MUST NEVER BE SOLD AS ONE. At the moment of the
2315z decision the bid was 0.20, so the market priced the position at
0.20 * 3.39 = $0.6780 and the sale would have netted $0.5865. The whole
difference, -$0.0915, is the taker fee. A defensive exit converts an
uncertain loss into a certain one and pays a fee for the conversion. Its
justification is tail control: at $5 aggregate across eight windows one
stranded leg can consume the daily halt and block every remaining window.
`decide()` therefore reports `improves_expected_value: False` always, so no
caller and no reader can mistake variance reduction for profit.

THE TRIGGER IS TIME, NOT DISTANCE, AND DELIBERATELY SO. The contrast
between 2300z (frozen complement 4-8 ticks away, filled at 0.71, +$0.1875)
and 2315z (18-27 ticks away, never filled, -$1.2543) suggests distance
predicts completion. That is TWO OBSERVATIONS and no threshold is fitted to
it here. Instead the guard is the deterministic end of completion
eligibility: defensive action is considered only in the last
`DEFENSIVE_ARM_S` seconds before the completion cutoff the venue clock
already defines.

WHAT THE CONSERVATIVE CHOICE COSTS, STATED. Exiting 2315z at the first
opportunity (23:21:50, bid 0.20) would have saved $0.5865. Waiting for the
guard to open saves ~$0.28-0.32 instead. We give up most of the theoretical
saving to buy the guarantee that we never pre-empt a complement that still
has runway -- which, in 2300z, was worth +$0.1875 and would have been
destroyed by an eager rule.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional


#: the pair cost a defensive exit may not exceed. The profitable ceiling is
#: 0.96; this is the separate, looser bound that applies ONLY to unwinding.
#:
#: WHERE 1.20 COMES FROM, which is the economics and not these two windows.
#: Holding an unmatched leg risks its ENTIRE cost. Exiting at pair cost P
#: loses (P - 1.00). Exiting is therefore the bounded choice whenever
#: P < 1.00 + first_leg_cost. The 0.96 entry ceiling means a first leg can
#: cost at most 0.96, so the weakest case this can face is a very cheap
#: first leg -- and `decide()` re-derives that bound per position rather
#: than trusting this constant alone.
DEFENSIVE_PAIR_CEILING = D("1.20")

#: campaign-wide realized defensive loss budget, inside the $5 daily halt.
DEFENSIVE_LOSS_BUDGET_USD = D("2.00")

#: how long before the BINDING completion terminal defensive action may be
#: considered. 60s of a 900s window. Short on purpose: see the docstring.
DEFENSIVE_ARM_S = 60.0

#: HOW LONG THE COMPLEMENT IS GIVEN before the bounded exit may act, measured
#: from the moment the leg became unmatched -- NOT from the window start, and
#: NOT as a slot near the terminal.
#:
#: WHY A DWELL REPLACES THE ARM WINDOW. `DEFENSIVE_ARM_S` with
#: `DEFENSIVE_MIN_EXECUTION_S` leaves a 45-SECOND actionable interval
#: ([T+540, T+585) at the deployed margin), and the executor's evaluation
#: cadence is irregular -- 32s, 63s and 131s gaps were all observed in one
#: production window. On 2026-10-23 the 0230z window happened to tick at
#: T+544 and exited; 0300z stepped T+524 -> T+587 and straddled the whole
#: interval, so the leg went to settlement for the full -$0.3580. A policy
#: whose trigger is narrower than its own sampling jitter fires by luck.
#:
#: WHY 30s. Accepted COMPLETEs that fill rest a median 8.61s against 30.85s
#: for those that die unfilled (`analysis/l5/COMPLETION_ECONOMICS.md`), so by
#: 30s a complement that was going to fill has overwhelmingly filled. The
#: held-out sweep is flat from 15s to 120s -- 1.6%-2.7% of windows below
#: -0.30/unit against 20.0% for the arm window -- so this is not a knife
#: edge. See `analysis/l5/STRANDED_LEG_RECOVERY.md`.
DEFENSIVE_DWELL_S = 30.0

#: the minimum time that must remain before the terminal for the defensive
#: SEQUENCE to run: cancel the resting complement, re-read it at the venue to
#: CONFIRM it is gone, then submit one order. Starting a cancel we cannot
#: finish is the one way this policy could leave a position worse than it
#: found it -- complement retired, nothing in its place. This is what makes
#: the answer to "does the final-minute guard provide enough execution time"
#: a check rather than an opinion: the effective action window at the
#: deployed margin is [T+540, T+585).
DEFENSIVE_MIN_EXECUTION_S = 15.0

#: the profitable maker ceiling, which this module never relaxes. Priority
#: one is still completion at or under it; defensive action is considered
#: only once that completion is no longer reachable at the live book.
MAKER_COMPLETE_CEILING = D("0.96")

#: The one wire order this venue has for unwinding, per verification 2. The
#: `side` is about the YES token; `outcomeSide` names the outcome you end up
#: long. Buying the complement of a held leg IS selling that leg.
DEFENSIVE_WIRE = {
    "UP": {"intent": "ORDER_INTENT_BUY_LONG", "side": "ORDER_SIDE_BUY",
           "outcomeSide": "OUTCOME_SIDE_YES"},
    "DOWN": {"intent": "ORDER_INTENT_BUY_SHORT", "side": "ORDER_SIDE_SELL",
             "outcomeSide": "OUTCOME_SIDE_NO"},
}


#: THE FEATURE FLAG. Absent, empty, misspelled or unreadable is OFF. Only
#: the exact strings below enable it, so a typo cannot half-arm a policy
#: that spends money, and the default on every deployment is disabled.
DEFENSIVE_ENABLED_ENV = "L5_DEFENSIVE_COMPLETION"
_TRUE = ("1", "yes", "true", "on")


def enabled() -> bool:
    import os
    try:
        return (os.getenv(DEFENSIVE_ENABLED_ENV) or "").strip().lower() \
            in _TRUE
    except BaseException:                                  # noqa: BLE001
        return False


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def binding_terminal_s(complete_cutoff_s: Any,
                       cancel_cutoff_s: Any = None) -> Optional[float]:
    """The EARLIER of the window's two completion clocks, in window seconds.

    VERIFICATION 1 LIVES HERE. `may_complete` refuses on whichever of
    `complete_cutoff_s()` (720) and `cancel_cutoff(t_end)` (600 at the
    deployed margin of 300) comes first, so a guard built on the first alone
    would arm in an interval where every order is already PAST_TERMINAL.
    Taking the minimum means a caller cannot arm this policy outside the
    interval the engine can actually act in, whatever the margin is set to.

    Unreadable refuses by returning None, which shuts the window.
    """
    vals = []
    for v in (complete_cutoff_s, cancel_cutoff_s):
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            return None
    return min(vals) if vals else None


def arm_window_open(now_t: float, window_start: float,
                    complete_cutoff_s: float,
                    arm_s: float = DEFENSIVE_ARM_S,
                    cancel_cutoff_s: Any = None) -> bool:
    """Is `now` inside the last `arm_s` seconds of completion eligibility?

    Measured against the BINDING terminal, not the looser of the two clocks.
    Closed at the terminal: past it `may_complete` refuses PAST_TERMINAL, so
    claiming otherwise would invent a fill.
    """
    try:
        off = float(now_t) - float(window_start)
    except (TypeError, ValueError):
        return False
    cut = binding_terminal_s(complete_cutoff_s, cancel_cutoff_s)
    if cut is None:
        return False
    return (cut - float(arm_s)) <= off < cut


def decide(*,
           now_t: float,
           window_start: float,
           complete_cutoff_s: float,
           unmatched_qty: Any,
           first_leg_cost: Any,
           exit_price: Any,
           displayed_depth: Any,
           taker_fee_per_contract: Any,
           cancel_cutoff_s: Any = None,
           maker_complement_price: Any = None,
           buy_side: Optional[str] = None,
           defensive_spent_usd: Any = D(0),
           defensive_pending: bool = False,
           window_settled: bool = False,
           resting_complement: Optional[Mapping[str, Any]] = None,
           pair_ceiling: D = DEFENSIVE_PAIR_CEILING,
           loss_budget_usd: D = DEFENSIVE_LOSS_BUDGET_USD,
           arm_s: float = DEFENSIVE_ARM_S,
           min_execution_s: float = DEFENSIVE_MIN_EXECUTION_S,
           stranded_since_t: Any = None,
           dwell_s: float = DEFENSIVE_DWELL_S,
           simulate: bool = False,
           ) -> Dict[str, Any]:
    """Should we pay to unwind this unmatched leg, and at what bounded cost?

    `exit_price` is the OWN price of the complementary side as the book
    actually shows it -- the executable price, not a maker price we hope to
    rest at. Every refusal is named. Nothing here submits, cancels or
    promises a fill.
    """
    out: Dict[str, Any] = {
        "act": False, "reason": None,
        # never, under any inputs. See the module docstring.
        "improves_expected_value": False,
        "labels": ("a defensive exit bounds a tail, it does not create edge: "
                   "at the touch it is expected-value neutral before fees "
                   "and negative by exactly the fee. A displayed size is NOT "
                   "a fill and this result never reports a position as "
                   "hedged -- only a venue-confirmed fill does that."),
    }

    # SIMULATION BYPASSES THE LIVE FLAG AND SAYS SO. The shadow must be
    # able to measure this policy while live submission stays disabled --
    # that is the entire point of measuring it first. `simulated: True`
    # rides every such verdict so no reader can mistake a modelled exit
    # for one that happened.
    if simulate:
        out["simulated"] = True
    elif not enabled():
        out["reason"] = "DEFENSIVE_DISABLED"
        return out

    # ONE OUTSTANDING ORDER. A defensive order already working owns the
    # slot; a second would breach the limit and could double the position
    # if both fill.
    if defensive_pending:
        out["reason"] = "DEFENSIVE_ALREADY_PENDING"
        return out

    # SETTLEMENT WHILE AN ORDER IS PENDING. Once the window is terminal the
    # venue will not complete anything; acting here would invent a fill and
    # the residual is already decided.
    if window_settled:
        out["reason"] = "WINDOW_ALREADY_SETTLED"
        return out

    qty = _d(unmatched_qty)
    leg = _d(first_leg_cost)
    px = _d(exit_price)
    fee = _d(taker_fee_per_contract)
    spent = _d(defensive_spent_usd)
    if qty is None or leg is None or px is None or fee is None \
            or spent is None:
        out["reason"] = "DEFENSIVE_INPUT_UNREADABLE"
        return out
    if qty <= 0:
        out["reason"] = "NO_UNMATCHED_INVENTORY"
        return out
    if leg <= 0 or px <= 0 or fee < 0 or spent < 0:
        out["reason"] = "DEFENSIVE_INPUT_IMPLAUSIBLE"
        return out

    terminal = binding_terminal_s(complete_cutoff_s, cancel_cutoff_s)
    out["binding_terminal_s"] = terminal
    # THE TIMING GATE. Supplying `stranded_since_t` selects the DWELL gate --
    # continuous from `dwell_s` after the leg became unmatched all the way to
    # the terminal. Omitting it keeps the original arm window, so no existing
    # caller changes behaviour by upgrading this module.
    #
    # The dwell does not weaken any economic gate below. It only stops the
    # 45-second arm window from deciding, by sampling luck, whether the
    # position that most needs the exit is allowed to have one.
    if stranded_since_t is None:
        if not arm_window_open(now_t, window_start, complete_cutoff_s, arm_s,
                               cancel_cutoff_s=cancel_cutoff_s):
            out["reason"] = "OUTSIDE_DEFENSIVE_ARM_WINDOW"
            return out
        out["timing_gate"] = "ARM_WINDOW"
    else:
        try:
            held_s = float(now_t) - float(stranded_since_t)
        except (TypeError, ValueError):
            out["reason"] = "DEFENSIVE_INPUT_UNREADABLE"
            return out
        out["timing_gate"] = "DWELL"
        out["stranded_for_s"] = round(held_s, 3)
        out["dwell_s"] = float(dwell_s)
        if held_s < 0:
            out["reason"] = "DEFENSIVE_INPUT_IMPLAUSIBLE"
            return out
        # A STRICT SUPERSET OF THE OLD GATE. The dwell ADDS the interval the
        # arm window could not reach; it never takes one away, so no position
        # that would have been exited before is left carrying now.
        if held_s < float(dwell_s) and not arm_window_open(
                now_t, window_start, complete_cutoff_s, arm_s,
                cancel_cutoff_s=cancel_cutoff_s):
            out["reason"] = "COMPLEMENT_STILL_WITHIN_DWELL"
            return out
        if terminal is None:
            out["reason"] = "DEFENSIVE_INPUT_UNREADABLE"
            return out

    # ENOUGH TIME TO FINISH WHAT WE START. The sequence is cancel ->
    # venue re-read -> submit, and a cancel we cannot follow with a
    # submission retires the profitable complement and puts nothing in its
    # place. Refusing here leaves the complement working to the deadline,
    # which is the state we would otherwise have been in anyway.
    try:
        remaining = float(terminal) - (float(now_t) - float(window_start))
    except (TypeError, ValueError):
        out["reason"] = "DEFENSIVE_INPUT_UNREADABLE"
        return out
    out["remaining_s"] = round(remaining, 3)
    if remaining < float(min_execution_s):
        out["reason"] = "INSUFFICIENT_EXECUTION_TIME"
        out["min_execution_s"] = float(min_execution_s)
        return out

    # PRIORITY ONE IS STILL THE PROFITABLE COMPLETION, and it is checked
    # against the LIVE book rather than against hope. A maker complement at
    # or inside the executable price needs no favour from the market: it can
    # fill now, at a pair cost under the 0.96 ceiling, for a PROFIT. Paying
    # the taker fee to unwind instead would be strictly worse, so defensive
    # action is refused while that is true. (`exit_price` is the executable
    # own price; a maker limit at least that aggressive is reachable.)
    mk = _d(maker_complement_price)
    if mk is not None and mk > 0:
        out["maker_complement_price"] = str(mk)
        out["maker_pair_cost"] = str(leg + mk)
        out["maker_reachable_now"] = bool(mk >= px)
        if (leg + mk) <= MAKER_COMPLETE_CEILING and mk >= px:
            out["reason"] = "MAKER_COMPLETION_STILL_REACHABLE"
            return out

    # A PROFITABLE COMPLEMENT STILL RESTING OWNS THE POSITION. It may fill,
    # and in 2300z it did. It is displaced only by a cancellation the venue
    # has CONFIRMED -- an unconfirmed cancel plus a defensive order is how
    # both fill and the position doubles instead of closing.
    if resting_complement:
        if not bool(resting_complement.get("cancel_confirmed")):
            out["reason"] = "RESTING_COMPLEMENT_NOT_CONFIRMED_CANCELLED"
            out["resting_complement"] = {
                "venue_order_id": resting_complement.get("venue_order_id"),
                "cancel_confirmed": False,
            }
            return out

    pair_cost = leg + px
    fees_usd = qty * fee
    # what the two courses can cost us, in dollars, worst case each
    hold_downside = qty * leg                 # our side loses: lose it all
    exit_downside = qty * (pair_cost - D(1)) + fees_usd

    # THE SAME NUMBERS IN THE VENUE'S OWN TERMS, because "pair cost" is our
    # ledger's vocabulary and this venue does not buy a second token: it
    # SELLS the one we hold, at the wire price, and credits the proceeds
    # (verification 2). The two views are arithmetically identical --
    # proceeds = 1 - exit_price and loss/contract = pair_cost - 1 -- and
    # both are reported so neither reader has to convert.
    proceeds = D(1) - px
    loss_per_contract = pair_cost - D(1)
    out.update({
        "qty": str(qty), "first_leg_cost": str(leg),
        "exit_price": str(px), "pair_cost": str(pair_cost),
        "fees_usd": str(fees_usd),
        "hold_downside_usd": str(hold_downside),
        "exit_downside_usd": str(exit_downside),
        "sale_proceeds_per_contract": str(proceeds),
        "loss_per_contract": str(loss_per_contract),
        "ceiling_as_loss_per_contract": str(pair_ceiling - D(1)),
        "venue_action": ("ONE wire order. On a single-instrument venue "
                         "buying the complement of a held leg IS selling "
                         "that leg: there is no reduce-only or "
                         "sell-to-close intent to choose instead."),
    })
    if buy_side in DEFENSIVE_WIRE:
        out["wire"] = dict(DEFENSIVE_WIRE[buy_side], buy_side=buy_side,
                           own_limit_price=str(px))
    out["alternatives"] = alternatives(
        qty=qty, first_leg_cost=leg, exit_price=px, fee=fee,
        maker_complement_price=mk)

    # A CHECK THAT WAS HERE AND IS GONE, because it could never fire and
    # reading it would have implied a protection that does not exist.
    # "Exit only while pair_cost < 1 + first_leg_cost" reduces, after
    # substituting pair_cost = first_leg_cost + exit_price, to
    # `exit_price < 1.00` -- true of every valid binary price. Worst-case
    # comparison ALWAYS favours exiting: holding risks the whole leg while
    # exiting recovers (1 - exit_price) per contract, so the exit wins
    # whenever that recovery beats the fee. `DOES_NOT_REDUCE_BOUNDED_
    # DOWNSIDE` below still states the comparison and will refuse the
    # fee-dominated corner, but it is NOT the binding protection and must
    # not be mistaken for one.
    #
    # WHAT ACTUALLY PROTECTS US is therefore the arm window above (do not
    # pre-empt a complement that still has runway), this ceiling, the
    # budget, and the depth test. The 2300z case is caught by the clock,
    # not by any economic comparison -- every economic comparison would
    # have told us to exit, and exiting would have cost -$0.5137.
    if pair_cost > pair_ceiling:
        out["reason"] = "DEFENSIVE_PAIR_CEILING"
        return out
    if exit_downside >= hold_downside:
        out["reason"] = "DOES_NOT_REDUCE_BOUNDED_DOWNSIDE"
        return out

    if spent + exit_downside > loss_budget_usd:
        out["reason"] = "DEFENSIVE_LOSS_BUDGET"
        out["budget_remaining_usd"] = str(loss_budget_usd - spent)
        return out

    # LIQUIDITY. A displayed size is the only evidence we have and it is not
    # a promise; unreadable depth refuses rather than assuming the book is
    # deep. The ratio is reported so a reader can judge the claim.
    depth = _d(displayed_depth)
    if depth is None:
        out["reason"] = "DEPTH_UNREADABLE"
        return out
    if depth < qty:
        out["reason"] = "INSUFFICIENT_DISPLAYED_DEPTH"
        out["depth_ratio"] = str(depth / qty) if qty else None
        return out
    out["depth_ratio"] = str((depth / qty).quantize(D("0.1")))

    out["act"] = True
    out["reason"] = "DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"
    out["saves_vs_holding_usd"] = str(hold_downside - exit_downside)
    return out


# --------------------------------------------------- the real alternatives

def alternatives(*, qty: D, first_leg_cost: D, exit_price: D, fee: D,
                 maker_complement_price: Optional[D] = None,
                 ) -> list:
    """Every course actually open to us at this instant, with its economics.

    WHY THIS IS THE COMPARISON AND NOT "BUY VERSUS SELL". Verification 2
    established that on this venue those two are the same wire order at the
    same price for the same fee, so choosing between them is not an economic
    decision and no amount of code would make it one. What genuinely differs
    is whether we keep working the profitable completion, unwind at the
    executable price, or hold the leg to settlement -- so those are what is
    priced here, in dollars, side by side.

    `worst_usd` is what the course can cost under the settlement that hurts;
    `best_usd` under the other. A certain course has one number for both.
    Nothing here is a fill and nothing here is a promise of one.
    """
    rows = [{
        "action": "HOLD_TO_SETTLEMENT",
        "wire": None,
        "executable_now": True,
        "certain": False,
        # our side settles 0: the whole leg is gone. Settles 1: it pays $1.
        "worst_usd": str(-(qty * first_leg_cost)),
        "best_usd": str(qty * (D(1) - first_leg_cost)),
        "note": ("the course 2315z actually took. It is not a decision that "
                 "was made; it is what happens when nothing else does."),
    }]
    if maker_complement_price is not None and maker_complement_price > 0:
        mpair = first_leg_cost + maker_complement_price
        rows.append({
            "action": "MAKER_COMPLETE_AT_CEILING",
            "wire": "same single order, posted rather than crossing",
            "executable_now": bool(maker_complement_price >= exit_price),
            "certain": False,
            "limit_own_price": str(maker_complement_price),
            "pair_cost": str(mpair),
            "within_maker_ceiling": bool(mpair <= MAKER_COMPLETE_CEILING),
            # a maker pair pays $1 and costs `mpair`, with no taker fee
            "worst_usd": str(qty * (D(1) - mpair)),
            "best_usd": str(qty * (D(1) - mpair)),
            "note": ("priority one. Worth more than any unwind whenever it "
                     "is reachable, which is why `decide` refuses defensive "
                     "action while it is."),
        })
    unwind = qty * (D(1) - (first_leg_cost + exit_price)) - qty * fee
    rows.append({
        "action": "DEFENSIVE_UNWIND",
        "wire": "ORDER_INTENT_BUY_LONG/BUY_SHORT at the executable price",
        "executable_now": True,
        "certain": True,
        "limit_own_price": str(exit_price),
        "proceeds_per_contract": str(D(1) - exit_price),
        "fees_usd": str(qty * fee),
        "worst_usd": str(unwind),
        "best_usd": str(unwind),
        "note": ("converts an uncertain loss into a certain one and pays "
                 "the taker fee for the conversion. Chosen for tail "
                 "control, never because it is better in expectation."),
    })
    return rows


# ------------------------------------------------- confirmed cancellation

def cancel_confirmed(result: Mapping[str, Any], coid: str) -> Dict[str, Any]:
    """Did the venue PROVE our resting complement is gone?

    Reads a `deadman()` result. The bar is the venue's own re-read, not a
    cancel ack -- `deadman` already classifies by `GET /v1/order/{id}` and
    documents that "cancelled means the VENUE reported a terminal state on
    re-read, never that a cancel call returned".

    EVERY AMBIGUOUS BUCKET REFUSES. `still_open` is the dangerous one: the
    cancel returned but the order is still working, so placing a defensive
    order beside it is how BOTH fill and the position doubles instead of
    closing. `unverified` and `no_venue_id` are unknowns, and an unknown
    resting order is treated exactly like a live one.

    A FILL BOOKED DURING THE CANCEL SHRINKS THE RESIDUAL. `deadman` books
    it with its cost basis; the defensive order must then be sized to what
    is left, not to what was unmatched before the cancel.
    """
    out: Dict[str, Any] = {
        "confirmed": False, "reason": None, "coid": coid,
        "filled_during_cancel": "0",
        "labels": ("a cancel ack is not proof. Only the venue's own re-read "
                   "retires an order, and any ambiguity is treated as still "
                   "resting."),
    }
    if not isinstance(result, Mapping):
        out["reason"] = "CANCEL_RESULT_UNREADABLE"
        return out

    def _ids(bucket: str) -> set:
        rows = result.get(bucket) or []
        if not isinstance(rows, (list, tuple)):
            return {"__unreadable__"}
        got = set()
        for r in rows:
            if isinstance(r, Mapping):
                got.add(str(r.get("client_order_id")))
            else:
                got.add("__unreadable__")
        return got

    for bad in ("still_open", "unverified", "no_venue_id"):
        if coid in _ids(bad) or "__unreadable__" in _ids(bad):
            out["reason"] = "CANCEL_NOT_CONFIRMED_" + bad.upper()
            return out
    # AN ERROR THE RE-READ SETTLED IS NOT AN UNKNOWN. `deadman` records a
    # cancel that raised even when the subsequent `GET /v1/order/{id}` came
    # back terminal, and stamps `resolved_by_reread` -- which is the COMMON
    # case, because cancelling an order the venue has already retired
    # raises. Refusing on that would have made this policy unusable in
    # exactly the situation it exists for. An error for OUR order that the
    # re-read did not settle still refuses, and so does an unreadable row;
    # another order's error says nothing about ours (and `only=[coid]`
    # means there should not be one).
    for r in (result.get("errors") or []):
        if not isinstance(r, Mapping):
            out["reason"] = "CANCEL_REPORTED_ERRORS"
            return out
        if str(r.get("client_order_id")) == coid \
                and not bool(r.get("resolved_by_reread")):
            out["reason"] = "CANCEL_REPORTED_ERRORS"
            return out

    for r in (result.get("filled") or []):
        if isinstance(r, Mapping) and str(r.get("client_order_id")) == coid:
            out["filled_during_cancel"] = str(r.get("qty") or "0")

    if coid not in _ids("cancelled"):
        out["reason"] = "CANCEL_NOT_REPORTED_FOR_THIS_ORDER"
        return out

    out["confirmed"] = True
    out["reason"] = "CANCEL_CONFIRMED_BY_VENUE_REREAD"
    return out
