"""The recovery runner must be incapable of moving anything.

It exists because Probe 04 left a real order unresolved at the venue, and the
only thing that can answer what happened to it is the venue. That makes it a
process holding live credentials against a live account, written in a hurry,
at exactly the moment nobody wants a surprise. So its incapability is
asserted structurally rather than trusted.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from src import pmus_auth as Q1
import scripts.pmus_readonly_recovery as R

RUNNER = pathlib.Path("scripts/pmus_readonly_recovery.py")
DOCKERFILE = pathlib.Path("deploy/pmus_readonly_recovery/Dockerfile")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_the_only_transport_in_the_image_refuses_every_mutation(method):
    with pytest.raises(PermissionError):
        Q1.ReadOnlyTransport().request(method, "/v1/orders", {}, Q1.API)


def test_the_image_does_not_contain_a_post_capable_module():
    """l5_pmus_transport owns _SignedHTTP, which can POST. It must be absent."""
    copied = [l.split()[1] for l in DOCKERFILE.read_text().splitlines()
              if l.strip().startswith("COPY ")]
    assert "src/pmus_auth.py" in copied
    for forbidden in ("l5_pmus_transport", "l5_live_execution",
                      "l5_order_body", "l5_preview_gate",
                      "l5_live_authorization", "pmus_q1_executor"):
        assert not any(forbidden in c for c in copied), forbidden


def test_the_runner_names_no_mutating_verb_anywhere():
    tree = ast.parse(RUNNER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.upper()
            if v in ("POST", "PUT", "PATCH", "DELETE"):
                # the one permitted mention is the proof that POST is refused
                continue
        if isinstance(node, ast.Attribute) and node.attr in (
                "submit", "cancel", "arm", "claim", "record_submit",
                "apply_fill", "save"):
            raise AssertionError("runner reaches for %s" % node.attr)


def test_every_request_the_runner_makes_is_a_get():
    tree = ast.parse(RUNNER.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "request":
            first = node.args[0] if node.args else None
            assert isinstance(first, ast.Constant), "non-literal method"
            assert first.value in ("GET", "POST"), first.value
            # the single POST is the capability check, which must be asserted
            # to raise rather than to succeed
            if first.value == "POST":
                assert "PermissionError" in RUNNER.read_text()


# --------------------------------------------------------------- redaction

@pytest.mark.parametrize("header", [
    "X-PM-Access-Key: pk_live_abcdef123456",
    "X-PM-Signature: 9f8e7d6c5b4a3f2e1d0c",
    "X-PM-Timestamp: 1790302550",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
])
def test_header_shaped_text_is_scrubbed(header):
    out = R.redact("failed: %s" % header)
    assert "REDACTED" in out
    assert header.split(": ", 1)[1] not in out


def test_the_credentials_themselves_are_scrubbed(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "KEYID-9f8e7d6c5b4a")
    monkeypatch.setenv(Q1.SECRET_ENV, "SECRET-0123456789abcdef")
    out = R.redact("key KEYID-9f8e7d6c5b4a secret SECRET-0123456789abcdef")
    assert "KEYID-9f8e7d6c5b4a" not in out
    assert "SECRET-0123456789abcdef" not in out


def test_the_local_scrubber_matches_the_shared_one(monkeypatch):
    """The copy is deliberate; it must not drift from what it copies."""
    from src.l5_pmus_transport import redact as shared
    monkeypatch.setenv(Q1.KEY_ENV, "KEYID-9f8e7d6c5b4a")
    monkeypatch.setenv(Q1.SECRET_ENV, "SECRET-0123456789abcdef")
    for s in ("X-PM-Signature: abc123def456",
              "Authorization: Bearer tok",
              "key KEYID-9f8e7d6c5b4a",
              "plain text with no secrets"):
        assert R.redact(s)[:600] == shared(s), s


# ------------------------------------------------------- shape reporting

def test_shape_of_describes_a_list_of_rows():
    got = R.shape_of([{"market": "m", "side": "UP", "qty": "1"}])
    assert got["type"] == "array" and got["len"] == 1
    assert got["first"]["type"] == "object"
    assert "market" in got["first"]["keys"]


def test_shape_of_describes_a_mapping_which_is_the_suspected_real_shape():
    """If positions comes back like this, p["market"] is a TypeError."""
    got = R.shape_of({"cpc-btc-1300z": {"qty": "1", "side": "UP"}})
    assert got["type"] == "object"
    assert "cpc-btc-1300z" in got["keys"]


def test_shape_of_terminates_on_deep_structures():
    deep = {}
    node = deep
    for _ in range(12):
        node["next"] = {}
        node = node["next"]
    assert "..." in json.dumps(R.shape_of(deep))


def test_find_order_returns_the_innermost_object_naming_the_order():
    payloads = [{"ok": True, "body": {"orders": [
        {"id": "OTHER", "status": "FILLED"},
        {"id": R.ORDER_ID, "status": "CANCELLED", "filledQuantity": "0"}]}}]
    found = R.find_order(payloads)
    assert len(found) == 1
    assert found[0]["status"] == "CANCELLED"


def test_find_order_ignores_reads_that_failed():
    assert R.find_order([{"ok": False, "detail": R.ORDER_ID}]) == []


def test_a_failed_read_never_stops_the_others(monkeypatch):
    calls = []

    class Boom:
        def request(self, method, path, body=None, base=None):
            calls.append(path)
            raise RuntimeError("venue down")

    monkeypatch.setattr(Q1, "ReadOnlyTransport", Boom)
    out = R.get("/v1/orders/open")
    assert out["ok"] is False and out["error"] == "RuntimeError"
    assert calls == ["/v1/orders/open"]
