"""Invariants of the inventory-aware complement controller.

The controller exists because the tail trace showed the engine holding 20 DOWN
at basis 0.5700 and then posting UP at 0.57, 0.40, ... 0.96, 0.97, 0.98, 0.99 --
following its own bid up as the market left, and completing pairs at 1.34.

These tests pin the properties that failure violated. The most important one is
`test_complement_quote_never_rises_because_the_touch_rose`: if that goes red the
defect is back.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P
from src import pmus_completion_shadow as S


def eng(**kw):
    kw.setdefault("clip", D(10))
    kw.setdefault("complement_controller", True)
    kw.setdefault("prospective_cap", False)
    return E.NativeEngine(**kw)


def bbo(bid, ask, t=0.0, last=None, vol="0"):
    return {"kind": "bbo", "t": t, "state": "MARKET_STATE_OPEN",
            "bid": D(str(bid)), "ask": D(str(ask)),
            "last": D(str(last)) if last is not None else D(str(bid)),
            "shares_traded_delta_lower_bound": vol}


# ------------------------------------------------------------------ the cap

def test_cap_is_theta_minus_the_held_basis():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    assert e.complement_cap("up") == D("0.39")          # 0.96 - 0.57


def test_cap_is_floored_to_the_venue_tick_never_richer():
    e = eng()
    e.bk.add("down", D(30), D("13.30"), maker=True)      # basis 0.443333...
    cap = e.complement_cap("up")
    assert cap == D("0.51")                              # 0.5166... floored
    assert cap + e.bk.avg("down") <= E.THETA


def test_no_cap_applies_when_flat_this_is_not_a_global_ceiling():
    e = eng()
    assert e.complement_cap("up") is None
    assert e.complement_cap("down") is None


def test_no_cap_applies_to_the_side_we_are_already_long():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    assert e.complement_cap("down") is None              # that is an EXTEND
    assert e.complement_cap("up") is not None            # that is a complement


def test_cap_can_be_non_positive_and_that_means_do_not_quote():
    e = eng()
    e.bk.add("down", D(10), D("9.90"), maker=True)        # basis 0.99
    assert e.complement_cap("up") <= 0


# -------------------------------------------- THE defect this closes

def test_complement_quote_never_rises_because_the_touch_rose():
    """The exact 0600z failure. Basis fixed, touch marching up: the posted
    complement price must not follow it."""
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    posted = []
    for i, bid in enumerate(("0.40", "0.60", "0.78", "0.84",
                             "0.96", "0.97", "0.99")):
        ask = str(min(D(bid) + D("0.01"), D("0.99")))
        for it in e.on_book(bbo(bid, ask, t=float(i))):
            if it.kind == E.REST and it.get("side") == "up":
                posted.append(D(str(it["price"])))
    assert posted, "no complement quote was ever posted"
    assert max(posted) <= D("0.39"), (
        "the complement was chased upward to %s" % max(posted))
    # and every posted quote implies an acceptable PAIR
    for px in posted:
        assert px + e.bk.avg("down") <= E.THETA


def test_the_pair_implied_by_any_complement_quote_never_exceeds_theta():
    """Property test over a grid of bases and touches."""
    for basis in ("0.20", "0.37", "0.5700", "0.80", "0.90"):
        for bid in ("0.05", "0.30", "0.50", "0.70", "0.90", "0.99"):
            e = eng()
            e.bk.add("down", D(20), D(20) * D(basis), maker=True)
            for it in e.on_book(bbo(bid, "0.99" if D(bid) >= D("0.98")
                                    else str(D(bid) + D("0.01")))):
                if it.kind == E.REST and it.get("side") == "up":
                    assert D(str(it["price"])) + D(basis) <= E.THETA


def test_a_capped_quote_is_labelled_so_the_journal_can_tell_them_apart():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    reasons = [it.get("reason") for it in e.on_book(bbo("0.80", "0.81"))
               if it.kind == E.REST and it.get("side") == "up"]
    assert reasons == ["COMPLEMENT_AT_BASIS_CAP"]


def test_an_uncapped_open_still_says_two_sided_our_touch():
    e = eng()
    reasons = {it.get("side"): it.get("reason")
               for it in e.on_book(bbo("0.40", "0.41")) if it.kind == E.REST}
    assert set(reasons.values()) == {"TWO_SIDED_OUR_TOUCH"}


# ------------------------------------------------------- sequential accounting

def test_the_cap_tightens_when_a_fill_raises_the_basis():
    e = eng()
    e.bk.add("down", D(10), D("5.00"), maker=True)        # basis 0.50
    assert e.complement_cap("up") == D("0.46")
    e.bk.add("down", D(10), D("7.00"), maker=True)        # basis now 0.60
    assert e.complement_cap("up") == D("0.36")


def test_the_cap_loosens_only_when_the_basis_itself_improves():
    e = eng()
    e.bk.add("down", D(10), D("6.00"), maker=True)        # 0.60
    before = e.complement_cap("up")
    e.bk.add("down", D(10), D("2.00"), maker=True)        # 0.40 avg
    after = e.complement_cap("up")
    assert after > before
    # and the pair it implies is still bounded, which is the real invariant
    assert after + e.bk.avg("down") <= E.THETA


def test_the_cap_reads_only_present_inventory_no_future_rows():
    """The cap is a pure function of the Book. Feeding later rows cannot change
    a cap computed now, which is what 'no lookahead' means here."""
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    cap_now = e.complement_cap("up")
    snapshot = (e.bk.q["down"], e.bk.c["down"])
    e.on_book(bbo("0.99", "0.99", t=5.0))
    assert e.complement_cap("up") == cap_now or (
        (e.bk.q["down"], e.bk.c["down"]) != snapshot)


# ------------------------------------------------------------------ EXTEND

def test_extend_of_the_long_side_is_refused_while_a_complement_is_outstanding():
    e = eng(extend_requires_balance=True)
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    sides = [it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
             if it.kind == E.REST]
    assert "down" not in sides, "kept buying the side already long"
    assert "up" in sides, "stopped working the complement too"
    assert e.extend_refused_while_imbalanced >= 1


def test_extend_from_flat_is_untouched_because_L5_extends():
    e = eng(extend_requires_balance=True)
    sides = sorted(it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
                   if it.kind == E.REST)
    assert sides == ["down", "up"], "a flat OPEN must quote both sides"
    assert e.extend_refused_while_imbalanced == 0


def test_extend_resumes_once_balance_is_restored():
    e = eng(extend_requires_balance=True)
    e.bk.add("down", D(10), D("5.00"), maker=True)
    e.bk.add("up", D(10), D("4.00"), maker=True)          # balanced again
    sides = sorted(it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
                   if it.kind == E.REST)
    assert sides == ["down", "up"]


# --------------------------------------------------------- fill model honours it

def test_a_resting_order_cannot_fill_above_its_own_limit():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    row = bbo("0.90", "0.91", last="0.90", vol="500")
    got = v.fills_for(e, row, "up", D("0.39"), D(10), None)
    assert got is None, "filled a 0.39 bid at a 0.90 touch"


def test_a_resting_order_fills_at_its_own_limit_not_at_a_cheaper_touch():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    row = bbo("0.30", "0.31", last="0.30", vol="500")
    got = v.fills_for(e, row, "up", D("0.39"), D(10), None)
    if got is not None:
        _qty, fpx = got
        assert fpx == D("0.39"), "took a discount the venue never gave us"


def test_the_frozen_arm_keeps_the_old_fill_model_exactly():
    assert P.ReplayVenue().respect_limit is False


# ----------------------------------------------------------------- NO inversion

def test_the_down_side_is_quoted_in_own_terms_one_minus_ask():
    """A NO inversion here would make every DOWN cap nonsense, so it is pinned
    at the quoting site and not only in the tape audit."""
    e = eng()
    got = {it.get("side"): D(str(it["price"]))
           for it in e.on_book(bbo("0.40", "0.45")) if it.kind == E.REST}
    assert got["up"] == D("0.40")
    assert got["down"] == D(1) - D("0.45")


def test_a_down_complement_cap_uses_the_up_basis():
    e = eng()
    e.bk.add("up", D(20), D(20) * D("0.30"), maker=True)
    assert e.complement_cap("down") == D("0.66")          # 0.96 - 0.30
    assert e.complement_cap("up") is None


# ------------------------------------------------------------------- fees

def test_no_maker_fee_is_assumed_in_the_cap_and_none_is_charged():
    """The cap is theta - basis with no fee term because a MAKER fill pays no
    fee at this venue. If the book ever charges one on a maker fill the cap is
    wrong, so both halves are pinned together."""
    bk = S.Book()
    bk.add("up", D(10), D("4.00"), maker=True)
    assert bk.fees == 0
    bk2 = S.Book()
    bk2.add("up", D(10), D("4.00"), maker=False)
    assert bk2.fees > 0


def test_frozen_checkpoint_is_untouched_by_the_controller_defaults():
    from scripts.audit_economic_complete import load, USABLE
    by = load()
    tot = sum(P.net_of(P.replay_window(by[s], winner=w, clip=D(50)))
              for s, w in USABLE.items())
    assert tot / len(USABLE) == D("6.116725359099999999999999992")


# ============================================================ REVIEW FIXES
# Each test below pins a defect the independent review confirmed at 0dbe3597.

# ---------------------------------------------------------------------- 5

def test_the_cap_includes_taker_fees_already_paid_on_the_held_side():
    """The reviewer's counterexample. Held UP = 10 taker @0.50 (fee $0.1738)
    + 10 maker @0.40 gives avg 0.45, so a cap of theta - avg = 0.51 read as
    "exactly theta" while the true all-in pair cost was 0.9686875."""
    e = eng()
    e.bk.add("up", D(10), D("5.00"), maker=False)
    e.bk.add("up", D(10), D("4.00"), maker=True)
    assert e.bk.avg("up") == D("0.45")
    assert e.bk.fee_per_share("up") > 0
    cap = e.complement_cap("down")
    assert cap == D("0.50"), cap                       # was 0.51
    assert cap + e.bk.economic_basis("up") <= E.THETA


def test_the_all_in_pair_cost_never_exceeds_theta_even_with_taker_inventory():
    for tq, tcost in (("10", "5.00"), ("20", "14.00"), ("5", "4.50")):
        e = eng()
        e.bk.add("up", D(tq), D(tcost), maker=False)
        cap = e.complement_cap("down")
        if cap is None or cap < E.PRICE_TICK:
            continue
        assert cap + e.bk.economic_basis("up") <= E.THETA


def test_attributed_fees_follow_shares_off_the_book():
    """Without this the remaining shares inherit departed shares' fees and the
    basis drifts upward, tightening the cap for the wrong reason."""
    bk = S.Book()
    bk.add("up", D(20), D("10.00"), maker=False)
    per_share = bk.fee_per_share("up")
    bk.release_fees("up", D(20), D(10))
    bk.q["up"] = D(10)
    assert bk.fee_per_share("up") == per_share
    bk.release_fees("up", D(10), D(0))
    assert bk.fees_side["up"] == 0


def test_the_fee_split_never_changes_the_fee_total():
    bk = S.Book()
    bk.add("up", D(10), D("5.00"), maker=False)
    bk.add("down", D(10), D("4.00"), maker=False)
    assert bk.fees == bk.fees_side["up"] + bk.fees_side["down"]


# ---------------------------------------------------------------------- 6b

def test_an_adverse_touch_move_cannot_worsen_the_permissible_price():
    """THE invariant, stated as the reviewer asked: with inventory unchanged,
    walking the touch upward cannot raise the cap or the posted price above it."""
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.40"), maker=True)
    cap0 = e.complement_cap("up")
    worst = None
    for i, bid in enumerate(("0.10", "0.30", "0.55", "0.56", "0.70",
                             "0.90", "0.99")):
        ask = "0.99" if D(bid) >= D("0.98") else str(D(bid) + D("0.01"))
        for it in e.on_book(bbo(bid, ask, t=float(i))):
            if it.kind == E.REST and it.get("side") == "up":
                px = D(str(it["price"]))
                worst = px if worst is None or px > worst else worst
        assert e.complement_cap("up") == cap0, "the cap moved with the touch"
    assert worst is not None and worst <= cap0


def test_the_quote_does_follow_an_improving_touch_and_that_is_intended():
    """Stated explicitly so the docs and the code agree: min(touch, cap) is
    not 'always the cap'. Documenting the real behaviour is part of the fix."""
    e = eng()
    e.bk.add("down", D(10), D(10) * D("0.30"), maker=True)
    cap = e.complement_cap("up")                        # 0.96 - 0.30 = 0.66
    posted = []
    for i, bid in enumerate(("0.05", "0.10", "0.20")):
        for it in e.on_book(bbo(bid, str(D(bid) + D("0.01")), t=float(i))):
            if it.kind == E.REST and it.get("side") == "up":
                posted.append(D(str(it["price"])))
    assert posted == [D("0.05"), D("0.10"), D("0.20")]
    assert all(p <= cap for p in posted)


# ---------------------------------------------------------------------- 6c

def test_a_resting_complement_is_cancelled_when_a_fill_tightens_the_cap():
    e = eng()
    e.bk.add("down", D(10), D("3.00"), maker=True)       # cap(up) = 0.66
    e.resting["up"] = {"px": D("0.66"), "qty": D(10)}
    e.bk.add("down", D(10), D("9.00"), maker=True)       # cap(up) = 0.36
    out = e.on_book(bbo("0.40", "0.41"))
    assert e.resting["up"] is None or \
        D(str(e.resting["up"]["px"])) <= e.complement_cap("up")
    assert any(it.kind == E.CANCEL
               and it.get("reason") == "CAP_TIGHTENED_STALE_QUOTE"
               for it in out)
    assert e.stale_complement_cancels >= 1


def test_a_missing_touch_cannot_let_a_stale_quote_survive():
    """The reachable case: the tape has 228 bbo rows with no bid and 276 with
    no ask, and step 4 skips a side it cannot price WITHOUT cancelling it."""
    e = eng()
    e.bk.add("down", D(10), D("3.00"), maker=True)
    e.resting["up"] = {"px": D("0.66"), "qty": D(10)}
    e.bk.add("down", D(10), D("9.00"), maker=True)
    row = bbo("0.40", "0.70")
    row["bid"] = None                                    # no UP touch at all
    e.on_book(row)
    assert e.resting["up"] is None, "stale 0.66 survived a missing bid"


def test_an_unreadable_book_still_cancels_and_carries_the_cancel():
    """StaleBook means no ACQUISITION, but a cancel needs no readable book and
    reduces risk, so it must not be lost with the refused tick."""
    e = eng()
    e.bk.add("down", D(10), D("3.00"), maker=True)
    e.resting["up"] = {"px": D("0.66"), "qty": D(10)}
    e.bk.add("down", D(10), D("9.00"), maker=True)
    row = bbo("0.40", "0.41")
    row["bid"] = row["ask"] = None
    with pytest.raises(E.StaleBook) as ei:
        e.on_book(row)
    assert e.resting["up"] is None
    assert any(it.get("reason") == "CAP_TIGHTENED_STALE_QUOTE"
               for it in ei.value.cancels)


def test_revalidation_never_touches_an_uncapped_side():
    e = eng()
    e.resting["up"] = {"px": D("0.90"), "qty": D(10)}    # flat: uncapped
    e.on_book(bbo("0.90", "0.91"))
    assert e.stale_complement_cancels == 0


# ---------------------------------------------------------------------- 7

def _ladder(bids, offers):
    return {"kind": "book", "bids": [[p, q] for p, q in bids],
            "offers": [[p, q] for p, q in offers]}


def test_queue_state_is_keyed_on_our_resting_price_not_the_touch():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    lad = _ladder([("0.60", "100"), ("0.39", "50")], [("0.61", "100")])
    row = bbo("0.60", "0.61", last="0.39", vol="10")
    v.fills_for(e, row, "up", D("0.39"), D(10), lad)
    st = v.qstate.get("up")
    assert st is not None and st["px"] == D("0.39"), (
        "queue keyed on %s, not on our resting price" % (st,))


def test_a_touch_move_does_not_reset_our_queue_position(monkeypatch):
    """Our order never moved, so it should be GAINING priority. Keying on the
    touch reset it to 'just joined' every time the touch moved -- 28 spurious
    resets were measured on the training cohort."""
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    lad = _ladder([("0.60", "100"), ("0.39", "50")], [("0.61", "100")])
    v.fills_for(e, bbo("0.60", "0.61", last="0.39", vol="10"),
                "up", D("0.39"), D(10), lad)
    ahead_1 = v.qstate["up"]["ahead"]
    lad2 = _ladder([("0.75", "100"), ("0.39", "50")], [("0.76", "100")])
    v.fills_for(e, bbo("0.75", "0.76", last="0.39", vol="10"),
                "up", D("0.39"), D(10), lad2)
    ahead_2 = v.qstate["up"]["ahead"]
    assert ahead_2 < ahead_1, (
        "queue reset when only the touch moved: %s -> %s" % (ahead_1, ahead_2))


def test_an_undisplayed_level_fails_closed_rather_than_guessing():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    lad = _ladder([("0.60", "100")], [("0.61", "100")])   # 0.39 not displayed
    got = v.fills_for(e, bbo("0.60", "0.61", last="0.39", vol="500"),
                      "up", D("0.39"), D(10), lad)
    assert got is None, "granted a fill at a level whose queue is unknowable"


def test_the_frozen_queue_keying_is_unchanged_without_respect_limit():
    v = P.ReplayVenue()
    e = eng(complement_controller=False)
    lad = _ladder([("0.60", "100")], [("0.61", "100")])
    v.fills_for(e, bbo("0.60", "0.61", last="0.60", vol="10"),
                "up", D("0.39"), D(10), lad)
    st = v.qstate.get("up")
    assert st is not None and st["px"] == D("0.60"), (
        "the frozen arm must still key on the touch: %s" % (st,))


# -------------------------------------------- KNOWN SCOPE LIMITATION (not fixed)

def test_flat_state_open_quotes_can_form_pairs_near_one_dollar():
    """PRESERVED, NOT FIXED, on purpose.

    The cap governs only the SECOND leg. Two simultaneous uncapped quotes from
    flat form a pair at `1 - spread`, so a one-tick spread yields a pair cost of
    0.99 -- above theta, and entirely outside the controller. This is consistent
    with the design (an OPEN is uncapped) and is the part of the maker leak the
    controller does NOT close. Quantified here so the subsequent validation
    reads its numbers knowing this, rather than rediscovering it.
    """
    e = eng()
    got = {it.get("side"): D(str(it["price"]))
           for it in e.on_book(bbo("0.49", "0.50")) if it.kind == E.REST}
    pair = got["up"] + got["down"]
    assert pair == D("0.99")
    assert pair > E.THETA, "if this ever fails the limitation is gone"


def test_the_scope_limitation_is_quantified_on_the_real_tape():
    """How often the uncapped first pair is above theta, on the training tape."""
    from scripts.audit_economic_complete import load, USABLE
    by = load()
    two_sided = above = 0
    for slug in USABLE:
        for row in by[slug]:
            if row.get("kind") != "bbo":
                continue
            bid, ask = S._d(row.get("bid")), S._d(row.get("ask"))
            if bid is None or ask is None:
                continue
            up, down = bid, D(1) - ask
            if not (D(0) < up < 1 and D(0) < down < 1):
                continue
            two_sided += 1
            if up + down > E.THETA:
                above += 1
    assert two_sided > 1000, two_sided
    frac = D(above) / D(two_sided)
    # the measurement, recorded rather than asserted tightly: on this tape the
    # uncapped two-sided quote is above theta on essentially every tick.
    assert frac > D("0.9"), (
        "flat-state pair cost above theta on %d of %d two-sided ticks (%.4f)"
        % (above, two_sided, frac))
