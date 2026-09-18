"""The wire shape of a prediction-markets request, which is what rejected
canary #1.

WHAT HAPPENED. The order was built correctly -- the contract's own
quantityMinimum 0.01 and priceMinimum 0.01 both admitted the size and the
price -- and then sent through Gemini's CLASSIC private-REST signer, which
transmits Content-Length: 0 and encodes the JSON into X-GEMINI-PAYLOAD. Every
prediction-markets endpoint that takes fields documents a JSON request body
instead. The venue received a request with no fields and answered HTTP 400.

WHY NOTHING CAUGHT IT EARLIER. The authenticated reads we had already proven
-- positions, settled positions -- take NO fields, so an empty body was the
correct body for all of them. The two conventions are indistinguishable until
a request actually carries something, and the first one that did was the order.

These tests pin the shape at the transport boundary so the next regression is
a test failure rather than a spent authorization.
"""
import asyncio
import json

import pytest

from src.gemini_execution import spec, transport as T


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Rec:
    """Captures exactly what would go on the wire; sends nothing."""

    def __init__(self, status=200, body=None):
        self.status, self.body, self.seen = status, (body or {}), []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, content=None):
        self.seen.append({"method": method, "url": url,
                          "headers": dict(headers or {}), "content": content})
        rec = self

        class R:
            status_code = rec.status
            text = json.dumps(rec.body)

            def json(self):
                return rec.body
        return R()


@pytest.fixture
def creds(monkeypatch):
    # obviously-fake values; no real credential is read, written or printed
    monkeypatch.setenv("GEMINI_API_KEY", "account-test-key")
    monkeypatch.setenv("GEMINI_API_SECRET", "test-secret")


@pytest.fixture
def wire(monkeypatch, creds):
    import src.gemini_prediction_auth as A
    rec = _Rec()
    monkeypatch.setattr(A.httpx, "AsyncClient", rec)
    return rec


# ---------------------------------------------------------------- the defect

def test_the_classic_signer_sends_an_empty_body(wire):
    """The regression itself: the fields do not reach the venue at all."""
    import src.gemini_prediction_auth as A
    _run(A.private_rest_request("POST", "/v1/anything",
                                params={"symbol": "GEMI-X", "quantity": "7"}))
    sent = wire.seen[-1]
    assert sent["content"] == b""
    assert "7" not in sent["content"].decode()


def test_the_json_signer_sends_the_fields_in_the_body(wire):
    import src.gemini_prediction_auth as A
    _run(A.private_rest_json("POST", "/v1/anything",
                             params={"symbol": "GEMI-X", "quantity": "7"}))
    sent = wire.seen[-1]
    body = json.loads(sent["content"].decode())
    assert body == {"symbol": "GEMI-X", "quantity": "7"}
    assert sent["headers"]["Content-Type"] == "application/json"


def test_the_json_signer_still_signs_what_it_sends(wire):
    """Auth is unchanged: the same fields are signed as well as sent, so the
    request is valid whichever half the venue reads."""
    import base64
    import src.gemini_prediction_auth as A
    _run(A.private_rest_json("POST", "/v1/anything",
                             params={"symbol": "GEMI-X"}))
    h = wire.seen[-1]["headers"]
    signed = json.loads(base64.b64decode(h["X-GEMINI-PAYLOAD"]))
    assert signed["request"] == "/v1/anything"
    assert signed["symbol"] == "GEMI-X"
    assert "nonce" in signed
    assert h["X-GEMINI-SIGNATURE"] and h["X-GEMINI-APIKEY"]


def test_the_classic_signer_is_unchanged_for_its_existing_callers(wire):
    """The read-only settlement probe depends on this exact behaviour; the fix
    adds a function rather than editing one that already has callers."""
    import base64
    import src.gemini_prediction_auth as A
    _run(A.private_rest_request("POST", "/v1/mytrades",
                                params={"symbol": "GEMI-X"}))
    h = wire.seen[-1]["headers"]
    assert h["Content-Type"] == "text/plain"
    signed = json.loads(base64.b64decode(h["X-GEMINI-PAYLOAD"]))
    assert signed["symbol"] == "GEMI-X"


# ------------------------------------------------------------------ the fix

def test_the_order_request_carries_its_fields_in_the_body(wire, monkeypatch):
    from src.gemini_execution import arming
    monkeypatch.setenv(arming.LIVE_ENV, "YES")
    monkeypatch.setenv(arming.AUTH_ENV, arming.EXPECTED_AUTH_ID)
    monkeypatch.setenv(arming.EXPIRY_ENV, "2099-01-01T00:00:00Z")
    _run(T.place(symbol="GEMI-BTC05M2609180045-UP", side="buy",
                 outcome="yes", price="0.02", quantity="0.01"))
    sent = wire.seen[-1]
    assert sent["url"].endswith(spec.PLACE_PATH)
    body = json.loads(sent["content"].decode())
    assert body["symbol"] == "GEMI-BTC05M2609180045-UP"
    assert body["quantity"] == "0.01" and body["price"] == "0.02"
    assert body["outcome"] == "yes" and body["side"] == "buy"
    assert body["orderType"] == spec.ORDER_TYPE_LIMIT
    assert body["makerOrCancel"] is True


def test_maker_only_survives_the_body_encoding(wire, monkeypatch):
    """A JSON body serialises the boolean as `true`, not the string "True".
    A venue reading "True" as truthy-by-accident is not a guarantee."""
    from src.gemini_execution import arming
    monkeypatch.setenv(arming.LIVE_ENV, "YES")
    monkeypatch.setenv(arming.AUTH_ENV, arming.EXPECTED_AUTH_ID)
    monkeypatch.setenv(arming.EXPIRY_ENV, "2099-01-01T00:00:00Z")
    _run(T.place(symbol="GEMI-X-UP", side="buy", outcome="yes",
                 price="0.02", quantity="0.01"))
    raw = wire.seen[-1]["content"].decode()
    assert '"makerOrCancel":true' in raw.replace(" ", "")


def test_a_disarmed_place_never_reaches_the_wire(wire, monkeypatch):
    from src.gemini_execution import arming
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    with pytest.raises(arming.NotArmed):
        _run(T.place(symbol="GEMI-X-UP", side="buy", outcome="yes",
                     price="0.02", quantity="0.01"))
    assert wire.seen == []


# ------------------------------------------------- the read-only discriminator
#
# The first version of this probe asked active-orders both ways, got 200 both
# ways, and reported BOTH_ACCEPTED. That reads like a finding and is not one:
# active-orders' `symbol` is an OPTIONAL filter, so an empty body is a valid
# request meaning "all orders", and on an empty account both answers are the
# same empty list. These tests pin the replacement, which asks a field that
# actually selects something -- and which reports INCONCLUSIVE rather than
# inventing an answer when it cannot tell.

def _probe(monkeypatch, table):
    """table: (ticker, json_body) -> dict merged over a 200 default."""
    seen = []

    async def fake(path, params=None, timeout=15.0, *, json_body=True):
        tk = (params or {}).get("eventTicker")
        seen.append((path, tk, json_body))
        r = dict(table.get((tk, json_body), {}))
        r.setdefault("status", 200)
        r.setdefault("ok", r["status"] == 200)
        r.setdefault("classification", "ACCEPTED" if r["ok"] else "REJECTED")
        return r

    monkeypatch.setattr(T, "_post", fake)
    return seen


def test_a_header_form_that_returns_the_same_payload_means_it_was_read(
        monkeypatch):
    """The venue read the field out of the signed header, so an empty body
    does NOT by itself explain the 400."""
    _probe(monkeypatch, {("A", True): {"body": {"v": 1}},
                         ("B", True): {"body": {"v": 2}},
                         ("A", False): {"body": {"v": 1}}})
    out = _run(T.probe_field_transport("A", "B"))
    assert out["verdict"] == "HEADER_PAYLOAD_ALSO_READ"
    assert "does NOT by itself explain" in out["why"]


def test_a_header_form_that_ignores_the_field_means_the_body_is_required(
        monkeypatch):
    _probe(monkeypatch, {("A", True): {"body": {"v": 1}},
                         ("B", True): {"body": {"v": 2}},
                         ("A", False): {"body": {"v": 2}}})
    assert _run(T.probe_field_transport("A", "B"))["verdict"] == \
        "JSON_BODY_REQUIRED"


def test_a_rejected_header_form_means_the_body_is_required(monkeypatch):
    _probe(monkeypatch, {("A", True): {"body": {"v": 1}},
                         ("B", True): {"body": {"v": 2}},
                         ("A", False): {"status": 400}})
    assert _run(T.probe_field_transport("A", "B"))["verdict"] == \
        "JSON_BODY_REQUIRED"


def test_an_endpoint_that_ignores_the_field_is_inconclusive_not_a_verdict(
        monkeypatch):
    """The exact trap the first probe fell into: identical answers are the
    absence of a discriminator, never evidence that both shapes work."""
    _probe(monkeypatch, {("A", True): {"body": {"v": 9}},
                         ("B", True): {"body": {"v": 9}},
                         ("A", False): {"body": {"v": 9}}})
    out = _run(T.probe_field_transport("A", "B"))
    assert out["verdict"] == "INCONCLUSIVE"
    assert "field_is_honoured" not in out


def test_an_unreadable_venue_is_never_a_verdict(monkeypatch):
    _probe(monkeypatch, {("A", True): {"status": None, "ok": False},
                         ("B", True): {"status": None, "ok": False},
                         ("A", False): {"status": None, "ok": False}})
    out = _run(T.probe_field_transport("A", "B"))
    assert out["verdict"] in ("UNREADABLE", "NEITHER_ACCEPTED")
    assert out["verdict"] not in ("HEADER_PAYLOAD_ALSO_READ",
                                 "JSON_BODY_REQUIRED")


def test_the_probe_only_touches_a_read_path(monkeypatch):
    seen = _probe(monkeypatch, {("A", True): {"body": {"v": 1}},
                                ("B", True): {"body": {"v": 2}},
                                ("A", False): {"body": {"v": 1}}})
    _run(T.probe_field_transport("A", "B"))
    paths = {p for p, _, _ in seen}
    assert paths == {T.VOLUME_PATH}
    assert not (paths & set(spec.MUTATION_PATHS))


def test_the_probe_compares_two_different_tickers(monkeypatch):
    seen = _probe(monkeypatch, {("A", True): {"body": {"v": 1}},
                                ("B", True): {"body": {"v": 2}},
                                ("A", False): {"body": {"v": 1}}})
    _run(T.probe_field_transport("A", "B"))
    assert sorted({tk for _, tk, _ in seen}) == ["A", "B"]
    assert ("%s" % T.VOLUME_PATH, "A", False) in seen


# ----------------------------------------------- the reconciliation read

def test_active_orders_falls_back_when_the_documented_shape_is_rejected(
        monkeypatch):
    """An unreadable reconciliation read is what makes an ambiguous
    submission unresolvable, so it asks both ways before giving up."""
    calls = []

    async def fake(path, params=None, timeout=15.0, *, json_body=True):
        calls.append(json_body)
        return ({"status": 400, "ok": False} if json_body
                else {"status": 200, "ok": True, "body": []})

    monkeypatch.setattr(T, "_post", fake)
    out = _run(T.active_orders("GEMI-X-UP"))
    assert out["ok"] is True
    assert out["request_shape"] == "header_payload"
    assert out["json_body_attempt"]["status"] == 400
    assert calls == [True, False]


def test_active_orders_does_not_ask_twice_when_the_first_shape_works(
        monkeypatch):
    calls = []

    async def fake(path, params=None, timeout=15.0, *, json_body=True):
        calls.append(json_body)
        return {"status": 200, "ok": True, "body": []}

    monkeypatch.setattr(T, "_post", fake)
    assert _run(T.active_orders("GEMI-X-UP"))["request_shape"] == "json_body"
    assert calls == [True]


def test_an_unreadable_order_book_is_not_an_empty_one(monkeypatch):
    """_orders_of returns [] for a FAILED read as much as for an empty
    account. Without the ok check the exposure gate reports clear on a read
    that never happened."""
    from src.gemini_execution import run_canary as RC

    async def fake_active(symbol=None):
        return {"ok": False, "status": 401, "classification": "REJECTED"}

    monkeypatch.setattr(T, "active_orders", fake_active)
    out = _run(RC.no_existing_canary_exposure("GEMI-X-UP"))
    assert out["clear"] is False
    assert "unreadable" in out["why"]
