"""The E1 cohort runner. FROZEN. DISARMED. NOT AUTHORIZED.

NO LIVE ORDER IS PLACED BY ANY PATH IN THIS MODULE AS IT STANDS. The arming
gate is the owner's `analysis.l5.live_exec.ledger.may_submit`, no owner value
is set, and nothing here sets one. `boot()` reports the arming state and
`eligible()` refuses on it; it does not acquire it.

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
* `src.l5_clob_ws` is the timing authority. REST is reachability only and
  may never order the tape.
* `src.pmus_fee_schedule` is the fee source. Fees in the PAIR-COST GATE are
  modelled-forward and labelled so; fees in the LEDGER are the venue's own
  reported numbers and nothing else.
"""
from __future__ import annotations

import copy
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Dict, Mapping, Optional

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort.spec import (E1_FINGERPRINT, FIRST_LEG_QTY,
                                        MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_FIRST_LEG_PRICE,
                                        PAIR_COST_CEILING, PROBE, SPEC,
                                        VENUE_MIN_QTY, VENUE_TICK, WINDOWS,
                                        assert_frozen, boot_assertions,
                                        diff_report)
from analysis.l5.e1_cohort.stopping import CohortTracker, project_feasibility
from analysis.l5.live_exec2 import orders as O
from src import l5_clob_ws as WS
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
    quantity. A partial fill yields a partial complement."""
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


# -------------------------------------------------------------- eligibility

def _ws_ok(ws: Mapping[str, Any], *, now: float) -> list:
    bad = []
    if ws.get("timing_authority") != WS.TIMING_WEBSOCKET:
        bad.append("TIMING_AUTHORITY_NOT_WEBSOCKET_STRICT")
    if not ws.get("both_tokens_streaming"):
        bad.append("BOTH_TOKENS_NOT_STREAMING")
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


def eligible(snapshot: Mapping[str, Any], tracker: CohortTracker,
             *, now: float) -> Dict[str, Any]:
    """Rules E1..E12, all evaluated and reported together, before any
    network call. `ok` False means NO ORDER and NO LATCH CONSUMPTION."""
    assert_frozen()
    r = []
    arming = O.armed_env_summary()
    if not arming.get("armed"):
        r.append("E1_DISARMED")
    pin = snapshot.get("fingerprint_pinned") or {}
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

    price = D(str(snapshot.get("first_leg_price", "0")))
    qty = D(str(snapshot.get("qty", FIRST_LEG_QTY)))
    if qty != FIRST_LEG_QTY:
        r.append("QTY_NOT_THE_FROZEN_0.13:%s" % qty)
    notional = qty * price

    gate = tracker.may_place(window=window, notional_usd=notional)
    if not gate["ok"]:
        r.extend(gate["refusals"])

    fl = (snapshot.get("first_leg") or {})
    best_bid = fl.get("best_bid")
    best_ask = fl.get("best_ask")
    if best_bid is None or D(str(price)) != D(str(best_bid)):
        r.append("E9_NOT_AT_SAME_SIDE_BEST_BID:%s!=%s" % (price, best_bid))
    if price % VENUE_TICK != 0:
        r.append("E9_OFF_TICK:%s" % price)
    if price > MAX_FIRST_LEG_PRICE:
        r.append("E9_PRICE_ABOVE_CEILING:%s" % price)
    if best_ask is not None and price >= D(str(best_ask)):
        r.append("E9_WOULD_CROSS:%s>=%s" % (price, best_ask))

    r.extend("E7_" + x for x in _ws_ok(snapshot.get("ws_state") or {},
                                       now=now))

    comp = None
    try:
        comp = complement_price(
            first_leg_price=price, qty=qty,
            complement_best_bid=(snapshot.get("complement")
                                 or {}).get("best_bid", "0"))
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

def submit_first_leg(snapshot: Mapping[str, Any], tracker: CohortTracker,
                     *, now: float,
                     transport_factory: Any = O.BoundedOrderTransport
                     ) -> Dict[str, Any]:
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
    side = snapshot["first_leg"]["side"]
    price = snapshot["first_leg_price"]
    qty = snapshot.get("qty", FIRST_LEG_QTY)

    slot = L.consume_attempt(window=window, slug=slug, side=side,
                             price=price, qty=qty)
    t = transport_factory()
    try:
        out = O.submit_one(t, slug=slug, outcome_side=side, price=price,
                           qty=qty, checks=checks)
    except O.SubmissionAmbiguous:
        tracker.record_ambiguous_submission()
        raise
    tracker.record_placement(window=window,
                             notional_usd=D(str(qty)) * D(str(price)))
    out["e1_latch_slot"] = slot
    out["e1_window"] = window
    return out


def submit_complement(*, window: int, slot: int, snapshot: Mapping[str, Any],
                      first_leg_filled_qty: Any, tracker: CohortTracker,
                      transport_factory: Any = O.BoundedOrderTransport
                      ) -> Dict[str, Any]:
    """Rules C1..C8. ONE submission per first-leg fill, maker only, sized to
    the ACTUAL filled quantity. No taker rescue, no chasing, no re-quote --
    the re-quote is refused at the latch, not only in the policy."""
    assert_frozen()
    q = complement_qty(first_leg_filled_qty=first_leg_filled_qty)
    plan = complement_price(
        first_leg_price=snapshot["first_leg_price"], qty=q,
        complement_best_bid=(snapshot.get("complement")
                             or {}).get("best_bid", "0"))
    side = snapshot["complement"]["side"]
    rec = L.consume_complement(window=window, slot=slot,
                               slug=snapshot["market"], side=side,
                               price=plan["price_usd"], qty=q)
    t = transport_factory()
    try:
        out = O.submit_one(t, slug=snapshot["market"], outcome_side=side,
                           price=plan["price_usd"], qty=q,
                           checks={"ok": True})
    except O.SubmissionAmbiguous:
        tracker.record_ambiguous_submission()
        raise
    out["e1_complement_plan"] = plan
    out["e1_complement_qty"] = str(q)
    out["e1_latch_slot"] = rec
    out["sized_to_actual_filled_qty"] = True
    return out


# -------------------------------------------------------------------- boot

def boot() -> Dict[str, Any]:
    """Boot assertions, every time. Fails closed. Reports arming; never
    acquires it."""
    rep = boot_assertions()
    rep.update({
        "diff_report": diff_report(),
        "feasibility": project_feasibility(),
        "arming": O.armed_env_summary(),
        # the latch is NOT read here: reading it needs the owner's session
        # id, and boot() must not require an owner value to run.
        "latch": {"kind": copy.deepcopy(SPEC["latch"]["kind"]),
                  "slots_per_window": MAX_ATTEMPTS_PER_WINDOW,
                  "NOTE": ("read with latch.cohort_state() once the owner "
                           "has set a session id")},
        "fee_schedule_fingerprint": FEE.FINGERPRINT,
        "timing_authority": WS.TIMING_WEBSOCKET,
        "AUTHORIZATION": "NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE SET.",
        "NO_ORDER_WILL_BE_PLACED": True,
    })
    return rep
