"""The cancel body must carry a non-empty marketSlug.

THE DEFECT THIS PINS. On 2026-10-18 order D68KZ0SG10JR rested for 530s
while the watchdog and then the executor both posted `{}` to the correct
route, with a correct signature, and the venue refused every attempt with
HTTP 400 Bad Request. The order left only when the market expired.
`pmus_q1_executor.cancel_exact` -- the only cancel in this repository ever
used against a live PMUS order -- has always sent `{"marketSlug": slug}`.

These tests assert the REQUEST, not a mock's return value: route, method,
body, identifier type, and that an absent slug refuses rather than falling
back to the empty body that caused the failure.
"""
import json

import pytest

import src.l5_watchdog as W


# ----------------------------------------------------- the watchdog transport

class Recorder:
    """Stands in for the signed opener; records exactly what was sent."""

    def __init__(self, raise_on_post=None):
        self.calls = []
        self.raise_on_post = raise_on_post

    def __call__(self, method, path, body, base):
        self.calls.append({"method": method, "path": path, "body": body,
                           "base": base})
        if method == "POST" and self.raise_on_post is not None:
            raise self.raise_on_post
        return {"ok": True}


def _t(rec):
    return W.CancelOnlyTransport(opener=rec)


def test_the_cancel_body_carries_the_market_slug():
    rec = Recorder()
    _t(rec).cancel("D68KZ0SG10JR", "cpc-btc-updown-15m-2026-10-18-2315z")
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["method"] == "POST"
    assert c["path"] == "/v1/order/D68KZ0SG10JR/cancel"
    assert c["body"] == {"marketSlug": "cpc-btc-updown-15m-2026-10-18-2315z"}


def test_the_body_is_never_empty():
    """The exact shape that produced the 400."""
    rec = Recorder()
    _t(rec).cancel("D68KZ0SG10JR", "cpc-btc-updown-15m-2026-10-18-2315z")
    assert rec.calls[0]["body"] != {}
    assert json.dumps(rec.calls[0]["body"]) == \
        '{"marketSlug": "cpc-btc-updown-15m-2026-10-18-2315z"}'


@pytest.mark.parametrize("slug", [None, "", "   "])
def test_an_absent_slug_REFUSES_rather_than_posting_an_empty_body(slug):
    rec = Recorder()
    with pytest.raises(W.MutationRefused):
        _t(rec).cancel("D68KZ0SG10JR", slug)
    assert rec.calls == []          # nothing reached the wire


@pytest.mark.parametrize("oid", [None, "", "  "])
def test_an_absent_venue_order_id_still_refuses(oid):
    rec = Recorder()
    with pytest.raises(W.MutationRefused):
        _t(rec).cancel(oid, "cpc-btc-updown-15m-2026-10-18-2315z")
    assert rec.calls == []


def test_the_identifier_is_the_venue_id_and_is_not_rewritten():
    """A client order id would cancel nothing, so the id must pass through
    verbatim into the path."""
    rec = Recorder()
    _t(rec).cancel("D68KZ0SG10JR", "slug-x")
    assert "D68KZ0SG10JR" in rec.calls[0]["path"]
    assert rec.calls[0]["path"].endswith("/cancel")


def test_the_route_regex_still_admits_only_the_cancel_route():
    rec = Recorder()
    t = _t(rec)
    with pytest.raises(W.MutationRefused):
        t.request("POST", "/v1/orders", {"marketSlug": "x"})
    with pytest.raises(W.MutationRefused):
        t.request("DELETE", "/v1/order/D1/cancel", {})
    assert rec.calls == []


def test_signing_is_unchanged_by_the_body():
    """PMUS signs ts+method+path and does NOT sign the body, so carrying a
    field cannot change how the request authenticates. The opener sees the
    body; the signature inputs are method and path only."""
    rec = Recorder()
    _t(rec).cancel("D1", "slug-x")
    c = rec.calls[0]
    assert c["method"] == "POST" and c["path"] == "/v1/order/D1/cancel"


# ----------------------------------------------------- sanitized error detail

class FakeHTTPError(Exception):
    code = 400

    def __init__(self, body=b'{"message":"marketSlug is required"}'):
        super().__init__("HTTP Error 400: Bad Request")
        self._body = body
        self.reason = "Bad Request"

    def read(self):
        return self._body


def test_the_sanitized_detail_captures_status_and_body():
    d = W.http_error_detail(FakeHTTPError())
    assert d["error"] == "FakeHTTPError"
    assert d["status"] == 400
    assert "marketSlug is required" in d["body"]


def test_the_detail_redacts_header_and_signature_shaped_text():
    d = W.http_error_detail(FakeHTTPError(
        b'{"echo":"authorization: Bearer abc123", '
        b'"h":"x-pm-signature: deadbeef"}'))
    assert "abc123" not in d["body"]
    assert "deadbeef" not in d["body"]
    assert "[REDACTED]" in d["body"]


def test_the_detail_is_truncated():
    d = W.http_error_detail(FakeHTTPError(b"x" * 5000))
    assert len(d["body"]) <= 400


def test_an_unreadable_body_does_not_raise():
    class Nasty(Exception):
        code = 400

        def read(self):
            raise OSError("stream closed")

    d = W.http_error_detail(Nasty())
    assert d["body"] == "[UNREADABLE]"
    assert d["status"] == 400


def test_an_error_with_no_body_at_all_is_still_described():
    d = W.http_error_detail(ValueError("nope"))
    assert d["error"] == "ValueError"
    assert "body" not in d


def test_the_detail_never_raises_on_a_bare_exception():
    for exc in (RuntimeError("x"), TimeoutError(), KeyError("k")):
        assert W.http_error_detail(exc)["error"] == type(exc).__name__


# ----------------------------------------------------- the executor transport

import src.l5_pmus_transport as T


class FakeQ1Transport(T.PmusTransport):
    """The real cancel path with the wire and the clock replaced."""

    def __init__(self, *, armed, oracle, slug="cpc-btc-updown-15m-X"):
        self.sent = []
        self.armed = armed
        self.market_slug = slug
        self.last_cancel_error = None
        if oracle is not None:
            self.cancel_owner_oracle = oracle
        self.limiter = type("L", (), {"before": lambda *a, **k: None,
                                      "on_success": lambda *a, **k: None,
                                      "on_429": lambda *a, **k: None})()

    def _request(self, method, path, body=None, base=None):
        self.sent.append({"method": method, "path": path, "body": body})
        return {"ok": True}


def _creds(monkeypatch):
    monkeypatch.setattr(T.Q1, "has_credentials", staticmethod(lambda: True))


def test_executor_cancel_sends_the_slug_on_the_oracle_path(monkeypatch):
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=False, oracle=lambda oid: True)
    t.cancel("D68KZ0SG10JR", market_slug="cpc-btc-updown-15m-2026-10-18-2315z")
    assert t.sent == [{"method": "POST",
                       "path": "/v1/order/D68KZ0SG10JR/cancel",
                       "body": {"marketSlug":
                                "cpc-btc-updown-15m-2026-10-18-2315z"}}]


def test_executor_cancel_sends_the_slug_on_the_armed_no_oracle_path(monkeypatch):
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=True, oracle=None)
    t.cancel("D1", market_slug="slug-a")
    assert t.sent[0]["body"] == {"marketSlug": "slug-a"}


def test_the_orders_own_slug_wins_over_the_transports_current_market(monkeypatch):
    """An orphan sweep runs after roll has moved on, so the transport's
    market can name a DIFFERENT window than the order being cancelled."""
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=True, oracle=None, slug="cpc-btc-updown-15m-NEW")
    t.cancel("D1", market_slug="cpc-btc-updown-15m-OLD")
    assert t.sent[0]["body"] == {"marketSlug": "cpc-btc-updown-15m-OLD"}


def test_the_transport_slug_is_only_the_fallback(monkeypatch):
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=True, oracle=None, slug="cpc-btc-updown-15m-FB")
    t.cancel("D1")
    assert t.sent[0]["body"] == {"marketSlug": "cpc-btc-updown-15m-FB"}


def test_no_slug_anywhere_REFUSES_and_sends_nothing(monkeypatch):
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=True, oracle=None, slug="")
    with pytest.raises(T.CancelRefused) as e:
        t.cancel("D1")
    assert "NO_MARKET_SLUG" in str(e.value)
    assert t.sent == []


def test_ownership_refusal_still_precedes_the_wire(monkeypatch):
    _creds(monkeypatch)
    t = FakeQ1Transport(armed=False, oracle=lambda oid: False)
    with pytest.raises(T.CancelRefused):
        t.cancel("D1", market_slug="slug-a")
    assert t.sent == []


def test_a_failed_cancel_records_the_sanitized_detail(monkeypatch):
    _creds(monkeypatch)

    class Boom(FakeQ1Transport):
        def _request(self, method, path, body=None, base=None):
            raise FakeHTTPError()

    t = Boom(armed=True, oracle=None, slug="slug-a")
    with pytest.raises(FakeHTTPError):
        t.cancel("D68KZ0SG10JR", market_slug="slug-a")
    d = t.last_cancel_error
    assert d["status"] == 400
    assert "marketSlug is required" in d["body"]
    assert d["venue_order_id"] == "D68KZ0SG10JR"
    assert d["market_slug"] == "slug-a"
    assert d["endpoint"] == "/v1/order/{id}/cancel" and d["method"] == "POST"


def test_the_transport_redactor_consumes_the_whole_value():
    """The pre-existing `\\S+` stopped at the first space, so
    "Authorization: Bearer abc123" left the credential in the log."""
    out = T.redact("authorization: Bearer abc123SECRET")
    assert "abc123SECRET" not in out and "[REDACTED]" in out


def test_redaction_preserves_surrounding_json_structure():
    out = T.redact('{"a":"token: xyz987","b":"keep"}')
    assert "xyz987" not in out and "keep" in out
