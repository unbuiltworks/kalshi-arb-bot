"""The place-ack capture: an OBSERVER, and provably nothing more.

The successful `order.place` acknowledgement shape has never been recorded.
CONTINUOUS-2 placed two accepted orders and the driver parsed them with a
parser recovered from the canary; the shape went into the evidence file as
INFERRED. The cost of inferring was already paid: the first live cancel sent
`clientOrderId` and the venue answered -1013, because the id is nested under
`result`.

These tests hold the capture to two properties that matter more than the
evidence it gathers:

  IT CANNOT CHANGE EXECUTION -- same object out, same exceptions, same arm
  gate, and a recording failure that stays a note rather than becoming a raise.

  IT CANNOT MOVE THE AUTHORIZED HASH -- the owner authorization names
  ws_continuous's executable_sha, so the capture lives at the session seam the
  invoker owns and the driver stays byte-for-byte what was authorized.

Fixtures are built from analysis/gemini/venue_shapes_20260918.json, the
VERBATIM payloads the venue sent during the first live window. A fixture
kinder than the venue is what hid the last three defects.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from src.gemini_execution import arming, run_continuous as R, spec
from src.gemini_execution import ws_continuous as WC

SHAPES = json.load(open(os.path.join("analysis", "gemini",
                                     "venue_shapes_20260918.json"),
                        encoding="utf-8"))
REAL_CANCEL_REJECT = SHAPES["cancel_reject_ack"]
REAL_OPEN = SHAPES["open_orders"]

#: the shape ws_canary's recovered parser expects, which the driver now reads.
#: Labelled INFERRED here too: these tests prove the CAPTURE works on it, they
#: do not claim the venue sends it. Only a live ack can claim that.
INFERRED_PLACE_ACK = {
    "id": "l5cp-0f0e0d0c",
    "status": 200,
    "result": {"orderId": 145828836242288478,
               "clientOrderId": "l5cp-owner-2026-yes-774c492b",
               "symbol": "GEMI-BTC05M2609181435-UP",
               "outcome": "yes", "price": "0.03", "quantity": "0.01",
               "timeInForce": "maker-or-cancel", "status": "open"},
}


class FakeSession:
    """Stands in for GeminiMakerSession. Returns EXACT objects, raises real
    exception types, and records nothing of its own."""

    def __init__(self, replies=None, raises=None):
        self.replies = list(replies or [])
        self.raises = raises
        self.calls = []
        self.closed = False
        self.id_prefix = "fake"

    async def request(self, method, params, **kw):
        self.calls.append((method, params, kw))
        if self.raises is not None:
            raise self.raises
        return self.replies.pop(0) if self.replies else {"status": 200}

    async def close(self):
        self.closed = True

    def frame(self, method, params, request_id=None):
        return {"id": request_id or "fid", "method": method, "params": params}


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        coro)


# ------------------------------------------------ THE OBSERVER CHANGES NOTHING

def test_request_returns_the_venue_object_itself_not_a_copy():
    """Identity, not equality. A copy would be a different object reaching the
    driver than the one the venue sent, which is a behaviour change."""
    inner = FakeSession(replies=[INFERRED_PLACE_ACK])
    s = R.RecordingSession(inner)
    got = run(s.request(spec.WS_PLACE_METHOD, {"a": 1}))
    assert got is INFERRED_PLACE_ACK


def test_params_reach_the_inner_session_unchanged():
    inner = FakeSession(replies=[INFERRED_PLACE_ACK])
    s = R.RecordingSession(inner)
    params = {"symbol": "GEMI-X", "price": "0.03"}
    run(s.request(spec.WS_PLACE_METHOD, params, timeout=5.0))
    method, got, kw = inner.calls[0]
    assert method == spec.WS_PLACE_METHOD
    assert got is params
    assert kw == {"timeout": 5.0}


def test_exceptions_propagate_with_their_type_and_message_intact():
    """The driver's UNKNOWN/RECONCILE path keys off seeing the exception. An
    observer that swallowed one would turn an unresolved submission into a
    silent success -- the single worst failure available here."""
    boom = RuntimeError("socket died mid-frame")
    s = R.RecordingSession(FakeSession(raises=boom))
    with pytest.raises(RuntimeError) as ei:
        run(s.request(spec.WS_PLACE_METHOD, {}))
    assert ei.value is boom


def test_a_failed_request_is_still_recorded_with_its_error():
    s = R.RecordingSession(FakeSession(raises=RuntimeError("timeout")))
    with pytest.raises(RuntimeError):
        run(s.request(spec.WS_PLACE_METHOD, {}))
    assert len(s.wire) == 1
    assert s.wire[0]["ack"] is None
    assert "timeout" in s.wire[0]["error"]


def test_cancelled_error_is_not_converted_into_a_normal_exception():
    s = R.RecordingSession(FakeSession(raises=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        run(s.request(spec.WS_CANCEL_METHOD, {}))


def test_the_observer_does_not_bypass_the_arm_gate():
    """The arm gate lives inside the REAL session's request. Delegation means
    the observer cannot route around it."""
    s = R.RecordingSession(FakeSession(raises=arming.NotArmed("not armed")))
    with pytest.raises(arming.NotArmed):
        run(s.request(spec.WS_PLACE_METHOD, {}))


def test_every_other_attribute_delegates_to_the_real_session():
    inner = FakeSession()
    s = R.RecordingSession(inner)
    assert s.id_prefix == "fake"
    run(s.close())
    assert inner.closed is True


def test_missing_attribute_raises_attributeerror_and_does_not_recurse():
    s = R.RecordingSession(FakeSession())
    with pytest.raises(AttributeError):
        _ = s.no_such_attribute


def test_a_recording_failure_is_a_note_never_a_raise(monkeypatch):
    """If sanitizing ever throws, the ORDER still goes through. Evidence is
    worth less than execution correctness and the code says so."""
    def explode(*a, **k):
        raise ValueError("sanitizer bug")
    monkeypatch.setattr(R, "sanitize", explode)
    s = R.RecordingSession(FakeSession(replies=[INFERRED_PLACE_ACK]))
    got = run(s.request(spec.WS_PLACE_METHOD, {}))
    assert got is INFERRED_PLACE_ACK
    assert "sanitizer bug" in s.wire[0]["record_error"]


# ------------------------------------------------------------- SANITIZATION

def test_credential_shaped_values_are_redacted_but_their_keys_survive():
    out = R.sanitize({"apiKey": "abc", "signature": "deadbeef",
                      "nonce": 12345, "orderId": 145828836242288478})
    assert out["apiKey"] == "<redacted:str>"
    assert out["signature"] == "<redacted:str>"
    assert out["nonce"] == "<redacted:int>"
    assert out["orderId"] == 145828836242288478


def test_a_large_order_id_survives_sanitizing_exactly():
    """145828836242288478 exceeds 2**53. A lossy path would round it and the
    cancel it feeds would then name an order that does not exist."""
    oid = 145828836242288478
    assert oid > 2 ** 53
    assert R.sanitize({"orderId": oid})["orderId"] == oid


def test_sanitize_preserves_the_real_open_order_row_verbatim():
    out = R.sanitize(REAL_OPEN[0])
    for k, v in REAL_OPEN[0].items():
        if k in ("globalOrderId",):
            continue
        assert out[k] == v, k


def test_sanitize_does_not_crash_on_unserializable_values():
    assert isinstance(R.sanitize({"x": object()})["x"], str)


def test_long_strings_are_truncated_rather_than_filling_the_artifact():
    out = R.sanitize({"msg": "z" * 5000})
    assert out["msg"].endswith("<truncated>")
    assert len(out["msg"]) < 700


# --------------------------------------------------- WHERE THE ORDER ID LIVES

def test_order_id_path_is_reported_for_the_nested_shape():
    paths = R.order_id_paths(INFERRED_PLACE_ACK)
    assert [p["path"] for p in paths] == ["result.orderId"]
    assert paths[0]["value"] == 145828836242288478
    assert paths[0]["type"] == "int"


def test_order_id_path_would_have_exposed_the_continuous_2_defect():
    """The driver read ack['orderId']; the venue nests it under result. This
    records WHERE it actually was, so the next reader does not have to guess."""
    paths = R.order_id_paths(INFERRED_PLACE_ACK)
    assert "orderId" not in [p["path"] for p in paths]
    assert "result.orderId" in [p["path"] for p in paths]


def test_order_id_path_is_empty_on_the_real_cancel_reject():
    assert R.order_id_paths(REAL_CANCEL_REJECT) == []


def test_shape_of_distinguishes_an_int_order_id_from_a_string_one():
    assert R.shape_of({"result": {"orderId": 1}})["result"]["orderId"] == "int"
    assert R.shape_of({"result": {"orderId": "1"}})["result"]["orderId"] == "str"


# ------------------------------------------------------- THE OBSERVED SUMMARY

def test_observed_place_acks_marks_a_200_accepted():
    s = R.RecordingSession(FakeSession(replies=[INFERRED_PLACE_ACK]))
    run(s.request(spec.WS_PLACE_METHOD, {"clientOrderId": "c1"}))
    [row] = R.observed_place_acks(s.wire)
    assert row["accepted"] is True
    assert row["status"] == spec.WS_ACK_OK == 200
    assert row["client_order_id"] == "c1"
    assert row["order_id_paths"][0]["path"] == "result.orderId"


def test_observed_place_acks_marks_the_real_rejection_not_accepted():
    s = R.RecordingSession(FakeSession(replies=[REAL_CANCEL_REJECT]))
    run(s.request(spec.WS_PLACE_METHOD, {}))
    [row] = R.observed_place_acks(s.wire)
    assert row["accepted"] is False
    assert row["status"] == 400
    assert row["ack"]["error"]["code"] == -1013


def test_observed_place_acks_ignores_cancels_and_subscriptions():
    s = R.RecordingSession(FakeSession(replies=[{"status": 200},
                                                {"status": 200}]))
    run(s.request(spec.WS_CANCEL_METHOD, {"orderId": 1}))
    run(s.request(spec.WS_SUBSCRIBE_METHOD, ["orders"]))
    assert R.observed_place_acks(s.wire) == []


def test_no_place_means_an_empty_list_not_a_fabricated_row():
    assert R.observed_place_acks([]) == []


# ------------------------------------ THE POSITIVE PATH, THROUGH THE REAL DRIVER

def test_the_driver_places_through_the_observer_and_the_ack_is_captured(
        tmp_path, monkeypatch):
    """END TO END on the real Driver.place: the order is accepted, the venue
    orderId is parsed from result, AND the raw ack is on the wire log.

    This is the pass test that pairs with every refusal test. Without it a
    capture that recorded nothing would still look green."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = WC.Driver("OWNER-TEST", "GEMI-BTC05M2609181435-UP",
                  ledger_path=str(tmp_path / "led.jsonl"))
    d.inv = WC.CP.Inventory()
    d.state = WC.AUTHORIZED
    s = R.RecordingSession(FakeSession(replies=[INFERRED_PLACE_ACK]))

    out = run(d.place(s, {"leg": "YES", "price": "0.03", "qty": "0.01"}))

    assert out["placed"] is True
    assert out["venue_order_id"] == 145828836242288478
    [row] = R.observed_place_acks(s.wire)
    assert row["accepted"] is True
    assert row["ack"]["result"]["orderId"] == 145828836242288478
    assert row["evidence"].startswith("OBSERVED")


def test_capture_is_present_but_the_driver_hash_is_untouched():
    """The whole reason the capture lives in the invoker. If this ever fails,
    the authorization stopped naming the code that runs."""
    assert WC.executable_sha() == (
        "842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4")
    # None means no authorization is installed, which is honest. What must
    # never happen is AUTHORIZED_SHA naming code OTHER than what runs.
    assert WC.AUTHORIZED_SHA in (None, WC.executable_sha())


def test_the_invoker_is_the_one_holding_the_capture_not_the_driver():
    src = open("src/gemini_execution/ws_continuous.py", encoding="utf-8").read()
    assert "RecordingSession" not in src
    assert "observed_place_acks" not in src
