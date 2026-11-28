"""The readiness verdict must see the halt that actually stops arming.

THE INCIDENT, 2026-11-09 08:30Z. An owner-approved single-window maker-only
diagnostic produced zero orders. Verified from production:

  * `submission_gate` -> blocked true, reason NOT_ARMED, shadow_lock FALSE
    (the owner had cleared it), halt_latch CLEAR, owner_arm_token true;
  * `campaign` -> approval_state "approved", the owner's own document
    digest, permits_this_window true, windows_spent 0, max_windows 1;
  * `halt` true and `halt_local_env` true;
  * every decision in the window: `SAFETY_OVERLAY:OPERATOR_HALT`, 162 of
    them, `order: null`, `intent: null`, `armed: false`;
  * and `authorization_preflight` -> **ok TRUE, fail []**.

`Runner.try_arm` opens with

    if t is None or t.armed or self.halted(): return

so the LOCAL operator halt stops arming before any numbered step of
`authorize_arming` is reached. The preflight -- the artifact an operator
reads to decide a window is ready -- did not evaluate that line at all. It
checked the shadow lock, the owner token, the deadline, the campaign, the
watchdog latch and nine other things, and reported READY.

NOTHING WAS LOST BUT THE WINDOW. `windows_spent` stayed 0, no claim was
written, exposure stayed $0, and the engine failed CLOSED -- the correct
direction. The defect is observability: a readiness verdict that cannot see
a blocker is worse than no verdict, because it is believed.

THE REPAIR IS STRUCTURAL, NOT A SECOND CHECK. `Runner.halted` now delegates
to the module-level `operator_halted()`, and the preflight calls that same
function. A duplicated `os.getenv` in the preflight would have been a
second opinion, and a second opinion is what the incident was.

THIS DOES NOT WEAKEN OR CLEAR THE HALT. It only makes it visible. Every
test below asserts the halt still refuses.
"""
from __future__ import annotations

import inspect

import pytest

from src import l5_live_authorization as AUTH
from src import l5_pmus_live_runner as R


def rows(pf):
    return {str(r.get("check")): r for r in pf.get("rows") or ()}


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch):
    """A deterministic baseline: the halt explicitly absent unless a test
    sets it, so none of these read an ambient value."""
    monkeypatch.delenv(R.LIVE_HALT_ENV, raising=False)
    yield


# =====================================================================
# ONE EXPRESSION, SO THEY CANNOT DISAGREE
# =====================================================================

def test_runner_halted_delegates_to_the_module_function():
    """THE STRUCTURAL ASSERTION. If `Runner.halted` ever reads the
    environment itself again, the preflight and `try_arm` can diverge and
    the incident is back."""
    src = inspect.getsource(R.Runner.halted)
    assert "operator_halted()" in src, src
    assert "getenv" not in src, src


def test_try_arm_still_consults_halted_first():
    """The other half of the pair: the money path must keep reading it."""
    src = inspect.getsource(R.Runner.try_arm)
    head = src.split("\n")[:8]
    assert any("self.halted()" in l for l in head), head


@pytest.mark.parametrize("raw,halted", [
    ("1", True), ("yes", True), ("YES", True), ("true", True),
    ("0", True), ("false", True), ("off", True), ("no", True),
    (" x ", True), ("  ", False), ("", False),
])
def test_any_nonempty_value_halts_because_it_is_a_deadman(monkeypatch, raw,
                                                          halted):
    """There is NO 'false' spelling. `"0"` halts. The permissive reading is
    the empty one, which is what a deadman requires -- a typo must stop
    trading, not enable it."""
    monkeypatch.setenv(R.LIVE_HALT_ENV, raw)
    assert R.operator_halted() is halted, raw
    pf = AUTH.authorization_preflight("")
    row = rows(pf)["local_operator_halt"]
    assert row["verdict"] == ("FAIL" if halted else "PASS"), (raw, row)


def test_the_runner_and_the_preflight_agree_on_every_spelling(monkeypatch):
    for raw in ("1", "0", "", "  ", "halt", "no"):
        monkeypatch.setenv(R.LIVE_HALT_ENV, raw)
        engaged = R.operator_halted()
        row = rows(AUTH.authorization_preflight(""))["local_operator_halt"]
        assert bool(row.get("engaged")) is engaged, (raw, row)
        assert (row["verdict"] == "FAIL") is engaged, (raw, row)


# =====================================================================
# THE VERDICT THE INCIDENT NEEDED
# =====================================================================

def test_the_preflight_cannot_report_ok_while_the_halt_is_engaged(
        monkeypatch):
    """THE REGRESSION. This is precisely what reported ok=true at 08:30Z."""
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    pf = AUTH.authorization_preflight("")
    assert pf["ok"] is not True, pf["fail"]
    assert "local_operator_halt" in (pf["fail"] or []), pf


def test_the_halt_is_NAMED_in_fail_not_merely_implied(monkeypatch):
    """An operator has to be able to act on it. A verdict that is false for
    unnamed reasons sends them hunting."""
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    pf = AUTH.authorization_preflight("")
    row = rows(pf)["local_operator_halt"]
    assert row["engaged"] is True
    assert row["env"] == R.LIVE_HALT_ENV == "L5_LIVE_HALT"
    assert "try_arm" in str(row.get("blocks")), row
    assert "deadman" in str(row.get("note")), row


def test_it_passes_and_is_still_reported_when_the_halt_is_clear(monkeypatch):
    """Reported either way: a check that only appears when it fails leaves
    an operator unable to confirm it was evaluated at all."""
    monkeypatch.delenv(R.LIVE_HALT_ENV, raising=False)
    pf = AUTH.authorization_preflight("")
    row = rows(pf)["local_operator_halt"]
    assert row["verdict"] == "PASS"
    assert row["engaged"] is False
    assert "local_operator_halt" not in (pf["fail"] or [])


# =====================================================================
# IT IS A DIFFERENT THING FROM THE INDEPENDENT WATCHDOG
# =====================================================================

def test_the_local_halt_and_the_watchdog_latch_are_separate_rows(
        monkeypatch):
    """Either alone stops arming, so collapsing them would hide whichever
    was not chosen. At 08:30Z the latch was CLEAR and the local halt was
    engaged -- reporting only the latch is how the window was lost."""
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    r = rows(AUTH.authorization_preflight(""))
    assert "local_operator_halt" in r
    assert "halt_gate" in r
    assert r["local_operator_halt"]["verdict"] == "FAIL"
    # the two carry different blocking sites
    assert str(r["local_operator_halt"].get("blocks")) != str(
        r["halt_gate"].get("blocks"))


def test_the_shadow_lock_and_the_local_halt_are_separate_rows(monkeypatch):
    """The 08:30Z shape exactly: shadow lock CLEARED, local halt ENGAGED."""
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    r = rows(AUTH.authorization_preflight(""))
    assert r["shadow_lock"]["verdict"] == "PASS", r["shadow_lock"]
    assert r["local_operator_halt"]["verdict"] == "FAIL"
    pf = AUTH.authorization_preflight("")
    assert pf["ok"] is not True
    assert "shadow_lock" not in (pf["fail"] or [])
    assert "local_operator_halt" in (pf["fail"] or [])


# =====================================================================
# FAIL CLOSED, AND NEVER WEAKEN
# =====================================================================

def test_an_unreadable_halt_is_UNKNOWN_and_never_a_pass(monkeypatch):
    """UNKNOWN is not PASS -- the preflight's own label says so."""
    def boom():
        raise RuntimeError("cannot read the halt")

    monkeypatch.setattr(R, "operator_halted", boom)
    pf = AUTH.authorization_preflight("")
    row = rows(pf)["local_operator_halt"]
    assert row["verdict"] == "UNKNOWN", row
    assert pf["ok"] is not True
    assert "local_operator_halt" in (pf["unknown"] or []), pf


def test_the_preflight_clears_nothing(monkeypatch):
    """READ-ONLY. It must not be able to lift the halt it reports."""
    monkeypatch.setenv(R.LIVE_HALT_ENV, "engaged-by-operator")
    AUTH.authorization_preflight("")
    import os
    assert os.environ[R.LIVE_HALT_ENV] == "engaged-by-operator"
    assert R.operator_halted() is True


def test_nothing_in_the_preflight_writes_the_halt_variable():
    src = inspect.getsource(AUTH.authorization_preflight)
    for bad in ("environ[", "putenv", "setenv", "os.environ.pop"):
        assert bad not in src, bad
