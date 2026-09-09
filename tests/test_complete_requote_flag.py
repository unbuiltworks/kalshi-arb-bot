"""COMPLETE re-quote ships DARK, and the default must stay proven.

L5_COMPLETION_EXTENSION_GATE_v1 is frozen and accumulating forward evidence.
A second change to the completion surface, live at the same time, would make
neither attributable. So this change exists in the binary and does nothing
until an owner arms it, and these tests pin exactly that -- including that
the OPEN/EXTEND behaviour is byte-for-byte unchanged either way.
"""
from __future__ import annotations

import re

import pytest

from src import pmus_spring_l5_session as ses

_SRC = open(ses.__file__).read()


# ------------------------------------------------------------- the default
def test_the_flag_is_off_by_default():
    assert ses.DEFAULT_COMPLETE_REQUOTE is False


def test_with_no_env_set_complete_cannot_requote(monkeypatch):
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV, raising=False)
    assert ses.complete_requote_enabled() is False
    assert ses.requote_relations() == ("open", "extend")


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " on "])
def test_explicit_arming_values_turn_it_on(monkeypatch, raw):
    monkeypatch.setenv(ses.COMPLETE_REQUOTE_ENV, raw)
    assert ses.complete_requote_enabled() is True
    assert ses.requote_relations() == ("open", "extend", "complete")


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe", "2"])
def test_anything_else_leaves_it_off(monkeypatch, raw):
    """Fail closed: an unrecognised value must not arm live money."""
    monkeypatch.setenv(ses.COMPLETE_REQUOTE_ENV, raw)
    assert ses.complete_requote_enabled() is False
    assert "complete" not in ses.requote_relations()


def test_the_flag_is_read_at_call_time_not_at_import(monkeypatch):
    """An import that happened before the env was set must not pin it."""
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV, raising=False)
    assert ses.requote_relations() == ("open", "extend")
    monkeypatch.setenv(ses.COMPLETE_REQUOTE_ENV, "1")
    assert ses.requote_relations() == ("open", "extend", "complete")
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV)
    assert ses.requote_relations() == ("open", "extend")


# ------------------------------------------------- open/extend are untouched
def test_open_and_extend_requote_in_both_states(monkeypatch):
    for raw, expect in (("1", True), ("0", False)):
        monkeypatch.setenv(ses.COMPLETE_REQUOTE_ENV, raw)
        rel = ses.requote_relations()
        assert "open" in rel and "extend" in rel
        assert ("complete" in rel) is expect


def test_there_is_exactly_one_requote_condition_and_it_uses_the_helper():
    """No second copy of the tuple can drift away from the helper."""
    assert _SRC.count('return f"touch_moved:{touch}"') == 1
    assert _SRC.count("if relation_ in requote_relations() and touch != px_:") == 1
    # the old hard-coded tuple must be gone from the condition
    assert 'if relation_ in ("open", "extend") and touch != px_:' not in _SRC


# ------------------------------------------------------ the guards it keeps
def test_the_crossing_guard_still_precedes_the_touch_check():
    cross = _SRC.index('return f"would_cross:{opp_ask}"')
    touch = _SRC.index('return f"touch_moved:{touch}"')
    assert cross < touch
    guard = _SRC[_SRC.index("if px_ >= D(str(opp_ask)):"):cross]
    assert "relation_" not in guard          # applies to every relation


def test_the_rejection_breaker_is_untouched():
    assert ses.COMPLETE_REJECT_STREAK_MAX == 3
    assert _SRC.count("if complete_reject_streak >= COMPLETE_REJECT_STREAK_MAX:") == 1
    # the streak still advances only on venue rejections
    assert _SRC.count('if end_reason.startswith("rejected"):') >= 1


def test_no_money_or_risk_limit_moved():
    """Every surface the owner named as preserved, checked by value."""
    assert ses.SPRING_BAND_LO == __import__("decimal").Decimal("0.35")
    assert ses.SPRING_BAND_HI == __import__("decimal").Decimal("0.65")
    assert ses.DEFAULT_MAX_COMPLETE_ACTIONS == 12
    for surface in ("unmatched_usd", "post_only", "maker"):
        assert surface in _SRC


def test_the_change_adds_no_new_price_source():
    """The re-quote price is the touch the model returned, never a markup."""
    # the touch is computed once, from the book, and reused
    assert _SRC.count('return f"touch_moved:{touch}"') == 1
    # no arithmetic that would pay beyond the touch
    for markup in ("touch + spec", "touch + D(", "touch + tick", "px_ + tick"):
        assert markup not in _SRC, markup


# -------------------------------------------------- why it ships dark at all
def test_the_reason_for_the_default_is_written_where_it_is_set():
    block = _SRC[_SRC.index("COMPLETE_REQUOTE_ENV"): _SRC.index(
        "def complete_requote_enabled")]
    head = _SRC[_SRC.index("COMPLETE_REJECT_STREAK_MAX = 3"):
                _SRC.index("def complete_requote_enabled")]
    assert "L5_COMPLETION_EXTENSION_GATE_v1" in head
    assert "DEFAULT OFF" in head
    assert "0 of 868" in head
    assert "fidelity, not a new strategy" in head


# --- preflight for arming: every bound must still fire ------------------
_LIVE_SRC = open("src/pmus_spring_l5_session.py").read()


def _idx(needle):
    i = _LIVE_SRC.find(needle)
    assert i >= 0, f"missing from the live path: {needle}"
    return i


def test_post_only_would_cross_fires_BEFORE_the_reprice_and_for_every_relation():
    """The one bound that arming could plausibly breach.

    would_cross must be evaluated ahead of the touch_moved check, and must NOT
    be gated on relation -- otherwise an armed COMPLETE could re-quote to a
    price at or through the opposite ask and breach post-only."""
    cross = _idx('return f"would_cross:{opp_ask}"')
    requote = _idx('if relation_ in requote_relations() and touch != px_:')
    assert cross < requote, "would_cross must precede the reprice check"
    guard = _LIVE_SRC[_idx('if px_ >= D(str(opp_ask)):'):cross]
    assert "relation" not in guard, "post-only guard must not be relation-gated"


def test_the_reprice_posts_at_the_touch_and_never_beyond_it():
    """Fidelity, not aggression: the re-quote posts at the model's touch."""
    assert 'return f"touch_moved:{touch}"' in _LIVE_SRC
    tick = _idx('touch = (D(str(touch)) / spec["tick"]).to_integral_value(')
    assert tick < _idx('if relation_ in requote_relations() and touch != px_:')


def test_arming_changes_exactly_one_tuple_and_no_other_bound():
    """The complete live-path diff. Any other gate appearing or vanishing
    between armed and unarmed states would make the change non-minimal."""
    import os
    from src import pmus_spring_l5_session as ses
    for bound in ('price_bound_left', 'band_left', 'would_cross',
                  'spend', 'residual', 'rest'):
        assert bound in _LIVE_SRC, f"{bound} bound vanished from the live path"
    os.environ.pop(ses.COMPLETE_REQUOTE_ENV, None)
    off = ses.requote_relations()
    os.environ[ses.COMPLETE_REQUOTE_ENV] = "1"
    on = ses.requote_relations()
    os.environ.pop(ses.COMPLETE_REQUOTE_ENV, None)
    assert set(on) - set(off) == {"complete"}
    assert set(off) - set(on) == set()
