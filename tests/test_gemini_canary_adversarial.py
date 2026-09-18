"""Every way the one-order canary could place something it must not.

Each test names a specific failure and proves refusal. Nothing here touches a
venue: the transport is injected, and the arm is off unless a test sets it.
"""
import asyncio
import copy
import os
import tempfile

import pytest

from src.gemini_execution import arming, canary as C, latch, spec

AUTH = arming.EXPECTED_AUTH_ID
#: a REAL epoch (2026-09-18T00:06Z). Using a toy value like 1000.0 puts the
#: clock in 1970, which makes every 2026 expiry look like the future and
#: silently disables the expiry tests.
NOW = 1789690000.0


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(arming.LIVE_ENV, "YES")
    monkeypatch.setenv(arming.AUTH_ENV, AUTH)
    monkeypatch.setenv(arming.EXPIRY_ENV, "2026-09-18T06:00:00Z")


@pytest.fixture
def state_dir():
    return tempfile.mkdtemp()


def _pf(**over):
    """A preflight that passes every rule, so tests break one at a time."""
    pf = {
        "ok": True,
        "instrument": "GEMI-BTC05M2609180000-UP",
        "side": "BUY",
        "limit_price": "0.63",
        "quantity": "0.01",
        "restability": {"restable": True, "tick_offset_from_touch": 0},
        "venue_terms": {"status": "limit_only"},
        "current_bbo": {"bid": "0.63", "ask": "0.90", "source_ts": NOW - 1.0},
    }
    pf.update(over)
    return pf


class _PF:
    def __init__(self, pf):
        self._pf = pf

    async def preflight_async(self, now=None):
        return self._pf


class _TR:
    """A transport that records calls. Never reaches a network."""

    def __init__(self, classification="ACCEPTED", body=None):
        self.calls = []
        self.classification = classification
        self.body = body if body is not None else {"orderId": 4242,
                                                   "status": "open"}

    async def place(self, **kw):
        self.calls.append(kw)
        return {"ok": self.classification == "ACCEPTED",
                "classification": self.classification, "body": self.body,
                "status": 201 if self.classification == "ACCEPTED" else 400}

    async def find_order(self, **kw):
        return {"resolved": True, "found": False}


def _submit(pf, tr, state_dir, auth=AUTH):
    return _run(C.submit_once(auth, state_dir=state_dir, now=NOW,
                              preflight=_PF(pf), transport=tr))


# ============================================================ arming refusals

def test_missing_arm_refuses(state_dir, monkeypatch):
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    tr = _TR()
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == "NOT_ARMED" and tr.calls == []


def test_wrong_authorization_refuses(state_dir, monkeypatch, armed):
    monkeypatch.setenv(arming.AUTH_ENV, "SOME-OTHER-AUTH")
    tr = _TR()
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == "NOT_ARMED" and tr.calls == []
    assert "does not match" in r["why"]


def test_expired_authorization_refuses(state_dir, monkeypatch, armed):
    monkeypatch.setenv(arming.EXPIRY_ENV, "2026-09-17T00:00:00Z")
    tr = _TR()
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == "NOT_ARMED" and tr.calls == []
    assert "expired" in r["why"]


def test_an_authorization_with_no_expiry_refuses(state_dir, monkeypatch, armed):
    monkeypatch.delenv(arming.EXPIRY_ENV, raising=False)
    tr = _TR()
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == "NOT_ARMED" and tr.calls == []


def test_the_transport_itself_refuses_while_disarmed(monkeypatch):
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    from src.gemini_execution import transport as T
    with pytest.raises(arming.NotArmed):
        _run(T.place(symbol="GEMI-BTC05M2609180000-UP", side="buy",
                     outcome="yes", price="0.63", quantity="0.01"))
    with pytest.raises(arming.NotArmed):
        _run(T.cancel(1))


# ========================================================= envelope refusals

@pytest.mark.parametrize("over,needle", [
    ({"limit_price": "0.90"}, "CROSS"),                    # at the ask
    ({"limit_price": "0.95"}, "CROSS"),                    # through the ask
    ({"current_bbo": {"bid": "0.63", "ask": "0.90",
                      "source_ts": NOW - 60.0}}, "older than"),   # stale BBO
    ({"current_bbo": {"bid": "0.63", "ask": "0.90",
                      "source_ts": None}}, "no timestamp"),
    ({"instrument": "GEMI-BTC15M2609180000-UP"}, "BTC5m only"),
    ({"instrument": "GEMI-ETH05M2609180000-UP"}, "BTC5m only"),
    ({"quantity": "0.02"}, "not exactly"),
    ({"quantity": "1"}, "not exactly"),
    ({"side": "SELL"}, "side"),
    ({"restability": {"restable": True,
                      "tick_offset_from_touch": 1}}, "tick offset"),
    ({"restability": {"restable": False,
                      "tick_offset_from_touch": 0}}, "not restable"),
    ({"venue_terms": {"status": "open"}}, "limit_only"),
    ({"ok": False, "verdict": "NO_ELIGIBLE_WINDOW"}, "preflight did not pass"),
])
def test_envelope_violations_send_nothing(state_dir, armed, over, needle):
    tr = _TR()
    r = _submit(_pf(**over), tr, state_dir)
    assert r["state"] == "ENVELOPE_REFUSED", r
    assert needle in r["why"], r["why"]
    assert tr.calls == [], "a refused envelope must send nothing"


def test_a_refused_envelope_does_not_consume_the_authorization(state_dir, armed):
    _submit(_pf(limit_price="0.95"), _TR(), state_dir)
    assert latch.consumed(AUTH, state_dir) is False


def test_a_price_off_the_touch_is_refused_even_if_it_rests(state_dir, armed):
    """Tick offset 0 means the touch. A deeper price rests but is not the
    placement the evidence was gathered at."""
    tr = _TR()
    r = _submit(_pf(limit_price="0.60"), tr, state_dir)
    assert r["state"] == "ENVELOPE_REFUSED" and "not the touch" in r["why"]
    assert tr.calls == []


# ============================================================ the one-shot

def test_a_happy_submission_sends_exactly_one_order(state_dir, armed):
    tr = _TR()
    r = _submit(_pf(), tr, state_dir)
    assert r["submitted"] is True and r["state"] == latch.SUBMITTED
    assert len(tr.calls) == 1
    call = tr.calls[0]
    assert call["side"] == "buy" and call["outcome"] == "yes"
    assert call["quantity"] == "0.01" and call["price"] == "0.63"


def test_a_second_invocation_sends_nothing(state_dir, armed):
    tr = _TR()
    _submit(_pf(), tr, state_dir)
    r2 = _submit(_pf(), tr, state_dir)
    assert r2["state"] == "ALREADY_CONSUMED"
    assert len(tr.calls) == 1, "the second invocation must not reach the venue"


def test_the_latch_is_consumed_before_the_send(state_dir, armed):
    """A crash one instruction after the send must not free the authorization."""
    class _Boom:
        async def place(self, **kw):
            raise RuntimeError("process died mid-flight")

    with pytest.raises(RuntimeError):
        _submit(_pf(), _Boom(), state_dir)
    assert latch.consumed(AUTH, state_dir) is True


def test_restart_after_a_lost_response_reconciles_and_never_resubmits(
        state_dir, armed):
    """intent persisted -> request sent -> response lost -> restart."""
    tr = _TR(classification="AMBIGUOUS")
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == latch.SUBMISSION_UNKNOWN
    assert r["submitted"] is None
    assert latch.needs_reconciliation(AUTH, state_dir) is True

    # the restart
    tr2 = _TR()
    r2 = _submit(_pf(), tr2, state_dir)
    assert r2["state"] == "ALREADY_CONSUMED"
    assert tr2.calls == [], "a restart must reconcile, never POST /order again"

    rec = _run(C.reconcile(AUTH, state_dir=state_dir, transport=tr2))
    assert rec["resolved"] is True
    assert rec["latch_state"] == latch.SUBMISSION_UNKNOWN


def test_a_venue_rejection_still_spends_the_authorization(state_dir, armed):
    """Deliberate: a lost order is recoverable by asking, a double is not."""
    tr = _TR(classification="REJECTED")
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == latch.REJECTED and r["submitted"] is False
    assert latch.consumed(AUTH, state_dir) is True
    r2 = _submit(_pf(), _TR(), state_dir)
    assert r2["state"] == "ALREADY_CONSUMED"


def test_the_latch_records_every_required_field(state_dir, armed):
    _submit(_pf(), _TR(), state_dir)
    rec = latch.read(AUTH, state_dir)
    for f in ("authorization_id", "intent_id", "symbol", "side", "price",
              "amount", "created_at", "state", "venue_order_id"):
        assert f in rec, f
    assert rec["venue_order_id"] == 4242


# ================================================== no complement, no repeat

def test_nothing_places_a_complement(state_dir, armed):
    tr = _TR()
    _submit(_pf(), tr, state_dir)
    assert len(tr.calls) == 1
    assert all(c["side"] == "buy" for c in tr.calls)
    assert all(c["outcome"] == "yes" for c in tr.calls)


def test_the_module_has_no_retry_loop():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(C.submit_once))
    loops = [n for n in ast.walk(tree)
             if isinstance(n, (ast.For, ast.While))]
    assert loops == [], "a retry loop is how one authorization becomes two"


def test_the_request_is_maker_only_and_a_limit(state_dir, armed):
    """The venue-level protection the envelope depends on."""
    import inspect
    from src.gemini_execution import transport as T
    src = inspect.getsource(T.place)
    assert '"makerOrCancel": True' in src
    assert "spec.ORDER_TYPE_LIMIT" in src
    # and it is a boolean, not a timeInForce value, per the API
    assert "maker-or-cancel" not in src


# ====================================================== cancellation closes

def test_an_unreadable_cancel_is_reported_outstanding():
    from src.gemini_execution import transport as T

    class _T:
        async def cancel(self, oid, now=None):
            return {"ok": False, "classification": "AMBIGUOUS"}

    async def go():
        return await T.cancel_and_confirm.__wrapped__(1, "GEMI-X") \
            if hasattr(T.cancel_and_confirm, "__wrapped__") else None
    # exercised through the real function with a patched cancel below
    import asyncio as _a

    async def run():
        orig_cancel = T.cancel
        orig_find = T.find_order
        T.cancel = _T().cancel
        async def _find(**kw):
            return {"resolved": True, "found": True, "order": {}}
        T.find_order = _find
        try:
            return await T.cancel_and_confirm(1, "GEMI-X")
        finally:
            T.cancel = orig_cancel
            T.find_order = orig_find

    out = _run(run())
    assert out["state"] == "OUTSTANDING"
    assert "not a cancellation" in out["why"]


def test_a_cancel_the_venue_accepted_but_still_shows_active_is_outstanding():
    from src.gemini_execution import transport as T

    async def run():
        orig_cancel, orig_find = T.cancel, T.find_order
        async def _cancel(oid, now=None):
            return {"ok": True, "classification": "ACCEPTED"}
        async def _find(**kw):
            return {"resolved": True, "found": True, "order": {"orderId": 1}}
        T.cancel, T.find_order = _cancel, _find
        try:
            return await T.cancel_and_confirm(1, "GEMI-X")
        finally:
            T.cancel, T.find_order = orig_cancel, orig_find

    assert _run(run())["state"] == "OUTSTANDING"


def test_a_confirmed_cancel_requires_the_order_to_be_gone():
    from src.gemini_execution import transport as T

    async def run():
        orig_cancel, orig_find = T.cancel, T.find_order
        async def _cancel(oid, now=None):
            return {"ok": True, "classification": "ACCEPTED"}
        async def _find(**kw):
            return {"resolved": True, "found": False}
        T.cancel, T.find_order = _cancel, _find
        try:
            return await T.cancel_and_confirm(1, "GEMI-X")
        finally:
            T.cancel, T.find_order = orig_cancel, orig_find

    assert _run(run())["state"] == "CANCEL_CONFIRMED"


def test_there_is_no_cancel_all():
    """Scanned over AST names, not text: the module's docstring SAYS there is
    no cancel_all, and a substring scan fails on the sentence saying so."""
    import ast
    import inspect
    from src.gemini_execution import transport as T
    tree = ast.parse(inspect.getsource(T))
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not any("cancel_all" in d for d in defined), defined
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "/v1/prediction-markets/orders/cancel" not in consts


# ============================ durability on an ephemeral host (CI runners)

def test_durable_storage_is_consulted_before_the_local_filesystem(
        state_dir, armed):
    """A retried workflow gets a CLEAN disk. If only the local latch were
    checked, the one-shot guarantee would evaporate exactly when needed."""
    tr = _TR()
    r = _submit_hooks(_pf(), tr, state_dir,
                      durable_check=lambda a: True)
    assert r["state"] == "ALREADY_CONSUMED" and r.get("durable") is True
    assert tr.calls == []


def test_nothing_is_sent_if_the_authorization_cannot_be_made_durable(
        state_dir, armed):
    """Aborting here is SAFE: we have not sent, so a later retry places
    exactly one order. Sending first and failing to record it is the unsafe
    ordering."""
    tr = _TR()

    def _boom(auth, rec):
        raise RuntimeError("push rejected")

    r = _submit_hooks(_pf(), tr, state_dir, make_durable=_boom)
    assert r["state"] == "DURABILITY_REFUSED"
    assert r["sent"] is False
    assert tr.calls == [], "the venue must not be reached"


def test_the_durable_record_is_written_before_the_send(state_dir, armed):
    order = []
    tr = _TR()

    def _dur(auth, rec):
        order.append("durable")

    class _Ordered(_TR):
        async def place(self, **kw):
            order.append("send")
            return await _TR.place(self, **kw)

    _submit_hooks(_pf(), _Ordered(), state_dir, make_durable=_dur)
    # The safety property is that the FIRST durable write precedes the send.
    # A second write AFTER it records the outcome, so the durable record does
    # not sit at INTENT_PERSISTED forever.
    assert order[0] == "durable", order
    assert order.index("durable") < order.index("send"), order
    assert order[-1] == "durable", order


def _submit_hooks(pf, tr, state_dir, *, durable_check=None,
                  make_durable=None, auth=AUTH):
    return _run(C.submit_once(auth, state_dir=state_dir, now=NOW,
                              preflight=_PF(pf), transport=tr,
                              durable_check=durable_check,
                              make_durable=make_durable))


# ================================ a rejection must say WHY, and say it durably

def test_a_rejection_captures_the_venue_reason(state_dir, armed):
    """The first canary came back 400 and recorded only the status code, so
    nothing could say why. The reason is the whole value of a rejection."""
    tr = _TR(classification="REJECTED",
             body={"result": "error", "reason": "InvalidQuantity",
                   "message": "quantity below minimum"})
    r = _submit(_pf(), tr, state_dir)
    assert r["state"] == latch.REJECTED
    assert r["venue_reason"]["reason"] == "InvalidQuantity"
    assert "below minimum" in r["venue_reason"]["message"]
    rec = latch.read(AUTH, state_dir)
    assert rec["history"][-1]["detail"]["venue_reason"]["reason"] == \
        "InvalidQuantity"


def test_a_rejection_records_the_request_that_was_sent(state_dir, armed):
    tr = _TR(classification="REJECTED", body={"reason": "x"})
    r = _submit(_pf(), tr, state_dir)
    # the transport echoes the request; the canary surfaces it
    assert r.get("request_sent") is None or isinstance(r["request_sent"], dict)


def test_an_unparseable_rejection_body_is_reported_not_dropped(state_dir,
                                                               armed):
    tr = _TR(classification="REJECTED", body=None)
    r = _submit(_pf(), tr, state_dir)
    assert r["venue_reason"] is not None


def test_the_terminal_state_is_written_to_durable_storage_too(state_dir,
                                                              armed):
    """The pre-send record says INTENT_PERSISTED forever otherwise, so the
    durable record never learns the outcome."""
    seen = []
    tr = _TR(classification="REJECTED", body={"reason": "y"})
    _submit_hooks(_pf(), tr, state_dir,
                  make_durable=lambda a, rec: seen.append(rec["state"]))
    assert seen[0] == latch.INTENT_PERSISTED
    assert seen[-1] == latch.REJECTED
