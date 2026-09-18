"""Structural and behavioural tests for the WS transport adapter.

These pin the things the adapter exists to get right, and in particular the
three defects an earlier draft had: reconciliation that could not see a resting
maker order, a rejection whose reason was thrown away, and a private latch that
replaced the reviewed one.
"""
import asyncio
import json

import pytest

from src.gemini_execution import ws_transport as W
from src.gemini_execution import arming, latch, spec


# --------------------------------------------------------------- fake venue
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("unparseable")
        return self._payload


class FakeSession:
    """Stands in for GeminiMakerSession. Records every frame it is given."""

    def __init__(self, ack=None, *, connect_error=None, send_error=None):
        self.ack = ack if ack is not None else {"status": 200}
        self.connect_error = connect_error
        self.send_error = send_error
        self.sent = []
        self.connected = False
        self.closed = False

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def request(self, method, params, *, timeout=4.0):
        self.sent.append((method, params))
        if self.send_error is not None:
            raise self.send_error
        return dict(self.ack)

    async def close(self):
        self.closed = True


def rest_returning(rows_by_path):
    """A fake private_rest_request that serves canned rows per path."""
    seen = []

    async def _rest(method, path, *, params=None, timeout=4.0):
        seen.append((path, dict(params or {})))
        return FakeResponse({"orders": rows_by_path.get(path, [])})

    _rest.seen = seen
    return _rest


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(arming.LIVE_ENV, "YES")
    monkeypatch.setenv(arming.AUTH_ENV, arming.EXPECTED_AUTH_ID)
    monkeypatch.setenv(arming.EXPIRY_ENV, "2099-01-01T00:00:00Z")


ROW = {"orderId": "145828835409069189", "clientOrderId": "cid-1",
       "status": "open", "filledQuantity": "0", "quantity": "0.01"}


# ------------------------------------------------------------- the WS frame
def test_sanitized_frame_is_the_proven_ws_dialect():
    assert W.sanitized_ws_frame(symbol="GEMI-BTC05M2609180200-UP",
                                price="0.48", quantity="0.01") == {
        "id": "<request-id>", "method": "order.place",
        "params": {"symbol": "GEMI-BTC05M2609180200-UP", "side": "BUY",
                   "type": "LIMIT", "timeInForce": "MOC", "price": "0.48",
                   "quantity": "0.01", "eventOutcome": "YES",
                   "clientOrderId": "<client-order-id>"}}


def test_maker_only_is_moc_and_never_the_rest_spelling():
    """makerOrCancel is the REST boolean; it means nothing on this socket."""
    p = W.ws_order_params(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                          outcome="yes", price="0.48", quantity="0.01",
                          client_order_id="cid")
    assert p["timeInForce"] == "MOC"
    assert "makerOrCancel" not in p
    assert p["side"] == "BUY" and p["eventOutcome"] == "YES"


def test_canonical_lowercase_intent_is_accepted_and_translated():
    """The envelope emits the venue's canonical spelling; the adapter converts."""
    p = W.ws_order_params(symbol="GEMI-BTC05M2609180200-UP",
                          side=spec.SIDE_BUY, outcome=spec.OUTCOME_YES,
                          price="0.48", quantity="0.01", client_order_id="c")
    assert p["side"] == "BUY" and p["type"] == "LIMIT"


@pytest.mark.parametrize("kw", [
    {"side": "sell"}, {"outcome": "no"}, {"symbol": "GEMI-MLB-2608192240-X"}])
def test_envelope_shape_is_refused_outside_btc5m_buy_yes(kw):
    base = dict(symbol="GEMI-BTC05M2609180200-UP", side="buy", outcome="yes",
                price="0.48", quantity="0.01", client_order_id="c")
    base.update(kw)
    with pytest.raises(ValueError):
        W.ws_order_params(**base)


# ------------------------------------------------------------------ arming
@pytest.mark.asyncio
async def test_place_is_arm_gated_and_sends_nothing_while_disarmed(monkeypatch):
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    session = FakeSession()
    tr = W.WsOrderTransport(session_factory=lambda: session)
    with pytest.raises(arming.NotArmed):
        await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                       outcome="yes", price="0.48", quantity="0.01")
    assert session.sent == [] and session.connected is False


# --------------------------------------------- the resting-maker reconcile
@pytest.mark.asyncio
async def test_resting_maker_order_is_found_via_active_orders(armed):
    """THE defect this test exists for.

    A MOC maker order that rests is the EXPECTED success of this canary, and
    it is neither filled nor cancelled. A reconciliation that asks only those
    two histories reports the success case as absent.
    """
    rest = rest_returning({W.ACTIVE_PATH: [dict(ROW, clientOrderId="cid-x")]})
    tr = W.WsOrderTransport(session_factory=lambda: FakeSession({"status": 200}),
                            rest=rest)
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01",
                         client_order_id="cid-x")
    assert res["classification"] == "ACCEPTED"
    assert res["body"][spec.ORDER_ID_FIELD] == ROW["orderId"]
    assert rest.seen[0][0] == W.ACTIVE_PATH, "active orders must be asked FIRST"


def test_reconcile_query_order_puts_active_first():
    q = W.reconcile_queries("GEMI-BTC05M2609180200-UP")
    assert q[0][0] == W.ACTIVE_PATH
    assert [p for p, _ in q[1:]] == [W.HISTORY_PATH] * len(W.HISTORY_STATUSES)
    assert [pa["status"] for _, pa in q[1:]] == list(W.HISTORY_STATUSES)


@pytest.mark.asyncio
async def test_accepted_but_unreadable_is_accepted_not_ambiguous(armed):
    """An ack of 200 means the order exists. A failed READ does not undo that."""
    rest = rest_returning({})
    tr = W.WsOrderTransport(session_factory=lambda: FakeSession({"status": 200}),
                            rest=rest, reconcile_attempts=2,
                            reconcile_pause_s=0)
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == "ACCEPTED"
    assert res["reconciliation_unreadable"] is True


# ------------------------------------------------------- rejection capture
@pytest.mark.asyncio
async def test_rejection_captures_the_venue_reason(armed):
    """Canary #1 recorded only its status code and could not say why."""
    ack = {"status": 400, "result": "error", "reason": "InvalidQuantity",
           "message": "quantity below minimum"}
    tr = W.WsOrderTransport(session_factory=lambda: FakeSession(ack),
                            rest=rest_returning({}))
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == "REJECTED"
    assert res["body"]["reason"] == "InvalidQuantity"
    assert res["body"]["message"] == "quantity below minimum"


@pytest.mark.asyncio
async def test_rejection_without_named_fields_still_records_something(armed):
    tr = W.WsOrderTransport(session_factory=lambda: FakeSession({"status": 422}),
                            rest=rest_returning({}))
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == "REJECTED"
    assert res["body"], "a rejection must never record an empty reason"


# --------------------------------------------------------------- ambiguity
@pytest.mark.asyncio
async def test_send_failure_is_ambiguous_and_marked_sent(armed):
    tr = W.WsOrderTransport(
        session_factory=lambda: FakeSession(send_error=asyncio.TimeoutError()),
        rest=rest_returning({}))
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == W.AMBIGUOUS and res["sent"] is True


@pytest.mark.asyncio
async def test_connect_failure_is_a_refusal_because_nothing_was_sent(armed):
    tr = W.WsOrderTransport(
        session_factory=lambda: FakeSession(connect_error=OSError("no route")),
        rest=rest_returning({}))
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == "REJECTED" and res["sent"] is False


@pytest.mark.asyncio
async def test_ack_without_status_is_not_read_as_a_rejection(armed):
    """int(ack.get('status', 0)) != 200 would call an accepted order refused."""
    tr = W.WsOrderTransport(session_factory=lambda: FakeSession({"result": "ok"}),
                            rest=rest_returning({}))
    res = await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                         outcome="yes", price="0.48", quantity="0.01")
    assert res["classification"] == W.AMBIGUOUS


@pytest.mark.asyncio
async def test_session_is_always_closed(armed):
    session = FakeSession(send_error=RuntimeError("boom"))
    tr = W.WsOrderTransport(session_factory=lambda: session,
                            rest=rest_returning({}))
    await tr.place(symbol="GEMI-BTC05M2609180200-UP", side="buy",
                   outcome="yes", price="0.48", quantity="0.01")
    assert session.closed is True


# ------------------------------------------- reuse of the reviewed machinery
def test_adapter_does_not_reimplement_the_latch():
    """The one-shot guarantee has exactly one implementation.

    A private latch here would silently drop the durable_check that makes the
    guarantee survive an ephemeral runner, and the strict state vocabulary.
    """
    import ast
    tree = ast.parse(open(W.__file__, encoding="utf-8").read())
    # AST, not a text search: the module docstring DESCRIBES the latch it must
    # not contain, and a substring check cannot tell prose from code.
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "O_EXCL" not in names, "the latch belongs to gemini_execution.latch"
    funcs = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_consume_latch" not in funcs


def test_strict_accounting_states_come_from_the_reviewed_vocabulary():
    for state in ("INTENT_PERSISTED", "SUBMITTED",
                  "SUBMISSION_UNKNOWN_RECONCILE_REQUIRED", "REJECTED",
                  "FILLED", "REALIZED"):
        assert state in latch.STATES


@pytest.mark.asyncio
async def test_submit_once_ws_refuses_while_disarmed(monkeypatch, tmp_path):
    """The default path submits nothing without both keys."""
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    monkeypatch.delenv(arming.AUTH_ENV, raising=False)
    called = {"n": 0}

    class Boom:
        async def place(self, **kw):
            called["n"] += 1
            raise AssertionError("no frame may be constructed while disarmed")

    res = await W.submit_once_ws("OWNER-TEST", transport=Boom(),
                                 state_dir=str(tmp_path))
    assert res["submitted"] is False and res["state"] == "NOT_ARMED"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_a_consumed_authorization_is_never_resubmitted(armed, tmp_path):
    latch.consume("OWNER-TEST", {"symbol": "s"}, str(tmp_path))

    class Boom:
        async def place(self, **kw):
            raise AssertionError("a spent authorization must not reach a venue")

    res = await W.submit_once_ws("OWNER-TEST", transport=Boom(),
                                 state_dir=str(tmp_path))
    assert res["submitted"] is False and res["state"] == "ALREADY_CONSUMED"


# ------------------------------------------------------ the evidence record
def test_transport_evidence_does_not_overclaim():
    ev = W.WS_TRANSPORT_EVIDENCE
    assert ev["venue_write_path"] == "PROVEN"
    assert ev["fractional_quantity_over_this_transport"] == "UNPROVEN"
    assert ev["maker_only_over_this_transport"] == "UNPROVEN"
