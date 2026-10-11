"""The acquisition resting predicate: default off, fails closed, and never
widens on a bad value.

The engine's current rule cancels an OPEN/EXTEND whenever `touch != px_`, so
a quote below the bid cannot survive a single poll. These tests pin the
replacement predicate's behaviour before it is wired into the shared engine
file.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_acquisition_rest as ar

TICK_PMUS = "0.0025"
TICK_GEMINI = "0.01"


# ------------------------------------------------------------- default off
def test_default_is_todays_behaviour(monkeypatch):
    monkeypatch.delenv(ar.REST_BEHIND_TOUCH_ENV, raising=False)
    assert ar.enabled() is False
    assert ar.keep_resting("open", "0.30", "0.32", TICK_GEMINI) is False


def test_only_the_exact_flag_value_arms_it(monkeypatch):
    for v in ("", "0", "true", "yes", "1 ", "01"):
        monkeypatch.setenv(ar.REST_BEHIND_TOUCH_ENV, v)
        assert ar.enabled() is (v.strip() == "1"), v


# --------------------------------------------------------------- tolerance
def test_tolerance_defaults_to_zero_so_arming_alone_never_widens(monkeypatch):
    monkeypatch.setenv(ar.REST_BEHIND_TOUCH_ENV, "1")
    monkeypatch.delenv(ar.REST_BEHIND_TICKS_ENV, raising=False)
    assert ar.tolerance_ticks() == 0
    # at the touch exactly: kept (the caller's equality would not even fire)
    assert ar.keep_resting("open", "0.32", "0.32", TICK_GEMINI) is True
    # one tick behind with zero tolerance: cancel
    assert ar.keep_resting("open", "0.31", "0.32", TICK_GEMINI) is False


@pytest.mark.parametrize("raw", ["", "nonsense", "-3", "0", "1.5"])
def test_a_bad_tolerance_narrows_never_widens(monkeypatch, raw):
    monkeypatch.setenv(ar.REST_BEHIND_TOUCH_ENV, "1")
    monkeypatch.setenv(ar.REST_BEHIND_TICKS_ENV, raw)
    assert ar.tolerance_ticks() == 0
    assert ar.keep_resting("open", "0.31", "0.32", TICK_GEMINI) is False


def test_within_tolerance_keeps_resting_and_beyond_it_cancels():
    # 4 ticks of 0.0025 = $0.01, the PMUS candidate threshold
    k = lambda px, t: ar.keep_resting(                      # noqa: E731
        "open", px, t, TICK_PMUS, armed=True, ticks=4)
    assert k("0.5600", "0.5600") is True        # at the touch
    assert k("0.5575", "0.5600") is True        # 1 tick behind
    assert k("0.5500", "0.5600") is True        # 4 ticks = $0.01 behind
    assert k("0.5475", "0.5600") is False       # 5 ticks: beyond tolerance


def test_the_threshold_is_in_the_markets_own_ticks_not_in_dollars():
    """A tolerance of 1 tick means $0.01 on Gemini and $0.0025 on PMUS. This
    is the whole reason the threshold is a parameter: carrying '1 tick' from
    one venue to the other changes the money by 4x."""
    assert ar.keep_resting("open", "0.55", "0.56", TICK_GEMINI,
                           armed=True, ticks=1) is True
    # the same $0.01 gap is FOUR PMUS ticks, so 1 tick does not cover it
    assert ar.keep_resting("open", "0.5500", "0.5600", TICK_PMUS,
                           armed=True, ticks=1) is False
    assert ar.keep_resting("open", "0.5500", "0.5600", TICK_PMUS,
                           armed=True, ticks=4) is True


# ------------------------------------------------------------------- scope
def test_complete_is_not_an_acquisition_relation():
    """COMPLETE prices from the pair bound and has PR #188's own suppression.
    This predicate must not reach it."""
    assert "complete" not in ar.ACQUISITION_RELATIONS
    assert ar.keep_resting("complete", "0.55", "0.56", TICK_GEMINI,
                           armed=True, ticks=8) is False


@pytest.mark.parametrize("rel", ["open", "extend"])
def test_both_acquisition_relations_are_covered(rel):
    assert ar.keep_resting(rel, "0.55", "0.56", TICK_GEMINI,
                           armed=True, ticks=1) is True


def test_a_quote_above_the_touch_is_still_cancelled():
    """Stale-high is the one direction that must keep cancelling: the point
    of the change is to tolerate being BEHIND the bid, not ahead of it."""
    assert ar.keep_resting("open", "0.57", "0.56", TICK_GEMINI,
                           armed=True, ticks=8) is False


# ------------------------------------------------------------ fails closed
@pytest.mark.parametrize("px,touch,tick", [
    (None, "0.56", TICK_GEMINI),
    ("0.55", None, TICK_GEMINI),
    ("0.55", "0.56", None),
    ("0.55", "0.56", "0"),          # a zero tick would divide the rule away
    ("", "0.56", TICK_GEMINI),
    ("abc", "0.56", TICK_GEMINI),
])
def test_unreadable_inputs_fall_through_to_the_existing_cancel(px, touch, tick):
    assert ar.keep_resting("open", px, touch, tick,
                           armed=True, ticks=8) is False


def test_describe_reports_the_configuration_in_force(monkeypatch):
    monkeypatch.setenv(ar.REST_BEHIND_TOUCH_ENV, "1")
    monkeypatch.setenv(ar.REST_BEHIND_TICKS_ENV, "4")
    d = ar.describe()
    assert d["armed"] is True and d["tolerance_ticks"] == 4
    assert d["acquisition_relations"] == ["open", "extend"]


# --------------------------------------------------- no money path in here
def test_the_module_cannot_reach_a_transport_or_an_executor():
    import ast
    import pathlib

    src = pathlib.Path(ar.__file__).read_text()
    banned = ("l5_pmus_transport", "pmus_q1_executor", "live_exec",
              "requests", "httpx", "urllib")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [getattr(n, "module", None) or ""] + [
                a.name for a in n.names]
            for nm in names:
                for bad in banned:
                    assert bad not in (nm or ""), (nm, bad)
