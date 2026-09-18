"""Every way the L5 WebSocket canary could place something it must not.

This is the transport that has actually worked against this venue, which
makes it the dangerous one: the REST path's safety was partly an accident of
it never being accepted. Nothing here touches a socket.
"""
import asyncio
import json
import tempfile

import pytest

from src.gemini_execution import (arming, latch, payload as P, spec,
                                  ws_canary as WC, ws_session as WS)

AUTH = arming.EXPECTED_AUTH_ID
NOW = 1789690000.0                      # 2026-09-18T00:06Z, a real epoch


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
    pf = {"ok": True, "instrument": "GEMI-BTC05M2609180000-UP", "side": "BUY",
          "limit_price": "0.63", "quantity": "0.01",
          "restability": {"restable": True, "tick_offset_from_touch": 0},
          "venue_terms": {"status": "limit_only"},
          "current_bbo": {"bid": "0.63", "ask": "0.90", "source_ts": NOW - 1}}
    pf.update(over)
    return pf


def _terms(**over):
    t = {"readable": True, "quantityMinimum": "0.01",
         "quantityIncrement": "0.01", "priceMinimum": "0.01",
         "priceIncrement": "0.01", "priceMaximum": None,
         "quantityMaximum": None, "contract_status": "active",
         "market_state": "open"}
    t.update(over)
    return t


class _Sess:
    """Records frames. Never opens anything."""

    def __init__(self, ack=None, raises=None):
        self.ack = ack if ack is not None else {"status": 200,
                                                "result": {"orderId": "77"}}
        self.raises, self.sent = raises, []

    async def connect(self):
        raise AssertionError("an injected session must not be connected")

    async def request(self, method, params, *, timeout=4.0, now=None):
        self.sent.append({"method": method, "params": params})
        if self.raises:
            raise self.raises
        return self.ack

    async def close(self):
        pass


def _wire(monkeypatch, pf=None, terms=None, clear=True):
    class _PF:
        async def preflight_async(self, now=None):
            return pf or _pf()

    # ws_canary does `from src import gemini_canary_preflight`, which reads
    # the ATTRIBUTE on the src package, not sys.modules. Patching only
    # sys.modules worked when this file ran alone and silently did nothing
    # once another test had already imported the real module -- so the
    # package attribute is what has to be replaced. Both are set so neither
    # import form can reach the real preflight.
    import src as _src_pkg
    stub = _PF()
    monkeypatch.setattr(_src_pkg, "gemini_canary_preflight", stub,
                        raising=False)
    monkeypatch.setitem(__import__("sys").modules,
                        "src.gemini_canary_preflight", stub)
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: terms or _terms())

    async def exposure(symbol):
        return ({"clear": True} if clear
                else {"clear": False, "why": "active orders unreadable"})
    return exposure


# ------------------------------------------------------------ the arm

def test_disarmed_sends_nothing(state_dir, monkeypatch):
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    exp = _wire(monkeypatch)
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "NOT_ARMED"
    assert s.sent == []
    assert not latch.consumed(AUTH, state_dir)


def test_the_session_itself_refuses_a_mutation_while_disarmed(monkeypatch):
    """Structural: even holding a live socket, a disarmed process cannot
    frame an order."""
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    sess = WS.GeminiMakerSession()
    sess.ws = object()                    # pretend it is connected
    with pytest.raises(arming.NotArmed):
        _run(sess.request(spec.WS_PLACE_METHOD, {"symbol": "X"}, now=NOW))
    assert sess.sent == []


def test_reads_still_work_while_disarmed(monkeypatch):
    """Reconciliation must work when placing does not, so the gate is on
    mutation methods only -- not on the socket."""
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    sess = WS.GeminiMakerSession()
    sess.ws = None
    with pytest.raises(WS.WsRefused) as e:          # not NotArmed
        _run(sess.request(spec.WS_SUBSCRIBE_METHOD, ["orders@account"]))
    assert "not connected" in str(e.value)


def test_the_blanket_cancel_is_refused_even_armed(armed, monkeypatch):
    sess = WS.GeminiMakerSession()
    sess.ws = object()
    with pytest.raises(WS.WsRefused) as e:
        _run(sess.request(spec.WS_CANCEL_SESSION_METHOD, {}, now=NOW))
    assert "blanket cancel" in str(e.value)
    assert sess.sent == []


# -------------------------------------------------------- the one shot

def test_a_happy_submission_sends_exactly_one_frame(state_dir, armed,
                                                    monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["submitted"] is True and out["state"] == latch.SUBMITTED
    assert len(s.sent) == 1
    assert s.sent[0]["method"] == spec.WS_PLACE_METHOD
    assert out["venue_order_id"] == "77"
    assert "SUBMITTED only" in out["accounting"]


def test_a_second_invocation_sends_nothing(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch)
    _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                           session=_Sess(), exposure_check=exp))
    s2 = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s2, exposure_check=exp))
    assert out["state"] == "ALREADY_CONSUMED"
    assert s2.sent == []


def test_durable_storage_is_consulted_before_the_local_filesystem(
        state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp,
                                 durable_check=lambda a: True))
    assert out["state"] == "ALREADY_CONSUMED" and out["durable"] is True
    assert s.sent == []


def test_nothing_is_sent_if_the_latch_cannot_be_made_durable(state_dir, armed,
                                                            monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess()

    def boom(auth_id, rec):
        raise RuntimeError("push rejected")

    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp,
                                 make_durable=boom))
    assert out["state"] == "DURABILITY_REFUSED" and out["sent"] is False
    assert s.sent == []


def test_the_latch_is_consumed_before_the_frame(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch)
    seen = {}

    class _S(_Sess):
        async def request(self, method, params, *, timeout=4.0, now=None):
            seen["consumed_at_send"] = latch.consumed(AUTH, state_dir)
            return await super().request(method, params, timeout=timeout,
                                         now=now)

    _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW, session=_S(),
                           exposure_check=exp))
    assert seen["consumed_at_send"] is True


# ------------------------------------------------------ fail closed

def test_unreadable_exposure_refuses(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch, clear=False)
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "PREFLIGHT_REFUSED"
    assert "unreadable" in out["why"]
    assert s.sent == []
    assert not latch.consumed(AUTH, state_dir)


@pytest.mark.parametrize("over,needle", [
    ({"ok": False, "verdict": "NO_WINDOW"}, "preflight"),
    ({"instrument": "GEMI-ETH05M2609180000-UP"}, "btc5m only"),
    ({"current_bbo": {"bid": "0.63", "ask": "0.90",
                      "source_ts": NOW - 600}}, "old"),
    ({"limit_price": "0.90"}, "cross and take"),
    ({"limit_price": "0.70"}, "touch"),
])
def test_envelope_violations_send_nothing(state_dir, armed, monkeypatch,
                                          over, needle):
    exp = _wire(monkeypatch, pf=_pf(**over))
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "ENVELOPE_REFUSED"
    assert needle in out["why"].lower()
    assert s.sent == [] and not latch.consumed(AUTH, state_dir)


def test_an_untradeable_contract_sends_nothing(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch, terms=_terms(market_state="closed"))
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "ENVELOPE_REFUSED" and s.sent == []


# --------------------------------------------------- what the venue said

def test_a_rejecting_ack_captures_everything_it_carried(state_dir, armed,
                                                        monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess(ack={"status": 400, "result": "error", "reason": "InvalidQty",
                   "message": "quantity below minimum"})
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == latch.REJECTED and out["ack_status"] == 400
    assert out["venue_reason"]["reason"] == "InvalidQty"
    assert out["venue_reason"]["message"] == "quantity below minimum"
    assert out["request_sent"]["params"]["quantity"] == "0.01"
    rec = latch.read(AUTH, state_dir)
    assert rec["state"] == latch.REJECTED


def test_a_rejection_still_spends_the_authorization(state_dir, armed,
                                                    monkeypatch):
    exp = _wire(monkeypatch)
    _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                           session=_Sess(ack={"status": 400}),
                           exposure_check=exp))
    assert latch.consumed(AUTH, state_dir)


def test_a_failed_send_is_unknown_never_no_order(state_dir, armed,
                                                 monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess(raises=asyncio.TimeoutError())
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == latch.SUBMISSION_UNKNOWN
    assert out["submitted"] is None          # never False
    assert "not evidence that no order exists" in out["why"]
    assert out["client_order_id"] and out["request_sent"]


def test_nothing_places_a_complement_or_a_second_order(state_dir, armed,
                                                       monkeypatch):
    exp = _wire(monkeypatch)
    s = _Sess()
    _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW, session=s,
                           exposure_check=exp))
    outcomes = {f["params"]["eventOutcome"] for f in s.sent}
    assert outcomes == {"YES"}
    assert len(s.sent) == 1


def test_the_module_has_no_retry_loop():
    """Asserted over the AST, not the text. A substring scan for "retry"
    matches the sentence "there is no retry." -- it flags exactly the prose
    that promises the property it is meant to check."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(WC.submit_once_ws).lstrip())
    loops = [n for n in ast.walk(tree)
             if isinstance(n, (ast.For, ast.While, ast.AsyncFor))]
    assert loops == [], [type(n).__name__ for n in loops]
    sends = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "request"]
    assert len(sends) == 1, "exactly one send, textually and structurally"


# ------------------------------------------------------- the frame

def _params(**over):
    p = P.build_ws_order(symbol="GEMI-BTC05M2609180000-UP", price="0.63",
                         quantity="0.01", client_order_id="l5c-abc")
    p.update(over)
    return p


def test_the_frame_is_exactly_the_known_good_shape():
    f = P.sanitized_ws_frame(_params(), request_id="rid")["frame"]
    assert set(f) == {"id", "method", "params"}
    assert f["method"] == "order.place"
    assert list(f["params"]) == list(spec.WS_ORDER_FIELDS)
    assert f["params"]["side"] == "BUY"
    assert f["params"]["type"] == "LIMIT"
    assert f["params"]["timeInForce"] == "MOC"
    assert f["params"]["eventOutcome"] == "YES"


def test_the_frame_carries_no_credential_material():
    blob = json.dumps(P.sanitized_ws_frame(_params(), request_id="r")["frame"])
    for s in ("apikey", "signature", "secret", "nonce", "payload"):
        assert s not in blob.lower(), s


@pytest.mark.parametrize("field", sorted(P.WS_FORBIDDEN))
def test_the_rest_dialect_is_refused_by_name_on_this_transport(field):
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_ws_structure(_params(**{field: "x"}))
    assert field in str(e.value)


@pytest.mark.parametrize("bad", ["GTC", "good-til-cancel", "IOC", "FOK",
                                 "fill-or-kill", "", None])
def test_only_maker_only_is_accepted_as_the_tif(bad):
    """Both recovered historical rows were fill-or-kill, i.e. TAKERS. This
    canary is maker-only at the venue level or it is nothing."""
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_ws_types(_params(timeInForce=bad))
    assert "timeInForce" in str(e.value)


@pytest.mark.parametrize("bad", [0.63, 63, None, "", "abc"])
def test_price_and_quantity_must_be_decimal_strings(bad):
    with pytest.raises(P.PayloadViolation):
        P.assert_ws_types(_params(price=bad))
    with pytest.raises(P.PayloadViolation):
        P.assert_ws_types(_params(quantity=bad))


@pytest.mark.parametrize("bad", ["", "x" * 37, None, 12345])
def test_the_client_order_id_is_bounded_like_the_recovered_path(bad):
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_ws_types(_params(clientOrderId=bad))
    assert "clientOrderId" in str(e.value)


def test_the_client_order_id_is_truncated_not_rejected():
    cid = WC.client_order_id("canary-" + "f" * 40)
    assert len(cid) == spec.WS_CLIENT_ID_MAX
    P.assert_ws_types(_params(clientOrderId=cid))


@pytest.mark.parametrize("field", list(spec.WS_ORDER_FIELDS))
def test_a_missing_param_is_refused(field):
    p = _params()
    p.pop(field)
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_ws_structure(p)
    assert field in str(e.value)


# ------------------------------------------------------- the dry run

def test_the_dry_run_connects_to_nothing(monkeypatch):
    _wire(monkeypatch)
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "resolution_for", lambda s: {"verdict": "PENDING"})
    monkeypatch.setattr(MD, "max_cash_exposure", lambda q, p: "0.0063")

    def no_sockets(*a, **k):
        raise AssertionError("the dry run must not open a socket")

    monkeypatch.setattr(WS.GeminiMakerSession, "connect", no_sockets)
    out = _run(WC.dry_run(now=NOW, request_id="rid"))
    assert out["ok"] is True and out["sent"] is False
    assert out["frame"]["params"]["quantity"] == "0.01"
    assert out["armed"] is False


def test_the_dry_run_never_constructs_a_session():
    import inspect
    src = inspect.getsource(WC.dry_run)
    assert "GeminiMakerSession" not in src
    assert "connect" not in src


# --------------------------------------------- only one path can submit

def test_exactly_one_function_in_the_package_can_send_an_order_frame():
    import ast
    import pathlib
    senders = []
    for p in pathlib.Path("src/gemini_execution").glob("*.py"):
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", ""))
            if name != "request":
                continue
            for a in n.args:
                if (isinstance(a, ast.Attribute)
                        and a.attr == "WS_PLACE_METHOD"):
                    senders.append(str(p))
    assert senders == ["src/gemini_execution/ws_canary.py"], senders


def test_no_module_outside_the_execution_package_names_the_ws_method():
    import pathlib
    offenders = []
    for root in ("src", "analysis", "scripts"):
        base = pathlib.Path(root)
        for p in base.rglob("*.py") if base.exists() else []:
            if str(p).startswith("src/gemini_execution"):
                continue
            if spec.WS_PLACE_METHOD in p.read_text():
                offenders.append(str(p))
    assert not offenders, offenders


# ------------------------------------- absorbed from the parallel adapter
#
# src/gemini_l5_ws_canary.py landed on this branch from a parallel session
# and was REMOVED rather than kept, because it was a second submission path
# outside the guarded package whose only gate was a non-empty authorization
# STRING -- no arm, no exposure check, a latch that could not survive an
# ephemeral runner, and a rejection recorded as a bare status code. These
# tests carry over every property it did assert, so nothing it protected is
# lost by consolidating onto the gated path.

def test_the_envelope_is_exactly_one_btc5m_maker_order():
    assert WC.MAX_ORDERS == 1 and WC.MAX_RESIDUAL == "0.01"
    p = _params()
    assert p["type"] == "LIMIT" and p["timeInForce"] == "MOC"
    assert p["symbol"].startswith("GEMI-BTC05M")
    assert p["quantity"] == "0.01"


def test_the_bounded_envelope_is_recorded_in_the_durable_intent(
        state_dir, armed, monkeypatch):
    """The latch should state what was AUTHORIZED, not only what was sent."""
    exp = _wire(monkeypatch)
    _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW, session=_Sess(),
                           exposure_check=exp))
    env = (latch.read(AUTH, state_dir) or {}).get("envelope") or {}
    assert env["max_orders"] == 1
    assert env["max_residual"] == "0.01"
    assert env["maker_only"] is True
    assert env["transport"] == "WEBSOCKET"


def test_a_non_resting_price_is_refused(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch, pf=_pf(restability={"restable": False,
                                                 "tick_offset_from_touch": 0}))
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "ENVELOPE_REFUSED"
    assert "rest" in out["why"].lower()
    assert s.sent == []


def test_a_wrong_quantity_is_refused(state_dir, armed, monkeypatch):
    exp = _wire(monkeypatch, pf=_pf(quantity="1"))
    s = _Sess()
    out = _run(WC.submit_once_ws(AUTH, state_dir=state_dir, now=NOW,
                                 session=s, exposure_check=exp))
    assert out["state"] == "ENVELOPE_REFUSED"
    assert s.sent == []


def test_the_removed_adapter_is_gone_and_stays_gone():
    """A second submission path is the thing the isolation guard exists to
    prevent, so its absence is asserted rather than assumed."""
    import pathlib
    assert not pathlib.Path("src/gemini_l5_ws_canary.py").exists()
    assert not pathlib.Path("tests/test_gemini_l5_ws_canary.py").exists()
