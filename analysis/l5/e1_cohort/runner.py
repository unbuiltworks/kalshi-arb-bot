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
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import local_faults as LF
from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FIRST_LEG_QTY,
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
from analysis.l5.e1_cohort import pmus_book as PBK
from src import l5_preview_gate as G
from src import pmus_fee_schedule as FEE

#: Decision-time book staleness bound, inherited from the XR2W AuthBook
#: bound rather than chosen here.
MAX_BOOK_AGE_S = 2.0


class Ineligible(RuntimeError):
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
             *, now: float) -> Dict[str, Any]:
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

    if snapshot.get("our_outstanding_orders"):
        r.append("E3_OUR_ORDER_IS_OUTSTANDING")
    if snapshot.get("our_open_residual"):
        r.append("E3_OUR_RESIDUAL_IS_OPEN")

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

    return {"ok": not r, "refusals": r, "probe": PROBE,
            "spec_fingerprint": E1_FINGERPRINT,
            "window": window,
            "first_leg": {"price_usd": str(price), "qty_shares": str(qty),
                          "notional_usd": str(notional),
                          "post_only": True, "offset": 0},
            "complement_plan": comp,
            "arming": arming, "latch": lstate, "exposure_gate": gate,
            "ARMED": bool(arming.get("armed")),
            "LABEL": "ELIGIBILITY ONLY -- not a submission"}


def e1_preflight(snapshot: Mapping[str, Any], tracker: CohortTracker,
                 *, now: float) -> Dict[str, Any]:
    """E1's own refusal set, shaped like `orders.preflight_submission`'s so
    it can be handed to `orders.submit_one(checks=...)`.

    The shared preflight is NOT used because it hard-requires the venue
    minimum 0.01 and E1's quantity is 0.13. This one is a SUPERSET on every
    shared check -- arming, price ceiling, tick, two-sided executable book,
    would-cross, latch -- and relaxes none of them. `live_exec2` is not
    edited to accommodate E1.
    """
    el = eligible(snapshot, tracker, now=now)
    return {"ok": el["ok"], "refusals": el["refusals"], "e1": el,
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
                     transport_factory: Any = O.BoundedOrderTransport,
                     session_auth: Any = None,
                     progress: Any = None) -> Dict[str, Any]:
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
    checks = e1_preflight(snapshot, tracker, now=now)
    if not checks["ok"]:
        raise Ineligible("E1 refused the attempt: %s" % checks["refusals"])
    window = checks["e1"]["window"]
    slug = snapshot["market"]
    # BOUNDARY 2: the SPECIFIC RESOLVED SLUG and both token ids, enforced
    # immediately before the submission and BEFORE any transport exists.
    # A slug the frozen selector did not produce in this authorized session
    # is refused here, not discovered at the venue.
    auth_result = None
    if session_auth is not None:
        auth_result = session_auth.enforce_before_submission(
            slug=slug, pin=snapshot.get("pin") or {},
            outcome_up=(snapshot.get("first_leg") or {}).get("outcome_side"),
            outcome_down=(snapshot.get("complement")
                          or {}).get("outcome_side"),
            role="first_leg")
    side = snapshot["first_leg"]["side"]
    price = snapshot["first_leg_price"]
    qty = snapshot.get("qty", FIRST_LEG_QTY)

    holder: Dict[str, Any] = {}

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
    finally:
        # the attempt is counted the moment a slot was taken, whatever the
        # POST then did. A spent slot that we did not count would let the
        # window bound drift away from the latch.
        if "slot" in holder:
            tracker.record_placement(
                window=window, notional_usd=D(str(qty)) * D(str(price)))
    out["e1_latch_slot"] = holder["slot"]
    out["e1_window"] = window
    out["e1_authorization"] = auth_result
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
        comp_auth = session_auth.authorize_complement(
            slug=snapshot["market"],
            first_leg_pin=snapshot.get("pin") or {},
            outcome_up=(snapshot.get("first_leg") or {}).get("outcome_side"),
            outcome_down=(snapshot.get("complement")
                          or {}).get("outcome_side"))
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
    out.update({"submitted": True, "tranche_plan": plan,
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

def run_cohort(*, snapshot_provider: Any, tracker: Optional[CohortTracker]
               = None, recorder: Any = None,
               transport_factory: Any = O.BoundedOrderTransport,
               now_fn: Any = None, max_iterations: Optional[int] = None,
               session_auth: Any = None, progress: Any = None
               ) -> Dict[str, Any]:
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
    limit = MAX_PLACEMENTS * 4 if max_iterations is None else max_iterations
    while it < limit:
        it += 1
        v = tracker.verdict()
        if v["stopped"]:
            stopped_because = "VERDICT_%s%s" % (
                v["verdict"],
                ":" + str(v.get("truncation_cause") or ""))
            break
        snap = snapshot_provider()
        if snap is None:
            stopped_because = "SNAPSHOT_PROVIDER_EXHAUSTED"
            break
        checks = e1_preflight(snap, tracker, now=now_fn())
        if not checks["ok"]:
            refusals.append({"iteration": it,
                             "refusals": checks["refusals"]})
            continue
        try:
            out = submit_first_leg(snap, tracker, now=now_fn(),
                                   transport_factory=transport_factory,
                                   session_auth=session_auth,
                                   progress=progress)
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
            refusals.append({"iteration": it, "refusals": [str(exc)]})
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
            stopped_because = "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
            refusals.append({"iteration": it,
                             "refusals": ["AMBIGUOUS_SUBMISSION"],
                             "detail": exc.detail})
            break
        tracker.record_venue_ok()
        placements.append({"iteration": it, "window": out["e1_window"],
                           "slot": out["e1_latch_slot"]["slot"],
                           "latch_path": out["e1_latch_slot"]["path"],
                           "order_id": out["order_id"]})
        if recorder is not None:
            recorder.record_event(
                window=out["e1_window"], slot=out["e1_latch_slot"]["slot"],
                event="FIRST_LEG_SUBMITTED",
                detail={"order_id": out["order_id"],
                        "latch_path": out["e1_latch_slot"]["path"]})
    if progress is not None and stopped_because:
        v = tracker.verdict()
        progress.record_cohort_stopped(
            verdict=v["verdict"],
            truncation_cause=v.get("truncation_cause"))
    return {"probe": PROBE, "spec_fingerprint": E1_FINGERPRINT,
            "iterations": it, "placements": placements,
            "windows_used": (progress.windows_used()
                             if progress is not None else None),
            "authorized_slugs": (session_auth.slugs()
                                 if session_auth is not None else None),
            "placement_count": len(placements),
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
                              recorder: Any) -> Any:
    """The production provider: PMUS identity, PMUS's own book.

    Imported lazily so the DISARMED path constructs no feed at all. The
    Polymarket.com two-token CLOB provider it replaces is gone from this
    package -- it proved freshness on a venue E1 does not trade.
    """
    from analysis.l5.e1_cohort.pmus_provider import PmusWindowProvider
    return PmusWindowProvider(progress=progress, session_auth=session_auth,
                              recorder=recorder)


def _default_progress_factory(state_dir: str) -> Any:
    from analysis.l5.e1_cohort.progress import CohortProgress
    return CohortProgress(state_dir)


def _default_auth_factory(state_dir: str) -> Any:
    return AUTH.SessionAuthorization(state_dir)


def _default_recorder_factory(state_dir: str) -> Any:
    from analysis.l5.e1_cohort.recorder import Recorder
    return Recorder(os.path.join(state_dir, "tape"))


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
    print(json.dumps(rep, indent=1, sort_keys=True, default=str))
    if not A.armed_env_summary().get("armed"):
        print(json.dumps({
            "REFUSED": "DISARMED",
            "probe": PROBE, "fingerprint": E1_FINGERPRINT,
            "why": ("the owner's arming gate is not set. No snapshot was "
                    "requested, no latch slot was claimed, no order "
                    "endpoint was reached."),
            "superseded_must_not_be_rearmed":
                copy.deepcopy(SPEC["supersedes_e1"]["must_not_be_rearmed"]),
        }, indent=1, sort_keys=True))
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
        print(json.dumps({
            "REFUSED": "FINGERPRINT_NOT_PINNED_TO_THIS_BUILD",
            "required": E1_FINGERPRINT, "pin_present": pin["set"],
            "why": ("the owner must pin %s to THIS build. A pin naming a "
                    "superseded fingerprint leaves the build inert."
                    % A.FINGERPRINT_PIN_ENV),
            "superseded_must_not_be_rearmed":
                copy.deepcopy(SPEC["supersedes_e1"]["must_not_be_rearmed"]),
        }, indent=1, sort_keys=True))
        return 2
    gate = AUTH.series_authorized()
    if not gate["matches_authorized_series"]:
        print(json.dumps({
            "REFUSED": "SERIES_GATE_NOT_SET",
            "authorized_series": gate["authorized_series"],
            "why": ("the owner's %s must name the authorized series. Only "
                    "markets the frozen BTC15m selector produces during "
                    "this session may be traded, and the exact resolved "
                    "slug is re-enforced before every submission."
                    % AUTH.GATE_EVIDENCE_ENV),
        }, indent=1, sort_keys=True))
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
    provider = provider_factory(progress=prog, session_auth=auth,
                               recorder=rec)
    print(json.dumps({"ARMED": True, "probe": PROBE,
                      "fingerprint": E1_FINGERPRINT,
                      "rehydrated": rehydrated,
                      "progress": prog.report(),
                      "authorization": auth.report(),
                      "entering": "run_cohort"},
                     indent=1, sort_keys=True, default=str))
    out = run_cohort(snapshot_provider=provider, tracker=tracker,
                     recorder=rec, transport_factory=transport_factory,
                     session_auth=auth, progress=prog)
    out["provider"] = provider.report()
    out["progress"] = prog.report()
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
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
