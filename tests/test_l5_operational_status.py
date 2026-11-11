"""The single operational status, and its precedence.

Every fixture below is the SHAPE of evidence the live system already emits,
so the classifier that runs in the preflight is the one under test here.
"""
import pytest

from src import l5_operational_status as S

CLEAN = {
    "orders_uncertain": None, "reconciled": True,
    "pending_terminal": 0, "quarantine": 0,
    "halt_local_env": False, "halt_gate_permitted": True,
    "halt_latch": "CLEAR", "halt_gate_why": None,
    "campaign_approval_state": "approved", "auth_expires_in_s": 9000.0,
    "windows_remaining": 8, "window_refusal": None, "armed": False,
}


def st(**over):
    ev = dict(CLEAN); ev.update(over)
    return S.classify(ev)


# ------------------------------------------------------------ happy paths

def test_a_clean_disarmed_system_is_ready():
    r = st()
    assert r["status"] == S.READY
    assert r["reason"] is None


def test_armed_inside_an_authorized_window_is_running():
    assert st(armed=True)["status"] == S.RUNNING


# ------------------------------------------------------------ precedence

def test_reconciliation_beats_everything():
    r = st(orders_uncertain="phantom fill", halt_local_env=True,
           campaign_approval_state="approval_expired",
           auth_expires_in_s=-1.0, armed=True)
    assert r["status"] == S.RECONCILIATION_BLOCKED
    assert "orders_uncertain" in r["reason"]
    assert S.HALTED in r["other_blockers"]
    assert S.AUTH_EXPIRED in r["other_blockers"]


def test_halt_beats_auth_expiry():
    r = st(halt_local_env=True, auth_expires_in_s=-5.0)
    assert r["status"] == S.HALTED
    assert r["reason"] == "L5_LIVE_HALT_ASSERTED"
    assert S.AUTH_EXPIRED in r["other_blockers"]


def test_auth_expiry_beats_running():
    r = st(auth_expires_in_s=-1.0, armed=True)
    assert r["status"] == S.AUTH_EXPIRED


def test_the_precedence_order_is_explicit():
    assert S.PRECEDENCE == (S.RECONCILIATION_BLOCKED, S.HALTED,
                            S.AUTH_EXPIRED, S.RUNNING, S.READY)


# ------------------------------------------- reconciliation blocked

@pytest.mark.parametrize("over,frag", [
    ({"orders_uncertain": "reconcile raised"}, "orders_uncertain"),
    ({"reconciled": False}, "NOT_RECONCILED"),
    ({"pending_terminal": 1}, "PENDING_TERMINAL"),
    ({"quarantine": 2}, "QUARANTINED"),
    ({"pending_terminal": None}, "PENDING_TERMINAL_UNKNOWN"),
    ({"quarantine": None}, "QUARANTINED_UNKNOWN"),
])
def test_reconciliation_blockers(over, frag):
    r = st(**over)
    assert r["status"] == S.RECONCILIATION_BLOCKED
    assert frag in r["reason"] or any(frag in x for x in r["all_reasons"])


# ------------------------------------------------------------- halted

def test_the_local_env_lock_halts():
    r = st(halt_local_env=True)
    assert r["status"] == S.HALTED and r["reason"] == "L5_LIVE_HALT_ASSERTED"


def test_the_remote_latch_halts_independently_of_the_local_lock():
    """The two are separate locks; either alone must halt."""
    r = st(halt_local_env=False, halt_latch="HALT",
           halt_gate_permitted=False, halt_gate_why="halt_asserted")
    assert r["status"] == S.HALTED
    assert "REMOTE" in r["reason"]


def test_a_stale_halt_record_halts_and_is_not_an_unknown():
    """This is the exact 2026-11-05 blocker."""
    r = st(halt_gate_permitted=False, halt_gate_why="halt_record_stale",
           halt_latch="CLEAR")
    assert r["status"] == S.HALTED
    assert r["reason"] == "REMOTE_halt_record_stale"


def test_an_unestablished_halt_state_halts():
    ev = dict(CLEAN)
    del ev["halt_gate_permitted"]
    del ev["halt_latch"]
    r = S.classify(ev)
    assert r["status"] == S.HALTED
    assert r["reason"] == "HALT_STATE_UNESTABLISHED"


# -------------------------------------------------------- auth expired

@pytest.mark.parametrize("over,frag", [
    ({"campaign_approval_state": "approval_expired"}, "APPROVAL_APPROVAL_EXPIRED"),
    ({"campaign_approval_state": None}, "APPROVAL_UNESTABLISHED"),
    ({"auth_expires_in_s": -1.0}, "AUTHORIZATION_EXPIRED"),
    ({"auth_expires_in_s": None}, "DEADLINE_UNESTABLISHED"),
    ({"windows_remaining": 0}, "WINDOW_BUDGET_SPENT"),
    ({"window_refusal": "WINDOW_NOT_IN_AUTHORIZED_SET"},
     "WINDOW_NOT_IN_AUTHORIZED_SET"),
])
def test_auth_blockers(over, frag):
    r = st(**over)
    assert r["status"] == S.AUTH_EXPIRED
    assert frag in r["reason"] or any(frag in x for x in r["all_reasons"])


def test_a_spent_budget_mid_window_is_not_expired():
    """windows_remaining hits 0 on the LAST window while it still runs."""
    r = st(windows_remaining=0, window_in_progress=True, armed=True)
    assert r["status"] == S.RUNNING


# ------------------------------------------------- unknown is never ready

@pytest.mark.parametrize("missing", [
    "pending_terminal", "quarantine", "campaign_approval_state",
    "auth_expires_in_s",
])
def test_a_missing_field_never_reads_as_ready(missing):
    ev = dict(CLEAN)
    del ev[missing]
    assert S.classify(ev)["status"] != S.READY


def test_an_empty_evidence_set_is_never_ready():
    r = S.classify({})
    assert r["status"] != S.READY
    assert r["status"] in S.PRECEDENCE


def test_every_check_is_reported_even_when_passing():
    r = st()
    names = {c["check"] for c in r["checks"]}
    for n in ("orders_uncertain", "pending_terminal", "quarantine",
              "halt_local_env", "remote_halt", "campaign_approval",
              "auth_deadline", "window_budget", "authorized_window", "armed"):
        assert n in names, n


def test_a_blocking_check_names_the_state_it_blocks():
    r = st(halt_local_env=True)
    row = [c for c in r["checks"] if c["check"] == "halt_local_env"][0]
    assert row["ok"] is False
    assert row["blocks"] == S.HALTED


@pytest.mark.parametrize("v", [True, "true", "YES", "1", "on"])
def test_truthy_halt_spellings_all_halt(v):
    assert st(halt_local_env=v)["status"] == S.HALTED


@pytest.mark.parametrize("v", [False, "false", "0", "", None, "no"])
def test_falsey_halt_spellings_do_not_halt(v):
    assert st(halt_local_env=v)["status"] == S.READY
