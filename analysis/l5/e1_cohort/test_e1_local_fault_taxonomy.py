"""THE 2026-10-07T16:48:58Z REGRESSION.

The cohort opened window 0 -- the first window E1 ever opened -- asked for a
preview, and 20.8 ms later stopped with `truncation_cause:
VENUE_ERROR_STORM`. The venue had never been contacted. `cryptography` was
not installed in the image, `src.pmus_auth.signed_headers` imports it
lazily, and five identical `ModuleNotFoundError`s were laundered into five
venue errors by a broad `except Exception` and a handler that counted
`PreviewRejected`.

TWO PROOFS ARE REQUIRED AND BOTH ARE HERE.

  1. THE FULL CHAIN, under the PRODUCTION environment contract:
     env contract -> WINDOW_OPENED -> live-book snapshot -> eligibility ->
     placement decision -> submission boundary -> FAKE transport. This is
     `runner.main()`, not a reimplementation of it, and the credentials are
     the real variable names with a throwaway key.

  2. THE BROKEN CONTRACT: one explicit local failure, zero transport, zero
     latch, and NO VENUE_ERROR_STORM.

Every test here asserts on the PRODUCTION code path. None of them reaches a
network, a venue or a real credential.
"""
from __future__ import annotations

import base64

import pytest

from analysis.l5.e1_cohort import arming as AR
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import local_faults as LF
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import E1_FINGERPRINT
from src import pmus_auth as A

from analysis.l5.e1_cohort.test_e1_live_path import (        # noqa: F401
    FakeTransport, Clock, _isolate, arm, drive_main, fake_pin,
    _snapshot_from, BASE)


def break_the_contract(monkeypatch, *, how="dependency_missing"):
    """Make signing fail the way production actually failed, or the two
    other ways it plausibly could."""
    if how == "dependency_missing":
        import builtins
        real = builtins.__import__

        def no_crypto(name, *a, **k):
            if name.split(".")[0] == "cryptography":
                raise ModuleNotFoundError("No module named 'cryptography'")
            return real(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", no_crypto)
    elif how == "secret_unset":
        monkeypatch.delenv(A.SECRET_ENV, raising=False)
    elif how == "secret_unusable":
        monkeypatch.setenv(A.SECRET_ENV, "this is not base64 at all !!")
    elif how == "key_id_unset":
        monkeypatch.delenv(A.KEY_ENV, raising=False)
    else:                                                    # pragma: no cover
        raise AssertionError("unknown breakage %r" % how)


# =================== PROOF 1: THE FULL CHAIN, CONTRACT INTACT =============

def test_the_whole_chain_under_the_production_env_contract(
        monkeypatch, tmp_path, capsys):
    """env contract -> WINDOW_OPENED -> snapshot -> eligibility ->
    placement -> submission boundary -> FAKE transport.

    The point is not that orders are placed -- other tests cover that. The
    point is that the chain survives the new contract check, which sits
    before the first window, and that the contract check PROVED signing
    rather than being switched off.
    """
    clock = Clock(BASE + 10)
    driven = drive_main(monkeypatch, tmp_path, clock=clock,
                        starts=[int(BASE)])
    assert driven["rc"] == 0, driven["rc"]
    assert driven["run_cohort_called"] is True
    captured = driven["run_cohort_out"]
    # the env contract was satisfied with the REAL names
    assert A.has_credentials()
    # and PROVEN, locally, before any window
    proof = captured["signing_proof"]
    assert proof["ok"] is True
    assert proof["network_performed"] == "none"
    assert proof["venue_post"] == "none"
    assert proof["latch_touched"] is False
    assert proof["header_values_recorded"] is False
    assert proof["credential_contract"]["present"] == {
        A.KEY_ENV: True, A.SECRET_ENV: True}
    # the chain then ran to the fake transport
    assert captured["placement_count"] >= 1, captured["placement_count"]
    assert FakeTransport.posts, "the submission boundary was never reached"
    # frozen economics, unchanged
    assert all(p["quantity"] == 0.13 for p in FakeTransport.posts)
    assert all(p["post_only"] is True for p in FakeTransport.posts)
    # and NOT ONE venue error
    assert captured["tracker"]["venue_errors"] == 0
    assert captured["tracker"]["local_execution_faults"] == 0
    assert captured["spec_fingerprint"] == E1_FINGERPRINT


def test_the_contract_check_is_not_a_boolean_someone_can_set(monkeypatch,
                                                             tmp_path):
    """`verify_signing_contract` SIGNS. It does not ask whether the names
    are present: a key that is present and unusable is the failure mode a
    presence check would miss."""
    monkeypatch.setenv(A.SECRET_ENV, base64.b64encode(b"\x00" * 7).decode())
    assert A.has_credentials() is True       # "present"
    with pytest.raises(LF.LocalExecutionFault):
        LF.verify_signing_contract()         # but not usable


# ============ PROOF 2: THE BROKEN CONTRACT FAILS CLOSED, ONCE =============

@pytest.mark.parametrize("how", ["dependency_missing", "secret_unset",
                                 "secret_unusable", "key_id_unset"])
def test_a_broken_credential_contract_is_one_local_failure_not_a_storm(
        monkeypatch, tmp_path, how):
    """THE HEADLINE REGRESSION, in all four ways signing can break.

    One explicit local failure. Zero transport. Zero latch. No
    VENUE_ERROR_STORM.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    break_the_contract(monkeypatch, how=how)

    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=lambda: _snapshot_from(fake_pin(
                           int(BASE))),
                       tracker=tracker, transport_factory=FakeTransport,
                       now_fn=lambda: BASE + 10, max_iterations=40)

    # ONE explicit local failure
    assert out["stopped_because"] == "LOCAL_EXECUTION_FAULT"
    assert tracker.local_execution_faults == 1, tracker.local_execution_faults
    # NOT a venue error, and NOT a storm
    assert tracker.venue_errors == 0
    assert tracker.consecutive_venue_errors == 0
    v = tracker.verdict()
    assert v["truncation_cause"] == SP.TRUNCATION_LOCAL_FAULT
    assert v["truncation_cause"] != SP.TRUNCATION_VENUE_STORM
    assert not any("VENUE_ERROR_STORM" in s for s in tracker.hard_stops())
    # ZERO transport, ZERO latch, ZERO placements
    assert FakeTransport.posts == []
    assert out["transport_constructed"] is False
    assert out["venue_contacted"] is False
    assert out["latch_slots_consumed"] == 0
    assert out["placement_count"] == 0
    assert out["iterations"] == 0
    assert not (tmp_path / "e1_cohort").exists() or \
        list((tmp_path / "e1_cohort").glob("*.submitted")) == []


def test_it_stops_on_the_FIRST_fault_not_the_fifth(monkeypatch, tmp_path):
    """The old behaviour burned FIVE iterations of a 240-placement
    authorization discovering a fact that was true on the first one."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    break_the_contract(monkeypatch)
    tracker = SP.CohortTracker()
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return _snapshot_from(fake_pin(int(BASE)))

    R.run_cohort(snapshot_provider=provider, tracker=tracker,
                 transport_factory=FakeTransport, now_fn=lambda: BASE + 10,
                 max_iterations=40)
    assert tracker.local_execution_faults == 1
    assert calls["n"] == 0, ("a snapshot was requested even though signing "
                             "was already known to be broken")


def test_the_fault_is_persisted_to_the_tape_redacted(monkeypatch, tmp_path):
    """The owner reads the tape, not the logs. A local fault must be IN it,
    named, with its origin -- and with no secret value anywhere."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    secret = (A.__dict__ and __import__("os").environ[A.SECRET_ENV])
    break_the_contract(monkeypatch, how="dependency_missing")

    events = []

    class Rec:
        def record_event(self, **kw):
            events.append(kw)

    R.run_cohort(snapshot_provider=lambda: _snapshot_from(fake_pin(
                     int(BASE))),
                 transport_factory=FakeTransport, recorder=Rec(),
                 now_fn=lambda: BASE + 10, max_iterations=40)

    assert len(events) == 1, events
    ev = events[0]
    assert ev["event"] == "LOCAL_EXECUTION_FAULT"
    d = ev["detail"]
    assert d["exception_type"] == "builtins.ModuleNotFoundError"
    assert "cryptography" in d["exception_message_redacted"]
    assert d["is_a_venue_error"] is False
    assert d["reached_the_venue"] is False
    assert d["consumed_a_latch_slot"] is False
    assert d["deterministic_and_would_repeat_identically"] is True
    assert "pmus_auth" in str(d["origin"])
    assert d["REDACTED_AT_EVERY_DEPTH"] is True
    # the credential NAMES are recorded; the refuted hypothesis is recorded
    cc = d["credential_contract"]
    assert cc["names_read"] == sorted([A.KEY_ENV, A.SECRET_ENV])
    assert cc["names_not_read_by_any_code"] == ["PMUS_API_SECRET"]
    assert cc["VALUES_NEVER_READ_OR_RECORDED"] is True
    # AND NO SECRET VALUE, ANYWHERE IN THE WHOLE EVENT
    assert secret not in str(ev)


def test_redaction_subtracts_a_credential_that_reaches_a_message(monkeypatch):
    """`b64decode` and the key loader quote their input back in some
    messages, and a signing failure is exactly when that happens."""
    monkeypatch.setenv(A.SECRET_ENV, "SUPERSECRETVALUE1234567890")
    out = LF.redact("failed on SUPERSECRETVALUE1234567890 oh dear")
    assert "SUPERSECRETVALUE1234567890" not in out
    assert "<REDACTED>" in out
    nested = LF.redact_detail(
        {"a": ["SUPERSECRETVALUE1234567890", {"b": "x"}], "n": 1,
         "t": True, "z": None})
    assert "SUPERSECRETVALUE1234567890" not in str(nested)
    assert nested["n"] == 1 and nested["t"] is True and nested["z"] is None


# ============== THE TAXONOMY ITSELF: VENUE PROTECTION IS INTACT ==========

def test_a_genuine_venue_error_still_gets_the_bounded_storm_protection(
        monkeypatch, tmp_path):
    """The fix must not disarm the thing that works. With signing HEALTHY,
    a venue that rejects previews still produces venue errors and still
    stops at five."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)                    # contract INTACT

    class RejectingTransport:
        def request(self, method, path, body=None, base=None):
            raise R.O.VenueHTTPError(method=method, path=path, status=400,
                                     reason="Bad Request",
                                     body='{"error":"nope"}', headers={})

    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=lambda: _snapshot_from(fake_pin(
                           int(BASE))),
                       tracker=tracker,
                       transport_factory=RejectingTransport,
                       now_fn=lambda: BASE + 10, max_iterations=40)
    assert tracker.venue_errors == 5
    assert tracker.local_execution_faults == 0
    assert tracker.verdict()["truncation_cause"] == SP.TRUNCATION_VENUE_STORM
    assert out["placement_count"] == 0


def test_a_post_network_decode_failure_is_not_called_a_local_fault(
        monkeypatch, tmp_path):
    """THE FALSE-POSITIVE GUARD, and the reason the classifier re-runs the
    contract instead of matching exception types.

    `json.loads` of a truncated venue response raises ValueError, exactly as
    an unusable key does. With signing healthy it must stay a venue error.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)                    # contract INTACT

    class GarbleTransport:
        def request(self, method, path, body=None, base=None):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    tracker = SP.CohortTracker()
    R.run_cohort(snapshot_provider=lambda: _snapshot_from(fake_pin(
                     int(BASE))),
                 tracker=tracker, transport_factory=GarbleTransport,
                 now_fn=lambda: BASE + 10, max_iterations=40)
    assert tracker.local_execution_faults == 0, (
        "a post-network decode failure was mislabelled as a local fault")
    assert tracker.venue_errors == 5
    assert tracker.verdict()["truncation_cause"] == SP.TRUNCATION_VENUE_STORM


def test_the_same_ValueError_IS_a_local_fault_when_signing_is_broken(
        monkeypatch, tmp_path):
    """The other half of the same proof: identical exception, opposite
    classification, decided by reproduction."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    fault = LF.classify_preview_exception(ValueError("same text"))
    assert fault is None
    break_the_contract(monkeypatch, how="secret_unusable")
    fault = LF.classify_preview_exception(ValueError("same text"))
    assert isinstance(fault, LF.LocalExecutionFault)
    assert fault.detail["surfaced_at"] == "PREVIEW"
    assert fault.detail["original_exception_type"] == "builtins.ValueError"
    assert fault.detail["was_counted_as_a_venue_error"] is False


def test_a_local_fault_is_not_a_venue_exception_by_inheritance():
    """If it were a `PreviewRejected` subclass the old handler would still
    catch it and the storm would come back."""
    assert not issubclass(LF.LocalExecutionFault, R.O.PreviewRejected)
    assert not issubclass(LF.LocalExecutionFault, R.O.SubmissionAmbiguous)
    assert not issubclass(LF.LocalExecutionFault, R.O.VenueHTTPError)


def test_a_local_fault_cannot_manufacture_a_venue_storm():
    """Recording twenty local faults must leave the venue counters at
    zero."""
    t = SP.CohortTracker()
    for _ in range(20):
        t.record_local_execution_fault(detail={"x": 1})
    assert t.venue_errors == 0
    assert t.consecutive_venue_errors == 0
    assert t.verdict()["truncation_cause"] == SP.TRUNCATION_LOCAL_FAULT


def test_the_selftest_path_is_not_a_venue_route():
    """The contract check signs a path that the bounded transport would
    refuse, so it can never be mistaken for, or turned into, a request."""
    t = R.O.BoundedOrderTransport()
    with pytest.raises(R.O.MutationRefused):
        t.request("POST", LF.SIGNING_SELFTEST_PATH, {})
    assert LF.SIGNING_SELFTEST_PATH != R.O.PREVIEW_PATH
    assert LF.SIGNING_SELFTEST_PATH != R.O.SUBMIT_PATH


# ===================== THE DEPENDENCY, AT BUILD TIME ======================

def test_the_dockerfile_installs_the_signing_dependency():
    """The defect was an IMAGE defect. The suite cannot run the image, but
    it can refuse to let the install line disappear again."""
    src = open("deploy/l5_e1_cohort/Dockerfile").read()
    assert "pip install" in src and "cryptography==" in src, (
        "the image installs no signing dependency; this is the exact "
        "2026-10-07 defect")
    assert "ed25519.Ed25519PrivateKey.generate()" in src, (
        "the build does not PROVE it can sign; a lazy import means the "
        "entrypoint-import assertion cannot establish it")


def test_the_lazy_import_is_why_the_build_assertion_missed_it():
    """Documents the mechanism as an executable fact: importing the
    entrypoint still loads no third-party module, so the build-time signing
    proof is not redundant."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, scripts.l5_e1_cohort_run;"
         "print('cryptography' in sys.modules)"],
        capture_output=True, text=True, cwd=".")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", out.stdout


def test_a_fault_AFTER_the_latch_stays_AMBIGUOUS_on_purpose(monkeypatch,
                                                            tmp_path):
    """A DELIBERATE asymmetry, recorded so nobody 'improves' it later.

    Signing happens again for the SUBMIT POST. If it broke between the
    preview and the submit, the latch slot has ALREADY been consumed, and
    `_submit_once` raises `SubmissionAmbiguous` -- which is never retried
    and stops the cohort.

    Strictly, no request left the process, so the venue state is knowable.
    But asserting that requires certainty about where exactly the failure
    occurred relative to the socket, and being wrong in that direction
    means retrying a live order. The conservative classification is kept on
    purpose: the latch is spent, the cohort stops, the venue is reconciled.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)

    class BreaksAfterPreview:
        def __init__(self):
            self.n = 0

        def request(self, method, path, body=None, base=None):
            self.n += 1
            if path == R.O.PREVIEW_PATH:
                return {"order": dict(body["request"])}
            raise ModuleNotFoundError("No module named 'cryptography'")

    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=lambda: _snapshot_from(fake_pin(
                           int(BASE))),
                       tracker=tracker, transport_factory=BreaksAfterPreview,
                       now_fn=lambda: BASE + 10, max_iterations=40)
    assert out["stopped_because"] == \
        "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
    assert tracker.ambiguous_submissions == 1
    assert tracker.local_execution_faults == 0
    assert tracker.venue_errors == 0
    # and it stopped at ONE, not five
    assert len(out["refusals"]) == 1


def test_main_exits_nonzero_on_a_local_fault(monkeypatch, tmp_path):
    """A clean exit 0 is a claim that the run was fine. It was not."""
    clock = Clock(BASE + 10)

    # broken AT THE PRODUCTION SEAM. `drive_main` arms, and arming rewrites
    # the credential env, so breaking the env beforehand would be undone.
    def cannot_sign(method, path, now_ms=None):
        raise ModuleNotFoundError("No module named 'cryptography'")
    monkeypatch.setattr(A, "signed_headers", cannot_sign)

    driven = drive_main(monkeypatch, tmp_path, clock=clock,
                        starts=[int(BASE)])
    assert driven["rc"] == 3, driven["rc"]
    assert driven["run_cohort_out"]["stopped_because"] == \
        "LOCAL_EXECUTION_FAULT"
    assert FakeTransport.posts == []
