"""The read-only submission-claim probe, and its preflight gate.

WHY IT EXISTS. On 2026-10-19 the 0330z attempt passed all sixteen gates,
formed a complete plan, and then refused AUTHORIZATION_ALREADY_USED at the
wire -- naming no experiment, because the report's `submission_claim` is only
populated after a claim SUCCEEDS. The spent authorization was invisible until
the moment it mattered, and the diagnosis afterwards had to be reconstructed
from code and history rather than read off the refusal.

WHAT IS PINNED HERE: the probe reads and never writes; UNKNOWN is never
reported as an absence; SPENT refuses in preflight, before an order is even
reserved; UNKNOWN does NOT refuse, because the atomic O_EXCL claim at the
wire is still the guarantee and a failed stat() must not cost a window; and
the directory actually inspected is reported, so a misconfigured ledger path
cannot masquerade as a clean experiment.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import l5_cancel_mechanism as M                       # noqa: E402
from src import l5_live_authorization as AUTH                  # noqa: E402
from test_l5_cancel_mechanism_leg import (                     # noqa: E402
    MARKET, Store, Transport, mech)


def ledger():
    return pathlib.Path(AUTH.ledger_path())


def take_the_claim(market=MARKET):
    return AUTH.claim_submission(market, {"purpose": "test"})


# ============================================================ the probe reads

def test_an_absent_lock_reads_unused():
    st = AUTH.submission_claim_state()
    assert st["state"] == "UNUSED"
    assert st["why"] == "NO_SUCH_LOCK_IN_THE_LEDGER_DIRECTORY"


def test_a_taken_claim_reads_spent():
    take_the_claim()
    st = AUTH.submission_claim_state()
    assert st["state"] == "SPENT"
    assert st["why"] == "THE_ONE_SUBMISSION_LOCK_ALREADY_EXISTS"
    assert st.get("lock_mtime_utc"), "a spent claim should say when"


def test_the_sid_is_the_experiment_alone():
    """No market in the id: one authorization, one submission, TOTAL."""
    exp = AUTH.experiment_id()
    st = AUTH.submission_claim_state()
    assert st["sid"] == AUTH.submission_sid(exp) == "sub1.%s" % exp
    assert MARKET not in st["sid"]


def test_the_directory_inspected_is_reported():
    """A misconfigured ledger path would otherwise look like a clean
    experiment, so the operator is told WHICH directory answered."""
    st = AUTH.submission_claim_state()
    assert st["lock_dir"] == str(ledger().parent)


def test_an_explicit_experiment_overrides_the_environment():
    take_the_claim()
    assert AUTH.submission_claim_state()["state"] == "SPENT"
    other = AUTH.submission_claim_state("A-DIFFERENT-EXPERIMENT")
    assert other["state"] == "UNUSED"
    assert other["sid"] == "sub1.A-DIFFERENT-EXPERIMENT"


# =========================================== the probe writes NOTHING, ever

def test_the_probe_creates_nothing(tmp_path):
    d = ledger().parent
    d.mkdir(parents=True, exist_ok=True)
    before = sorted(p.name for p in d.iterdir())
    for _ in range(5):
        AUTH.submission_claim_state()
    assert sorted(p.name for p in d.iterdir()) == before


def test_probing_does_not_spend_the_claim():
    """The decisive property: after any number of probes the real claim must
    still be available, because a probe that took the lock would silently
    burn the one submission it was asked about."""
    for _ in range(3):
        assert AUTH.submission_claim_state()["state"] == "UNUSED"
    claim = take_the_claim()                 # must NOT raise
    assert claim["sid"] == AUTH.submission_sid(AUTH.experiment_id())
    assert AUTH.submission_claim_state()["state"] == "SPENT"


# ================================================= UNKNOWN is never UNUSED

def test_no_experiment_named_is_unknown_not_unused(monkeypatch):
    monkeypatch.setattr(AUTH, "experiment_id", lambda: "")
    st = AUTH.submission_claim_state()
    assert st["state"] == "UNKNOWN"
    assert st["why"] == "NO_EXPERIMENT_NAMED"
    assert st["state"] != "UNUSED"


def test_an_unreadable_ledger_is_unknown_not_unused(monkeypatch):
    def boom(self):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(pathlib.Path, "exists", boom)
    st = AUTH.submission_claim_state()
    assert st["state"] == "UNKNOWN"
    assert st["why"].startswith("LEDGER_UNREADABLE:")


def test_an_absent_ledger_directory_is_unused_with_its_reason(monkeypatch,
                                                              tmp_path):
    """An absent directory CANNOT contain the lock, so this one negative is
    a real absence -- and it still says so by name rather than silently."""
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER",
                       str(tmp_path / "nope" / "auth.jsonl"))
    st = AUTH.submission_claim_state()
    assert st["state"] == "UNUSED"
    assert st["why"] == "LEDGER_DIRECTORY_DOES_NOT_EXIST_YET"


# ================================================== the preflight gate

def test_the_gate_is_terminal_not_a_wait():
    """A spent claim never cures itself, so it must not be waited out."""
    assert "submission_claim_not_already_spent" not in M.WAIT_CHECKS


def test_a_spent_claim_refuses_in_preflight(monkeypatch):
    take_the_claim()
    st, t = Store(), Transport()
    m = mech(monkeypatch, store=st, transport=t)
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_REFUSED"
    assert "submission_claim_not_already_spent" in rep["failed_checks"]
    assert t.submits == []


def test_the_preflight_refusal_names_the_spent_experiment(monkeypatch):
    """The gap this closes: the 0330z refusal named no experiment at all."""
    take_the_claim()
    m = mech(monkeypatch, store=Store(), transport=Transport())
    rep = m.step()
    detail = [c["detail"] for c in rep["checks"]
              if c["check"] == "submission_claim_not_already_spent"][0]
    assert detail["experiment"] == AUTH.experiment_id()
    assert detail["sid"] == AUTH.submission_sid(AUTH.experiment_id())
    assert detail["state"] == "SPENT"
    assert detail["lock_dir"]


def test_a_spent_claim_reserves_no_order_at_all(monkeypatch):
    """Refusing in preflight is strictly earlier than the wire path: no
    client order id is minted and no reservation has to be released."""
    take_the_claim()
    st = Store()
    mech(monkeypatch, store=st, transport=Transport()).step()
    assert st.live_order_count() == 0


def test_an_unknown_probe_does_not_refuse_the_window(monkeypatch):
    """A stat() that failed must not cost an authorized window. The atomic
    claim at the wire still decides -- and here it is genuinely unused."""
    t = Transport()
    m = mech(monkeypatch, store=Store(), transport=t)
    m.submission_claim_state = lambda: {"state": "UNKNOWN",
                                        "why": "LEDGER_UNREADABLE:OSError"}
    assert m.step()["submitted"] == 1
    assert len(t.submits) == 1


def test_an_unknown_probe_is_reported_not_silently_passed(monkeypatch):
    m = mech(monkeypatch, store=Store(), transport=Transport())
    m.submission_claim_state = lambda: {"state": "UNKNOWN", "why": "X"}
    rep = m.step()
    detail = [c["detail"] for c in rep["checks"]
              if c["check"] == "submission_claim_not_already_spent"][0]
    assert detail["state"] == "UNKNOWN", "an unknown must stay visible"


def test_the_preflight_gate_does_not_replace_the_wire_claim(monkeypatch):
    """BOTH must hold. Preflight is an early detector; the O_EXCL claim is
    the guarantee, and it is what decides a lock taken in between."""
    take_the_claim()
    t = Transport()
    m = mech(monkeypatch, store=Store(), transport=t)
    m.submission_claim_state = lambda: {"state": "UNUSED", "why": "RACED"}
    rep = m.step()
    assert str(rep["refused"]).startswith("SUBMISSION_CLAIM:")
    assert "AUTHORIZATION_ALREADY_USED" in rep["refused"]
    assert t.submits == []


def test_a_probe_failure_cannot_raise_into_the_loop(monkeypatch):
    m = mech(monkeypatch, store=Store(), transport=Transport())

    def boom():
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(AUTH, "submission_claim_state", boom)
    st = m.submission_claim_state()
    assert st["state"] == "UNKNOWN"
    assert st["why"] == "PROBE_FAILED:RuntimeError"
