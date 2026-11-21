"""THE QUANTITY THAT ACTUALLY REACHES THE VENUE.

LIVE-2 (run 35573430715, 2026-09-21T07:41Z) was authorized at q=1 and executed
at q=0.01: the envelope is a CAP, not a size setter, and the rung qty came from
a module constant. Builder-level tests could not have caught it, because the
builder was handed the wrong number and faithfully built a correct frame around
it. So these tests drive the REAL Driver and capture the REAL WebSocket frame
that `session.request` would put on the wire.

`FakeSession.sent[i]["params"]["quantity"]` is the exact string the venue would
receive. Nothing below asserts on an intermediate.
"""
import asyncio
import dataclasses

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

SYM = "GEMI-BTC05M2609181200-UP"
AUTH = "OWNER-TEST-NOT-A-REAL-AUTHORIZATION"


class FakeSession:
    def __init__(self):
        self.default = {"status": 200, "result": {"orderId": 1}}
        self.sent = []

    async def request(self, method, params, timeout=5.0):
        self.sent.append({"method": method, "params": params})
        return self.default


@pytest.fixture
def _authorized(monkeypatch):
    monkeypatch.setattr(WC, "REQUIRED_AUTH_ID", AUTH)
    monkeypatch.setattr(WC, "AUTHORIZED_SHA", WC.executable_sha())
    monkeypatch.setattr(WC, "AUTHORIZED_ENVELOPE", WC.envelope_sha())
    monkeypatch.setattr(WC.arming, "EXPECTED_AUTH_ID", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_LIVE", "YES")
    monkeypatch.setenv("GEMINI_CANARY_AUTH", AUTH)
    monkeypatch.setenv("GEMINI_CANARY_AUTH_EXPIRES", "2099-01-01T00:00:00Z")
    assert WC.authorized(), WC.authorization_state()["why_not"]


def _run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def _ready(tmp_path, envelope):
    d = WC.Driver(AUTH, SYM, ledger_path=str(tmp_path / "mut.jsonl"),
                  now_fn=lambda: 1_789_000_000.0, envelope=envelope)
    d.state = WC.PREFLIGHT_OK
    d.state = WC.AUTHORIZED
    assert d.recover(venue_fills=[], venue_open=[], venue_orders=[])["recovered"]
    assert d.bind()["bound"]
    return d


def _book(ybid="0.55", yask="0.60"):
    return {"readable": True, "identity_consistent": True,
            "yes": {"bid": ybid, "ask": yask},
            "no": {"bid": str(1 - float(yask)), "ask": str(1 - float(ybid))}}


def _decide_and_send(tmp_path, envelope):
    """Full path: decide() -> diff_surface -> place() -> wire frame."""
    d = _ready(tmp_path, envelope)
    s = FakeSession()
    out = d.decide(200.0, book=_book())
    plan = out.get("plan")
    assert plan, "the driver produced no plan: %s" % out.get("why")
    for r in plan["place"]:
        _run(d.place(s, r["rung"]))
    assert s.sent, "no frame reached the session"
    return d, s, out


# ============================== (2) q=1 config -> q=1 ON THE WIRE
def test_a_q1_envelope_puts_q1_on_the_wire(tmp_path, _authorized):
    d, s, _ = _decide_and_send(tmp_path, LE.LIVE_Q1)
    qtys = {f["params"]["quantity"] for f in s.sent}
    assert qtys == {"1"}, (
        "LIVE_Q1 authorized q=1 but the wire carried %s -- this is the LIVE-2 "
        "defect" % qtys)
    assert d.env.name == "GEMINI_L5_LIVE_ENVELOPE_Q1_V1"


def test_the_wire_frame_is_a_real_order_frame_not_a_stub(tmp_path, _authorized):
    _, s, _ = _decide_and_send(tmp_path, LE.LIVE_Q1)
    p = s.sent[0]["params"]
    # the real WS order frame's own field names, taken from the frame rather
    # than assumed: side/type/timeInForce, not a spot-schema "outcome" key
    for k in ("symbol", "quantity", "price", "side", "type", "timeInForce"):
        assert k in p, (k, sorted(p))
    assert p["symbol"] == SYM
    assert p["side"] == "BUY" and p["type"] == "LIMIT"
    assert p["timeInForce"] == "MOC", "maker-only must survive to the wire"


# ============================== (3) q=0.01 STAYS q=0.01
def test_the_shadow_envelope_still_puts_001_on_the_wire(tmp_path, _authorized):
    _, s, _ = _decide_and_send(tmp_path, LE.SHADOW_Q001)
    assert {f["params"]["quantity"] for f in s.sent} == {"0.01"}


def test_the_default_envelope_is_the_shadow_size(tmp_path, _authorized):
    assert LE.DEFAULT is LE.SHADOW_Q001
    assert str(LE.DEFAULT.action_size) == "0.01"


# ============================== (4) BOUNDS SCALE, NEVER INHERIT 0.01's
def test_bounds_scale_with_q_and_do_not_inherit_the_shadow_values():
    b001 = CP.scaled_bounds("0.01")
    b1 = CP.scaled_bounds("1")
    assert b001["action_size"] == CP.D("0.01") and b001["k"] == CP.D("1")
    assert b1["action_size"] == CP.D("1") and b1["k"] == CP.D("100")
    for k in ("max_residual", "max_imbalance", "max_yes", "max_no",
              "max_total_spend"):
        assert b1[k] == b001[k] * 100, k
        assert b1[k] != b001[k], "%s silently inherited the q=0.01 value" % k


def test_the_same_number_of_rungs_fits_at_both_sizes():
    """Scaling must preserve BEHAVIOUR: the bound is the same count of rungs."""
    for q in ("0.01", "1"):
        b = CP.scaled_bounds(q)
        assert b["max_residual"] / b["action_size"] == CP.D(
            str(CP.MAX_SAME_SIDE_RUN))


def test_the_dollar_caps_do_not_scale_and_bind_tighter_at_q1():
    """scaled_bounds widens the CONTRACT bounds; the CASH caps are the
    envelope's and must not move with q."""
    assert LE.LIVE_Q1.max_committed_usd == CP.D("2.50")
    assert LE.LIVE_Q1.max_residual_usd == CP.D("2.00")
    assert LE.LIVE_Q1.max_working_orders == 2
    assert LE.LIVE_Q1.max_concurrent_markets == 1
    assert LE.LIVE_Q1.max_actions == 12


def test_a_q1_rung_is_refused_once_the_committed_cap_is_reached(tmp_path,
                                                                _authorized):
    """The cash cap, not the contract cap, is what stops a q=1 run."""
    d = _ready(tmp_path, LE.LIVE_Q1)
    s = FakeSession()
    placed = 0
    for i in range(6):
        try:
            _run(d.place(s, {"leg": "YES", "price": "0.55", "qty": "1",
                             "action": CP.OPEN}))
            placed += 1
        except CP.ProbeStop:
            break
    # WHICHEVER envelope bound binds first, a q=1 run is stopped by the
    # ENVELOPE and not by the contract-denominated controller bounds. Here the
    # working-orders cap (2) binds before the $2.50 committed cap, which is
    # correct and is why this asserts the class rather than one message.
    assert placed < 6, "no envelope bound stopped a repeated q=1 rung"
    assert d.state in (WC.STOPPED, WC.DO_NOT_TRADE)
    why = (d.why or "").lower()
    assert "risk gate refused" in why, d.why
    assert ("resting" in why or "committed" in why or "2.50" in why), d.why


def test_the_committed_cash_cap_itself_binds_at_q1():
    """Drive the $2.50 cap directly, with the order-count cap out of the way."""
    import dataclasses
    env = dataclasses.replace(LE.LIVE_Q1, max_working_orders=64)
    inv = CP.Inventory()
    # committed = resting + the rung being sent. 3 resting at $0.55 plus one
    # more is $2.20 and fits; 4 resting plus one more is $2.75 and does not.
    r = [{"leg": "YES", "price": "0.55", "qty": "1"} for _ in range(4)]
    ok = LE.would_breach("YES", "0.55", inv, qty="1", resting=r[:3],
                         extra_committed_usd=CP.D("0"), env=env)
    assert ok is None, ok
    bad = LE.would_breach("YES", "0.55", inv, qty="1", resting=r,
                          extra_committed_usd=CP.D("0"), env=env)
    assert bad and "2.50" in bad, bad


def test_a_downsized_rung_is_refused_under_a_q1_authorization(tmp_path,
                                                              _authorized):
    """The regression guard: a rung that came back at the shadow size under a
    q=1 envelope must STOP, not be sent."""
    d = _ready(tmp_path, LE.LIVE_Q1)
    d.guard = type("G", (), {
        "actions_taken": 0,
        "step": lambda self, st, inv, **kw: {
            "actions": [{"leg": "YES", "price": "0.55", "qty": "0.01",
                         "action": CP.OPEN}],
            "holds": [], "surface": None, "window_phase": None}})()
    out = d.decide(200.0, book=_book())
    assert d.state == WC.DO_NOT_TRADE
    assert "0.01" in (d.why or "") and "q=1" in (d.why or "")
    assert out.get("plan") is None


# ============================== (1) THE LATCH RECORDS THE LIVE ENVELOPE
def test_the_latch_intent_records_the_active_envelope_not_the_shadow():
    """LIVE-2's latch said amount 0.01 / max_residual 0.04 on a q=1 window.

    READ FROM `run_one_window`, which is where the intent is built now that
    the window body is a reusable primitive the cohort runner also drives.
    The invariant is unchanged and is checked in the SAME function that
    consumes the latch, so the record and the spend cannot come apart.
    """
    import inspect
    src = inspect.getsource(RC.run_one_window)
    assert "latch.consume(" in src, (
        "the intent must live with the consume that spends it")
    assert '"amount": str(envelope.action_size)' in src
    assert '"max_residual": str(envelope.max_residual)' in src
    assert '"max_residual_usd": str(envelope.max_residual_usd)' in src
    assert '"max_committed_usd": str(envelope.max_committed_usd)' in src
    assert '"envelope_name": envelope.name' in src
    assert '"amount": str(CP.ACTION_SIZE)' not in src, (
        "the latch is back on the shadow constant")
    assert '"max_residual": str(CP.MAX_RESIDUAL)' not in src


def test_the_latch_fields_differ_between_the_two_envelopes():
    a, b = LE.SHADOW_Q001, LE.LIVE_Q1
    assert str(a.action_size) == "0.01" and str(b.action_size) == "1"
    assert str(a.max_residual) == "0.04" and str(b.max_residual) == "2"
    assert str(a.max_committed_usd) == "0.15"
    assert str(b.max_committed_usd) == "2.50"


# ============================== refusal rather than silent default
@pytest.mark.parametrize("bad", [0, "0", "", "abc", -1, "-0.5"])
def test_a_malformed_action_size_is_refused_not_defaulted(bad):
    with pytest.raises(CP.ActionSizeRefused):
        CP.resolve_action_size(bad)


def test_only_absence_defaults():
    assert CP.resolve_action_size(None) == CP.ACTION_SIZE


# ============================== DISARMED
def test_the_shipped_constants_are_none():
    import importlib
    m = importlib.reload(WC)
    # ARMED 2026-09-21 under OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1.
    # The shipped constants are no longer None, and "None" was never the
    # property -- it was the absence of one. What ships must NAME THE CODE
    # THAT SHIPS WITH IT, so a constant surviving a change to the driver or
    # the bounds disarms rather than silently authorising them.
    if m.REQUIRED_AUTH_ID is not None:
        assert m.AUTHORIZED_SHA == m.executable_sha()
        assert m.AUTHORIZED_ENVELOPE == m.envelope_sha()
    else:
        assert m.AUTHORIZED_SHA is None
        assert m.AUTHORIZED_ENVELOPE is None
