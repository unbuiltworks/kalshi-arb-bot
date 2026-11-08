"""Three root causes from the owner's production evidence at 3620e232f.

The owner's independent check reported: `PROVISIONAL_PRICE_UNREADABLE`
from the opening controller, no `L5_BOOK_COMPLETION_TRIGGER` in the
sampled logs, and a ~5.4 s deferred cost-basis audit on affected ticks.
Reading the first deployed heartbeat alongside those gave three findings,
two of them defects.

  A. **My own instrumentation was wrong.** `detection_to_decision_s`
     reported n 256 median 451.744 s while every decision in the same
     record was `NO_INVENTORY` or `DECIDE_COOLDOWN`. With `up == dn == 0`,
     `up > dn` is False, so `held` is "DOWN" and `held_qty` is 0 -- a FLAT
     market was stamping a fill detection. The 451.7 s was time since the
     first tick of the window, not a latency.

  B. **One unreadable provisional price disqualified both sides.**
     `controller_provisional_price` returns None when `touch - tick <= 0`,
     which happens once the ask is a single tick or the bid is within a
     tick of 1.00 -- routine as a BTC15m window resolves. The other side
     is restable at that moment, and the controller refused to open at all.

  C. **The absent `L5_BOOK_COMPLETION_TRIGGER` is correct, not a fault.**
     That record is emitted only when a completion is APPROVED, and the
     heartbeat shows `actionable_decisions: 0` out of 320 decisions, all
     exiting before the economics. Pinned so it is not chased again.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_completion_latency as CL
from src import l5_live_execution as LX


# ============================================================ A: detection
def test_a_flat_market_is_not_a_held_position():
    """The guard, at the level of the arithmetic that produced the bug."""
    up, dn = D("0"), D("0")
    held, _other = ("UP", "DOWN") if up > dn else ("DOWN", "UP")
    held_qty = up if held == "UP" else dn
    assert held == "DOWN"          # the misleading part, unchanged
    assert held_qty == 0           # and the part the guard now reads
    assert not (held_qty > 0)


def test_the_runner_only_stamps_a_detection_when_something_is_held():
    import ast
    import inspect
    import textwrap
    from src import l5_pmus_live_runner as RUN
    src = textwrap.dedent(inspect.getsource(RUN.Runner.maybe_book_completion))
    tree = ast.parse(src).body[0]
    # find the `if held_qty > 0:` that guards the detection note
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body = "\n".join(ast.unparse(st) for st in node.body)
            if "note_fill_detected" in body:
                assert "held_qty" in ast.unparse(node.test), ast.unparse(
                    node.test)
                found = True
    assert found, "the detection note must sit under a held_qty guard"


def test_a_detection_free_record_reports_no_interval_rather_than_window_age():
    """What the production record should have said: 320 decisions, every
    one pre-economics, and NO latency figure at all."""
    c = CL.CompletionLatency()
    for i in range(8):
        c.note_decision("m", 100.0 + i * 5.0,
                        action="NO",
                        reason="NO_INVENTORY" if i % 2 else "DECIDE_COOLDOWN")
    r = c.report("m")
    assert r["decisions"] == 8
    assert r["detection_to_decision_s"]["n"] == 0
    assert r["detection_to_first_decision_s"] is None
    assert r["book_age_at_decision_s"]["n"] == 0


def test_the_first_decision_interval_is_reported_separately(
):
    """The aggregate is dominated by how long the window ran; the figure
    that answers "how long before we decided anything" is its own field."""
    c = CL.CompletionLatency()
    c.note_fill_detected("m", "UP", 100.0)
    for t in (103.0, 400.0, 900.0):
        c.note_decision("m", t, action="NO", reason="X", book_age_s=0.4)
    r = c.report("m")
    assert r["detection_to_first_decision_s"] == 3.0
    assert r["detection_to_decision_s"]["max"] == 800.0


# ====================================================== B: provisional price
@pytest.mark.parametrize("bid,ask,up_ok,dn_ok", [
    ("0.45", "0.46", True, True),      # ordinary book: both readable
    ("0.00", "0.01", False, True),     # ask at one tick: UP unreadable
    ("0.99", "1.00", True, False),     # bid a tick from 1: DOWN unreadable
    ("0.00", "0.00", False, True),     # ask at zero
])
def test_which_provisional_prices_are_readable_at_the_extremes(
        bid, ask, up_ok, dn_ok):
    book = {"bid": bid, "ask": ask}
    up = LX.controller_provisional_price("UP", book, "0.01")
    dn = LX.controller_provisional_price("DOWN", book, "0.01")
    assert (up is not None) is up_ok, ("UP", bid, ask, up)
    assert (dn is not None) is dn_ok, ("DOWN", bid, ask, dn)


def test_the_extreme_that_produced_the_production_refusal_leaves_one_side():
    """THE ROOT CAUSE, as arithmetic. An ask of one tick pins UP and
    leaves DOWN restable -- so refusing BOTH was the defect."""
    book = {"bid": "0.00", "ask": "0.01"}
    assert LX.controller_provisional_price("UP", book, "0.01") is None
    assert LX.controller_provisional_price("DOWN", book, "0.01") == D("0.99")


def test_an_unreadable_tick_still_disqualifies_both_sides():
    """A tick we cannot read is not a one-sided book; it is no book."""
    book = {"bid": "0.45", "ask": "0.46"}
    for bad in (None, "0", "-0.01", "abc"):
        assert LX.controller_provisional_price("UP", book, bad) is None
        assert LX.controller_provisional_price("DOWN", book, bad) is None


def test_an_unreadable_bid_or_ask_still_disqualifies_both_sides():
    for book in ({"bid": None, "ask": "0.46"}, {"bid": "0.45", "ask": None},
                 {}):
        assert LX.controller_provisional_price("UP", book, "0.01") is None
        assert LX.controller_provisional_price("DOWN", book, "0.01") is None


def test_the_controller_refuses_only_when_BOTH_are_unreadable():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_controller_open)
    assert "if px_up is None and px_dn is None:" in src
    assert "if px_up is None or px_dn is None:" not in src
    # and a forced choice is recorded rather than passed off as the cheaper
    assert "PROVISIONAL_SIDE_FORCED" in src


def test_a_forced_side_is_still_the_readable_one_not_a_guess():
    """Selection logic, in isolation: never invent a price for the pinned
    side, never prefer it, and never silently call it the cheaper."""
    for px_up, px_dn, want_side, want_forced in (
            (D("0.40"), D("0.50"), "UP", None),
            (D("0.60"), D("0.50"), "DOWN", None),
            (None, D("0.98"), "DOWN", "UP"),
            (D("0.98"), None, "UP", "DOWN")):
        if px_up is None and px_dn is None:
            raise AssertionError("not this case")
        if px_dn is None:
            side, px, forced = "UP", px_up, "DOWN"
        elif px_up is None:
            side, px, forced = "DOWN", px_dn, "UP"
        else:
            side, px, forced = (("UP", px_up, None) if px_up <= px_dn
                                else ("DOWN", px_dn, None))
        assert (side, forced) == (want_side, want_forced)
        assert px is not None


# ========================================= C: the absent trigger is correct
def test_the_completion_trigger_is_emitted_only_on_an_APPROVED_completion():
    """So its absence from the logs is the expected consequence of
    `actionable_decisions: 0`, not evidence of a broken emitter. The
    owner's observation was right and is not a fault to chase."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    i = src.index('emit("L5_BOOK_COMPLETION_TRIGGER"')
    before = src[:i]
    # every refusal path returns before the emit
    assert before.count("return dict(rec, ok=False") >= 2, before.count(
        "return dict(rec, ok=False")
    assert 'if not rec.get("ok")' in before


def test_a_report_with_no_actionable_decisions_explains_an_absent_trigger():
    c = CL.CompletionLatency()
    for i in range(10):
        c.note_decision("m", 100.0 + i, action="NO", reason="NO_INVENTORY")
    r = c.report("m")
    assert r["decisions"] == 10 and r["actionable_decisions"] == 0
    assert r["detection_to_first_actionable_s"] is None
