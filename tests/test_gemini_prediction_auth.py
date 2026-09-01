import base64
import hashlib
import hmac
import json

import pytest

import src.gemini_prediction_auth as auth
from src.gemini_prediction_auth import (
    is_account_scoped_key,
    rest_auth_headers,
    websocket_auth_headers,
)


def test_account_scoped_key_validation():
    assert is_account_scoped_key("account-abc") is True
    assert is_account_scoped_key("master-abc") is False
    assert is_account_scoped_key("") is False


def test_time_based_nonce_uses_epoch_milliseconds_and_stays_monotonic(monkeypatch):
    fixed_ns = 1_786_828_000_900_000_000
    monkeypatch.setattr(auth.time, "time_ns", lambda: fixed_ns)
    monkeypatch.setattr(auth, "_last_nonce", 0)
    first = auth.next_nonce()
    second = auth.next_nonce()
    assert first == 1_786_828_000_900
    assert second == first + 1


def test_time_based_nonce_handles_rapid_private_call_burst(monkeypatch):
    fixed_ns = 1_786_828_000_900_000_000
    monkeypatch.setattr(auth.time, "time_ns", lambda: fixed_ns)
    monkeypatch.setattr(auth, "_last_nonce", 0)
    nonces = [auth.next_nonce() for _ in range(1000)]
    assert nonces == sorted(set(nonces))
    assert nonces[-1] - nonces[0] == 999
    assert nonces[-1] <= nonces[0] + auth.MAX_NONCE_FUTURE_MS


def test_time_based_nonce_fails_closed_before_clock_window_drift(monkeypatch):
    fixed_ns = 1_786_828_000_900_000_000
    now_ms = fixed_ns // 1_000_000
    monkeypatch.setattr(auth.time, "time_ns", lambda: fixed_ns)
    monkeypatch.setattr(auth, "_last_nonce", now_ms + auth.MAX_NONCE_FUTURE_MS)
    with pytest.raises(RuntimeError, match="nonce rate exceeded"):
        auth.next_nonce()


def test_websocket_auth_headers_are_deterministic():
    headers = websocket_auth_headers("account-key", "secret", nonce=123456)
    payload = base64.b64encode(b"123456").decode("ascii")
    expected = hmac.new(b"secret", payload.encode("ascii"), hashlib.sha384).hexdigest()
    assert headers == {
        "X-GEMINI-APIKEY": "account-key",
        "X-GEMINI-NONCE": "123456",
        "X-GEMINI-PAYLOAD": payload,
        "X-GEMINI-SIGNATURE": expected,
    }


def test_rest_auth_payload_contains_request_nonce_and_params():
    headers = rest_auth_headers(
        "account-key",
        "secret",
        "/v1/prediction-markets/terms/status",
        nonce=987654,
        params={"foo": "bar"},
    )
    decoded = json.loads(base64.b64decode(headers["X-GEMINI-PAYLOAD"]).decode("utf-8"))
    assert decoded == {
        "request": "/v1/prediction-markets/terms/status",
        "nonce": 987654,
        "foo": "bar",
    }
    expected = hmac.new(
        b"secret",
        headers["X-GEMINI-PAYLOAD"].encode("ascii"),
        hashlib.sha384,
    ).hexdigest()
    assert headers["X-GEMINI-SIGNATURE"] == expected
    assert "secret" not in str(headers)
