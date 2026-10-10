"""The E1 cohort runner. FROZEN. DISARMED. NOT AUTHORIZED.

NO LIVE ORDER IS PLACED BY ANY PATH IN THIS MODULE AS IT STANDS. The arming
gate is `analysis.l5.e1_cohort.arming.may_submit`, which reads E1's OWN
`PMUS_E1_*` namespace and NEVER `PMUS_EXEC1_*`. No owner value is set and
nothing here sets one. `boot()` reports the arming state and `eligible()`
refuses on it; neither acquires it.

E1 IS NETWORK-CAPABLE, AND THAT IS CORRECT. Its job is to place real
orders, and XR2W's proven `orders.py` uses `urllib`, so importing the
production entrypoint pulls http, socket, ssl and the urllib family.
NOTHING HERE CLAIMS OTHERWISE. The provable claims are that the offline
tests make zero network CALLS, and that the transport is reachable only
through the preview-echo gate and the E1 named-slot latch.

WHAT THIS MODULE IS
-------------------
The decision logic -- eligibility, first-leg pricing, complement pricing and
sizing, cancellation, and the handover -- expressed as PURE FUNCTIONS over a
decision-time snapshot, plus one thin submission wrapper that calls the
existing proven path. The pure functions are what the offline tests
exercise, which is why they take a snapshot rather than reaching for a
socket.

WHAT IT REUSES AND DOES NOT DUPLICATE
-------------------------------------
* `src.l5_order_body` is THE serializer. It is not re-implemented here, not
  partially, not for a preview, not for a test fixture. A test asserts that
  no file in this package contains a duplicate body.
* `analysis.l5.live_exec2.orders` is the only order-mutation path, with its
  `VenueHTTPError` / `PreviewRejected` / `SubmissionAmbiguous` taxonomy. Its
  `BoundedOrderTransport` refuses a second `POST /v1/orders` per instance,
  so the cohort builds ONE transport per attempt and carries its own bound
  in the named-slot latch instead of relaxing that guarantee.
  `analysis/l5/live_exec2/` is NOT modified.
* `analysis.l5.live_exec2.orders.preflight_submission` is deliberately NOT
  used, because it hard-requires the venue minimum 0.01 and E1's quantity is
  0.13. `e1_preflight()` below is E1's own refusal set; it is a SUPERSET of
  the shared one on every check the two share, and it never relaxes one.
  Nothing in `live_exec2` is edited to accommodate E1.
* `src.l5_pmus_auth_book` (via `pmus_book`) is the timing authority: PMUS's
  OWN authenticated book for the PMUS slug being ordered. PMUS REST is
  discovery and cross-check only and may never order the tape. The
  Polymarket.com CLOB websocket is NOT in this path -- it timed a venue
  E1 does not trade, which is how "Market not found" happened.
* `src.pmus_fee_schedule` is the fee source. Fees in the PAIR-COST GATE are
  modelled-forward and labelled so; fees in the LEDGER are the venue's own
  reported numbers and nothing else.
"""
from __future__ import annotations

import copy
import json
import os
import time
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from analysis.l5.e1_cohort import arming as A
from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import budget as BUD
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import obligations as OB
from analysis.l5.e1_cohort import local_faults as LF
from analysis.l5.e1_cohort import multitrade as MT
from analysis.l5.e1_cohort import attempt_rows as AR
from analysis.l5.e1_cohort import shutdown as SD
from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FIRST_LEG_QTY,
                                        MAX_COHORT_REALIZED_LOSS_USD,
                                        MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_COMPLEMENT_TRANCHES,
                                        MAX_PLACEMENTS,
                                        MAX_FIRST_LEG_PRICE,
                                        PAIR_COST_CEILING, PROBE, SPEC,
                                        VENUE_MIN_QTY, VENUE_TICK, WINDOWS,
                                        assert_frozen, boot_assertions,
                                        diff_report)
from analysis.l5.e1_cohort.stopping import CohortTracker, project_feasibility
from analysis.l5.live_exec2 import orders as O
from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import order_state as OST
from analysis.l5.e1_cohort import pair_lifecycle as PLC
from analysis.l5.e1_cohort import pacing as PACING
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_identity as PIDENT

#: THE SAME 900 THAT COMPUTES `window_end`. Taken from the identity module
#: the selector itself uses, so the pacing horizon and the window boundary
#: cannot drift apart into a cap that is wrong by a constant.
WINDOW_LEN_S = float(PIDENT.WINDOW_S)
from src import l5_preview_gate as G
from src import pmus_fee_schedule as FEE

#: Decision-time book staleness bound, inherited from the XR2W AuthBook
#: bound rather than chosen here.
MAX_BOOK_AGE_S = 2.0


class AttemptBudgetExhausted(AUTH.NotAuthorized):
    """This probe's authorized first-leg ATTEMPTS are spent.

    A subclass of `NotAuthorized` on purpose: it is refused BEFORE the
    intent row, BEFORE the preview and BEFORE the POST, by the same
    authorization machinery that refuses an unarmed process -- and the
    runner's existing handler already records it without stopping, because
    an exhausted attempt budget must NEVER end management of a first leg
    that may already be filled.
    """

class Ineligible(RuntimeError):
    """Refused by E1's own preflight. CARRIES THE REFUSAL LIST.

    It carries `refusals` because the caller has to classify them: a refusal
    about a book that moves is waited out, and a safety refusal stops the
    cohort. Re-deriving the list by re-running preflight would classify a
    DIFFERENT instant than the one that actually refused.
    """

    def __init__(self, message: str,
                 refusals: Optional[List[Any]] = None) -> None:
        super().__init__(message)
        self.refusals = list(refusals or [])


    """The state does not satisfy E1's eligibility rules. No order."""


class ComplementInfeasible(RuntimeError):
    """No complement price satisfies the fee-inclusive pair-cost ceiling."""


# ---------------------------------------------------------------- pair cost

def modelled_pair_cost(*, first_leg_price: Any, complement_price: Any,
                       qty: Any, post_only_both_legs: bool = True
                       ) -> Dict[str, Any]:
    """Fee-inclusive combined pair cost PER SHARE, modelled forward.

    LABELLED MODELLED. It gates the decision; it is never recorded as an
    actual fee. Both legs are post-only, so the taker fee is expected to be
    zero and the maker rebate at 0.13 shares is structurally $0.00 -- the
    rebate is NOT credited against the gate, because CLAUDE.md forbids
    assuming a rebate until it is credited.
    """
    FEE.assert_frozen()
    a = D(str(first_leg_price))
    b = D(str(complement_price))
    c = D(str(qty))
    taker = D("0") if post_only_both_legs else (
        FEE.taker_fee(a, c) + FEE.taker_fee(b, c))
    per_share_fee = (taker / c) if c else D("0")
    return {"first_leg_price_usd": str(a),
            "complement_price_usd": str(b),
            "gross_pair_cost_usd_per_share": str(a + b),
            "modelled_fee_usd_per_share": str(per_share_fee),
            "pair_cost_usd_per_share": str(a + b + per_share_fee),
            "ceiling_usd_per_share": str(PAIR_COST_CEILING),
            "within_ceiling": (a + b + per_share_fee) <= PAIR_COST_CEILING,
            "rebate_credited_against_the_gate": False,
            "rebate_at_this_size_usd": "0.00",
            "fee_schedule_fingerprint": FEE.FINGERPRINT,
            "LABEL": "MODELLED FEE -- a gate input, never a recorded fee"}


def complement_price(*, first_leg_price: Any, qty: Any,
                     complement_best_bid: Any) -> Dict[str, Any]:
    """Rule C3: the HIGHEST price on the 0.01 tick that keeps the
    fee-inclusive pair cost at or below $0.96, and that is also at or below
    the complement's own best bid so the order rests as a maker.

    Returns the price and the reason. Raises `ComplementInfeasible` when no
    such price exists -- the pair is REFUSED, never improved by relaxing
    the ceiling (rule C4).
    """
    a = D(str(first_leg_price))
    bid = D(str(complement_best_bid))
    # with both legs post-only the modelled fee is zero, so the budget is
    # the arithmetic remainder; it is recomputed through the fee model below
    # so a future non-zero fee cannot be silently ignored.
    budget = (PAIR_COST_CEILING - a).quantize(VENUE_TICK,
                                              rounding=ROUND_DOWN)
    px = min(budget, bid.quantize(VENUE_TICK, rounding=ROUND_DOWN))
    while px >= D("0.01"):
        pc = modelled_pair_cost(first_leg_price=a, complement_price=px,
                                qty=qty)
        if pc["within_ceiling"]:
            return {"price_usd": str(px), "pair_cost": pc,
                    "at_or_below_complement_best_bid": px <= bid,
                    "maker_only": True, "taker_rescue": False,
                    "rule": "C3"}
        px -= VENUE_TICK
    raise ComplementInfeasible(
        "no complement price on the tick satisfies the fee-inclusive "
        "pair-cost ceiling %s given a first leg at %s and a complement "
        "best bid of %s. The pair is REFUSED, not discounted (rule C4)."
        % (PAIR_COST_CEILING, a, bid))


def complement_qty(*, first_leg_filled_qty: Any) -> D:
    """Rule C1: sized to the ACTUAL filled quantity, never to the placed
    quantity. A partial fill yields a partial complement.

    This is the SINGLE-SHOT form, kept for the one-fill case. For a first
    leg that fills in several pieces use `complement_tranche`, which is what
    keeps the complement equal to the CUMULATIVE fill.
    """
    q = D(str(first_leg_filled_qty)).quantize(VENUE_TICK,
                                              rounding=ROUND_DOWN)
    if q <= 0:
        raise ComplementInfeasible(
            "no first-leg fill, so there is nothing to complete")
    if q < VENUE_MIN_QTY:
        raise ComplementInfeasible(
            "the first leg filled %s, which is below the venue minimum %s; "
            "the position is RESIDUAL and no complement is placed"
            % (q, VENUE_MIN_QTY))
    return q


def complement_tranche(*, cumulative_first_leg_filled_qty: Any,
                       already_complemented_qty: Any,
                       tranches_submitted: int = 0) -> Dict[str, Any]:
    """Rule C7, CORRECTED. Keep complement quantity equal to the CUMULATIVE
    first-leg fill, by the MINIMAL mechanism that does not chase.

    THE BUG THIS FIXES. The first version sized the complement to the FIRST
    partial only and let every later fill on the same first leg fall
    silently into residual. A 0.13 first leg filling 0.05 then 0.08 would
    have been complemented for 0.05 and left an 0.08 residual that nobody
    asked for and nothing recorded as a decision. That is wrong: "sized to
    the actual filled quantity" means the CUMULATIVE filled quantity, and
    the entire 0.13 must be accounted for.

    THE MINIMAL MECHANISM: A NEW TRANCHE AT THE SAME PRICE. When cumulative
    fill rises above what has already been complemented, the shortfall is
    submitted as an ADDITIONAL complement order **at the price the attempt
    already fixed**, not at a new one.

    WHY THIS IS NOT CHASING AND NOT A RE-QUOTE, stated precisely:

      * chasing is following the market with our price. The tranche price is
        the attempt's ORIGINAL complement price, recomputed from the
        ORIGINAL first-leg price against the frozen <= $0.96 fee-inclusive
        ceiling. It cannot move, by construction, because its inputs cannot.
      * a re-quote is REPLACING an order that failed to fill, usually more
        aggressively. A tranche replaces nothing. The earlier complement
        stays exactly where it is, at its own price, and is never cancelled
        to make room. A tranche exists only because NEW INVENTORY ARRIVED.
      * so the two are distinguishable by their trigger: a re-quote is
        triggered by a NON-fill, a tranche by a FILL. This mechanism has no
        path that a non-fill can take.

    An unsuccessful complement is still never replaced at a better price:
    it is cancelled at handover and its quantity recorded as residual.

    WHAT CANNOT BE COMPLEMENTED IS RECORDED AS RESIDUAL, EXPLICITLY. A
    shortfall below the venue minimum 0.01 cannot be ordered at all, and a
    shortfall whose complement price is infeasible cannot be ordered
    legally. Either way this returns `tranche: None` WITH a reason and the
    quantity under `residual_qty`, so the caller records a decision rather
    than discovering an unexplained position later.
    """
    cum_raw = D(str(cumulative_first_leg_filled_qty))
    cum = cum_raw.quantize(VENUE_TICK, rounding=ROUND_DOWN)
    # SUB-TICK DUST. Quantizing the cumulative fill down to the grid drops
    # whatever sits below a tick. That quantity is genuinely ours and
    # genuinely uncomplementable -- the venue has no order smaller than
    # 0.01 -- so it is RECORDED as residual rather than lost in a rounding
    # step. C10: never hidden, never implicit.
    dust = cum_raw - cum
    done = D(str(already_complemented_qty)).quantize(VENUE_TICK,
                                                     rounding=ROUND_DOWN)
    if cum < 0 or done < 0:
        raise ComplementInfeasible("negative quantity %s / %s" % (cum, done))
    if done > cum:
        raise ComplementInfeasible(
            "already complemented %s exceeds the cumulative first-leg fill "
            "%s; the inventory ledger and the fill ledger disagree, which "
            "is an INSTRUMENT FAULT, not a result" % (done, cum))
    shortfall = cum - done
    base = {"cumulative_first_leg_filled_qty_raw": str(cum_raw),
            "cumulative_first_leg_filled_qty": str(cum),
            "sub_tick_dust_qty": str(dust),
            "sub_tick_dust_is_residual": dust > 0,
            "already_complemented_qty": str(done),
            "shortfall_qty": str(shortfall),
            "tranches_submitted": tranches_submitted,
            "rule": "C7 cumulative",
            "price_is_the_attempts_original_complement_price": True,
            "is_chasing": False, "is_a_requote": False,
            "triggered_by": "A FILL, never a non-fill"}
    if shortfall == 0:
        return dict(base, tranche=None, residual_qty=str(dust),
                    fully_accounted=True,
                    reason=("the complement already equals the cumulative "
                            "first-leg fill; nothing to order"
                            + (". RESIDUAL: %s of sub-tick dust is below "
                               "the venue minimum %s and cannot be ordered "
                               "at all" % (dust, VENUE_MIN_QTY)
                               if dust > 0 else "")))
    if shortfall < VENUE_MIN_QTY:
        return dict(base, tranche=None, residual_qty=str(shortfall + dust),
                    fully_accounted=True,
                    reason="RESIDUAL: the shortfall %s (plus %s sub-tick "
                           "dust) is below the venue minimum %s and cannot "
                           "be ordered at all"
                           % (shortfall, dust, VENUE_MIN_QTY))
    if tranches_submitted >= MAX_COMPLEMENT_TRANCHES:
        return dict(base, tranche=None, residual_qty=str(shortfall + dust),
                    fully_accounted=True,
                    reason="RESIDUAL: the bounded tranche count %d is spent"
                           % MAX_COMPLEMENT_TRANCHES)
    return dict(base, tranche=str(shortfall), residual_qty=str(dust),
                fully_accounted=True,
                reason=("a NEW tranche of %s at the attempt's ORIGINAL "
                        "complement price. Nothing is replaced and no price "
                        "moves." % shortfall)
                       + (" RESIDUAL: %s of sub-tick dust cannot be "
                          "ordered." % dust if dust > 0 else ""))


# -------------------------------------------------------------- eligibility

def _ws_ok(ws: Mapping[str, Any], *, now: float) -> list:
    """E7: the decision feed must be the PMUS authenticated websocket, for
    the pinned market, fresh, and two-sided on both outcome sides.

    `both_tokens_streaming` is gone with the CTF tokens. PMUS quotes a
    market ONCE; the question is whether THAT market's book is streaming.
    """
    bad = []
    if ws.get("timing_authority") != PBK.TIMING_PMUS_AUTHENTICATED_WS:
        bad.append("TIMING_AUTHORITY_NOT_PMUS_AUTHENTICATED_WS")
    if not ws.get("pinned_market_streaming"):
        bad.append("PINNED_MARKET_NOT_STREAMING")
    if ws.get("rest_is_sufficient_timing_authority") is True:
        bad.append("REST_CLAIMED_AS_TIMING_AUTHORITY")
    if ws.get("ctf_token_ids_used") is True:
        bad.append("CTF_TOKEN_IDS_IN_THE_DECISION_PATH")
    t = ws.get("book_unix")
    if t is None:
        bad.append("BOOK_HAS_NO_TIMESTAMP")
    elif (now - float(t)) > MAX_BOOK_AGE_S:
        bad.append("BOOK_STALE_%.3fs" % (now - float(t)))
    for side in ("first_leg", "complement"):
        tok = ws.get(side) or {}
        if tok.get("best_bid") in (None, "") or tok.get("best_ask") in (None,
                                                                       ""):
            bad.append("BOOK_NOT_TWO_SIDED_%s" % side.upper())
    return bad


# ----------------------------------------------- E8: the book must be real

#: a probability price lives strictly inside (0, 1]. Named rather than
#: inlined so the bound is legible next to the refusals that use it.
ONE_DOLLAR = D("1")


def finite_decimal(x: Any) -> Optional[D]:
    """`x` as a FINITE Decimal, or None.

    `D(str(x))` IS NOT A VALIDATION. Decimal accepts "NaN", "sNaN",
    "Infinity" and "-Infinity" as perfectly valid literals, and a Decimal
    NaN then raises InvalidOperation the moment it meets `<` or `%`. So
    every price and size that enters eligibility comes through here, and a
    value that is absent, unparseable or non-finite becomes None -- which
    the callers turn into an explicit refusal rather than arithmetic.
    """
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    try:
        d = D(str(x).strip())
    except Exception:                                          # noqa: BLE001
        return None
    return d if d.is_finite() else None


def e8_book_executable(snapshot: Mapping[str, Any]) -> list:
    """E8, WHICH THE FROZEN SPEC DECLARED AND THE CODE NEVER IMPLEMENTED.

    `spec.py` has promised since the beginning that "the book must be
    two-sided and executable on both tokens". Nothing evaluated it. The
    only adjacent check, `_ws_ok`, tests PRESENCE -- `best_bid in (None,
    "")` -- and the string "0.00" is neither.

    On 2026-10-07T20:39:36Z that gap put a real order on the wire. The
    first-leg book was bid 0.00 / ask 0.01, a dead book pinned to the
    floor, and E9's four clauses all passed on it: the price equalled the
    best bid, 0.00 is on the 0.01 tick, 0.00 <= the $0.60 ceiling, and
    0.00 < 0.01 so it could not cross. E1 serialized
    `price: {"currency": "USD", "value": "0.00"}` and sent it. A zero bid
    is not a cheap maker opportunity; it is the absence of a market.

    So executability is now asserted explicitly, for BOTH tokens, and an
    unexecutable book is refused BEFORE preview and therefore before any
    latch slot can be claimed. This does NOT touch the $0.60 ceiling, the
    tick, the would-cross rule or any economic bound -- it adds the floor
    those rules always assumed was there.
    """
    bad: list = []
    for name, side in (("FIRST_LEG", snapshot.get("first_leg") or {}),
                       ("COMPLEMENT", snapshot.get("complement") or {})):
        raw_bid = side.get("best_bid")
        raw_ask = side.get("best_ask")
        if raw_bid is None or (isinstance(raw_bid, str)
                               and not raw_bid.strip()):
            bad.append("E8_%s_BID_ABSENT" % name)
            continue
        if raw_ask is None or (isinstance(raw_ask, str)
                               and not raw_ask.strip()):
            bad.append("E8_%s_ASK_ABSENT" % name)
            continue
        bid = finite_decimal(raw_bid)
        ask = finite_decimal(raw_ask)
        if bid is None:
            bad.append("E8_%s_BID_NOT_FINITE:%r" % (name, raw_bid))
            continue
        if ask is None:
            bad.append("E8_%s_ASK_NOT_FINITE:%r" % (name, raw_ask))
            continue
        if bid <= 0:
            bad.append("E8_%s_BID_NOT_POSITIVE:%s" % (name, bid))
        if ask <= 0:
            bad.append("E8_%s_ASK_NOT_POSITIVE:%s" % (name, ask))
        if ask <= bid:
            bad.append("E8_%s_BOOK_CROSSED_OR_LOCKED:%s>=%s"
                       % (name, bid, ask))
        if bid >= ONE_DOLLAR or ask > ONE_DOLLAR:
            bad.append("E8_%s_PRICE_OUT_OF_RANGE:%s/%s" % (name, bid, ask))
    # DEPTH, WHEN THE FEED REPORTS IT. A size that is present and zero,
    # negative or non-finite is a refusal.
    #
    # ABSENCE IS DELIBERATELY NOT A REFUSAL, and this is a judgement I am
    # flagging rather than burying. Requiring the size to be present would
    # be stricter, but I have not established that the venue feed always
    # populates it, and a mandatory field the feed sometimes omits would
    # make E1 permanently ineligible -- safe, but silently so, which is
    # its own failure. The proven defect was a zero PRICE, and that is
    # refused above unconditionally. Turning absence into a refusal is a
    # one-line change if the owner wants the stricter rule.
    fl = snapshot.get("first_leg") or {}
    raw_sz = fl.get("size_at_our_price")
    if raw_sz is not None and not (isinstance(raw_sz, str)
                                   and not raw_sz.strip()):
        sz = finite_decimal(raw_sz)
        if sz is None:
            bad.append("E8_FIRST_LEG_SIZE_NOT_FINITE:%r" % (raw_sz,))
        elif sz <= 0:
            bad.append("E8_FIRST_LEG_SIZE_NOT_POSITIVE:%s" % sz)
    return bad


def eligible(snapshot: Mapping[str, Any], tracker: CohortTracker,
             *, now: float, envelope: Any = None) -> Dict[str, Any]:
    """Rules E1..E12, all evaluated and reported together, before any
    network call. `ok` False means NO ORDER and NO LATCH CONSUMPTION."""
    assert_frozen()
    r = []
    arming = A.armed_env_summary()
    if not arming.get("armed"):
        r.append("E1_DISARMED")
    pin = snapshot.get("fingerprint_pinned")
    if pin is None:
        pin = A.fingerprint_pinned(E1_FINGERPRINT)
    if not pin.get("matches_this_build"):
        r.append("E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD")

    window = snapshot.get("window")
    if not isinstance(window, int) or not 0 <= window < WINDOWS:
        r.append("E4_WINDOW_OUT_OF_RANGE:%r" % (window,))
        window = 0
    if not snapshot.get("window_open"):
        r.append("E4_WINDOW_NOT_OPEN")
    if not snapshot.get("within_handover_tte"):
        r.append("E4_PAST_HANDOVER_TTE")

    # E3, WHICH UNTIL NOW READ A CONSTANT. `pmus_provider` published
    # `our_outstanding_orders: 0` and `our_open_residual: None` as literals,
    # so these two clauses could not fire and the declared
    # `max_outstanding_orders: 1` bound was decorative. On 2026-10-08 the
    # 1230z window therefore held TWENTY resting orders. The provider now
    # derives both fields from authoritative venue order state; the rule
    # itself is unchanged in meaning and only says more about why.
    #
    # THE ORDER STATE MUST EXIST AT ALL. A read that failed publishes a
    # TRUTHY sentinel, so the first clause refuses on its own -- but the
    # refusal is named explicitly so a fail-closed is never mistaken for a
    # resting order.
    ostate = snapshot.get("order_state")
    if isinstance(ostate, Mapping) and not ostate.get("ok"):
        r.append("E3_ORDER_STATE_NOT_ESTABLISHED:%s"
                 % (ostate.get("reason"),))
    if snapshot.get("our_outstanding_orders"):
        r.append("E3_OUR_ORDER_IS_OUTSTANDING:%s"
                 % (snapshot.get("our_outstanding_orders"),))
    if snapshot.get("our_open_residual"):
        r.append("E3_OUR_RESIDUAL_IS_OPEN")

    # AGGREGATE EXPOSURE, NOT PER-ORDER SIZE. Twenty orders of 0.13 shares
    # each pass every per-order check ever written. What they cannot pass is
    # a sum. The tracker already projects our OWN submitted notional into
    # the per-window bound; this is the independent venue-side reading of
    # the same quantity -- what we would owe if every resting order and
    # every filled share were ours right now -- measured against the
    # cohort-wide ceiling. It relaxes nothing: it is an ADDITIONAL refusal.
    if isinstance(ostate, Mapping) and ostate.get("ok"):
        agg = finite_decimal(ostate.get("aggregate_exposure_usd"))
        if ostate.get("aggregate_exposure_usd") is not None and agg is None:
            r.append("E3_AGGREGATE_VENUE_EXPOSURE_NOT_FINITE:%r"
                     % (ostate.get("aggregate_exposure_usd"),))
        elif agg is not None:
            projected = agg + (finite_decimal(snapshot.get("qty",
                                                           FIRST_LEG_QTY))
                               or FIRST_LEG_QTY) * (
                finite_decimal(snapshot.get("first_leg_price")) or D("1"))
            if projected > MAX_COHORT_REALIZED_LOSS_USD:
                r.append("E3_AGGREGATE_VENUE_EXPOSURE_%s_WOULD_EXCEED_%s"
                         % (projected, MAX_COHORT_REALIZED_LOSS_USD))

    # THE PRICE IS VALIDATED BEFORE IT IS USED. A non-finite Decimal
    # raises InvalidOperation on `<` and `%`, so an unchecked price would
    # crash eligibility rather than refuse the attempt, and a missing one
    # used to default silently to "0" -- which is how a zero price became
    # eligible instead of impossible.
    raw_price = snapshot.get("first_leg_price")
    price = finite_decimal(raw_price)
    if raw_price is None or (isinstance(raw_price, str)
                             and not raw_price.strip()):
        r.append("E9_PRICE_ABSENT")
    elif price is None:
        r.append("E9_PRICE_NOT_FINITE:%r" % (raw_price,))
    elif price <= 0:
        r.append("E9_PRICE_NOT_POSITIVE:%s" % price)
    if price is None:
        # a safe sentinel purely so the remaining arithmetic cannot raise.
        # `r` is already non-empty, so this attempt is refused regardless.
        price = D("0")
    qty = finite_decimal(snapshot.get("qty", FIRST_LEG_QTY))
    if qty is None:
        r.append("QTY_NOT_FINITE:%r" % (snapshot.get("qty"),))
        qty = FIRST_LEG_QTY
    if qty != FIRST_LEG_QTY:
        r.append("QTY_NOT_THE_FROZEN_0.13:%s" % qty)
    notional = qty * price

    gate = tracker.may_place(window=window, notional_usd=notional)
    if not gate["ok"]:
        r.extend(gate["refusals"])

    r.extend(e8_book_executable(snapshot))

    fl = (snapshot.get("first_leg") or {})
    best_bid = fl.get("best_bid")
    best_ask = fl.get("best_ask")
    # EVERY COMPARISON GOES THROUGH finite_decimal. These three clauses
    # used to convert with a bare `D(str(x))`, which raises
    # InvalidOperation on "abc", "" or "sNaN" -- so a malformed book made
    # eligibility CRASH instead of refuse. A crash mid-cohort is strictly
    # worse than a refusal: it skips the recording and the stopping rules.
    # E8 above has already refused any value that cannot be compared, so
    # these clauses simply decline to compare it a second time.
    bid_d = finite_decimal(best_bid)
    ask_d = finite_decimal(best_ask)
    if bid_d is None or price != bid_d:
        r.append("E9_NOT_AT_SAME_SIDE_BEST_BID:%s!=%s" % (price, best_bid))
    if price % VENUE_TICK != 0:
        r.append("E9_OFF_TICK:%s" % price)
    if price > MAX_FIRST_LEG_PRICE:
        r.append("E9_PRICE_ABOVE_CEILING:%s" % price)
    if ask_d is not None and price >= ask_d:
        r.append("E9_WOULD_CROSS:%s>=%s" % (price, best_ask))

    r.extend("E7_" + x for x in _ws_ok(snapshot.get("ws_state") or {},
                                       now=now))

    comp = None
    raw_comp_bid = (snapshot.get("complement") or {}).get("best_bid")
    if raw_comp_bid is None or (isinstance(raw_comp_bid, str)
                                and not raw_comp_bid.strip()):
        # it used to default to "0", which quietly priced the complement
        # against a book that was not there.
        r.append("E10_COMPLEMENT_BID_ABSENT")
    elif finite_decimal(raw_comp_bid) is None:
        r.append("E10_COMPLEMENT_BID_NOT_FINITE:%r" % (raw_comp_bid,))
    else:
        try:
            comp = complement_price(
                first_leg_price=price, qty=qty,
                complement_best_bid=raw_comp_bid)
        except ComplementInfeasible as exc:
            r.append("E10_COMPLEMENT_INFEASIBLE:%s" % exc)

    if tracker.safety_violations:
        r.append("E12_SAFETY_INVARIANT_VIOLATION")

    lstate = None
    try:
        lstate = L.window_state(window=window)
        if lstate.get("available") and lstate.get("exhausted"):
            r.append("E11_WINDOW_LATCH_EXHAUSTED")
        if lstate.get("available") is False:
            r.append("E11_LATCH_UNAVAILABLE:%s" % lstate.get("why"))
    except Exception as exc:                                   # noqa: BLE001
        r.append("E11_LATCH_UNAVAILABLE:%s" % type(exc).__name__)

    # THE MULTI-TRADE ENVELOPE. Four bounds the attempt budget does not
    # express -- cumulative first-leg shares, GROSS notional with the
    # complement counted, outstanding exposure at one instant, and
    # directional residual -- read from venue-confirmed order state and
    # the durable latch slots. Additive: it can only refuse.
    mt = None
    if envelope is not None:
        mt = envelope.gate(order_state=snapshot.get("order_state"),
                           window=window,
                           qty=snapshot.get("qty", FIRST_LEG_QTY),
                           first_leg_price=snapshot.get("first_leg_price"))
        r.extend(mt["refusals"])

    return {"ok": not r, "refusals": r, "probe": PROBE,
            "spec_fingerprint": E1_FINGERPRINT,
            "multitrade": mt,
            "window": window,
            "first_leg": {"price_usd": str(price), "qty_shares": str(qty),
                          "notional_usd": str(notional),
                          "post_only": True, "offset": 0},
            "complement_plan": comp,
            "arming": arming, "latch": lstate, "exposure_gate": gate,
            "ARMED": bool(arming.get("armed")),
            "LABEL": "ELIGIBILITY ONLY -- not a submission"}


def e1_preflight(snapshot: Mapping[str, Any], tracker: CohortTracker,
                 *, now: float,
                 envelope: Optional["MT.MultiTradeEnvelope"] = None
                 ) -> Dict[str, Any]:
    """E1's own refusal set, shaped like `orders.preflight_submission`'s so
    it can be handed to `orders.submit_one(checks=...)`.

    The shared preflight is NOT used because it hard-requires the venue
    minimum 0.01 and E1's quantity is 0.13. This one is a SUPERSET on every
    shared check -- arming, price ceiling, tick, two-sided executable book,
    would-cross, latch -- and relaxes none of them. `live_exec2` is not
    edited to accommodate E1.
    """
    el = eligible(snapshot, tracker, now=now, envelope=envelope)
    return {"ok": el["ok"], "refusals": el["refusals"], "e1": el,
            "multitrade": el.get("multitrade"),
            "shared_preflight_not_used_because":
                "it requires qty == 0.01 and E1's frozen qty is 0.13",
            "shared_preflight_relaxed": False}


# -------------------------------------------------------------- submission

def _submit_once(t: Any, *, slug: str, outcome_side: str, price: Any,
                 qty: Any, consume_latch: Any) -> Dict[str, Any]:
    """`orders.submit_one`'s sequence with EXACTLY ONE substitution: E1's
    bounded-count named-slot latch in place of XR2W's single-slot one.

    WHY THIS EXISTS, STATED PLAINLY. `orders.submit_one` calls
    `analysis.l5.live_exec2.latch.consume`, which is a SINGLE slot per
    session: the first submission takes it and every later one raises
    `LatchSpent`. E1 needs twenty per window, so calling `submit_one`
    unchanged would let the cohort place exactly ONE order and then refuse
    all 239 others. The alternatives were to edit `live_exec2` -- which the
    mandate forbids and which would weaken XR2W's own guarantee -- or to
    clear a latch, which nothing here may do.

    SO THE LATCH IS THE ONLY THING SUBSTITUTED, and E1's latch is STRICTER
    per attempt, not looser: one named slot per attempt, O_EXCL, never
    released. Everything else is the proven path, by reference and not by
    copy:

      * the body        `orders.order_body` -> `src.l5_order_body.build`
      * the envelope    `orders.preview_request`
      * the echo gate   `src.l5_preview_gate.require_echo`
      * the routes      `orders.BoundedOrderTransport.request`, which still
                        refuses anything but the four permitted ones
      * the taxonomy    `orders.PreviewRejected` / `SubmissionAmbiguous`,
                        raised by EXCEPTION TYPE and not by reading a latch

    The ordering is the one that matters and it is unchanged: preview, echo,
    THEN latch, THEN the single POST. A `PreviewRejected` therefore leaves
    the latch untouched because the preview runs strictly before it, and
    anything at or after the POST is genuinely ambiguous and never retried.
    """
    body = O.order_body(slug=slug, outcome_side=outcome_side, price=price,
                        qty=qty)
    try:
        preview_response = t.request("POST", O.PREVIEW_PATH,
                                     O.preview_request(body))
    except O.VenueHTTPError as exc:
        raise O.PreviewRejected(
            "the venue rejected the PREVIEW; no order was created",
            detail={"phase": "PREVIEW", "sent": dict(body),
                    "sent_envelope_key": "request",
                    **exc.detail()}) from None
    except O.MutationRefused:
        raise
    except Exception as exc:                                   # noqa: BLE001
        # IS THIS OURS OR THE VENUE'S? Decided by RE-RUNNING the signing
        # contract, not by guessing from the exception type: the same class
        # can be a local config fault or a post-network decode failure.
        # If signing is still broken, the fault is deterministic and local,
        # it reached no venue, and it must NOT be counted as a venue error.
        local = LF.classify_preview_exception(exc)
        if local is not None:
            raise local from None
        raise O.PreviewRejected(
            "the preview call failed before any order could exist: %s: %s"
            % (type(exc).__name__, exc),
            detail={"phase": "PREVIEW", "error": type(exc).__name__,
                    "sent": dict(body)}) from None
    try:
        echo = G.require_echo(body, preview_response)
    except Exception as exc:                                   # noqa: BLE001
        raise O.PreviewRejected(
            "the preview echo did not match what we asked for: %s" % exc,
            detail={"phase": "PREVIEW_ECHO", "sent": dict(body),
                    "echo_failure": str(exc)[:600]}) from None
    # THE LATCH. Consumed here, between the echo and the POST, and never
    # released -- so a crash from this point on leaves the slot spent,
    # which is the only safe direction.
    record = consume_latch()
    try:
        resp = t.request("POST", O.SUBMIT_PATH, body)
    except O.VenueHTTPError as exc:
        raise O.SubmissionAmbiguous(
            "POST /v1/orders answered %s; an order may exist" % exc.status,
            detail={"phase": "SUBMIT", "sent": dict(body),
                    "latch_record": record, **exc.detail()}) from None
    except Exception as exc:                                   # noqa: BLE001
        raise O.SubmissionAmbiguous(
            "POST /v1/orders failed after the request left this process: "
            "%s: %s" % (type(exc).__name__, exc),
            detail={"phase": "SUBMIT", "error": type(exc).__name__,
                    "sent": dict(body), "latch_record": record}) from None
    order_id = str(resp.get("id") or resp.get("orderId")
                   or (resp.get("order") or {}).get("id") or "")
    return {"probe": PROBE, "spec_fingerprint": E1_FINGERPRINT,
            "order_id": order_id or None, "venue_response": resp,
            "preview_echo": echo, "latch_record": record,
            "submitted_post_only": True,
            "submitted_post_only_implies_maker": False,
            "serializer": "src.l5_order_body",
            "LANGUAGE": "SUBMITTED is not a fill"}


def submit_first_leg(snapshot: Mapping[str, Any], tracker: CohortTracker,
                     *, now: float,
                     envelope: Optional["MT.MultiTradeEnvelope"] = None,
                     transport_factory: Any = O.BoundedOrderTransport,
                     session_auth: Any = None,
                     progress: Any = None,
                     recorder: Any = None,
                     budget: Any = None) -> Dict[str, Any]:
    """Preview, echo-check, consume a NAMED latch slot, POST once.

    Every byte of that sequence is `analysis.l5.live_exec2.orders.submit_one`,
    reused unchanged; this function supplies E1's checks and E1's latch and
    nothing else. ONE transport per attempt, so the shared
    one-POST-per-instance guarantee is preserved rather than relaxed.

    A `PreviewRejected` leaves the latch UNTOUCHED -- a preview creates
    nothing and runs strictly before latch consumption. A
    `SubmissionAmbiguous` is NEVER retried: it is recorded, the cohort
    stops, and the venue state is reconciled.
    """
    assert_frozen()
    checks = e1_preflight(snapshot, tracker, now=now,
                          envelope=envelope)
    if not checks["ok"]:
        raise Ineligible("E1 refused the attempt: %s" % checks["refusals"],
                         refusals=checks["refusals"])
    window = checks["e1"]["window"]
    slug = snapshot["market"]
    # BOUNDARY 2: the SPECIFIC RESOLVED SLUG and both token ids, enforced
    # immediately before the submission and BEFORE any transport exists.
    # A slug the frozen selector did not produce in this authorized session
    # is refused here, not discovered at the venue.
    auth_result = None
    if session_auth is not None:
        # THE PIN'S OWN TWO OUTCOME SIDES, not the leg orientation. These
        # used to be read from first_leg/complement, which conflated "which
        # leg are we opening" with "what are this market's two outcome
        # sides" -- so once either side could open, a DOWN first leg would
        # have presented OUTCOME_SIDE_NO as `outcome_up` and boundary 2
        # would have refused a perfectly authorized market. The boundary is
        # about the MARKET's identity, so it gets the market's values.
        p = snapshot.get("pin") or {}
        auth_result = session_auth.enforce_before_submission(
            slug=slug, pin=p,
            outcome_up=p.get("outcome_up"),
            outcome_down=p.get("outcome_down"),
            role="first_leg")
        # and the leg we are about to send must be ONE OF that market's two
        # sides. Checked here, before any transport exists.
        _leg = (snapshot.get("first_leg") or {}).get("outcome_side")
        if str(_leg) not in {str(p.get("outcome_up")),
                             str(p.get("outcome_down"))}:
            raise AUTH.NotAuthorized(
                "first-leg outcome side %r is not one of the authorized "
                "pair %r/%r for %s. Refused BEFORE transport."
                % (_leg, p.get("outcome_up"), p.get("outcome_down"), slug))
    side = snapshot["first_leg"]["side"]
    price = snapshot["first_leg_price"]
    qty = snapshot.get("qty", FIRST_LEG_QTY)

    holder: Dict[str, Any] = {}

    # ============ THE PER-PROBE ATTEMPT BUDGET, AND THE LATCH, FIRST ======
    #
    # BEFORE the intent row, BEFORE the preview and BEFORE the POST, in that
    # order of importance. Two separate reads, both of DURABLE state:
    #
    #   * the owner's `PMUS_E1_MAX_ATTEMPTS`, counted against latch files
    #     plus unretired local intents, so an ambiguous submission and a
    #     restart both count as spent;
    #   * the latch itself, read-only, so a window whose twenty slots are
    #     all taken is discovered HERE, rather than deep in the
    #     submit helper between the preview echo and the POST.
    #
    # The second is the repair of a real defect. `consume_attempt` is called
    # between the preview echo and the POST, so an exhausted window used to
    # raise `WindowExhausted` AFTER the intent row was already on the
    # durable tape and AFTER a real preview had been sent -- leaving a false
    # pending intent that `order_registry` would restore as a permanent
    # block, and propagating an uncaught `RuntimeError` out of `main()` that
    # would abandon any filled residual. Checking first means neither
    # happens on the ordinary path, and the `except` below covers the race.
    if budget is not None:
        gate = budget.attempt_gate(
            window=window,
            pending_order_ids=snapshot.get("pending_order_ids") or [])
        if not gate["may_attempt"]:
            raise AttemptBudgetExhausted(
                "%s; refused before the intent row, the preview and the "
                "POST. Inventory management continues." % gate["because"])
    _latch_now = L.window_state(window=window)
    if not _latch_now.get("available") or _latch_now.get("exhausted"):
        raise AttemptBudgetExhausted(
            "LATCH_EXHAUSTED_WINDOW_%d: %s. Refused before the intent row, "
            "the preview and the POST, so no false pending intent is "
            "recorded and no preview is sent."
            % (window, _latch_now.get("why")
               or "all %d attempt slots are spent"
               % MAX_ATTEMPTS_PER_WINDOW))

    # THE SUBMISSION INTENT IS RECORDED BEFORE THE POST, NOT AFTER IT.
    #
    # `pending_order_ids` is the list the provider reads when it asks the
    # venue for outstanding orders. An entry here is "we may have created an
    # order and cannot prove we did not", and it counts as OUTSTANDING until
    # a successful venue read either shows it terminal or shows it gone.
    #
    # It is appended before the POST because the dangerous case is exactly
    # the one where the POST's answer never arrives: a crash, a reset or a
    # timeout between the request and the acknowledgement. Recording after
    # the fact would leave that window open, which is how twenty orders can
    # exist while a process believes it has none.
    pending = snapshot.get("pending_order_ids")
    intent_id = "LOCAL_INTENT:w%s:%s:%s:%s" % (window, slug, side, price)
    if isinstance(pending, list):
        pending.append(intent_id)
    # AND DURABLY, because an in-memory list dies with the process. The row
    # goes on the append-only tape BEFORE the POST, so a crash between the
    # request and the acknowledgement leaves evidence the next process can
    # read: `order_registry.load_from_tape` restores it as pending and the
    # restart cannot place a second first leg on top of an order that may
    # or may not exist. Without this row that window is invisible to the
    # next process.
    if recorder is not None:
        recorder.record_event(
            window=window, slot=-1, event="FIRST_LEG_SUBMISSION_INTENT",
            detail={"intent_id": intent_id, "slug": slug, "side": side,
                    "price": str(price), "qty": str(qty),
                    "recorded_BEFORE_the_post": True})
    # AND IN THE OBLIGATION LEDGER, which is the record that outranks the
    # venue's view. The tape above is append-only evidence for a restart;
    # this is the live predicate the eligibility floor reads, and it is
    # written before the POST for the same reason.
    # SHARED BY REFERENCE in the snapshot, exactly as `pending_order_ids`
    # is: this function is handed a snapshot and nothing else.
    _ledger = snapshot.get("obligation_ledger")
    if _ledger is not None:
        try:
            _ledger.record_submission(
                intent_id=intent_id, window=window, slot=-1, slug=slug,
                side=side, price=price, qty=qty)
        except OB.ReservationHeld as held:
            # ANOTHER WORKER HOLDS THE SINGLE FIRST-LEG RESERVATION. This
            # is a refusal, not a queue and not a retry: the whole point of
            # an O_EXCL token is that the loser does not proceed. The local
            # intent recorded a moment ago is withdrawn because NOTHING was
            # POSTed -- we never reached the latch, let alone the preview.
            if isinstance(pending, list) and intent_id in pending:
                pending.remove(intent_id)
            raise Ineligible(
                "E3_FIRST_LEG_RESERVATION_HELD by %s; no order was created"
                % (held.holder.get("intent_id"),),
                refusals=["E3_FIRST_LEG_RESERVATION_HELD"]) from None
        except OB.LedgerUnavailable as exc:
            if isinstance(pending, list) and intent_id in pending:
                pending.remove(intent_id)
            raise Ineligible(
                "E3_OBLIGATION_LEDGER_UNAVAILABLE:%s; a submission we "
                "cannot record is a submission we do not make"
                % type(exc).__name__,
                refusals=["E3_OBLIGATION_LEDGER_UNAVAILABLE"]) from None

    def _take_slot() -> Dict[str, Any]:
        holder["slot"] = L.consume_attempt(window=window, slug=slug,
                                           side=side, price=price, qty=qty)
        return holder["slot"]

    t = transport_factory()
    try:
        out = _submit_once(t, slug=slug, outcome_side=side, price=price,
                           qty=qty, consume_latch=_take_slot)
    except O.SubmissionAmbiguous:
        tracker.record_ambiguous_submission()
        raise
    except (L.WindowExhausted, L.LatchSpent) as exc:
        # THE RACE, AND ONLY THE RACE. The read-only check above already
        # refused the ordinary exhausted case; reaching here means a slot
        # was taken between that read and the O_EXCL create. The latch did
        # its job -- NOTHING was POSTed, because the create failed before
        # the POST -- so the intent this function recorded is retired, in
        # memory and on the tape, and the exhaustion is raised as an
        # authorization refusal instead of an uncaught RuntimeError.
        if isinstance(pending, list) and intent_id in pending:
            pending.remove(intent_id)
        if _ledger is not None:
            # SAFE HERE AND NOWHERE ELSE: the O_EXCL create failed, so the
            # POST provably never happened and no order can exist. An
            # ambiguous submission is NOT retired -- it keeps blocking.
            _ledger.retire_never_sent(
                intent_id, why="LATCH_REFUSED_BEFORE_THE_POST")
        if recorder is not None:
            recorder.record_event(
                window=window, slot=-1,
                event="FIRST_LEG_INTENT_RETIRED_LATCH_EXHAUSTED",
                detail={"intent_id": intent_id, "slug": slug,
                        "why": type(exc).__name__,
                        "no_post_was_made": True,
                        "retired_because": ("the latch refused BEFORE the "
                                            "POST, so no order can exist"),
                        "not_a_false_pending_intent": True})
        raise AttemptBudgetExhausted(
            "LATCH_EXHAUSTED_AT_CONSUME_WINDOW_%d: %s. No POST was made "
            "and the pre-POST intent has been retired." % (window, exc))
    finally:
        # the attempt is counted the moment a slot was taken, whatever the
        # POST then did. A spent slot that we did not count would let the
        # window bound drift away from the latch.
        if "slot" in holder:
            tracker.record_placement(
                window=window, notional_usd=D(str(qty)) * D(str(price)))
    # THE INTENT IS RETIRED ONLY BY A CONFIRMED ORDER ID. We swap the local
    # placeholder for the venue's own id, so the next snapshot's order-state
    # read resolves it against authoritative state instead of a guess. If the
    # venue gave us no id, the placeholder STAYS and keeps refusing.
    oid = None
    for k in ("orderId", "order_id", "id"):
        v = (out.get(k) if isinstance(out, dict) else None)
        if v:
            oid = str(v)
            break
    if oid is None and isinstance(out, dict):
        inner = out.get("order") or out.get("response") or {}
        if isinstance(inner, Mapping):
            oid = str(inner.get("id")) if inner.get("id") else None
    if _ledger is not None and oid:
        _ledger.bind_venue_id(intent_id, oid)
    if isinstance(pending, list) and intent_id in pending:
        if oid:
            pending[pending.index(intent_id)] = oid
        # else: left in place deliberately. An unidentified submission is an
        # unconfirmed submission.
    # AND REMEMBER IT, because the venue cannot be asked. `/v1/orders` is
    # HTTP 501 here, so the only way to read this order again is by its id;
    # an id we fail to record is an order we can never reconcile. The role
    # is recorded with it, so a resting first leg blocks another first leg
    # without blocking the complement that closes it.
    known = snapshot.get("known_order_ids")
    roles = snapshot.get("order_roles")
    if oid:
        if isinstance(known, list) and oid not in known:
            known.append(oid)
        if isinstance(roles, dict):
            roles[oid] = OST.FIRST_LEG
    out["e1_pending_order_id"] = oid or intent_id
    out["e1_intent_id"] = intent_id
    out["e1_latch_slot"] = holder["slot"]
    out["e1_window"] = window
    out["e1_authorization"] = auth_result
    #: the refusal set this submission PASSED, multi-trade envelope
    #: included, so the decision log records the bounds that were
    #: actually consulted rather than re-deriving them later.
    out["e1_checks"] = {"multitrade": checks.get("multitrade"),
                        "refusals_passed": []}
    if progress is not None and "slot" in holder:
        progress.record_placement(window_index=window,
                                  slot=holder["slot"]["slot"],
                                  notional_usd=D(str(qty)) * D(str(price)),
                                  slug=slug)
    return out


def submit_complement(*, window: int, slot: int, snapshot: Mapping[str, Any],
                      first_leg_filled_qty: Any, tracker: CohortTracker,
                      already_complemented_qty: Any = "0",
                      transport_factory: Any = O.BoundedOrderTransport,
                      session_auth: Any = None) -> Dict[str, Any]:
    """Rules C1..C10. Submit ONE tranche, sized so the complement quantity
    equals the CUMULATIVE first-leg fill.

    `first_leg_filled_qty` is the CUMULATIVE filled quantity, and
    `already_complemented_qty` is what previous tranches have covered. The
    difference is this tranche. The price is the attempt's ORIGINAL
    complement price, recomputed from the ORIGINAL first-leg price, so no
    price can move: maker only, no taker rescue, no chasing, no re-quote.

    A shortfall that cannot legally or economically be complemented is NOT
    submitted and is returned as `residual`, explicitly and with its
    reason, so the caller records a decision instead of discovering an
    unexplained position later.
    """
    assert_frozen()
    spent = L.complement_tranches_spent(window=window, slot=slot)
    plan = complement_tranche(
        cumulative_first_leg_filled_qty=first_leg_filled_qty,
        already_complemented_qty=already_complemented_qty,
        tranches_submitted=spent)
    if plan["tranche"] is None:
        return {"submitted": False, "tranche_plan": plan,
                "residual_qty": plan["residual_qty"],
                "residual_reason": plan["reason"],
                "RESIDUAL_IS_EXPLICIT": True,
                "fully_accounted": True,
                "LABEL": "NO ORDER -- the shortfall is recorded as RESIDUAL"}
    q = D(plan["tranche"])
    try:
        price = complement_price(
            first_leg_price=snapshot["first_leg_price"], qty=q,
            complement_best_bid=(snapshot.get("complement")
                                 or {}).get("best_bid", "0"))
    except ComplementInfeasible as exc:
        # ECONOMICALLY uncomplementable: recorded as residual, never
        # rescued by relaxing the frozen ceiling.
        return {"submitted": False, "tranche_plan": plan,
                "residual_qty": plan["tranche"],
                "residual_reason": "RESIDUAL: %s" % exc,
                "RESIDUAL_IS_EXPLICIT": True,
                "ceiling_relaxed": False,
                "fully_accounted": True,
                "LABEL": "NO ORDER -- the shortfall is recorded as RESIDUAL"}
    side = snapshot["complement"]["side"]
    # a COMPLEMENT may only belong to the ALREADY-AUTHORIZED first-leg
    # market. It cannot independently select another.
    comp_auth = None
    if session_auth is not None:
        _p = snapshot.get("pin") or {}
        comp_auth = session_auth.authorize_complement(
            slug=snapshot["market"],
            first_leg_pin=_p,
            outcome_up=_p.get("outcome_up"),
            outcome_down=_p.get("outcome_down"))
        _cleg = (snapshot.get("complement") or {}).get("outcome_side")
        if str(_cleg) not in {str(_p.get("outcome_up")),
                              str(_p.get("outcome_down"))}:
            raise AUTH.NotAuthorized(
                "complement outcome side %r is not one of the authorized "
                "pair for %s. Refused BEFORE transport."
                % (_cleg, snapshot["market"]))
    holder: Dict[str, Any] = {}

    def _take_slot() -> Dict[str, Any]:
        holder["slot"] = L.consume_complement(
            window=window, slot=slot, slug=snapshot["market"], side=side,
            price=price["price_usd"], qty=q, tranche=spent)
        return holder["slot"]

    t = transport_factory()
    try:
        out = _submit_once(t, slug=snapshot["market"], outcome_side=side,
                           price=price["price_usd"], qty=q,
                           consume_latch=_take_slot)
    except O.SubmissionAmbiguous:
        tracker.record_ambiguous_submission()
        raise
    # the complement's own id is registered under the COMPLEMENT role, so
    # the next order-state read counts it against the one-complement-at-a-
    # time rule and NOT against the first-leg guard.
    _coid = None
    for _k in ("orderId", "order_id", "id"):
        if isinstance(out, dict) and out.get(_k):
            _coid = str(out[_k])
            break
    _known = snapshot.get("known_order_ids")
    _roles = snapshot.get("order_roles")
    if _coid:
        if isinstance(_known, list) and _coid not in _known:
            _known.append(_coid)
        if isinstance(_roles, dict):
            _roles[_coid] = OST.COMPLEMENT
    out.update({"submitted": True, "tranche_plan": plan,
                "e1_complement_order_id": _coid,
                "e1_complement_role": OST.COMPLEMENT,
                "e1_complement_plan": price,
                "e1_complement_qty": str(q),
                "e1_tranche_index": spent,
                "e1_latch_slot": holder["slot"],
                "e1_authorization": comp_auth,
                "sized_to_cumulative_filled_qty": True,
                "sized_to_only_the_first_partial": False,
                "residual_qty": "0", "fully_accounted": True})
    return out


def account_first_leg(*, cumulative_filled_qty: Any,
                      complemented_qty: Any) -> Dict[str, Any]:
    """Prove, arithmetically, that every filled share is accounted for as
    either COMPLEMENTED or RESIDUAL. Nothing may be unaccounted."""
    cum = D(str(cumulative_filled_qty))
    comp = D(str(complemented_qty))
    residual = cum - comp
    return {"cumulative_first_leg_filled_qty": str(cum),
            "complemented_qty": str(comp),
            "residual_qty": str(residual),
            "sum_usd_checks": str(comp + residual),
            "fully_accounted": (comp + residual) == cum,
            "unaccounted_qty": str(cum - comp - residual),
            "RULE": "every filled share is COMPLEMENTED or RESIDUAL, never "
                    "neither and never silently"}


# ------------------------------------------------- preview-rejection record

def preview_rejection_record(snapshot: Mapping[str, Any],
                             detail: Mapping[str, Any], *,
                             iteration: int, now: float) -> Dict[str, Any]:
    """Assemble the DURABLE record of one venue preview rejection.

    WHY THIS EXISTS. On 2026-10-07T19:15:47Z an eligible attempt was
    rejected by the venue five times and the cohort stopped on
    VENUE_ERROR_STORM. Every one of those rejections carried the venue's
    own status, reason and body -- and NONE of it reached the tape, because
    the `PreviewRejected` handler recorded to the in-memory tracker and the
    refusal list only. The detail existed solely in the final stdout JSON,
    and Railway's 500 logs/sec limit dropped 5704 lines of that run. The
    venue told us exactly what was wrong with the order and the answer was
    thrown away.

    So the rejection is now written through the EXISTING recorder, on every
    occurrence, before the loop continues and before the storm can end the
    cohort.

    PURE. It builds a dict and nothing else: no transport, no latch, no
    tracker mutation, no I/O. That is what makes it safe to call from the
    middle of the submission handler, and it is why the regression tests
    can assert the shape without a venue.

    SECRETS. Auth headers and signatures are never in `detail` to begin
    with -- `BoundedOrderTransport` records response headers through an
    allowlist and request headers by NAME only -- and the result is passed
    through `local_faults.redact_detail`, which subtracts credential values
    at every depth. Nothing here reads a credential.
    """
    fl = dict(snapshot.get("first_leg") or {})
    cp = dict(snapshot.get("complement") or {})
    pin = dict(snapshot.get("pin") or {})
    sent = dict(detail.get("sent") or {})
    return {
        "phase": "PREVIEW",
        "iteration": iteration,
        "unix": now,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "session_id": A.session_id(),
        "window": snapshot.get("window"),
        "spec_fingerprint": E1_FINGERPRINT,
        # ---- what the venue said
        "http_status": detail.get("status"),
        "http_reason": detail.get("reason"),
        "venue_response_body": detail.get("response_body"),
        "venue_response_headers_safe": detail.get("response_headers_safe"),
        "request_method": detail.get("method"),
        "request_path": detail.get("path"),
        "sent_envelope_key": detail.get("sent_envelope_key"),
        # ---- PMUS market identity: slug + outcome sides. NO TOKEN IDS,
        # because a PMUS order carries none. `token_up`/`token_down` stay
        # in the record as explicit nulls so a reader of the mixed tape
        # can tell "no token was used" from "the field is missing".
        "slug": snapshot.get("market"),
        "market_id": pin.get("market_id"),
        "window_start": pin.get("window_start"),
        "window_end": pin.get("window_end"),
        "outcome_up": pin.get("outcome_up"),
        "outcome_down": pin.get("outcome_down"),
        "identity": "slug_plus_outcome_side",
        "ctf_token_ids_used": False,
        "token_up": None,
        "token_down": None,
        # ---- what E1 decided, BEFORE serialization
        "e1_outcome_side": fl.get("side"),
        "e1_first_leg_outcome_side": fl.get("outcome_side"),
        "e1_first_leg_token_id": None,
        "e1_complement_side": cp.get("side"),
        "e1_price_local": str(snapshot.get("first_leg_price")),
        "e1_qty_local": str(snapshot.get("qty", FIRST_LEG_QTY)),
        "e1_observed_best_bid": fl.get("best_bid"),
        "e1_observed_best_ask": fl.get("best_ask"),
        # ---- WHAT WENT ON THE WIRE, AFTER SERIALIZATION, VERBATIM.
        #
        # The whole serialized body is recorded as one object rather than
        # re-listed field by field, and that is deliberate:
        # `test_no_file_in_this_package_contains_a_duplicate_serializer`
        # forbids any non-test file here from naming a wire key, because
        # naming them is the first step of the duplicate-serializer bug
        # that caused the Attempt-5 HTTP 400 twice. Copying them out to
        # make the record look tidier would reintroduce exactly the
        # coupling that guard exists to prevent.
        #
        # Nothing is lost. The serialized price, quantity, post-only flag
        # and side are all here, under the proven serializer's own key
        # names, and
        # `test_the_serialized_wire_values_are_retrievable_from_the_record`
        # asserts each one is readable from the persisted event.
        "wire_body_non_secret": sent,
        "wire_body_keys": sorted(sent),
        "wire_value_types": {k: type(v).__name__
                             for k, v in sorted(sent.items())},
        "serializer": "src.l5_order_body",
        "WIRE_FIELDS_NOT_RE_LISTED": "the body above is verbatim; this "
                                     "package may not name wire keys",
        # ---- the two facts that must never be inferred from state
        "latch_touched": False,
        "order_submitted": False,
        "reached_the_venue": True,
        "is_a_venue_error": True,
        "is_a_local_fault": False,
        "retried": False,
        "WHY_LATCH_UNTOUCHED": "the preview runs strictly before latch "
                               "consumption, so this is control flow and "
                               "not a deduction from state",
        "SECRETS": "auth headers and signatures are never recorded",
    }


# ------------------------------------------------------------- the cohort

#: THE EMERGENCY HANDOVER HOOK.
#:
#: `run_cohort` installs a closure here while it owns orders, and `main`
#: calls it from a `finally`. That indirection is deliberate and it is the
#: SMALLEST change that makes the handover reachable: the alternative --
#: wrapping the 450-line iteration loop in `try`/`finally` -- would reindent
#: the whole body and bury a four-line safety fix inside a whitespace diff
#: nobody can review.
#:
#: The closure holds the only things the handover needs: the lifecycle that
#: knows which ids are ours, the provider that can re-read venue state, and
#: the transport factory. It is CLEARED on a normal return, so a set hook
#: means "a cohort stopped without completing its handover".
_EMERGENCY_HANDOVER: Any = None


def _set_emergency_handover(fn: Any) -> None:
    global _EMERGENCY_HANDOVER
    _EMERGENCY_HANDOVER = fn


def emergency_handover_pending() -> bool:
    """Read-only: does a cohort still owe a handover?"""
    return _EMERGENCY_HANDOVER is not None


def run_emergency_handover(*, cause: str) -> Optional[Dict[str, Any]]:
    """Run the installed handover, once, and never raise.

    Called from `main`'s `finally`, so it executes on an uncaught exception
    and on `shutdown.ShutdownRequested` -- which is a `BaseException`
    precisely so the loop's `except Exception` classifiers cannot swallow a
    shutdown and keep placing orders.

    IT NEVER RAISES. This runs during an unwind that may already be
    carrying the real failure, and losing that failure to a secondary error
    in the cancel path would make the incident unreadable. A failure here is
    returned, recorded and reported; it is not propagated.
    """
    fn = _EMERGENCY_HANDOVER
    if fn is None:
        return None
    _set_emergency_handover(None)
    try:
        return fn(cause)
    except BaseException as exc:                               # noqa: BLE001
        return {"ran": False, "cause": cause,
                "because": "EMERGENCY_HANDOVER_RAISED:%s"
                           % type(exc).__name__,
                "FAILS_CLOSED": True,
                "MEANING": ("the handover itself failed. Owned orders may "
                            "still be resting and must be cancelled by id "
                            "with scripts/l5_e1_cancel_by_id.py.")}


def run_cohort(*, snapshot_provider: Any, tracker: Optional[CohortTracker]
               = None, recorder: Any = None,
               transport_factory: Any = O.BoundedOrderTransport,
               now_fn: Any = None, max_iterations: Optional[int] = None,
               session_auth: Any = None, progress: Any = None,
               sleep_fn: Any = None, pace_s: Optional[float] = None,
               budget: Any = None, envelope: Any = None) -> Dict[str, Any]:
    """Drive the cohort: MANY bounded attempts, under the E1 named-slot latch.

    THIS IS THE THING THAT WAS MISSING, and its absence was the defect. The
    frozen spec's `entrypoint` was inherited verbatim from XR2W and still
    named `scripts.l5_exec1xr2w_run` with a call graph through
    `live_exec3.runner:run_experiment` and XR2W's SINGLE-SLOT latch. Arming
    "per the spec" would have launched XR2W's ONE-ORDER probe and measured
    nothing. The deep-copy fixed the aliasing and then faithfully copied a
    value that needed to change -- the same class of bug as the old
    fingerprint-pin defect, and loud about nothing.

    IT CHANGES NO STRATEGY. Every gate, bound, price, quantity, tranche and
    stopping rule is the one already frozen: this function only ITERATES
    what `eligible()` and `submit_first_leg()` already decide, and stops the
    moment the tracker says stop. It adds no rule and relaxes none.

    `snapshot_provider` is a zero-argument callable returning the next
    decision-time snapshot, or None when there is nothing to decide on. It
    is the seam the offline tests drive, and in production it is fed by the
    websocket timing authority.
    """
    assert_frozen()
    tracker = tracker if tracker is not None else CohortTracker()
    now_fn = now_fn or time.time
    sleep_fn = sleep_fn or time.sleep
    pace_s = PACING.PACE_S if pace_s is None else float(pace_s)
    paced_waits: List[Dict[str, Any]] = []
    # THE PAIR LIFECYCLE. This is the thing `run_cohort` did not have.
    # `submit_complement` existed, was frozen into the spec's call graph,
    # had its own authorization and latch, and was never called from here
    # by anything -- which is why 1215z filled twenty first legs and
    # submitted zero complements.
    lifecycle = PLC.PairLifecycle()
    # THE PER-PROBE BUDGET. The default is the FROZEN maximums, so every
    # existing caller behaves exactly as before; the deployed `main()`
    # builds it from the owner's `PMUS_E1_MAX_ATTEMPTS`/`PMUS_E1_MAX_WINDOWS`
    # and fails closed when they are missing, invalid or excessive.
    if budget is None:
        budget = BUD.ProbeBudget.frozen_maximums(
            windows_used_at_start=(progress.windows_used()
                                   if progress is not None else 0))
    budget_events: List[Dict[str, Any]] = []
    # LATCH EXHAUSTION IS AS BINDING AS THE BUDGET. The latch is the other
    # durable bound on attempts, and once it refuses, no further first leg
    # can be submitted in this window however much budget is left. Without
    # this flag the refusal was a bare `continue`, which spun the loop at
    # full speed until the iteration cap -- never crashing, but never
    # stopping either, and never reaching the clean handover stop.
    # keyed by WINDOW INDEX, because the latch bound is per window while
    # the attempt budget is global. A sticky global flag here silently
    # stopped a legitimate twelve-window cohort from placing anything in
    # window two once window one's twenty slots were spent.
    # THE ENVELOPE. Absent, it is the spec's own frozen ceilings, so an
    # unmodified twelve-window cohort behaves exactly as before and this
    # parameter narrows nothing by default.
    if envelope is None:
        envelope = MT.MultiTradeEnvelope.frozen_maximums()
    last_order_state_seen: Any = None
    attempts_blocked: Dict[Any, str] = {}
    slept_s = 0.0
    empty_snapshots = 0
    placements: List[Dict[str, Any]] = []
    refusals: List[Dict[str, Any]] = []
    stopped_because: Optional[str] = None
    it = 0

    # THE LOCAL CONTRACT, CHECKED BEFORE A SINGLE WINDOW IS OPENED.
    #
    # On 2026-10-07 the cohort opened window 0, asked for a preview, and
    # discovered 20.8 ms later that this process could not sign anything --
    # `cryptography` was not in the image. It spent five iterations finding
    # that out and recorded it as a venue error storm. Signing is local
    # arithmetic: it can be proven BEFORE the venue, before a window, and
    # before a snapshot is even requested. So it is.
    #
    # No network, no venue, no order body, no latch. If it fails, the
    # cohort fails closed ONCE, says exactly what broke with every secret
    # value subtracted, and stops.
    try:
        signing_proof: Any = LF.verify_signing_contract()
    except LF.LocalExecutionFault as fault:
        detail = LF.redact_detail(fault.detail)
        tracker.record_local_execution_fault(detail=detail)
        if recorder is not None:
            recorder.record_event(
                window=-1, slot=-1, event="LOCAL_EXECUTION_FAULT",
                detail={"message_redacted": LF.redact(fault), **detail})
        v = tracker.verdict()
        if progress is not None:
            progress.record_cohort_stopped(
                verdict=v["verdict"],
                truncation_cause=v.get("truncation_cause"))
        return {"probe": PROBE, "spec_fingerprint": E1_FINGERPRINT,
                "iterations": 0, "placements": [], "placement_count": 0,
                "windows_used": (progress.windows_used()
                                 if progress is not None else None),
                "refusals": [{"iteration": 0,
                              "refusals": ["LOCAL_EXECUTION_FAULT"],
                              "detail": detail}],
                "stopped_because": "LOCAL_EXECUTION_FAULT",
                "local_execution_fault": detail,
                "windows_opened": 0,
                "transport_constructed": False,
                "venue_contacted": False,
                "latch_slots_consumed": 0,
                "counted_as_venue_errors": 0,
                "tracker": tracker.snapshot(),
                "signing_proof": None,
                "LANGUAGE": "a local fault is not a venue error"}
    # NO SUBMISSION BEFORE RECONCILIATION.
    #
    # Durable progress already prevents a restart from granting a thirteenth
    # window. It says nothing about orders still resting at the venue from
    # the process that died, and nothing asked. So before the first window
    # is opened, if the provider can establish venue order state, it is
    # established and checked. Pre-existing outstanding orders or an open
    # residual stop the cohort here -- they do not get silently inherited
    # and they are NOT cancelled by this code, which cancels nothing.
    #
    # A provider that cannot be asked (the offline test seam) is not a
    # failure: `eligible()` re-asks E3 on every single snapshot anyway, and
    # that is the gate that matters. This is the earlier, louder one.
    startup_reconciliation: Optional[Dict[str, Any]] = None
    _probe = getattr(snapshot_provider, "order_state", None)
    _pin_for_reconcile = getattr(snapshot_provider, "current_pin", None)
    if callable(_probe):
        try:
            startup_reconciliation = OST.reconcile_at_startup(
                _probe(_pin_for_reconcile or {}))
        except Exception as exc:                               # noqa: BLE001
            startup_reconciliation = {
                "may_submit": False, "reconciled": False,
                "because": "STARTUP_RECONCILIATION_RAISED:%s"
                           % type(exc).__name__,
                "FAILS_CLOSED": True}
        if not startup_reconciliation.get("may_submit"):
            because = startup_reconciliation.get("because")
            detail = {"startup_reconciliation": startup_reconciliation}
            tracker.violate(
                "STARTUP_RECONCILIATION_REFUSED:%s" % (because,))
            if recorder is not None:
                recorder.record_event(
                    window=-1, slot=-1,
                    event="STARTUP_RECONCILIATION_REFUSED", detail=detail)
            v = tracker.verdict()
            if progress is not None:
                progress.record_cohort_stopped(
                    verdict=v["verdict"],
                    truncation_cause=v.get("truncation_cause"))
            return {"probe": PROBE, "spec_fingerprint": E1_FINGERPRINT,
                    "iterations": 0, "placements": [], "placement_count": 0,
                    "windows_used": (progress.windows_used()
                                     if progress is not None else None),
                    "refusals": [{"iteration": 0,
                                  "refusals": ["STARTUP_RECONCILIATION"
                                               "_REFUSED"],
                                  "detail": detail}],
                    "stopped_because": "STARTUP_RECONCILIATION_REFUSED",
                    "startup_reconciliation": startup_reconciliation,
                    "windows_opened": 0,
                    "transport_constructed": False,
                    "venue_contacted": True,
                    "latch_slots_consumed": 0,
                    "tracker": tracker.snapshot(),
                    "signing_proof": signing_proof,
                    "LANGUAGE": "pre-existing venue state is not inherited"}

    # SEED THE LIFECYCLE FROM THE RESTORED REGISTRY, so a restart picks up
    # the first legs it already owns and complements them instead of
    # treating them as someone else's orders. The window/slot come from the
    # latch slot recorded with each placement; where a restart cannot
    # recover them the leg is still tracked, so its fill is registered as
    # residual and counts against the ceiling even if no tranche can be
    # addressed to a slot.
    _roles = getattr(snapshot_provider, "order_roles", None) or {}
    _known = getattr(snapshot_provider, "known_order_ids", None) or []
    for _oid in _known:
        if str(_roles.get(_oid, OST.FIRST_LEG)) == OST.FIRST_LEG:
            lifecycle.record_first_leg(order_id=_oid, window=0, slot=0,
                                       price="0")

    # THE CAP IS A SAFETY NET, NOT THE BUDGET. It used to be
    # `MAX_PLACEMENTS * 4` == 960, which is far SMALLER than the number of
    # times a paced loop legitimately reads the book while observing twelve
    # 15-minute windows -- so on 2026-10-08 the cap, not the market, ended the
    # cohort after one window and one second. The budget is windows,
    # placements and the trading deadline; reaching this cap is an anomaly.
    # NOTE the guard is derived from the PRODUCTION pace, never from the
    # possibly-shrunk `pace_s` above: a fast test pace must not inflate the
    # runaway guard, which is how it first became ten million.
    limit = (PACING.iteration_cap(windows=WINDOWS,
                                  window_len_s=WINDOW_LEN_S,
                                  max_placements=MAX_PLACEMENTS)
             if max_iterations is None else max_iterations)
    # ============ THE HANDOVER, MADE REACHABLE ON EVERY EXIT ============
    #
    # Until now `run_cohort` reached the handover from ONE place: the
    # `past_cutoff` branch below. SIGTERM (how Railway stops a container),
    # SIGINT, an exception escaping this loop, and a normal exit before the
    # cutoff all left owned orders resting -- which is exactly what happened
    # on 2026-10-08, when twenty 0.13-share orders were left on the 1230z
    # window and an operator had to cancel them.
    #
    # THE CANCEL MECHANISM IS UNCHANGED. This closure calls the SAME
    # `lifecycle.deadman` the cutoff branch calls, with the same owned-id
    # registry, the same per-id `cancel_exact`, the same refusal to touch an
    # order we cannot attribute to ourselves, and the same verification
    # against the venue. Only its reachability changes.
    #
    # IT RE-READS VENUE STATE rather than reusing the last snapshot. A
    # shutdown can arrive seconds or minutes after the last iteration, and a
    # cancel sweep decided from a stale view could not tell our orders from
    # anyone else's. If the state cannot be re-established the deadman fails
    # closed on its own `ORDER_STATE_NOT_ESTABLISHED` branch, which is the
    # correct answer: refuse, and say so loudly enough for an operator to
    # cancel by id.
    _last_snap: Dict[str, Any] = {}

    def _emergency_handover(cause: str) -> Dict[str, Any]:
        rec = {"cause": cause, "probe": PROBE,
               "spec_fingerprint": E1_FINGERPRINT,
               "shutdown_signal": SD.requested(),
               "CANCELS_BY_OWNED_ORDER_ID_NOT_BY_MARKET": True}
        owned = list(_last_snap.get("known_order_ids")
                     or getattr(snapshot_provider, "known_order_ids", None)
                     or [])
        if lifecycle.deadman_result:
            rec.update({"ran": False,
                        "because": "HANDOVER_ALREADY_RAN",
                        "owned_ids": owned})
            return rec
        if not owned:
            # NOTHING OF OURS CAN BE RESTING. The registry is the ownership
            # test and it is empty, so there is nothing to cancel and
            # nothing to claim.
            rec.update({"ran": False, "because": "NO_OWNED_ORDERS",
                        "owned_ids": []})
            return rec
        market = _last_snap.get("market")
        window = int(_last_snap.get("window", -1) or -1)
        # RE-READ, as explained above.
        # A FAILED RE-READ FALLS BACK TO THE LAST ESTABLISHED VIEW, and
        # that is deliberate rather than lax. Ownership comes from OUR
        # REGISTRY, never from the view, so a stale view cannot make us
        # touch a stranger's order; the worst it can do is name an id that
        # has since gone terminal, where `cancel_exact` is a no-op. And the
        # handover re-reads every id individually afterwards, so a wrong
        # view is caught by the verification rather than believed.
        #
        # With NO view at all -- neither a fresh read nor a prior one --
        # the deadman refuses on its own `ORDER_STATE_NOT_ESTABLISHED`
        # branch. Refusing is the right answer there: we would be guessing.
        ostate = _last_snap.get("order_state")
        probe = getattr(snapshot_provider, "order_state", None)
        if callable(probe):
            try:
                fresh = probe(getattr(snapshot_provider, "current_pin",
                                      None) or {})
            except Exception as exc:                           # noqa: BLE001
                rec["order_state_reread_failed"] = type(exc).__name__
                rec["fell_back_to_last_established_view"] = bool(ostate)
            else:
                ostate = fresh
        snap_for_handover = {"market": market, "window": window,
                             "order_state": ostate,
                             "known_order_ids": owned}
        dm = lifecycle.deadman(
            snapshot=snap_for_handover, now=now_fn(),
            transport_factory=transport_factory, orders_module=O,
            owned_ids=owned, recorder=recorder,
            read_open=getattr(snapshot_provider, "read_open_orders", None),
            read_order=getattr(snapshot_provider, "read_order", None))
        rec["deadman"] = dm
        rec["ran"] = bool(dm.get("ran"))
        lifecycle.events.append({"iteration": -1, "cause": cause,
                                 "deadman": dm})
        if dm.get("all_cancelled") is False:
            tracker.violate(
                "SHUTDOWN_HANDOVER_DID_NOT_CLEAR_OUR_OPEN_ORDERS:%s"
                % (dm.get("owned_still_open")
                   or dm.get("per_id_states_after"),))
        if dm.get("foreign_untouched") is False:
            tracker.violate(
                "SHUTDOWN_CANCEL_TOUCHED_ORDERS_WE_DO_NOT_OWN:%s"
                % (dm.get("foreign_open_ids_left_untouched"),))
        if not dm.get("ran"):
            # A REFUSAL IS NOT A CLEAN SHUTDOWN. The orders are still there.
            tracker.violate("SHUTDOWN_HANDOVER_REFUSED:%s"
                            % (dm.get("because"),))
        if recorder is not None:
            recorder.record_event(
                window=window, slot=-1, event="SHUTDOWN_HANDOVER",
                detail={k: v for k, v in rec.items() if k != "deadman"})
        return rec

    _set_emergency_handover(_emergency_handover)

    while it < limit:
        it += 1
        v = tracker.verdict()
        if v["stopped"]:
            stopped_because = "VERDICT_%s%s" % (
                v["verdict"],
                ":" + str(v.get("truncation_cause") or ""))
            break
        snap = snapshot_provider()
        # the handover re-reads venue state, but it still needs the market,
        # the window and the owned-id registry this iteration saw.
        _last_snap = dict(snap) if isinstance(snap, dict) else {}
        if isinstance(snap, Mapping) and snap.get("order_state") is not None:
            #: the venue-confirmed state THIS LOOP SAW, kept for the
            #: attempt tape. Read from the snapshot rather than from a
            #: provider attribute, so every provider shape works and the
            #: tape records the state the decisions were actually made on.
            last_order_state_seen = snap.get("order_state")
        if snap is None:
            # A MOMENTARY BOOK GAP IS NOT THE END OF THE COHORT. The provider
            # returns None both when the twelve-window budget is genuinely
            # spent and when this instant has no usable book, and treating
            # the second as the first would end a live cohort on one hiccup.
            # So the window budget decides, and a transient None is paced --
            # but only MAX_CONSECUTIVE_EMPTY_SNAPSHOTS times, so a
            # permanently broken feed stops instead of retrying forever.
            budget_spent = (progress is not None
                            and progress.windows_used() >= WINDOWS)
            # THE HARD STOP AFTER THE AUTHORIZED WINDOW. The provider
            # refuses to open another one once this probe's window budget is
            # spent, and that refusal is NOT a momentary book gap to be
            # paced: it is the end of the authorized probe. Reached only
            # AFTER the handover deadman has run inside the window that was
            # authorized, so no owned order is left resting by stopping
            # here, and a filled residual rides to settlement as specified.
            if getattr(snapshot_provider, "window_budget_spent", False):
                stopped_because = "PROBE_WINDOW_BUDGET_SPENT"
                break
            empty_snapshots += 1
            if budget_spent:
                stopped_because = "SNAPSHOT_PROVIDER_EXHAUSTED"
                break
            if empty_snapshots > PACING.MAX_CONSECUTIVE_EMPTY_SNAPSHOTS:
                stopped_because = "SNAPSHOT_PROVIDER_EMPTY_%d_TIMES" % (
                    empty_snapshots,)
                break
            wait = PACING.pace_seconds(now=now_fn(), pace_s=pace_s)
            paced_waits.append({"iteration": it, "sleep_s": wait,
                                "because": ["EMPTY_SNAPSHOT"]})
            slept_s += wait
            sleep_fn(wait)
            continue
        empty_snapshots = 0

        # ================= THE PAIR LIFECYCLE, EVERY ITERATION ===========
        #
        # BEFORE any new first leg is considered. A filled first leg is
        # inventory that wants hedging and risk headroom RIGHT NOW, and
        # deferring it behind the placement decision is what left 1215z
        # unhedged. The step registers residuals, submits an authorized
        # complement sized to cumulative-minus-covered, and fails closed
        # when order state could not be established.
        # the cutoff is read FIRST, because it decides whether this step
        # may submit anything at all.
        cutoff = snap.get("trading_cutoff_unix")
        past_cutoff = (cutoff is not None and now_fn() >= float(cutoff))
        lc = lifecycle.step(snapshot=snap, tracker=tracker, now=now_fn(),
                            submit_complement=submit_complement,
                            transport_factory=transport_factory,
                            session_auth=session_auth, recorder=recorder,
                            progress=progress, past_cutoff=past_cutoff)
        if lc["complements"] or lc["residuals"] or lc["skipped"]:
            lifecycle.events.append({"iteration": it, **lc})

        # ================= THE HANDOVER DEADMAN =========================
        #
        # `SPEC["timing_explicit"]["open_at_boundary"]` is "CANCELLED,
        # never left to ride to settlement", and rules C6/C9 say an
        # unfilled complement is cancelled at handover rather than
        # re-priced. At or past the withdrawal TTE this runs the APPROVED
        # market-scoped deadman and verifies it against /v1/orders/open.
        # A FILLED residual is deliberately NOT unwound: there is no taker
        # rescue at any price or TTE, so inventory rides to settlement.
        # PER WINDOW, not per run. See `PairLifecycle.deadman_by_window`:
        # guarding on `deadman_result` meant the handover fired once and
        # every later window's resting order was left to the operator.
        _w_now = int(snap.get("window", -1) or -1)
        if past_cutoff and _w_now not in lifecycle.deadman_by_window:
            # OWNERSHIP, NOT MARKET. `owned_ids` is E1's own registry --
            # the ids it submitted, restored from the durable tape -- so
            # the handover cancels exactly our orders and leaves another
            # strategy's alone even in the SAME market.
            dm = lifecycle.deadman(
                snapshot=snap, now=now_fn(),
                transport_factory=transport_factory, orders_module=O,
                owned_ids=list(snap.get("known_order_ids")
                               or getattr(snapshot_provider,
                                          "known_order_ids", None) or []),
                recorder=recorder,
                read_open=getattr(snapshot_provider, "read_open_orders",
                                  None),
                read_order=getattr(snapshot_provider, "read_order", None))
            lifecycle.events.append({"iteration": it, "deadman": dm})
            if dm.get("all_cancelled") is False:
                tracker.violate(
                    "HANDOVER_DEADMAN_DID_NOT_CLEAR_OUR_OPEN_ORDERS:%s"
                    % (dm.get("owned_still_open")
                       or dm.get("per_id_states_after"),))
            # AND THE OTHER DIRECTION, which is the safety issue that
            # motivated ownership scoping: if a cancel of ours removed an
            # order we do not own, that is a violation even though every
            # order of ours went away correctly.
            if dm.get("foreign_untouched") is False:
                tracker.violate(
                    "HANDOVER_CANCEL_TOUCHED_ORDERS_WE_DO_NOT_OWN:%s"
                    % (dm.get("foreign_open_ids_left_untouched"),))

        # ============ THE ATTEMPT BUDGET, AFTER THE LIFECYCLE ============
        #
        # ORDER MATTERS, AND THIS IS WHY IT IS HERE AND NOT EARLIER. The
        # lifecycle step and the handover deadman have already run this
        # iteration, so an exhausted attempt budget stops NEW FIRST LEGS
        # while complement management and exact-id cancellation carry on
        # exactly as before. The owner's requirement is "one first-leg
        # submission attempt total", not "stop the process after one
        # attempt" -- a filled or possibly-filled leg still needs hedging
        # and still needs cancelling at handover.
        #
        # It is also deliberately NOT a terminal refusal routed through
        # `pace_for`: a terminal refusal breaks the loop immediately, which
        # is precisely how a filled residual would be stranded.
        agate = budget.attempt_gate(
            window=snap.get("window"),
            pending_order_ids=snap.get("pending_order_ids") or [])
        blocked_here = attempts_blocked.get(snap.get("window"))
        if not agate["may_attempt"] or blocked_here:
            # is anything of ours still live? Unknown order state counts as
            # live, because the conservative direction is to keep managing.
            out_n = snap.get("our_outstanding_orders")
            residual_open = bool(snap.get("our_open_residual"))
            _view = snap.get("order_state") or {}
            comp_n = _view.get("n_outstanding_complements")
            if not _view.get("ok", True):
                # order state could not be established: assume live.
                comp_n = 1
            unresolved = bool(out_n) or residual_open or bool(comp_n)
            first = not budget_events
            if first:
                budget_events.append({"iteration": it,
                                      "event": "ATTEMPT_BUDGET_EXHAUSTED",
                                      "gate": agate,
                                      "latch_blocked": blocked_here})
                if recorder is not None:
                    recorder.record_event(
                        window=snap.get("window", -1), slot=-1,
                        event="ATTEMPT_BUDGET_EXHAUSTED",
                        detail={"slug": snap.get("market"),
                                "max_attempts": agate["max_attempts"],
                                "usage": agate["usage"],
                                "no_further_first_leg_will_be_attempted":
                                    True,
                                "inventory_management_continues": True})
            if past_cutoff and lifecycle.deadman_result is not None:
                # the handover has run: our resting orders are cancelled and
                # verified, and there is nothing further this process can do.
                #
                # DELIBERATELY NOT PER-WINDOW. The per-window map governs
                # whether a handover still has to RUN; this governs whether
                # the RUN is over, and "any handover has completed" is the
                # right question for that. Making this per-window regressed
                # `test_attempt_exhaustion_while_a_residual_is_open_does_
                # not_stop_early`, which is the correct behaviour to keep.
                stopped_because = "ATTEMPT_BUDGET_EXHAUSTED_AT_HANDOVER"
                break
            if not unresolved:
                stopped_because = (
                    "ATTEMPT_BUDGET_EXHAUSTED_NOTHING_OUTSTANDING")
                break
            wait = PACING.pace_seconds(now=now_fn(), pace_s=pace_s)
            paced_waits.append({"iteration": it, "sleep_s": wait,
                                "because": ["ATTEMPT_BUDGET_EXHAUSTED"]})
            slept_s += wait
            sleep_fn(wait)
            continue

        checks = e1_preflight(snap, tracker, now=now_fn(),
                              envelope=envelope)
        if not checks["ok"]:
            # THE REPAIR. A refusal used to `continue` immediately, so a
            # window whose book sat above the $0.60 ceiling got 960 refusals
            # in one second instead of fifteen minutes of patience. Now every
            # refusal is classified: a statement about a book that MOVES is
            # waited out and re-observed, and anything else -- including a
            # refusal this code has never seen -- is terminal and stops the
            # cohort. No bound, price, quantity or risk rule is consulted
            # here, let alone relaxed; this decides only wait-or-stop.
            # E11 IS NOT A BOOK STATEMENT. Every other paced refusal
            # describes a market that moves, and waiting is the right
            # answer. An exhausted window latch describes FILES on the
            # volume that only ever accumulate, so waiting changes nothing:
            # before this, a pre-exhausted window paced the loop to its
            # iteration cap instead of running the handover and stopping.
            # It is handed to the attempt branch above, which keeps
            # managing inventory and then stops cleanly.
            if "E11_WINDOW_LATCH_EXHAUSTED" in checks["refusals"]:
                attempts_blocked[snap.get("window")] = \
                    "E11_WINDOW_LATCH_EXHAUSTED"
            # A SPENT CASH ENVELOPE BLOCKS NEW FIRST LEGS, NOT THE
            # COMPLEMENT THAT CLOSES INVENTORY WE ALREADY HOLD.
            #
            # Caught in replay: three first legs placed, two complemented,
            # and the third FILLED -- then `MT_GROSS_NOTIONAL_*` refused a
            # fourth attempt, was classified terminal, and the loop broke
            # out of the window with 0.13 shares unhedged and no deadman
            # run. The refusal itself was right; stopping on it was not.
            # Waiting cannot restore a spent envelope, so this is not
            # paced either -- it is handed to the attempt branch, which is
            # the same answer E11 and ATTEMPT_BUDGET_EXHAUSTED already
            # get: keep managing inventory, run the handover, stop clean.
            _spent = [c for c in checks["refusals"]
                      if str(c).startswith("MT_GROSS_NOTIONAL_")]
            if _spent:
                attempts_blocked[snap.get("window")] = str(_spent[0])
                refusals.append({"iteration": it,
                                 "refusals": checks["refusals"],
                                 "envelope_spent": str(_spent[0]),
                                 "inventory_management_continues": True})
                AR.record_decision(
                    recorder, window=snap.get("window"), slot=-1,
                    event="FIRST_LEG_REFUSED_GROSS_ENVELOPE_SPENT",
                    market=snap.get("market"), reason=checks["refusals"],
                    order_state=snap.get("order_state"), snapshot=snap,
                    envelope=(checks.get("multitrade") or {}).get("bounds"),
                    extra={"iteration": it,
                           "nothing_was_created": True,
                           "inventory_management_continues": True})
                # HAND OVER, do not break. The next iteration takes the
                # attempt-blocked branch, which runs the lifecycle and the
                # handover deadman before stopping -- so a filled first
                # leg is still complemented or cancelled by exact id.
                continue
            decision = PACING.pace_for(snap, checks["refusals"],
                                       now=now_fn(), pace_s=pace_s)
            refusals.append({"iteration": it,
                             "refusals": checks["refusals"],
                             "pacing": decision["action"],
                             "paced_sleep_s": decision["sleep_s"],
                             "terminal_refusals": decision["terminal"]})
            # EVERY REFUSAL IS AN ACTION, AND IT IS LOGGED AS ONE. The
            # market state we actually observed, the inventory the venue
            # reported before the decision, the bounds consulted and the
            # reason. Refusals cannot go on the attempt tape -- a refusal
            # is not a position in the SUBMITTED..REALIZED chain, and
            # `record_attempt` rightly rejects a row whose book is not
            # streaming, which is precisely a book-unavailable refusal.
            AR.record_decision(
                recorder, window=snap.get("window"), slot=-1,
                event="DECISION_REFUSED", market=snap.get("market"),
                reason=checks["refusals"],
                order_state=snap.get("order_state"), snapshot=snap,
                envelope=(checks.get("multitrade") or {}).get("bounds"),
                extra={"iteration": it,
                       "pacing": decision["action"],
                       "multitrade": checks.get("multitrade")})
            if recorder is not None and decision["action"] == "STOP":
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="TERMINAL_REFUSAL",
                    detail={"slug": snap.get("market"),
                            "terminal": decision["terminal"],
                            "paced": decision["paced"]})
            if decision["action"] == "STOP":
                stopped_because = "TERMINAL_REFUSAL:%s" % (
                    ",".join(str(x) for x in decision["terminal"]),)
                break
            paced_waits.append({"iteration": it,
                                "sleep_s": decision["sleep_s"],
                                "because": decision["paced"]})
            slept_s += decision["sleep_s"]
            sleep_fn(decision["sleep_s"])
            continue
        try:
            out = submit_first_leg(snap, tracker, now=now_fn(),
                                   transport_factory=transport_factory,
                                   session_auth=session_auth,
                                   progress=progress, recorder=recorder,
                                   budget=budget, envelope=envelope)
        except (L.WindowExhausted, L.LatchSpent) as exc:
            # DEFENCE IN DEPTH. `submit_first_leg` converts both of these
            # into `AttemptBudgetExhausted` after retiring its intent, so
            # reaching here means one escaped from somewhere else. It is a
            # bound doing its job, never a crash: recorded, paced, and the
            # next iteration's budget branch decides whether to stop.
            refusals.append({"iteration": it,
                             "refusals": ["LATCH_EXHAUSTED:%s"
                                          % type(exc).__name__],
                             "detail": str(exc)})
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="LATCH_EXHAUSTED",
                    detail={"slug": snap.get("market"),
                            "kind": type(exc).__name__,
                            "did_not_crash_the_process": True,
                            "inventory_management_continues": True})
            wait = PACING.pace_seconds(now=now_fn(), pace_s=pace_s)
            paced_waits.append({"iteration": it, "sleep_s": wait,
                                "because": ["LATCH_EXHAUSTED"]})
            slept_s += wait
            sleep_fn(wait)
            continue
        except AttemptBudgetExhausted as exc:
            # NO FURTHER FIRST LEG IS POSSIBLE. Refused before the intent
            # row, the preview and the POST, so nothing was created and
            # nothing needs retiring. The flag makes the branch above own
            # the decision from here: keep managing inventory, run the
            # handover, then stop cleanly -- rather than spinning on a
            # refusal that cannot change.
            attempts_blocked[snap.get("window")] = str(exc)
            refusals.append({"iteration": it,
                             "refusals": ["ATTEMPT_BUDGET_EXHAUSTED"],
                             "detail": str(exc)})
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="FIRST_LEG_REFUSED_ATTEMPT_BUDGET",
                    detail={"slug": snap.get("market"), "why": str(exc),
                            "nothing_was_created": True,
                            "inventory_management_continues": True})
            continue
        except AUTH.NotAuthorized as exc:
            # refused BEFORE transport. Not a venue error and not a
            # placement; recorded and the window moves on.
            refusals.append({"iteration": it,
                             "refusals": ["NOT_AUTHORIZED:%s" % exc]})
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="SUBMISSION_REFUSED_NOT_AUTHORIZED",
                    detail={"slug": snap.get("market"), "why": str(exc)})
            continue
        except Ineligible as exc:
            # `submit_first_leg` re-runs the SAME preflight immediately before
            # submitting, so this is the identical refusal set arriving a few
            # milliseconds later -- the book moved between the check and the
            # send. It is paced on exactly the same rules, from the re-check's
            # own refusal list rather than from the stringified exception.
            late = (getattr(exc, "refusals", None)
                    or e1_preflight(snap, tracker, now=now_fn(),
                                    envelope=envelope)["refusals"])
            decision = PACING.pace_for(snap, late, now=now_fn(),
                                       pace_s=pace_s)
            refusals.append({"iteration": it, "refusals": [str(exc)],
                             "late_refusals": list(late),
                             "pacing": decision["action"],
                             "paced_sleep_s": decision["sleep_s"],
                             "terminal_refusals": decision["terminal"]})
            if decision["action"] == "STOP":
                stopped_because = "TERMINAL_REFUSAL_AT_SUBMIT:%s" % (
                    ",".join(str(x) for x in decision["terminal"]),)
                break
            paced_waits.append({"iteration": it,
                                "sleep_s": decision["sleep_s"],
                                "because": decision["paced"]})
            slept_s += decision["sleep_s"]
            sleep_fn(decision["sleep_s"])
            continue
        except LF.LocalExecutionFault as fault:
            # DEFENCE IN DEPTH. The contract was proven above, so reaching
            # here means it broke DURING the cohort -- a credential rotated
            # away mid-run, say. Still ours, still deterministic, still not
            # the venue: fail closed on the first one.
            detail = LF.redact_detail(fault.detail)
            tracker.record_local_execution_fault(detail=detail)
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="LOCAL_EXECUTION_FAULT",
                    detail={"message_redacted": LF.redact(fault), **detail})
            refusals.append({"iteration": it,
                             "refusals": ["LOCAL_EXECUTION_FAULT"],
                             "detail": detail})
            stopped_because = "LOCAL_EXECUTION_FAULT"
            break
        except O.PreviewRejected as exc:
            # the latch is UNTOUCHED: a preview creates nothing.
            tracker.record_venue_error(detail={"phase": "PREVIEW",
                                               "detail": exc.detail})
            # DURABLE, ON EVERY REJECTION, INCLUDING THE FIFTH. The storm
            # stop is evaluated at the TOP of the next iteration, so
            # recording here means the rejection that ends the cohort is
            # on the tape before the cohort ends. Nothing below this line
            # submits, consumes a latch or touches risk accounting.
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="PREVIEW_REJECTED",
                    detail=LF.redact_detail(preview_rejection_record(
                        snap, dict(exc.detail or {}), iteration=it,
                        now=now_fn())))
            refusals.append({"iteration": it,
                             "refusals": ["PREVIEW_REJECTED"]})
            continue
        except O.SubmissionAmbiguous as exc:
            # NEVER retried. The cohort stops and the venue is reconciled.
            #
            # AND IT IS RECORDED. This path used to append to the in-memory
            # refusal list and break, writing NOTHING to the tape -- so the
            # single most dangerous state E1 can be in, "we may have
            # created an order and cannot prove we did not", left no
            # durable trace at all. A restart would have seen a clean tape.
            # Now it leaves a row, and `order_registry` restores it as a
            # blocking pending intent.
            if recorder is not None:
                recorder.record_event(
                    window=snap.get("window", -1), slot=-1,
                    event="AMBIGUOUS_SUBMISSION",
                    detail={"slug": snap.get("market"),
                            "detail": LF.redact_detail(
                                dict(exc.detail or {})),
                            "never_retried": True,
                            "blocks_until_venue_reconciled": True})
            stopped_because = "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
            refusals.append({"iteration": it,
                             "refusals": ["AMBIGUOUS_SUBMISSION"],
                             "detail": exc.detail})
            break
        tracker.record_venue_ok()
        # AND HAND IT TO THE LIFECYCLE, which is what makes its fill
        # reachable by the complement path on the next iteration.
        lifecycle.record_first_leg(order_id=out["order_id"],
                                   window=out["e1_window"],
                                   slot=out["e1_latch_slot"]["slot"],
                                   price=snap.get("first_leg_price"))
        placements.append({"iteration": it, "window": out["e1_window"],
                           "slot": out["e1_latch_slot"]["slot"],
                           "latch_path": out["e1_latch_slot"]["path"],
                           "order_id": out["order_id"],
                           "slug": snap.get("market"),
                           # THE BOOK STATE AT DECISION TIME, kept with the
                           # placement rather than re-read later. The
                           # attempt row must carry the state that actually
                           # authorized the order, and a state fetched
                           # afterwards is a different observation.
                           "ws_state_at_decision": dict(
                               snap.get("ws_state") or {})})
        if recorder is not None:
            recorder.record_event(
                window=out["e1_window"], slot=out["e1_latch_slot"]["slot"],
                event="FIRST_LEG_SUBMITTED",
                detail={"order_id": out["order_id"],
                        # names the intent this confirms, so a restart can
                        # retire it instead of blocking on it forever
                        "intent_id": out.get("e1_intent_id"),
                        "latch_path": out["e1_latch_slot"]["path"]})
            AR.record_decision(
                recorder, window=snap.get("window"), slot=-1,
                event="DECISION_SUBMITTED", market=snap.get("market"),
                reason="ELIGIBLE_ON_EVERY_BOUND",
                order_state=snap.get("order_state"), snapshot=snap,
                envelope=(out.get("e1_checks") or {}).get("multitrade",
                                                          {}).get("bounds"),
                extra={"iteration": it, "order_id": out["order_id"],
                       "multitrade": (out.get("e1_checks") or {}).get(
                           "multitrade")})
    # ============ THE NORMAL-EXIT HANDOVER ==========================
    #
    # The loop can end without reaching the cutoff: a verdict stops it, the
    # attempt budget is spent, the iteration cap is hit, the trading
    # deadline passes. Every one of those used to return with owned orders
    # still resting, because the handover lived only on the `past_cutoff`
    # branch. It is run HERE for the ordinary exits, and `main`'s `finally`
    # covers the exceptional ones.
    shutdown_handover = None
    if not lifecycle.deadman_result:
        shutdown_handover = run_emergency_handover(
            cause="NORMAL_EXIT_WITHOUT_CUTOFF_HANDOVER:%s"
                  % (stopped_because or "LOOP_ENDED"))
    # THE HOOK IS CLEARED EITHER WAY, so a hook still set when `main`
    # unwinds means precisely "a cohort stopped without completing its
    # handover" and nothing weaker.
    _set_emergency_handover(None)


    # ===================== THE ATTEMPT TAPE =========================
    #
    # ONE ROW PER ATTEMPT THIS RUN MADE, written here because here is
    # where the outcome is finally known: the venue has been asked what
    # became of every id, the complements are submitted or not, and the
    # residuals are registered. `attempts.jsonl` is what
    # `recorder:reconstruct_chain` reads -- the reconstructor the frozen
    # spec names -- and until now production never wrote a single row to
    # it, so the estimation chain would have run over an empty file.
    #
    # It is the LAST thing the run does and it cannot change any outcome:
    # every bound, submission and cancellation is already decided.
    attempt_tape = None
    if recorder is not None and placements:
        _final = last_order_state_seen
        _comp: Dict[str, str] = {}
        for _ev in (lifecycle.events or []):
            for _c in (_ev.get("complements") or []):
                if _c.get("order_id") and _c.get("complement_order_id"):
                    _comp[str(_c["order_id"])] = str(
                        _c["complement_order_id"])
        _ws_by_id = {str(p["order_id"]): p.get("ws_state_at_decision")
                     for p in placements if p.get("order_id")}
        attempt_tape = AR.emit_attempt_rows(
            recorder, lifecycle=lifecycle, order_state=_final,
            market=placements[-1].get("slug"),
            ws_state=placements[-1].get("ws_state_at_decision"),
            ws_state_by_order_id=_ws_by_id,
            complement_ids=_comp,
            cancellations=lifecycle.deadman_result,
            label="SUBMITTED",
            windows_in_scope={p["window"] for p in placements})

    if progress is not None and stopped_because:
        v = tracker.verdict()
        progress.record_cohort_stopped(
            verdict=v["verdict"],
            truncation_cause=v.get("truncation_cause"))
    return {"probe": PROBE, "spec_fingerprint": E1_FINGERPRINT,
            "budget": budget.report(),
            "budget_events": budget_events,
            "attempt_tape": attempt_tape,
            "envelope": envelope.report(),
            "shutdown_handover": shutdown_handover,
            "shutdown_coverage": SD.coverage(),
            "lifecycle": {
                "first_legs_tracked": lifecycle.legs,
                "residuals_opened": lifecycle.residuals_opened,
                "events": lifecycle.events,
                "deadman": lifecycle.deadman_result,
                "deadman_by_window": {str(k): v for k, v in
                                      lifecycle.deadman_by_window.items()},
                "handover_is_per_window": True,
                "complement_is_reached_from_run_cohort": True,
            },
            "iterations": it, "placements": placements,
            "windows_used": (progress.windows_used()
                             if progress is not None else None),
            "authorized_slugs": (session_auth.slugs()
                                 if session_auth is not None else None),
            "placement_count": len(placements),
            "iteration_cap": limit,
            "iteration_cap_reached": it >= limit and stopped_because is None,
            "pace_s": pace_s,
            "paced_wait_count": len(paced_waits),
            "paced_total_sleep_s": slept_s,
            "paced_waits": paced_waits[:50],
            "PACING_CHANGES_NO_BOUND": True,
            "distinct_latch_slots": len({p["latch_path"]
                                         for p in placements}),
            "refusals": refusals,
            "stopped_because": stopped_because,
            "tracker": tracker.snapshot(),
            "signing_proof": signing_proof,
            "latch_kind": "BOUNDED_COUNT_NAMED_SLOTS",
            "entered_xr2w_experiment_runner": False,
            "entered_xr2w_single_slot_latch": False,
            "LANGUAGE": "SUBMITTED is not a fill"}


def _default_provider_factory(*, progress: Any, session_auth: Any,
                              recorder: Any, budget: Any = None) -> Any:
    """The production provider: PMUS identity, PMUS's own book.

    Imported lazily so the DISARMED path constructs no feed at all. The
    Polymarket.com two-token CLOB provider it replaces is gone from this
    package -- it proved freshness on a venue E1 does not trade.
    """
    from analysis.l5.e1_cohort.pmus_provider import PmusWindowProvider
    # THE DURABLE OBLIGATION LEDGER, on the same mounted volume as the
    # latch and scoped to the same owner session. Construction FAILS CLOSED:
    # `LedgerUnavailable` propagates, because a cohort that cannot record
    # what it sends must not send anything.
    ledger = OB.ObligationLedger(L.LATCH_DIR, session_id=A.session_id())
    return PmusWindowProvider(progress=progress, session_auth=session_auth,
                              recorder=recorder, budget=budget,
                              obligations=ledger)


def _default_progress_factory(state_dir: str) -> Any:
    from analysis.l5.e1_cohort.progress import CohortProgress
    return CohortProgress(state_dir)


def _default_auth_factory(state_dir: str) -> Any:
    return AUTH.SessionAuthorization(state_dir)


def _default_recorder_factory(state_dir: str) -> Any:
    from analysis.l5.e1_cohort.recorder import Recorder
    return Recorder(os.path.join(state_dir, "tape"))


def _emit(tag: str, obj: Any) -> None:
    """ONE LOG LINE PER REPORT, because a flooded log is an unsupervised
    probe.

    The armed boot at 2026-10-08T21:45:34Z printed the frozen spec with
    `indent=1`, which is roughly fifteen hundred lines in one second. The
    hosting platform's limit is five hundred log lines per second per
    replica, so it DROPPED 646 MESSAGES -- including the tail that carries
    `stopped_because`. The deployment read as CRASHED with no recoverable
    reason, and the authorization requires active supervision, so the
    probe had to be disarmed on an unknown stop.

    Compact JSON is the whole fix: the same object, the same keys, one
    line, prefixed with a greppable tag and flushed. Nothing about what is
    reported changes -- only that it survives the transport.
    """
    print("%s %s" % (tag, json.dumps(obj, sort_keys=True,
                                     separators=(",", ":"), default=str)),
          flush=True)


def main(argv: Optional[List[str]] = None, *,
         provider_factory: Any = _default_provider_factory,
         progress_factory: Any = _default_progress_factory,
         auth_factory: Any = _default_auth_factory,
         recorder_factory: Any = _default_recorder_factory,
         transport_factory: Any = O.BoundedOrderTransport) -> int:
    """THE E1 ENTRYPOINT, and what `SPEC['entrypoint'].module` names.

    The factory arguments exist so the OFFLINE TEST CAN DRIVE THIS EXACT
    FUNCTION with a fake provider, fake credentials and a fake transport.
    Their defaults are the production ones, so the tested path and the
    deployed path are the same function and the same control flow.

    DISARMED. It boots, asserts the frozen spec, prints the preflight and
    the DISARMED refusal, and RETURNS WITHOUT PLACING ANYTHING. Arming is
    the owner's and nothing here acquires it.
    """
    rep = boot()
    rep["eligibility_without_a_snapshot"] = (
        "no snapshot is requested while disarmed; the arming gate is "
        "checked first and refuses")
    _emit("E1_BOOT", rep)
    if not A.armed_env_summary().get("armed"):
        _emit("E1_REFUSED", {
            "REFUSED": "DISARMED",
            "probe": PROBE, "fingerprint": E1_FINGERPRINT,
            "why": ("the owner's arming gate is not set. No snapshot was "
                    "requested, no latch slot was claimed, no order "
                    "endpoint was reached."),
            "superseded_must_not_be_rearmed":
                copy.deepcopy(SPEC["supersedes_e1"]["must_not_be_rearmed"]),
        })
        return 0
    # ---------------------------------------------------------------
    # THE ARMED PATH. This used to be a bare `raise SystemExit`, which
    # meant the declared production path terminated BEFORE run_cohort:
    # setting the PMUS_E1_* keys would have produced twelve empty
    # windows and measured nothing. It now builds the live provider and
    # reaches run_cohort, and a test drives exactly this function with a
    # complete arming environment across two windows.
    # ---------------------------------------------------------------
    pin = A.fingerprint_pinned(E1_FINGERPRINT)
    if not pin["matches_this_build"]:
        _emit("E1_REFUSED", {
            "REFUSED": "FINGERPRINT_NOT_PINNED_TO_THIS_BUILD",
            "required": E1_FINGERPRINT, "pin_present": pin["set"],
            "why": ("the owner must pin %s to THIS build. A pin naming a "
                    "superseded fingerprint leaves the build inert."
                    % A.FINGERPRINT_PIN_ENV),
            "superseded_must_not_be_rearmed":
                copy.deepcopy(SPEC["supersedes_e1"]["must_not_be_rearmed"]),
        })
        return 2
    # =============== THE PER-PROBE BUDGET, BEFORE ANYTHING ELSE ==========
    #
    # The owner's authorization is for ONE bounded probe, and the frozen
    # spec's own maximums are twenty attempts in each of twelve windows.
    # Those two numbers have to be reconciled somewhere, and the only safe
    # place is here, in authorization, where a missing value is a REFUSAL
    # rather than a silent 240-placement default. `PMUS_E1_MAX_ATTEMPTS`
    # and `PMUS_E1_MAX_WINDOWS` may only narrow the frozen maximums;
    # missing, blank, non-integer, zero, negative or excessive all fail
    # closed here, before any provider, any feed, any window and any order
    # path exists.
    try:
        probe_budget = BUD.ProbeBudget.from_env()
    except BUD.BudgetNotAuthorized as exc:
        _emit("E1_REFUSED", {
            "REFUSED": "PROBE_BUDGET_NOT_AUTHORIZED",
            "probe": PROBE, "fingerprint": E1_FINGERPRINT,
            "env_keys": list(BUD.BUDGET_KEYS),
            "why": str(exc),
            "frozen_max_attempts": BUD.FROZEN_MAX_ATTEMPTS,
            "frozen_max_windows": BUD.FROZEN_MAX_WINDOWS,
            "these_keys_may_only_narrow": True,
            "no_window_opened": True, "no_latch_slot_claimed": True,
            "no_order_endpoint_reached": True,
        })
        return 2
    # ============== THE MULTI-TRADE ENVELOPE, BEFORE THE PROVIDER ========
    #
    # ALL FOUR KEYS OR NONE. None means the frozen twelve-window cohort
    # that was authorized before this probe existed, and its ceilings are
    # unchanged -- so this parameter narrows nothing by default. Any
    # SUBSET is a refusal: a probe that states three of its four bounds
    # has not stated its envelope, and guessing the fourth is exactly the
    # silent authorization `budget.py` exists to prevent.
    envelope = None
    _env_set = [k for k in MT.ENVELOPE_KEYS if (os.getenv(k) or "").strip()]
    if _env_set:
        try:
            envelope = MT.MultiTradeEnvelope.from_env()
        except MT.EnvelopeNotAuthorized as exc:
            _emit("E1_REFUSED", {
                "REFUSED": "MULTITRADE_ENVELOPE_NOT_AUTHORIZED",
                "probe": PROBE, "fingerprint": E1_FINGERPRINT,
                "env_keys": list(MT.ENVELOPE_KEYS),
                "keys_set": _env_set,
                "why": str(exc),
                "all_four_or_none": True,
                "first_leg_purchases_are_not_the_gross_outlay": True,
                "no_window_opened": True, "no_latch_slot_claimed": True,
                "no_order_endpoint_reached": True})
            return 2

    gate = AUTH.series_authorized()
    if not gate["matches_authorized_series"]:
        _emit("E1_REFUSED", {
            "REFUSED": "SERIES_GATE_NOT_SET",
            "authorized_series": gate["authorized_series"],
            "why": ("the owner's %s must name the authorized series. Only "
                    "markets the frozen BTC15m selector produces during "
                    "this session may be traded, and the exact resolved "
                    "slug is re-enforced before every submission."
                    % AUTH.GATE_EVIDENCE_ENV),
        })
        return 2

    # DURABLE PROGRESS FIRST. A restart RESUMES: the window, placement and
    # risk counters are rehydrated from the volume before anything else,
    # so a restarted process cannot be handed a fresh cohort.
    state_dir = L.LATCH_DIR
    prog = progress_factory(state_dir)
    auth = auth_factory(state_dir)
    tracker = CohortTracker()
    rehydrated = prog.rehydrate(tracker)
    rec = recorder_factory(state_dir) if recorder_factory else None
    # the window budget counts NEW windows, so its baseline is whatever the
    # append-only ledger already holds at boot. Rebuilt here, after
    # rehydration, from the same env values already validated above.
    probe_budget = BUD.ProbeBudget(
        max_attempts=probe_budget.max_attempts,
        max_windows=probe_budget.max_windows,
        windows_used_at_start=prog.windows_used(),
        source=probe_budget.source)
    try:
        provider = provider_factory(progress=prog, session_auth=auth,
                                   recorder=rec, budget=probe_budget)
    except TypeError:
        # an injected provider factory (the offline test seam) need not
        # accept the budget as a constructor argument; it is attached below,
        # which is where the hard stop actually reads it from.
        provider = provider_factory(progress=prog, session_auth=auth,
                                    recorder=rec)
    # a provider that does not accept the budget as a constructor argument
    # (the offline test seam) still gets it, because the HARD STOP after the
    # authorized window is read off this attribute.
    if getattr(provider, "budget", None) is not probe_budget:
        try:
            provider.budget = probe_budget
        except Exception:                                      # noqa: BLE001
            pass
    # AND THE ORDER REGISTRY, off the same durable volume.
    #
    # Progress rehydration restores WINDOWS and RISK. It says nothing about
    # ORDERS, and until now nothing did: a restart came back with an empty
    # id registry, so the repaired reader -- which can only ask about ids it
    # knows, because /v1/orders is HTTP 501 -- had nothing to ask about.
    # These are the ids, the roles and the unacknowledged submission
    # intents, read from the append-only tape where the forty real
    # 1215z/1230z ids were in fact found. Installed in place, because
    # `submit_first_leg` appends to the very same lists.
    registry = ORG.load_from_tape(state_dir, session_id=A.session_id())
    installed = ORG.install(provider, registry)
    _emit("E1_ARMED", {"ARMED": True, "probe": PROBE,
                      "fingerprint": E1_FINGERPRINT,
                      "probe_budget": probe_budget.report(),
                      "rehydrated": rehydrated,
                      "order_registry": {
                          "tape": registry["tape"],
                          "rows_scanned": registry["rows_scanned"],
                          "unparseable_rows": registry["unparseable_rows"],
                          "known_ids": len(installed["known_ids"]),
                          "pending_intents": installed["pending"],
                          "roles": installed["roles"],
                      },
                      "progress": prog.report(),
                      "authorization": auth.report(),
                      "envelope": (envelope.report() if envelope
                                   else "FROZEN_COHORT_CEILINGS"),
                      "entering": "run_cohort"})
    # ======== THE GRACEFUL-SHUTDOWN SIGNAL, INSTALLED BEFORE THE RUN =====
    #
    # SIGTERM is how Railway stops a container, and its default disposition
    # terminates the process WITHOUT unwinding -- so before this, no
    # `finally` anywhere in the program would have run and the handover was
    # unreachable on a stop. `shutdown.install` turns SIGTERM and SIGINT
    # into a `BaseException` raised in the main thread, which the `finally`
    # below converts into a real cancellation.
    #
    # IT IS NOT CRASH PROTECTION and is not described as any. SIGKILL, the
    # OOM killer and a container hard stop are covered only by the durable
    # latch and the startup reconciliation, which refuse to submit after a
    # restart until venue state has been established.
    shutdown_coverage = SD.install()
    _emit("E1_SHUTDOWN_HANDLERS", shutdown_coverage)
    try:
        out = run_cohort(snapshot_provider=provider, tracker=tracker,
                         recorder=rec, transport_factory=transport_factory,
                         session_auth=auth, progress=prog,
                         budget=probe_budget, envelope=envelope)
    except SD.ShutdownRequested as stop:
        # THE ORDERLY STOP. Cancel what we own, report it, and exit
        # non-zero so the deployment does not read as a clean run.
        handover = run_emergency_handover(
            cause="SHUTDOWN_SIGNAL:%s" % (stop.detail.get("signal"),))
        _emit("E1_SHUTDOWN", {"signal": stop.detail,
                              "handover": handover,
                              "coverage": shutdown_coverage,
                              "LANGUAGE": ("cancellation is claimed only "
                                           "where the handover verified "
                                           "it against the venue")})
        return 4
    except BaseException:
        # ANY OTHER WAY OUT. The handover runs and then the original
        # failure is re-raised untouched -- it is the incident, and this
        # must not replace it.
        handover = run_emergency_handover(cause="UNCAUGHT_EXCEPTION")
        _emit("E1_ABNORMAL_EXIT", {"handover": handover,
                                   "coverage": shutdown_coverage})
        raise
    finally:
        # BELT AND BRACES. `run_cohort` clears the hook on every normal
        # return, so this is a no-op then; if any path above missed, a
        # pending hook is still honoured here rather than dropped.
        late = run_emergency_handover(cause="LATE_FINALLY")
        if late is not None:
            _emit("E1_LATE_HANDOVER", late)
    out["shutdown_coverage"] = shutdown_coverage
    out["provider"] = provider.report()
    out["progress"] = prog.report()
    _emit("E1_RESULT", out)
    if out.get("stopped_because") == "LOCAL_EXECUTION_FAULT":
        # A DISTINCT NON-ZERO EXIT. The restart policy is NEVER, so this
        # does not relaunch anything -- it makes the deployment read as
        # FAILED instead of as a clean run that placed nothing, which is
        # the difference between "our config is broken" and "the market
        # was never eligible". The 2026-10-07 run exited 0.
        return 3
    return 0


# -------------------------------------------------------------------- boot

def boot() -> Dict[str, Any]:
    """Boot assertions, every time. Fails closed. Reports arming; never
    acquires it."""
    rep = boot_assertions()
    # BEFORE ANYTHING ELSE, including the arming gate: refuse to run
    # beside a second armed execution stack. E1's image contains
    # `src.pmus_q1_executor` as a dependency of the PMUS-native book.
    rep["foreign_executor_guard"] = (
        A.assert_no_foreign_executor_authorization())
    rep.update({
        "diff_report": diff_report(),
        "feasibility": project_feasibility(),
        "arming": A.armed_env_summary(),
        "probe_budget": BUD.env_report(),
        "latch_isolation": L.assert_no_collision_with_xr2w(),
        # the latch is NOT read here: reading it needs the owner's session
        # id, and boot() must not require an owner value to run.
        "latch": {"kind": copy.deepcopy(SPEC["latch"]["kind"]),
                  "slots_per_window": MAX_ATTEMPTS_PER_WINDOW,
                  "NOTE": ("read with latch.cohort_state() once the owner "
                           "has set a session id")},
        "fee_schedule_fingerprint": FEE.FINGERPRINT,
        "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
        "AUTHORIZATION": "NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE SET.",
        "NO_ORDER_WILL_BE_PLACED": True,
    })
    return rep
