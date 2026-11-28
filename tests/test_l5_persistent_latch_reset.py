"""The owner's one-time latch reset, and what cannot clear a latch.

A latch is the memory of why a lane stopped. These tests are mostly about
what must NOT clear one: restarts, envelopes, approvals, the arming
ceremony, the resident loop, and every malformed, stale, mis-addressed or
reused reset document. One document, naming one latch by its hash, with the
lane unable to trade and the watchdog clear, moves it to MONITOR -- never
to ARMED -- and leaves the original record in an audit file.
"""
from __future__ import annotations

import copy
import datetime as dt
import inspect
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_btc5m_runner as RUN
from src import l5_persistent_mode as P

from tests.test_l5_persistent_arm import (
    END, FIRST, NOW, SHA, campaign, do, envelope,
)

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
LATCHED_AT = dt.datetime(2026, 11, 17, 22, 9, 41, tzinfo=UTC)
T = dt.datetime(2026, 11, 20, 14, 0, 0, tzinfo=UTC)
SAFE = {"watchdog_halt_clear": True, "own_live_orders": 0,
        "own_exposure_usd": D("0"), "lane_can_submit": False}


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "state.json")
    assert P.write_state(P.STOPPED, why="WATCHDOG:HALT:halt_asserted",
                         now=LATCHED_AT, path=p)["ok"]
    return p


def ident(p):
    return P.latch_identity(P.read_state(p))


def reset(p, **over):
    d = {"approved": True, "action": P.RESET_ACTION,
         "latch_sha256": ident(p), "latch_state": "STOPPED",
         "reason": "watchdog halt of 2026-11-17 reviewed; cause resolved",
         "issued_utc": "2026-11-20T13:50:00Z",
         "expires_utc": "2026-11-20T14:30:00Z",
         "nonce": "reset-20261120-a", "acknowledgement":
             P.RESET_ACKNOWLEDGEMENT}
    d.update(over)
    return json.dumps(d)


def clear(p, raw=None, *, now=T, safe=None, **over):
    return P.clear_latch(raw=reset(p, **over) if raw is None else raw,
                         now=now, safe=SAFE if safe is None else safe,
                         path=p)


def disk(p):
    return Path(p).read_bytes()


# ------------------------------------------------------- the one way it works

def test_the_named_latch_goes_to_monitor_and_never_to_armed(path):
    was = ident(path)
    out = clear(path)
    assert out["ok"] and out["cleared"] and out["armed"] is False
    assert out["was"] == "STOPPED" and out["state"] == P.MONITOR
    st = P.read_state(path)
    assert st["state"] == P.MONITOR
    assert "LATCH_CLEARED_BY_OWNER" in st["detail"]["why"]
    assert was[:16] in st["detail"]["why"]


def test_the_original_record_is_preserved_verbatim_in_the_audit_file(path):
    original = json.loads(Path(path).read_text())
    was = ident(path)
    clear(path)
    lines = Path(path + P.RESET_LOG_SUFFIX).read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["original"] == original
    assert rec["latch_sha256"] == was and rec["to"] == P.MONITOR
    assert rec["reset"]["nonce"] == "reset-20261120-a"
    assert rec["reset"]["reason"].startswith("watchdog halt of 2026-11-17")
    assert rec["conditions"]["watchdog_halt_clear"] == "True"
    assert rec["t_utc"] == T.isoformat()


def test_a_halted_latch_is_cleared_the_same_way(tmp_path):
    p = str(tmp_path / "s.json")
    P.write_state(P.HALTED, why="LEDGER:halt", now=LATCHED_AT, path=p)
    assert clear(p, latch_state="HALTED")["cleared"] is True


def test_after_a_reset_arming_still_needs_the_owners_documents(path):
    clear(path)
    assert P.read_state(path)["state"] == P.MONITOR
    out = P.may_submit(market="cpc-btc-updown-5m-2026-11-16-1820z",
                       strategy="LATE_FAVORITE", post_only=True,
                       notional_usd=D("0.05"), market_exposure_usd=D(0),
                       aggregate_exposure_usd=D(0), day_realized_usd=D(0),
                       now=NOW, raw=envelope(), deployment=SHA, path=path)
    assert out["ok"] is False and out["reason"] == "STATE_NOT_ARMED"


# --------------------------------------------- what can NOT clear a latch

def test_a_restart_does_not_clear_it(path):
    before = disk(path)
    for _ in range(3):                         # three "processes"
        assert P.read_state(path)["state"] == P.STOPPED
        assert P.stored_state_report(path)["latched"] is True
    assert disk(path) == before


def test_the_arming_ceremony_does_not_clear_it(tmp_path):
    p = str(tmp_path / "state.json")
    P.write_state(P.STOPPED, why="x", now=LATCHED_AT, path=p)
    before = disk(p)
    out = do(tmp_path)                         # valid envelope + campaign
    assert out["ok"] is False and out["reason"] == "STATE_STOPPED"
    assert disk(p) == before


@pytest.mark.parametrize("env, camp", [
    (envelope(nonce="a-brand-new-envelope"), None),
    (None, campaign(nonce="b5m-a-brand-new-campaign-0001")),
    (envelope(expires_utc="2026-11-16T17:00:00Z"), None),
    (None, campaign(expires_utc="2026-11-16T17:00:00Z")),
])
def test_new_or_expired_documents_do_not_clear_it(tmp_path, env, camp):
    p = str(tmp_path / "state.json")
    P.write_state(P.HALTED, why="x", now=LATCHED_AT, path=p)
    before = disk(p)
    out = do(tmp_path, env=env, camp=camp)
    assert out["ok"] is False and out.get("armed") is False
    assert disk(p) == before and P.read_state(p)["state"] == P.HALTED


def test_the_resident_loop_never_writes_over_a_latch():
    src = inspect.getsource(RUN)
    i = src.index("NEVER OVER A LATCH")
    block = src[i:i + 900]
    assert "_cur not in PMODE.LATCHED" in block


def test_clear_latch_is_the_only_path_from_a_latch_to_monitor():
    """Every other MONITOR write in the runner is guarded against a latch
    or follows a state that cannot be one."""
    src = (ROOT / "src" / "l5_persistent_mode.py").read_text()
    assert src.count("write_state(\n        MONITOR") == 1
    assert "LATCH_CLEARED_BY_OWNER" in src
    for name in ("l5_btc5m_execution.py", "l5_5m_fleet.py"):
        assert "clear_latch" not in (ROOT / "src" / name).read_text()


# ------------------------------------------------ every refusal changes nothing

def _refused(p, why, **kw):
    before = disk(p)
    out = clear(p, **kw)
    assert out["ok"] is False and out["cleared"] is False
    assert out["reason"] == why, out
    assert disk(p) == before
    assert not Path(p + P.RESET_LOG_SUFFIX).exists()
    return out


@pytest.mark.parametrize("over, why", [
    ({"approved": "yes"}, "RESET_NOT_APPROVED"),
    ({"approved": False}, "RESET_NOT_APPROVED"),
    ({"action": "CLEAR_AND_ARM"}, "RESET_ACTION"),
    ({"acknowledgement": "ok"}, "RESET_ACKNOWLEDGEMENT"),
    ({"nonce": "short"}, "RESET_NONCE_TOO_SHORT"),
    ({"nonce": 12345678}, "RESET_NONCE_TOO_SHORT"),
    ({"reason": "because"}, "RESET_REASON_TOO_SHORT"),
    ({"issued_utc": "soon"}, "RESET_TIMES_UNREADABLE"),
    ({"issued_utc": "2026-11-20T14:05:00Z"}, "RESET_ISSUED_IN_THE_FUTURE"),
    ({"expires_utc": "2026-11-20T13:59:00Z"}, "RESET_EXPIRED"),
    ({"expires_utc": "2026-11-20T13:40:00Z"}, "RESET_LIFETIME"),
    ({"expires_utc": "2026-11-21T13:50:00Z"}, "RESET_LIFETIME"),
    ({"latch_state": "HALTED"}, "RESET_IS_FOR_ANOTHER_STATE"),
    ({"latch_sha256": "0" * 64}, "RESET_IS_FOR_ANOTHER_LATCH"),
    ({"latch_sha256": None}, "RESET_IS_FOR_ANOTHER_LATCH"),
])
def test_a_document_that_is_not_exactly_right_refuses(path, over, why):
    _refused(path, why, **over)


@pytest.mark.parametrize("raw, why", [
    ("", "RESET_ABSENT"), ("   ", "RESET_ABSENT"), (None, "RESET_ABSENT"),
    ("{not json", "RESET_UNPARSEABLE"), ("[]", "RESET_NOT_AN_OBJECT"),
    ('{"approved": true}', "RESET_FIELDS"),
])
def test_an_absent_or_malformed_document_refuses(path, raw, why):
    before = disk(path)
    out = P.clear_latch(raw=raw, now=T, safe=SAFE, path=path)
    assert out["reason"] == why and disk(path) == before


def test_an_unknown_field_refuses(path):
    d = json.loads(reset(path))
    d["also_arm"] = True
    before = disk(path)
    out = P.clear_latch(raw=json.dumps(d), now=T, safe=SAFE, path=path)
    assert out["reason"] == "RESET_FIELDS" and out["unknown"] == ["also_arm"]
    assert disk(path) == before


@pytest.mark.parametrize("safe, why", [
    (None, "RESET_CONDITIONS_NOT_ESTABLISHED"),
    ({}, "RESET_CONDITIONS_NOT_ESTABLISHED"),
    ({k: v for k, v in SAFE.items() if k != "own_exposure_usd"},
     "RESET_CONDITIONS_NOT_ESTABLISHED"),
    (dict(SAFE, watchdog_halt_clear=False),
     "RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR"),
    (dict(SAFE, watchdog_halt_clear=None),
     "RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR"),
    (dict(SAFE, watchdog_halt_clear="yes"),
     "RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR"),
    (dict(SAFE, own_live_orders=1), "RESET_WHILE_ORDERS_LIVE"),
    (dict(SAFE, own_live_orders=None), "RESET_WHILE_ORDERS_LIVE"),
    (dict(SAFE, own_live_orders=False), "RESET_WHILE_ORDERS_LIVE"),
    (dict(SAFE, own_exposure_usd=D("0.05")), "RESET_WHILE_EXPOSED"),
    (dict(SAFE, own_exposure_usd=None), "RESET_WHILE_EXPOSED"),
    (dict(SAFE, own_exposure_usd="unknown"), "RESET_WHILE_EXPOSED"),
    (dict(SAFE, lane_can_submit=True), "RESET_WHILE_THE_LANE_COULD_SUBMIT"),
    (dict(SAFE, lane_can_submit=None), "RESET_WHILE_THE_LANE_COULD_SUBMIT"),
])
def test_each_safety_condition_is_required_and_exact(path, safe, why):
    before = disk(path)
    out = P.clear_latch(raw=reset(path), now=T, safe=safe, path=path)
    assert out["reason"] == why and out["cleared"] is False
    assert disk(path) == before


def test_a_state_that_is_not_latched_is_left_alone(tmp_path):
    for state in (P.MONITOR, P.ARMED):
        p = str(tmp_path / (state + ".json"))
        P.write_state(state, why="x", now=LATCHED_AT, path=p)
        before = disk(p)
        out = P.clear_latch(raw=reset(p), now=T, safe=SAFE, path=p)
        assert out["reason"] == "NOT_LATCHED" and disk(p) == before
    p = str(tmp_path / "never.json")
    out = P.clear_latch(raw="{}", now=T, safe=SAFE, path=p)
    assert out["reason"] == "NOT_LATCHED" and not Path(p).exists()


def test_an_unreadable_state_refuses(tmp_path):
    p = str(tmp_path / "bad.json")
    Path(p).write_text("{corrupt")
    out = P.clear_latch(raw="{}", now=T, safe=SAFE, path=p)
    assert out["reason"] == "STATE:STATE_CORRUPT"
    assert Path(p).read_text() == "{corrupt"


# ------------------------------------------------------------- one time only

def test_the_same_document_cannot_clear_a_later_latch(path):
    raw = reset(path)
    assert P.clear_latch(raw=raw, now=T, safe=SAFE, path=path)["cleared"]
    later = T + dt.timedelta(minutes=5)
    P.write_state(P.STOPPED, why="WATCHDOG:again", now=later, path=path)
    before = disk(path)
    out = P.clear_latch(raw=raw, now=later + dt.timedelta(minutes=1),
                        safe=SAFE, path=path)
    assert out["reason"] == "RESET_IS_FOR_ANOTHER_LATCH"
    assert disk(path) == before


def test_a_spent_nonce_cannot_be_reused_even_with_the_right_hash(path):
    assert clear(path)["cleared"]
    later = T + dt.timedelta(minutes=5)
    P.write_state(P.STOPPED, why="WATCHDOG:again", now=later, path=path)
    before = disk(path)
    out = clear(path, now=later + dt.timedelta(minutes=1))   # fresh hash
    assert out["reason"] == "RESET_ALREADY_USED" and disk(path) == before
    assert clear(path, now=later + dt.timedelta(minutes=1),
                 nonce="reset-20261120-b")["cleared"] is True
    assert len(Path(path + P.RESET_LOG_SUFFIX).read_text().splitlines()) == 2


def test_two_latches_never_share_an_identity(tmp_path):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    P.write_state(P.STOPPED, why="x", now=LATCHED_AT, path=a)
    P.write_state(P.STOPPED, why="x",
                  now=LATCHED_AT + dt.timedelta(seconds=1), path=b)
    assert ident(a) != ident(b)
    P.write_state(P.STOPPED, why="y", now=LATCHED_AT, path=b)
    assert ident(a) != ident(b)
    assert P.latch_identity({"ok": False}) is None


# ------------------------------------------------------- the audit comes first

def test_if_the_audit_cannot_be_written_the_latch_stays(path, monkeypatch):
    real = open

    def boom(f, *a, **k):
        if str(f).endswith(P.RESET_LOG_SUFFIX):
            raise OSError("volume read-only")
        return real(f, *a, **k)
    monkeypatch.setattr("builtins.open", boom)
    before = disk(path)
    out = clear(path)
    assert out["reason"] == "RESET_AUDIT_NOT_WRITTEN"
    assert disk(path) == before and P.read_state(path)["state"] == P.STOPPED


def test_a_corrupt_audit_file_refuses(path):
    Path(path + P.RESET_LOG_SUFFIX).write_text("{corrupt\n")
    before = disk(path)
    out = clear(path)
    assert out["reason"] == "RESET_AUDIT_UNREADABLE" and disk(path) == before


def test_the_audit_is_written_before_the_state(path, monkeypatch):
    seen = {}
    real = P.write_state

    def spy(state, **kw):
        seen["audit_at_write"] = Path(path + P.RESET_LOG_SUFFIX).exists()
        return real(state, **kw)
    monkeypatch.setattr(P, "write_state", spy)
    clear(path)
    assert seen["audit_at_write"] is True


# ---------------------------------------------------------------- visibility

def test_a_latch_is_reported_whatever_the_envelope_says(path, monkeypatch):
    monkeypatch.setenv(P.STATE_PATH_ENV, path)
    for env in ("", envelope(expires_utc="2026-11-16T17:00:00Z")):
        monkeypatch.setenv(P.ENVELOPE_ENV, env)
        rep = P.stored_state_report()
        assert rep["state"] == "STOPPED" and rep["latched"] is True
        assert rep["would_refuse_arming"] is True
        assert rep["latch_sha256"] == ident(path)
        assert rep["at_utc"] == LATCHED_AT.isoformat()
        assert RUN.resident_state_fields() == {
            "stored_state": "STOPPED", "latched": True,
            "latch_sha256": ident(path), "would_refuse_arming": True}


def test_an_unlatched_state_reports_no_hash(tmp_path):
    p = str(tmp_path / "m.json")
    P.write_state(P.MONITOR, why="x", now=LATCHED_AT, path=p)
    rep = P.stored_state_report(p)
    assert rep["latched"] is False and rep["latch_sha256"] is None
    assert rep["would_refuse_arming"] is False


def test_an_unreadable_state_reports_that_arming_would_be_refused(tmp_path):
    p = str(tmp_path / "bad.json")
    Path(p).write_text("{")
    rep = P.stored_state_report(p)
    assert rep["readable"] is False and rep["would_refuse_arming"] is True


def test_the_runner_reports_the_stored_state_at_boot_and_while_resident():
    src = inspect.getsource(RUN)
    assert 'emit("L5_BTC5M_STORED_STATE", PMODE.stored_state_report())' in src
    i = src.index('emit("L5_BTC5M_RESIDENT_DISARMED"')
    assert "**resident_state_fields()," in src[i:i + 200]
    # reported BEFORE the arming ceremony, and the reset runs before both
    a = src.index("clear_latch_if_owner_asked(ex,")
    b = src.index('emit("L5_BTC5M_STORED_STATE"')
    c = src.index("_armed = PMODE.enter_armed(")
    assert a < b < c


# ----------------------------------------------------- the runner's conditions

class Ex:
    def __init__(self, live=0, own="0", boom=False):
        self._live, self._own, self._boom = live, own, boom

    def live_orders(self):
        if self._boom:
            raise RuntimeError("ledger unreadable")
        return {str(i): {} for i in range(self._live)}

    def account_exposure(self):
        return {"own_usd": self._own}


@pytest.fixture
def lane(path, monkeypatch):
    monkeypatch.setenv(P.STATE_PATH_ENV, path)
    monkeypatch.setenv(P.LATCH_RESET_ENV, reset(path))
    monkeypatch.setenv("L5_SHADOW_ONLY", "1")
    monkeypatch.delenv(RUN.X.LIVE_ENV, raising=False)
    monkeypatch.setattr(RUN, "shared_halt",
                        lambda url: {"ok": True, "reason": None})
    return path


def test_no_document_means_nothing_is_attempted(lane, monkeypatch):
    monkeypatch.delenv(P.LATCH_RESET_ENV)
    assert RUN.clear_latch_if_owner_asked(Ex(), T) is None
    assert P.read_state(lane)["state"] == P.STOPPED


def test_the_runner_clears_when_every_condition_holds(lane):
    out = RUN.clear_latch_if_owner_asked(Ex(), T)
    assert out["cleared"] is True and out["armed"] is False
    assert P.read_state(lane)["state"] == P.MONITOR


@pytest.mark.parametrize("setup, why", [
    (lambda m: m.setattr(RUN, "shared_halt", lambda u: {
        "ok": False, "reason": "HALT:halt_asserted"}),
     "RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR"),
    (lambda m: m.setattr(RUN, "shared_halt", lambda u: {
        "ok": False, "reason": "HALT_URL_NOT_SET"}),
     "RESET_WHILE_WATCHDOG_HALT_NOT_CLEAR"),
    (lambda m: m.setenv("L5_SHADOW_ONLY", "0"),
     "RESET_WHILE_THE_LANE_COULD_SUBMIT"),
    (lambda m: m.setenv(RUN.X.LIVE_ENV, RUN.X.ARM_TOKEN),
     "RESET_WHILE_THE_LANE_COULD_SUBMIT"),
])
def test_the_runner_refuses_when_a_switch_or_the_watchdog_says_no(
        lane, monkeypatch, setup, why):
    setup(monkeypatch)
    before = disk(lane)
    out = RUN.clear_latch_if_owner_asked(Ex(), T)
    assert out["reason"] == why and disk(lane) == before


@pytest.mark.parametrize("ex, why", [
    (Ex(live=1), "RESET_WHILE_ORDERS_LIVE"),
    (Ex(own="0.05"), "RESET_WHILE_EXPOSED"),
    (Ex(own=None), "RESET_CONDITIONS_NOT_ESTABLISHED"),
    (Ex(boom=True), "RESET_CONDITIONS_NOT_ESTABLISHED"),
])
def test_the_runner_refuses_on_its_own_orders_or_exposure(lane, ex, why):
    before = disk(lane)
    out = RUN.clear_latch_if_owner_asked(ex, T)
    assert out["reason"] == why and disk(lane) == before


def test_a_reset_and_an_arming_cannot_share_a_boot(lane, monkeypatch):
    """Armable switches refuse the reset; with them off the lane cannot
    submit. So the latch clears on one restart and trading needs another."""
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(RUN.X.LIVE_ENV, RUN.X.ARM_TOKEN)
    assert RUN.clear_latch_if_owner_asked(Ex(), T)["cleared"] is False
    assert P.read_state(lane)["state"] == P.STOPPED


# ============================ A STORED ARMED IS NOT A FRESH AUTHORIZATION
#
# The loop's gate asks only whether the envelope is valid and the stored
# state is ARMED. So an ARMED left by an earlier campaign plus any valid
# envelope used to pass it whenever the arming ceremony refused or did not
# run. `demote_stale_armed` removes a stored ARMED this boot did not earn.

from src import l5_live_authorization as AUTH   # noqa: E402

GATE_NOW = dt.datetime(2026, 11, 16, 18, 21, 0, tzinfo=UTC)


@pytest.fixture
def old_armed(tmp_path, monkeypatch):
    """ARMED on disk for an EARLIER campaign, its documents long replaced."""
    p = str(tmp_path / "state.json")
    out = do(tmp_path)                              # campaign A armed
    assert out["ok"] and P.read_state(p)["state"] == P.ARMED
    monkeypatch.setenv(P.STATE_PATH_ENV, p)
    monkeypatch.setenv(P.DEPLOYMENT_ENV, SHA)
    return p


def set_docs(monkeypatch, env=None, camp=None):
    monkeypatch.setenv(P.ENVELOPE_ENV, envelope() if env is None else env)
    monkeypatch.setenv(AUTH.CAMPAIGN_APPROVAL_ENV,
                       campaign() if camp is None else camp)


def test_the_weakness_a_new_valid_envelope_passes_the_gate_on_an_old_armed(
        old_armed, monkeypatch):
    """Stated as it was: without the demotion, the gate says yes."""
    set_docs(monkeypatch, env=envelope(nonce="a-different-envelope-2"),
             camp=campaign(nonce="b5m-a-different-campaign-0002"))
    assert RUN.persistent_gate(now=GATE_NOW)["ok"] is True


@pytest.mark.parametrize("refusal", [
    "ARMED_FOR_ANOTHER_CAMPAIGN", "CAMPAIGN_ALREADY_STARTED",
    "CAMPAIGN_NOT_APPROVED", "ENVELOPE:ENVELOPE_EXPIRED",
])
def test_a_refused_ceremony_removes_an_armed_it_did_not_earn(
        old_armed, monkeypatch, refusal):
    set_docs(monkeypatch, env=envelope(nonce="a-different-envelope-2"),
             camp=campaign(nonce="b5m-a-different-campaign-0002"))
    out = RUN.demote_stale_armed({"ok": False, "reason": refusal}, GATE_NOW)
    assert out["demoted"] is True and out["ceremony"] == refusal
    assert P.read_state(old_armed)["state"] == P.MONITOR
    gate = RUN.persistent_gate(now=GATE_NOW)
    assert gate["ok"] is False and gate["reason"] == "STATE_NOT_ARMED"


def test_no_ceremony_at_all_also_removes_it(old_armed, monkeypatch):
    """No planned market, so the ceremony never ran: the old ARMED goes."""
    set_docs(monkeypatch, env=envelope(nonce="a-different-envelope-2"))
    out = RUN.demote_stale_armed(None, GATE_NOW)
    assert out["demoted"] is True
    assert out["ceremony"] == "NO_CEREMONY_THIS_BOOT"
    assert P.read_state(old_armed)["state"] == P.MONITOR


@pytest.mark.parametrize("env, camp", [
    ("", None), (None, ""), ("", ""),
])
def test_missing_documents_never_keep_an_armed(old_armed, monkeypatch,
                                               env, camp):
    set_docs(monkeypatch, env=env, camp=camp)
    out = RUN.demote_stale_armed({"ok": False, "reason": "x"}, GATE_NOW)
    assert out["demoted"] is True
    assert P.read_state(old_armed)["state"] == P.MONITOR


def test_the_same_documents_after_a_restart_keep_it(old_armed, monkeypatch):
    """The identical authorization, re-read mid-campaign: the ceremony
    refuses because the first window has passed, and ARMED rightly stays."""
    set_docs(monkeypatch)
    before = disk(old_armed)
    out = RUN.demote_stale_armed(
        {"ok": False, "reason": "CAMPAIGN_ALREADY_STARTED"}, GATE_NOW)
    assert out == {"kept": True, "state": P.ARMED, "why": out["why"]}
    assert disk(old_armed) == before


def test_the_same_documents_do_not_keep_it_once_the_envelope_expires(
        old_armed, monkeypatch):
    set_docs(monkeypatch)
    late = dt.datetime(2026, 11, 16, 18, 41, 0, tzinfo=UTC)
    out = RUN.demote_stale_armed(
        {"ok": False, "reason": "ENVELOPE:ENVELOPE_EXPIRED"}, late)
    assert out["demoted"] is True
    assert P.read_state(old_armed)["state"] == P.MONITOR


def test_a_ceremony_that_succeeded_is_left_alone(old_armed, monkeypatch):
    set_docs(monkeypatch)
    before = disk(old_armed)
    assert RUN.demote_stale_armed({"ok": True, "armed": True}, GATE_NOW) \
        is None
    assert disk(old_armed) == before


@pytest.mark.parametrize("state", [P.STOPPED, P.HALTED, P.MONITOR])
def test_it_never_touches_a_latch_or_a_resting_state(tmp_path, monkeypatch,
                                                     state):
    p = str(tmp_path / "s.json")
    P.write_state(state, why="x", now=LATCHED_AT, path=p)
    monkeypatch.setenv(P.STATE_PATH_ENV, p)
    set_docs(monkeypatch)
    before = disk(p)
    assert RUN.demote_stale_armed({"ok": False, "reason": "x"}, GATE_NOW) \
        is None
    assert disk(p) == before


def test_if_it_cannot_be_removed_the_process_must_not_continue(
        old_armed, monkeypatch):
    set_docs(monkeypatch, env=envelope(nonce="a-different-envelope-2"))
    monkeypatch.setattr(P, "write_state", lambda *a, **k: {
        "ok": False, "reason": "STATE_NOT_WRITTEN"})
    out = RUN.demote_stale_armed({"ok": False, "reason": "x"}, GATE_NOW)
    assert out["demoted"] is False
    src = inspect.getsource(RUN)
    i = src.index("_stale = demote_stale_armed(_armed,")
    block = src[i:i + 700]
    assert 'if _stale.get("demoted") is False:' in block
    assert "return 0" in block


def test_the_demotion_runs_after_the_ceremony_and_before_the_loop():
    src = inspect.getsource(RUN)
    a = src.index("_armed = PMODE.enter_armed(")
    b = src.index("_stale = demote_stale_armed(_armed,")
    c = src.index("PERSISTENT MODE TAKES OVER THE LOOP")
    assert a < b < c
    assert src.count("_stale = demote_stale_armed(") == 1
