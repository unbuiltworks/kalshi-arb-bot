"""The effective configuration, on the record before the arming gate.

WHY THIS EXISTS. On 2026-11-17 `l5-btc5m-live` exited at boot with
ENVELOPE_EXPIRED -- code 0, a designed dead-man, which Railway reports as
CRASHED -- and has been down since. The question "is bounded repetition
actually enabled in production?" was then unanswerable from outside:

  - the variables are all PRESENT on the service
    (L5_BTC5M_STRADDLE_REPEAT, _MAX_SIDE_USD, _MAX_UNMATCHED_USD, and the
    three probabilities), so their existence proves nothing about values;
  - their VALUES are redacted to anything but a session token;
  - and the lane exits before it writes a single step report, so no
    effective-parameter record ever reached the log.

The owner's correction was exactly this: a dataclass default and a
deployed value are different facts, and nothing distinguished them. These
tests pin the line that does.

IT MUST BE EMITTED BEFORE THE ARMING CEREMONY, because the case where the
configuration most needs reading is the case where the process is about
to stop.
"""
from __future__ import annotations

import inspect

import pytest

from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN


def _src():
    return inspect.getsource(RUN)


# ------------------------------------------- it is on the record, early

def test_the_line_is_emitted_before_the_arming_ceremony():
    """Source order: PLAN, then EFFECTIVE_CONFIG, then ARM. If the config
    moved after the gate it would be absent in exactly the situation that
    motivated it."""
    s = _src()
    i_plan = s.index('emit("L5_BTC5M_PLAN"')
    i_cfg = s.index('emit("L5_BTC5M_EFFECTIVE_CONFIG"')
    i_arm = s.index('emit("L5_BTC5M_ARM"')
    assert i_plan < i_cfg < i_arm


def test_it_cannot_stop_the_lane_from_starting_or_refusing():
    s = _src()
    i = s.index('emit("L5_BTC5M_EFFECTIVE_CONFIG"')
    #: up to the arming emit, NOT a fixed 3000 characters: the report grew
    #: (requote, loss ceiling, probe fields) past that arbitrary window while
    #: the guard it looks for was still there. The property is unchanged.
    block = s[i:s.index('emit("L5_BTC5M_ARM"', i)]
    assert "except BaseException" in block
    assert '{"error": type(_exc).__name__}' in block


def _code_between(start: str, end: str) -> str:
    """Source between two markers with COMMENT LINES STRIPPED.

    Scanning raw source matched the word "submit" inside a nearby
    comment -- the third time in this session a prose mention tripped a
    string test. Comments are not calls.
    """
    s = _src()
    block = s[s.index(start):s.index(end)]
    return "\n".join(ln for ln in block.splitlines()
                      if not ln.strip().startswith("#"))


def test_it_reads_no_venue():
    block = _code_between("_ep = ex.effective_params()",
                          'emit("L5_BTC5M_ARM"')
    for forbidden in ("urlopen", "requests", "transport", "submit",
                      "book_fn", "p_fn"):
        assert forbidden not in block, forbidden


# -------------------------- it reports the REPEAT controls specifically

@pytest.mark.parametrize("field", [
    "entry_mode", "straddle_repeat_enabled", "straddle_max_side_usd",
    "straddle_max_unmatched_usd", "live_order_bound",
    "max_resting_entries", "account_cap_usd", "account_lanes",
    "no_new_entry_after_s", "min_complement_shares",
    "inventory_ranking", "taker_completion",
])
def test_every_control_the_owner_asked_about_is_reported(field):
    assert '"%s"' % field in _src(), field


@pytest.mark.parametrize("field", [
    "p_pair_natural_set", "p_complete_set", "p_lone_win_set"])
def test_the_three_probabilities_are_reported_as_set_or_not(field):
    """Whether the gate CAN run is the operational fact. The numbers
    themselves are the owner's and are not printed."""
    assert '"%s"' % field in _src(), field


def test_no_probability_VALUE_is_printed():
    block = _code_between('emit("L5_BTC5M_EFFECTIVE_CONFIG"',
                          'emit("L5_BTC5M_ARM"')
    for leak in ("str(_ep.straddle_p_pair_natural",
                 "str(_ep.straddle_p_complete",
                 "str(_ep.straddle_p_lone_win"):
        assert leak not in block, leak


# ------------------------- the readers it uses are the real production ones

def test_it_reports_the_RESOLVED_value_not_the_dataclass_default():
    """The owner's distinction, as a test: with the env set, the reported
    value must follow the environment rather than Params()."""
    block = _code_between('emit("L5_BTC5M_EFFECTIVE_CONFIG"',
                          'emit("L5_BTC5M_ARM"')
    s = _src()
    #: resolved through the env readers and effective_params, not Params()
    assert "X.straddle_repeat_enabled()" in block
    assert "X.straddle_max_side_usd()" in block
    assert "ex.effective_params()" in s
    assert "C.Params()" not in block


@pytest.mark.parametrize("raw,want", [
    ("YES", True), ("yes", True), ("Yes", True),
    ("NO", False), ("1", False), ("TRUE", False), ("", False),
])
def test_the_repeat_reader_itself_only_accepts_YES(monkeypatch, raw, want):
    """Pinned here too, because this is the value the line reports and
    the one the owner needs to trust."""
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, raw)
    assert X.straddle_repeat_enabled() is want


def test_repetition_is_off_when_unset(monkeypatch):
    monkeypatch.delenv(X.STRADDLE_REPEAT_ENV, raising=False)
    assert X.straddle_repeat_enabled() is False


def test_the_two_ceilings_refuse_the_mode_when_unset(monkeypatch):
    """Repeated entry with no unmatched ceiling is a directional bet in a
    straddle's clothes, so both default to 0 = refuse."""
    for env in (X.STRADDLE_MAX_SIDE_USD_ENV,
                X.STRADDLE_MAX_UNMATCHED_USD_ENV):
        monkeypatch.delenv(env, raising=False)
    assert X.straddle_max_side_usd() == 0
    assert X.straddle_max_unmatched_usd() == 0
