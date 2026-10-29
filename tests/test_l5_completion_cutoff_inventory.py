"""P0: completion eligibility is a MARKET fact, not a ROUND fact.

THE DEFECT THESE PIN, reproduced from production 1215z on 2026-10-28.

At 12:22:46 a third UP leg filled, leaving the market holding 4.21 UP
against 1.92 DOWN -- 2.29 unmatched. The DOWN completion was submitted at
12:23:53, rested 56s unfilled and was cancelled at 12:24:49. The probe then
advanced to round 4, a FRESH round with no claimed legs, so

    _entering = not any(_claimed.values())

read True, and the gate asked `may_enter` (T+600) instead of `may_complete`
(T+720). Eleven seconds later entry closed. Production logged, at 12:25:03
and again at 12:26:16:

    key                  TWO_SIDED_PROBE:PAST_ENTRY_CUTOFF
    decision_role        COMPLEMENT
    two_sided_complement null

120 seconds of authorized completion runway went unused while 2.29 UP sat
unmatched. At 12:25:03 the book was bid 0.5700, so DOWN was available at
own 0.43 for a pair cost of 0.799 against the 0.96 ceiling. Completing
there returns +$0.45; the strand realized -$0.8457. The 2.29 contracts
expired worthless and the window settled -$0.3835.

`may_complete`'s own docstring already states the intent these restore:
"Deliberately later than `may_enter`: completing removes risk, so the
engine keeps doing it after it has stopped opening."

The second property matters as much as the first: the later deadline must
NOT become a late directional opening. A flat market past T+600 stays shut,
and past T+600 only the leg that REDUCES the residual may be placed.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

from tests.test_l5_two_sided_wiring import (_book, _row, gates,  # noqa: F401
                                            mk, probe)

#: T+650: past the 600s entry cutoff, inside the 720s completion cutoff.
INSIDE_COMPLETION = 650.0
#: T+100: comfortably inside both.
EARLY = 100.0

#: Completing a DOWN residual BUYS UP, so the pair only clears the 0.96 cap
#: on a book where UP is cheap. With bid 0.60/ask 0.61 the UP maker quote is
#: own 0.58 and 0.39 + 0.58 = 0.97 is CORRECTLY refused above the cap -- that
#: is the economics working, not the cutoff defect, so the mirror cases use a
#: low book instead of weakening the cap.
LOW = {"bid": "0.30", "ask": "0.31"}


@pytest.fixture()
def at(monkeypatch, probe):
    """Place `now` a chosen number of seconds into the window."""
    H, _E = probe

    def _set(offset):
        monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                            lambda slug: H.NOW().timestamp() - offset)
    return _set


def _hold(ex, up="2.29", down="0"):
    """Unmatched inventory with NO round claimed -- the production shape
    after a completion was cancelled and the round advanced."""
    from src import l5_live_execution as E
    if D(up) > 0:
        ex.store.apply_fill("m", "UP", D(up), D("0.3693"), basis=E.OWN)
    if D(down) > 0:
        ex.store.apply_fill("m", "DOWN", D(down), D("0.39"), basis=E.OWN)


# --------------------------------------------- P0: the completion survives

def test_unmatched_inventory_may_still_COMPLETE_past_the_entry_cutoff(
        probe, tmp_path, at):
    """THE FAILING TEST. 2.29 UP unmatched, fresh round, T+650."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    assert ex.probe_legs_claimed("m", ex.probe_round("m")) == {
        "UP": False, "DOWN": False}, "the round must be FRESH"
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, ("nothing reached the wire: %s"
                    % (rec.get("refused_reason"),))
    assert t.sent[-1]["side"] == "DOWN", "it must COMPLETE, not open"


def test_the_refusal_is_not_PAST_ENTRY_CUTOFF_while_inventory_is_unmatched(
        probe, tmp_path, at):
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert rec.get("refused_reason") != "PAST_ENTRY_CUTOFF"


def test_a_DOWN_residual_completes_with_an_UP_leg_past_the_cutoff(
        probe, tmp_path, at):
    """The mirror: holding DOWN, the reducing leg is UP."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex, up="0", down="2.29")
    rec = ex.handle(_row(H), _book(**LOW),
                    market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert t.sent[-1]["side"] == "UP"


def test_a_partially_matched_market_completes_only_the_remainder(
        probe, tmp_path, at):
    """Production's exact numbers: 4.21 UP against 1.92 DOWN."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex, up="4.21", down="1.92")
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert t.sent[-1]["side"] == "DOWN"
    # never more than the unmatched remainder
    assert D(t.sent[-1]["qty"]) <= D("2.29")


# ------------------------------- the later deadline is NOT a late opening

def test_a_FLAT_market_still_cannot_open_past_the_entry_cutoff(
        probe, tmp_path, at):
    """The entry cutoff is preserved exactly where there is nothing to
    reduce. This is the property the fix must not trade away."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent, "a flat market opened a NEW leg past T+600"
    assert rec.get("refused_reason") == "PAST_ENTRY_CUTOFF"


def test_nothing_may_be_placed_past_the_COMPLETION_cutoff_either(
        probe, tmp_path, at):
    """T+730 is past `complete_cutoff_s` (720): even a completion stops."""
    H, E = probe
    at(TS.COMPLETE_CUTOFF_S + 10.0)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent
    assert rec.get("refused_reason") in ("PAST_COMPLETE_CUTOFF",
                                         "PAST_TERMINAL")


def test_past_the_entry_cutoff_the_placed_leg_REDUCES_the_residual(
        probe, tmp_path, at):
    """Whatever the plan's cheapest leg is, past T+600 only the reducing
    side may reach the wire."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex, up="2.29", down="0")
    before = ex.store.residual("m")
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert before > 0
    assert t.sent, rec.get("refused_reason")
    # UP held, so the only leg permitted past T+600 is the reducing one
    assert t.sent[-1]["side"] == "DOWN"


# ------------------------------------------------- early behaviour unchanged

def test_an_early_FLAT_window_still_opens_normally(probe, tmp_path, at):
    """Byte-for-byte prior behaviour inside the entry window."""
    H, E = probe
    at(EARLY)
    ex, t = mk(H, E, tmp_path)
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert t.sent[0]["side"] == "DOWN"       # cheaper leg first


def test_an_early_window_with_a_residual_still_reduces_it_first(
        probe, tmp_path, at):
    H, E = probe
    at(EARLY)
    ex, t = mk(H, E, tmp_path)
    _hold(ex, up="0", down="2.29")
    rec = ex.handle(_row(H), _book(**LOW),
                    market="m", window="21:00")
    assert t.sent, rec.get("refused_reason")
    assert t.sent[-1]["side"] == "UP"


def test_both_placement_gates_are_still_called_by_name(probe):
    """`test_l5_ts_deadline_boundaries` pins these call sites; the fix must
    keep BOTH, because it chooses between them rather than dropping one."""
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E.FaithfulExecutor.handle)
    assert "TS.may_enter(" in src
    assert "TS.may_complete(" in src


# ================= late completion cannot OVERSHOOT or REVERSE the residual
#
# The extra 120s of completion runway is only safe if what it places can
# never become a NEW directional position on the other side. Two mechanisms
# carry that, and these assert both through the real executor rather than
# trusting their interaction:
#
#   probe_reducing_side  reads the PROSPECTIVE residual -- realized plus
#                        every outstanding order as if it filled -- so a
#                        complement already resting for the whole residual
#                        leaves nothing to reduce and the gate reverts to
#                        ENTRY semantics, which past T+600 means refusal.
#   probe_leg_qty_cap    clips a reducing leg to the magnitude of that same
#                        prospective residual, so it cannot cross zero.


def _resting(ex, market, side, qty, px):
    """An outstanding order the store counts in `outstanding_residual`."""
    ex.store.record_submit("coid-%s-%s" % (side, qty),
                           {"market": market, "side": side,
                            "qty": str(qty), "price": str(px),
                            "mode": "MAKER"}, mode="MAKER")


def test_a_complement_already_resting_for_the_whole_residual_adds_nothing(
        probe, tmp_path, at):
    """2.29 UP held and 2.29 DOWN already working: prospective residual is
    zero, so there is nothing to reduce and no second order is placed."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    _resting(ex, "m", "DOWN", "2.29", "0.37")
    assert ex.probe_reducing_side("m") is None
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent, "placed a SECOND completion over a full one"
    assert rec.get("refused_reason") is not None


def test_a_partial_complement_resting_leaves_only_the_remainder(
        probe, tmp_path, at):
    """2.29 UP held, 1.00 DOWN working: 1.29 remains reducible and a further
    completion may never exceed it."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    _resting(ex, "m", "DOWN", "1.00", "0.37")
    assert ex.probe_reducing_side("m") == "DOWN"
    assert ex.store.residual("m") + ex.store.outstanding_residual("m") \
        == D("1.29")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    if t.sent:
        assert t.sent[-1]["side"] == "DOWN"
        assert D(t.sent[-1]["qty"]) <= D("1.29"), "overshot the remainder"


def test_the_qty_cap_is_the_PROSPECTIVE_residual_not_the_realized_one(
        probe, tmp_path, at):
    """The cap must count outstanding orders, or two sequential completions
    each sized to the REALIZED residual would reverse the book."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, _t = mk(H, E, tmp_path)
    _hold(ex)
    _resting(ex, "m", "DOWN", "1.00", "0.37")
    assert ex.probe_leg_qty_cap("m", "DOWN", D("2.29")) <= D("1.29")


def test_when_the_outstanding_order_FILLS_no_further_completion_is_placed(
        probe, tmp_path, at):
    """The sequence that would reverse the residual: place the completion,
    let the VENUE fill it, then tick again.

    The fill is the transport's, not the test's. An earlier draft booked the
    whole intent by hand on top of the ack `FakeTransport` had already
    recorded, counted 2.29 twice and accused the engine of an overshoot it
    had not made -- the engine sent exactly the 2.29 the residual called
    for.
    """
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
    _hold(ex)
    rec1 = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert t.sent, rec1.get("refused_reason")
    assert t.sent[-1]["side"] == "DOWN"
    # the venue filled the whole completion: matched exactly, never crossed
    assert ex.store.residual("m") == D("0"), ex.store.residual("m")
    sent_before = len(t.sent)
    rec2 = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert ex.store.residual("m") == D("0"), "the residual reversed"
    assert len(t.sent) == sent_before, (
        "opened a NEW leg past the entry cutoff: %s"
        % ([x["side"] for x in t.sent], rec2.get("refused_reason")))


def test_a_PARTIAL_fill_leaves_only_the_remainder_reducible(
        probe, tmp_path, at):
    """`FakeTransport` acks 1.00 of the 2.29 offered, so the residual walks
    2.29 -> 1.29. The next completion may take that and no more, and the
    book may never cross zero on the way."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)           # fill_all=False: acks 1.00
    _hold(ex)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert ex.store.residual("m") == D("1.29"), ex.store.residual("m")
    for _ in range(4):
        ex.handle(_row(H), _book(), market="m", window="21:00")
        assert ex.store.residual("m") >= D("0"), "crossed zero"
    # every leg that ever went out was the reducing one
    assert {x["side"] for x in t.sent} == {"DOWN"}


def test_a_completion_may_never_cross_zero_even_offered_a_huge_plan(
        probe, tmp_path, at):
    """Directly: the cap clips to the residual magnitude, both signs."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, _t = mk(H, E, tmp_path)
    _hold(ex, up="0.50", down="0")
    assert ex.probe_leg_qty_cap("m", "DOWN", D("99")) <= D("0.50")


# ================================================= fail closed, not open


def test_an_unreadable_residual_is_treated_as_an_ENTRY_and_refused_late(
        probe, tmp_path, at, monkeypatch):
    """An unreadable residual must not buy the LATER deadline. It reverts to
    entry semantics, which past T+600 is a refusal."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)

    def boom(*_a, **_k):
        raise RuntimeError("inventory unreadable")
    monkeypatch.setattr(ex.store, "residual", boom)
    assert ex.probe_reducing_side("m") is None
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent
    assert rec.get("refused_reason") == "PAST_ENTRY_CUTOFF"


def test_an_unreadable_outstanding_residual_also_fails_closed(
        probe, tmp_path, at, monkeypatch):
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)

    def boom(*_a, **_k):
        raise RuntimeError("outstanding unreadable")
    monkeypatch.setattr(ex.store, "outstanding_residual", boom)
    assert ex.probe_reducing_side("m") is None
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent
    assert rec.get("refused_reason") == "PAST_ENTRY_CUTOFF"


def test_an_UNCERTAIN_order_state_refuses_the_late_completion(
        probe, tmp_path, at):
    """An order we cannot place at the venue is assumed live. No completion
    may be submitted on top of that ignorance, late or otherwise."""
    H, E = probe
    at(INSIDE_COMPLETION)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    ex.store.mark_uncertain("TEST_UNVERIFIED_SUBMIT")
    rec = ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent, "submitted while the order state was UNCERTAIN"
    assert rec.get("refused_reason") is not None


def test_an_UNCERTAIN_state_refuses_EARLY_completions_too(
        probe, tmp_path, at):
    """Not a property of the cutoff: the same refusal holds inside the
    entry window, so P0 did not open a late-only hole."""
    H, E = probe
    at(EARLY)
    ex, t = mk(H, E, tmp_path)
    _hold(ex)
    ex.store.mark_uncertain("TEST_UNVERIFIED_SUBMIT")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert not t.sent


def test_the_reducing_side_is_None_when_flat_and_when_matched(
        probe, tmp_path, at):
    H, E = probe
    at(EARLY)
    ex, _t = mk(H, E, tmp_path)
    assert ex.probe_reducing_side("m") is None          # flat
    _hold(ex, up="2", down="2")
    assert ex.probe_reducing_side("m") is None          # matched
