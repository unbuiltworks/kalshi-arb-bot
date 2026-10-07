"""THE 2026-10-07T19:15:47Z OBSERVABILITY REGRESSION.

An eligible attempt reached the venue and was rejected five times. The
venue named the problem in each response body. All five answers were lost,
because the `PreviewRejected` handler wrote to the in-memory tracker and
the refusal list and NEVER to the tape -- the detail lived only in the
final stdout JSON, and Railway dropped 5704 log lines on that run. We
learned that the venue said no and nothing about why.

These tests prove the rejection is now durable, that the FIFTH one -- the
one that trips VENUE_ERROR_STORM and ends the cohort -- is on the tape
before the cohort ends, and that recording cannot do any of the things
recording must never do.

Nothing here reaches a network or a real credential.
"""
from __future__ import annotations

import json

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import local_faults as LF
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import E1_FINGERPRINT
from src import pmus_auth as A

from analysis.l5.e1_cohort.test_e1_live_path import (        # noqa: F401
    FakeTransport, _isolate, arm, fake_pin, _snapshot_from, BASE)


#: five DISTINCT venue answers, so a test cannot pass by recording one
#: rejection five times, and the fifth is identifiable on its own.
ANSWERS = [
    (400, "Bad Request",
     '{"error":"quantity below minimum","code":"MIN_QTY"}'),
    (400, "Bad Request", '{"error":"price not on tick","code":"TICK"}'),
    (403, "Forbidden", '{"error":"account not permitted","code":"ACCT"}'),
    (422, "Unprocessable", '{"error":"post-only would cross","code":"XING"}'),
    (400, "Bad Request", '{"error":"unknown market","code":"MARKET"}'),
]


class RejectingTransport:
    """Rejects every PREVIEW with its own distinct answer. Never accepts a
    submission, and records every path it was asked for.

    THE COUNTER IS CLASS-LEVEL ON PURPOSE. `submit_first_leg` constructs a
    NEW transport for every attempt -- that is how the one-POST-per-instance
    guarantee is preserved -- so an instance counter would reset and hand
    out the same answer five times. My first version of this fixture did
    exactly that, and the test caught the fixture rather than the code.
    `test_a_fresh_transport_is_built_per_attempt` pins the behaviour so the
    reason is not lost again.
    """

    paths = []
    n = 0
    instances = 0

    def __init__(self):
        RejectingTransport.instances += 1

    def request(self, method, path, body=None, base=None):
        RejectingTransport.paths.append((method, path))
        if path != R.O.PREVIEW_PATH:
            raise AssertionError("submission attempted: %s %s"
                                 % (method, path))
        st, reason, bod = ANSWERS[min(RejectingTransport.n,
                                      len(ANSWERS) - 1)]
        RejectingTransport.n += 1
        raise R.O.VenueHTTPError(method=method, path=path, status=st,
                                 reason=reason, body=bod,
                                 headers={"content-type": "application/json"})


class CollectingRecorder:
    def __init__(self, raises=False):
        self.events = []
        self.raises = raises

    def record_event(self, **kw):
        if self.raises:
            raise RuntimeError("recorder is broken")
        self.events.append(kw)
        return kw


def run(monkeypatch, tmp_path, *, recorder=None, transport=RejectingTransport):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    RejectingTransport.paths = []
    RejectingTransport.n = 0
    RejectingTransport.instances = 0
    FakeTransport.posts = []
    arm(monkeypatch)
    tracker = SP.CohortTracker()
    out = R.run_cohort(
        snapshot_provider=lambda: _snapshot_from(fake_pin(int(BASE))),
        tracker=tracker, transport_factory=transport, recorder=recorder,
        now_fn=lambda: BASE + 10, max_iterations=40)
    return tracker, out


# ============ PROOF 1: FIVE REJECTIONS, FIVE DURABLE EVENTS ==============

def test_five_preview_rejections_produce_five_durable_events(monkeypatch,
                                                             tmp_path):
    rec = CollectingRecorder()
    tracker, out = run(monkeypatch, tmp_path, recorder=rec)

    evs = [e for e in rec.events if e["event"] == "PREVIEW_REJECTED"]
    assert len(evs) == 5, [e["event"] for e in rec.events]
    assert tracker.venue_errors == 5
    assert tracker.verdict()["truncation_cause"] == SP.TRUNCATION_VENUE_STORM

    # five DISTINCT venue answers, in order, none overwritten or deduped
    got = [(e["detail"]["http_status"], e["detail"]["venue_response_body"])
           for e in evs]
    want = [(st, bod) for st, _r, bod in ANSWERS]
    assert got == want, got


def test_the_terminal_fifth_rejection_is_on_the_tape(monkeypatch, tmp_path):
    """The fifth is the one that ends the cohort. It is also the one most
    likely to be lost, because the storm stop fires immediately after."""
    rec = CollectingRecorder()
    tracker, out = run(monkeypatch, tmp_path, recorder=rec)
    evs = [e for e in rec.events if e["event"] == "PREVIEW_REJECTED"]
    fifth = evs[-1]["detail"]
    assert fifth["http_status"] == 400
    assert "MARKET" in fifth["venue_response_body"]
    assert fifth["iteration"] == 5
    # and the cohort really did terminate on it
    assert out["stopped_because"] == \
        "VERDICT_CONTINUE_INSUFFICIENT_EVIDENCE:VENUE_ERROR_STORM"
    assert tracker.consecutive_venue_errors == 5


def test_every_required_field_is_present_and_populated(monkeypatch, tmp_path):
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    d = [e for e in rec.events
         if e["event"] == "PREVIEW_REJECTED"][0]["detail"]

    assert d["phase"] == "PREVIEW"
    for k in ("unix", "utc", "session_id", "iteration"):
        assert d[k] not in (None, ""), k
    assert d["window"] == 0
    assert d["spec_fingerprint"] == E1_FINGERPRINT
    # the venue's own words
    assert d["http_status"] == 400
    assert d["http_reason"] == "Bad Request"
    assert "MIN_QTY" in d["venue_response_body"]
    assert d["request_method"] == "POST"
    assert d["request_path"] == R.O.PREVIEW_PATH
    # identity
    assert d["slug"].startswith("btc-updown-15m-")
    assert d["token_up"] and d["token_down"]
    assert d["token_up"] != d["token_down"]
    # E1's decision before serialization
    assert d["e1_outcome_side"] == "UP"
    assert d["e1_price_local"] == "0.50"
    assert d["e1_qty_local"] == "0.13"
    assert d["e1_observed_best_bid"] == "0.50"
    assert d["e1_observed_best_ask"] == "0.52"
    # the wire, after serialization -- read from the verbatim body
    assert d["wire_body_non_secret"], "no serialized body was recorded"
    assert d["serializer"] == "src.l5_order_body"
    # the two facts that must never be inferred
    assert d["latch_touched"] is False
    assert d["order_submitted"] is False
    assert d["is_a_venue_error"] is True
    assert d["is_a_local_fault"] is False


def test_the_record_is_enough_to_reproduce_the_rejection(monkeypatch,
                                                         tmp_path):
    """The point of the record is that someone can rebuild the request."""
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    d = [e for e in rec.events
         if e["event"] == "PREVIEW_REJECTED"][0]["detail"]
    body = d["wire_body_non_secret"]
    rebuilt = R.O.order_body(slug=d["slug"], outcome_side=d["e1_outcome_side"],
                             price=d["e1_price_local"],
                             qty=d["e1_qty_local"])
    assert rebuilt == body, (rebuilt, body)
    assert d["sent_envelope_key"] == "request"


# ================= PROOF 2: RECORDING IS INERT ===========================

def test_recording_never_submits(monkeypatch, tmp_path):
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    assert FakeTransport.posts == []
    assert all(p == R.O.PREVIEW_PATH for _m, p in RejectingTransport.paths)
    assert R.O.SUBMIT_PATH not in [p for _m, p in RejectingTransport.paths]


def test_recording_never_consumes_a_latch(monkeypatch, tmp_path):
    rec = CollectingRecorder()
    tracker, out = run(monkeypatch, tmp_path, recorder=rec)
    assert tracker.placements == 0
    assert out["placement_count"] == 0
    d = tmp_path / "e1_cohort"
    assert not d.exists() or list(d.glob("*.submitted")) == []


def test_recording_does_not_alter_risk_accounting(monkeypatch, tmp_path):
    """DIFFERENTIAL. The tracker must end in the IDENTICAL state whether or
    not a recorder is attached. If recording moved a counter, this fails."""
    t_off, out_off = run(monkeypatch, tmp_path, recorder=None)
    t_on, out_on = run(monkeypatch, tmp_path, recorder=CollectingRecorder())

    a = json.loads(json.dumps(t_off.snapshot(), sort_keys=True, default=str))
    b = json.loads(json.dumps(t_on.snapshot(), sort_keys=True, default=str))
    assert a == b, "recording changed the tracker state"
    assert out_off["stopped_because"] == out_on["stopped_because"]
    assert out_off["refusals"] == out_on["refusals"]
    assert out_off["placement_count"] == out_on["placement_count"] == 0


def test_recording_does_not_turn_a_rejection_into_an_ambiguous_submission(
        monkeypatch, tmp_path):
    rec = CollectingRecorder()
    tracker, out = run(monkeypatch, tmp_path, recorder=rec)
    assert tracker.ambiguous_submissions == 0
    assert "AMBIGUOUS" not in (out["stopped_because"] or "")
    assert not any("AMBIGUOUS" in s for s in tracker.hard_stops())


def test_a_broken_recorder_is_not_misclassified_as_ambiguous(monkeypatch,
                                                             tmp_path):
    """A recorder that raises must not be laundered into an ambiguous
    submission or a local fault -- nothing was submitted either way."""
    with pytest.raises(RuntimeError, match="recorder is broken"):
        run(monkeypatch, tmp_path, recorder=CollectingRecorder(raises=True))
    assert FakeTransport.posts == []
    assert R.O.SUBMIT_PATH not in [p for _m, p in RejectingTransport.paths]


def test_recording_does_not_become_a_local_fault(monkeypatch, tmp_path):
    """A venue rejection with healthy signing stays a venue error. The
    local-fault classifier must not be dragged in by the new record."""
    rec = CollectingRecorder()
    tracker, _ = run(monkeypatch, tmp_path, recorder=rec)
    assert tracker.local_execution_faults == 0
    assert tracker.venue_errors == 5


# ===================== PROOF 3: NO SECRET EVER ===========================

def test_no_credential_value_reaches_the_record(monkeypatch, tmp_path):
    monkeypatch.setenv(A.SECRET_ENV, "SUPERSECRETKEYVALUE999")
    monkeypatch.setenv(A.KEY_ENV, "SECRETKEYIDVALUE888")
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    blob = json.dumps(rec.events, default=str)
    assert "SUPERSECRETKEYVALUE999" not in blob
    assert "SECRETKEYIDVALUE888" not in blob
    d = [e for e in rec.events
         if e["event"] == "PREVIEW_REJECTED"][0]["detail"]
    assert d["REDACTED_AT_EVERY_DEPTH"] is True


def test_no_auth_header_or_signature_is_recorded(monkeypatch, tmp_path):
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    blob = json.dumps(rec.events, default=str).lower()
    for banned in ("x-pm-signature", "x-pm-access-key", "x-pm-timestamp",
                   "authorization"):
        assert banned not in blob, banned


# ============== PROOF 4: THE BUILDER ITSELF IS PURE ======================

def test_the_record_builder_has_no_side_effects(monkeypatch, tmp_path):
    """It is called from the middle of the submission handler, so it must
    not be able to do anything at all except return a dict."""
    monkeypatch.setattr(L, "consume_attempt", lambda **k: (_ for _ in ()
                                                           ).throw(
        AssertionError("latch touched by the record builder")))
    snap = _snapshot_from(fake_pin(int(BASE)))
    rec = R.preview_rejection_record(
        snap, {"status": 400, "reason": "Bad Request",
               "response_body": "{}", "method": "POST",
               "path": R.O.PREVIEW_PATH, "sent": {"quantity": 0.13}},
        iteration=3, now=BASE + 10)
    assert rec["iteration"] == 3
    assert rec["latch_touched"] is False
    assert rec["order_submitted"] is False
    assert FakeTransport.posts == []


def test_the_handler_records_before_it_continues():
    """Source-level: the recorder call must sit BEFORE the `continue`, or
    the fifth rejection is lost to the storm exactly as it was."""
    src = open("analysis/l5/e1_cohort/runner.py").read()
    i = src.index("except O.PreviewRejected as exc:")
    block = src[i:i + 1400]
    assert "PREVIEW_REJECTED" in block
    assert block.index("recorder.record_event") < block.index("continue")
    assert "redact_detail" in block


def test_a_fresh_transport_is_built_per_attempt(monkeypatch, tmp_path):
    """Five attempts, five transports. This is the one-POST-per-instance
    guarantee E1 inherits, and it is why the fixture above counts
    class-wide."""
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    assert RejectingTransport.instances == 5, RejectingTransport.instances
    assert len(RejectingTransport.paths) == 5


def test_the_serialized_wire_values_are_retrievable_from_the_record(
        monkeypatch, tmp_path):
    """The record stores the serialized body VERBATIM instead of re-listing
    its fields, because this package may not name wire keys. So prove here,
    where naming them IS allowed, that every value the owner asked for is
    actually readable out of the persisted event.
    """
    rec = CollectingRecorder()
    run(monkeypatch, tmp_path, recorder=rec)
    d = [e for e in rec.events
         if e["event"] == "PREVIEW_REJECTED"][0]["detail"]
    body = d["wire_body_non_secret"]

    assert body["quantity"] == 0.13
    assert isinstance(body["quantity"], float), "quantity must be a number"
    assert body["price"]["currency"] == "USD"
    assert body["price"]["value"] is not None
    assert body["participateDontInitiate"] is True
    assert body["marketSlug"] == d["slug"]
    assert body["outcomeSide"] in ("OUTCOME_SIDE_YES", "OUTCOME_SIDE_NO")
    # and the record says what each one is, without naming it
    assert d["wire_value_types"]["quantity"] == "float"
    assert "participateDontInitiate" in d["wire_body_keys"]
