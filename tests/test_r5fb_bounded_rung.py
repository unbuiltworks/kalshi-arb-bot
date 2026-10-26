"""R5FB: the l5_touch COMPLETE re-quote inside the $5 envelope.

OWNER 2026-10-26, answering the envelope question: run the mechanism, but
inside the $5 bound the L5 live engine already carries, not R5FT's $30.

THE POINT OF THESE TESTS is that exactly two levers moved and both moved DOWN.
A rung that quietly relaxed a third would defeat the reason it exists, so every
other field is pinned against R5FT field by field rather than eyeballed.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_ramp_ladder as L
from src import pmus_l5_continuous_session as C


def _rung(rid):
    for r in L.ALL_RUNGS:
        if r["id"] == rid:
            return r
    raise AssertionError("%s not defined" % rid)


# --------------------------------------------------------------- it exists
def test_r5fb_is_defined():
    assert _rung("R5FB")["id"] == "R5FB"


def test_the_whole_three_window_campaign_fits_the_five_dollar_envelope():
    """The cap is PER MARKET, and three sequential markets cumulate: capital
    spent on window one is not recovered until it settles."""
    r = _rung("R5FB")
    cap = D(str(r["per_market_cap_usd"]))
    n = int(r["markets_per_campaign"])
    assert cap == D("1.50")
    assert n == 3
    assert cap * n == D("4.50")
    assert cap * n < D("5.00")


def test_the_independent_five_dollar_daily_halt_is_honoured_on_this_path_too():
    assert D(str(_rung("R5FB")["daily_loss_stop_usd"])) == D("-5.00")


# ------------------------------------------- the mechanism is NOT watered down
def test_the_mechanism_under_test_is_identical_to_the_canary():
    r = _rung("R5FB")
    assert L.complement_pricing(r) == "l5_touch"
    assert D(str(L.pair_cost_max(r))) == D("1.00")


def test_only_two_levers_moved_and_both_moved_DOWN():
    """Field-by-field against R5FT. A third relaxation would defeat the rung."""
    fb, ft = _rung("R5FB"), _rung("R5FT")
    moved = {"per_market_cap_usd", "daily_loss_stop_usd",
             "markets_per_campaign",   # 1 -> 3: that IS the campaign shape
             "id", "name", "derived_from"}
    for k in set(ft) | set(fb):
        if k in moved:
            continue
        assert fb.get(k) == ft.get(k), (k, fb.get(k), ft.get(k))
    # and the two money levers are strictly tighter
    assert D(str(fb["per_market_cap_usd"])) < D(str(ft["per_market_cap_usd"]))
    assert D(str(fb["daily_loss_stop_usd"])) > D(str(ft["daily_loss_stop_usd"]))


def test_the_canary_rung_itself_is_untouched():
    """We bounded a copy; we did not weaken or rewrite R5FT."""
    ft = _rung("R5FT")
    assert D(str(ft["per_market_cap_usd"])) == D("30.00")
    assert D(str(ft["daily_loss_stop_usd"])) == D("-30.00")


# ------------------------------------------------------ it is the rearm target
def test_the_rearm_target_is_the_bounded_rung():
    assert C.REARM_RUNG == "R5FB"


def test_the_startcommand_sed_is_now_a_NO_OP_and_that_is_documented():
    """The service rewrites ^REARM_RUNG = "R5F"$ -> "R5FT". That pattern is
    anchored on exactly "R5F", so "R5FB" survives it untouched -- which is why
    no start-command change is needed. If the sed were ever edited to match
    "R5FB" it would retarget a $5 campaign at the $30 rung, so the hazard is
    written down beside the constant."""
    import re
    src = open(C.__file__).read()
    assert re.sub(r'^REARM_RUNG = "R5F"$', 'REARM_RUNG = "R5FT"',
                  'REARM_RUNG = "R5FB"', flags=re.M) == 'REARM_RUNG = "R5FB"'
    head = src[:src.index('REARM_RUNG = "R5FB"')]
    assert "no-op" in head and "sed" in head
    assert "$30" in head or "30.00" in head


def test_the_rearm_configuration_still_expects_the_requote_armed():
    """The guard and the flag are part of the re-arm target, not a surprise."""
    assert "complete" in C.REARM_REQUOTE_RELATIONS
    assert D(str(C.REARM_PAIR_CEILING)) == D("1.00")


# ---------------------------------------------- an unset rung fails CLOSED
def test_an_unset_rung_env_does_not_select_the_bounded_rung(monkeypatch):
    """PMUS_SPRING_RUNG must be set deliberately. Defaulting to R1 means the
    boot assertion mismatches and the probe stays halted, which is correct."""
    monkeypatch.delenv(L.RUNG_ENV, raising=False)
    L._ACTIVE["id"] = None
    assert L.active()["id"] != "R5FB"


@pytest.mark.parametrize("rid", ["R5FB"])
def test_the_rung_is_selectable_by_env(monkeypatch, rid):
    monkeypatch.setenv(L.RUNG_ENV, rid)
    L._ACTIVE["id"] = None
    a = L.active()
    assert a["id"] == rid
    assert D(str(a["per_market_cap_usd"])) == D("1.50")
