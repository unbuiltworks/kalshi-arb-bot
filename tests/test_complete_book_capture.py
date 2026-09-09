"""Instrumentation that touches the live path must be provably inert.

The owner's condition was explicit: capture the authenticated book on
ordinary COMPLETEs "if it can be done without affecting order latency or
live behavior". These tests pin the three properties that make that true --
post-hoc placement, no venue call, and a breaker so an unreachable recorder
costs one timeout per session rather than one per completion -- and pin that
the three book populations can never be silently pooled.
"""
from __future__ import annotations

import re

from src import pmus_spring_l5_session as ses

_SRC = open(ses.__file__).read()


def _fn(name):
    """The source of a nested function, for structural assertions."""
    i = _SRC.index(f"def {name}(")
    j = _SRC.index("\n        def ", i + 10)
    return _SRC[i:j]


# ----------------------------------------------------------------- bounds
def test_the_capture_is_bounded_per_session_and_by_a_gap():
    assert ses.COMPLETE_BOOK_MAX_PER_SESSION == 8
    assert ses.COMPLETE_BOOK_MIN_GAP_S == 2.0
    body = _fn("_complete_book")
    assert "COMPLETE_BOOK_MAX_PER_SESSION" in body
    assert "COMPLETE_BOOK_MIN_GAP_S" in body


def test_one_failure_breaks_the_circuit_for_the_rest_of_the_session():
    """An unreachable recorder must cost one timeout, not one per complete."""
    body = _fn("_complete_book")
    assert 'if _complete_book_state["broken"]:' in body
    assert 'return' in body.split('if _complete_book_state["broken"]:')[1][:40]
    assert '_complete_book_state["broken"] = True' in body
    assert 'if book.get("unreadable")' in body


def test_the_budgets_are_reset_at_the_session_boundary():
    """They are module-level dicts; without a reset they cap the PROCESS."""
    i = _SRC.index("def run_session(")
    head = _SRC[i:i + 1600]
    assert '_control_state.update({"n": 0, "last": 0.0})' in head
    assert '_complete_book_state.update(' in head
    assert "_run_session_body" in head
    # the reset must precede the body, or it caps the process again
    assert head.index("_complete_book_state.update(") < head.index(
        "_run_session_body(")


# ------------------------------------------------------- no venue traffic
def test_the_complete_capture_makes_no_venue_call():
    """Unlike the #93 control path, which fetches a fresh venue BBO."""
    body = _fn("_complete_book")
    for venue in ("outcome_bbo", "q1.", "t.request", "_venue_market_status"):
        assert venue not in body, venue
    # and the control path still does make one, so the two are distinguishable
    assert "outcome_bbo" in _fn("_control_book")


def test_it_only_reads_the_recorder_status_document():
    assert "_recorder_book(slug, side_up_)" in _fn("_complete_book")


# --------------------------------------------------------- post-hoc only
def test_it_runs_after_the_order_resolved_and_only_for_completes():
    call = _SRC[_SRC.index("_control_book(side_up_, px_, report"):]
    call = call[:600]
    assert 'if relation_ == "complete":' in call
    assert "_complete_book(side_up_, report[\"orders\"][-1])" in call
    # guarded, so a diagnostic can never halt a session
    assert "except Exception:" in call


def test_the_call_site_sits_inside_the_non_rejected_harvest_branch():
    i = _SRC.index('if not end_reason.startswith("rejected"):')
    seg = _SRC[i:i + 900]
    assert "_complete_book(" in seg


def test_it_returns_nothing_and_writes_only_diagnostic_fields():
    body = _fn("_complete_book")
    assert "-> None:" in body
    written = set(re.findall(r'row\["([a-z_]+)"\]\s*=', body))
    assert written == {"book_capture", "recorder_book"}, written


# ------------------------------------------------- populations stay apart
def test_the_three_book_populations_are_labelled_and_never_pooled():
    body = _fn("_complete_book")
    assert 'row["book_capture"] = "complete_observation"' in body
    # it must NOT set the #93 control marker
    assert "acceptance_control" not in body.split('"""')[2]
    # and it yields to a book already attached by control or rejection
    assert 'if row.get("recorder_book") is not None:' in body


def test_the_control_group_still_owns_its_marker():
    assert 'row["acceptance_control"] = True' in _fn("_control_book")


# ------------------------------------------------ nothing else moved
def test_no_money_or_risk_limit_changed():
    from decimal import Decimal as D
    assert ses.SPRING_BAND_LO == D("0.35") and ses.SPRING_BAND_HI == D("0.65")
    assert ses.COMPLETE_REJECT_STREAK_MAX == 3
    assert ses.DEFAULT_MAX_COMPLETE_ACTIONS == 12
    assert ses.DEFAULT_COMPLETE_REQUOTE is False
    assert ses.CONTROL_BOOK_MAX_PER_SESSION == 24


def test_the_requote_gate_is_untouched_by_this_change(monkeypatch):
    """Pin the environment: this asserts the DEFAULT, so it must not read an
    ambient PMUS_SPRING_COMPLETE_REQUOTE an operator happens to have set."""
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV, raising=False)
    assert "if relation_ in requote_relations() and touch != px_:" in _SRC
    assert ses.requote_relations() == ("open", "extend")


def test_arming_the_flag_adds_complete_and_nothing_else(monkeypatch):
    """The whole live-path diff, stated as a test: one tuple member."""
    monkeypatch.setenv(ses.COMPLETE_REQUOTE_ENV, "1")
    assert ses.requote_relations() == ("open", "extend", "complete")
