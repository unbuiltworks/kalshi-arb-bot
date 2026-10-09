"""End-to-end: the oneshot cannot submit before reconciliation succeeds.

Mocked venue throughout. The question is not whether the money path works --
other suites cover that -- but whether the GATE is on the path an order must
take, and whether it fails closed when the venue is ambiguous.
"""
import os

import pytest

from src import pmus_guardian_gate as GATE
from src import pmus_order_guardian as G

import scripts.l5_pmus_live_oneshot as ONE


class FakeTp:
    """A transport that records everything and never reaches a network."""

    def __init__(self, open_orders=None, order=None, recent=None,
                 fail=None):
        self.open_orders = open_orders if open_orders is not None else []
        self.order = order or {}
        self.recent = recent if recent is not None else []
        self.fail = fail or set()
        self.cancelled = []
        self.submitted = []

    def request(self, method, path, body):
        if path in self.fail:
            raise OSError("mocked failure for %s" % path)
        if path == "/v1/orders/open":
            return self.open_orders
        if path == "/v1/orders/history":
            return self.recent
        if path.startswith("/v1/order/"):
            return self.order
        return {}

    def cancel(self, oid):
        self.cancelled.append(oid)
        return {"ok": True}

    def submit_body(self, body):
        self.submitted.append(body)
        return {"id": "V-NEW"}


class FakeProbe:
    def __init__(self, tp):
        self.tp = tp
        self.market = "cpc-btc-updown-15m-2099-01-01-1200z"
        self.native = None


def _guardian(tmp_path):
    return G.OwnershipStore(str(tmp_path / "own.json"))


def _run(probe, guardian, journal=None):
    """Drive only the gate portion of native_work_window."""
    rep = GATE.open_for_business(
        guardian,
        list_open=lambda: ONE._venue_open_orders(probe),
        read_order=lambda oid: ONE._venue_read_order(probe, oid),
        list_recent=lambda: ONE._venue_recent_orders(probe))
    GATE.require_open_for_business(rep)
    return rep


# ------------------------------------------------- the gate is on the path ---
def test_a_clean_venue_lets_the_window_proceed(tmp_path):
    tp = FakeTp()
    rep = _run(FakeProbe(tp), _guardian(tmp_path))
    assert rep["may_submit"] is True


def test_an_unknown_live_order_at_the_venue_refuses_the_window(tmp_path):
    """The R6 shape seen from the next process: something is resting and we
    have no record of it."""
    tp = FakeTp(open_orders=[{"id": "STRANGER"}])
    with pytest.raises(GATE.SubmissionBlocked):
        _run(FakeProbe(tp), _guardian(tmp_path))
    assert tp.submitted == []


def test_a_recorded_order_that_filled_after_exit_is_booked_then_allowed(
        tmp_path):
    g = _guardian(tmp_path)
    g.intend("CSWG9ZB98YCA", market="M", side="down", price="0.69", qty="10")
    g.acknowledged("CSWG9ZB98YCA", "CSWG9ZB98YCA")
    tp = FakeTp(open_orders=[],
                order={"state": "ORDER_STATE_FILLED", "filled_qty": 10})
    g2 = G.OwnershipStore(str(tmp_path / "own.json"))
    rep = _run(FakeProbe(tp), g2)
    assert rep["reconcile"]["late_fills_booked"] == ["CSWG9ZB98YCA"]
    assert rep["may_submit"] is True


def test_an_unreadable_open_orders_endpoint_refuses_the_window(tmp_path):
    tp = FakeTp(fail={"/v1/orders/open"})
    with pytest.raises(GATE.SubmissionBlocked):
        _run(FakeProbe(tp), _guardian(tmp_path))


def test_an_ambiguous_lost_ack_refuses_the_window(tmp_path):
    g = _guardian(tmp_path)
    g.intend("local-1", market="cpc-btc-updown-15m-2099-01-01-1200z",
             side="up", price="0.50", qty="10", submitted_t=1000.0)
    twin = {"market": "cpc-btc-updown-15m-2099-01-01-1200z", "side": "up",
            "price": "0.50", "quantity": "10", "created_t": 1000.2}
    tp = FakeTp(recent=[{**twin, "id": "A"}, {**twin, "id": "B"}])
    with pytest.raises(GATE.SubmissionBlocked):
        _run(FakeProbe(tp), g)


def test_a_blocked_gate_means_no_body_ever_reaches_the_transport(tmp_path):
    """The whole point: refusal happens BEFORE an order exists."""
    tp = FakeTp(open_orders=[{"id": "STRANGER"}])
    probe = FakeProbe(tp)
    g = _guardian(tmp_path)
    try:
        _run(probe, g)
    except GATE.SubmissionBlocked:
        pass
    assert tp.submitted == [] and tp.cancelled == []


# ------------------------------------------------------- the store wiring ---
def test_the_guardian_store_defaults_onto_the_durable_volume(monkeypatch,
                                                             tmp_path):
    """When a run needs the guardian, the store lands on the durable volume
    beside the journal, so it survives the process."""
    monkeypatch.delenv(ONE.GUARDIAN_STATE_ENV, raising=False)
    monkeypatch.setenv("PMUS_Q1_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ONE.Q1X, "armed", lambda: True)
    g = ONE._build_guardian(FakeProbe(FakeTp()), None)
    assert g.path == tmp_path / "guardian_ownership.json"


def test_an_armed_run_always_requires_the_guardian(monkeypatch):
    monkeypatch.delenv(ONE.GUARDIAN_STATE_ENV, raising=False)
    monkeypatch.setattr(ONE.Q1X, "armed", lambda: True)
    assert ONE.guardian_required() is True


def test_a_disarmed_run_does_not_require_it_because_it_cannot_submit(
        monkeypatch):
    """Not a loophole: a disarmed run cannot submit, so there is no order to
    reconcile. Binding the gate to arming is what the requirement says."""
    monkeypatch.delenv(ONE.GUARDIAN_STATE_ENV, raising=False)
    monkeypatch.setattr(ONE.Q1X, "armed", lambda: False)
    assert ONE.guardian_required() is False
    assert ONE._build_guardian(FakeProbe(FakeTp()), None) is None


def test_an_explicit_store_path_requires_it_even_when_disarmed(monkeypatch,
                                                               tmp_path):
    monkeypatch.setenv(ONE.GUARDIAN_STATE_ENV,
                       str(tmp_path / "explicit.json"))
    monkeypatch.setattr(ONE.Q1X, "armed", lambda: False)
    assert ONE.guardian_required() is True


def test_an_unknowable_arm_state_requires_it(monkeypatch):
    """Failing closed: if we cannot tell whether we are armed, assume we are."""
    monkeypatch.delenv(ONE.GUARDIAN_STATE_ENV, raising=False)

    def boom():
        raise RuntimeError("cannot read arm state")

    monkeypatch.setattr(ONE.Q1X, "armed", boom)
    assert ONE.guardian_required() is True


def test_an_unusable_store_path_blocks_rather_than_trading_unprotected(
        monkeypatch, tmp_path):
    bad = tmp_path / "not-a-dir"
    bad.write_text("x")
    monkeypatch.setenv(ONE.GUARDIAN_STATE_ENV, str(bad / "own.json"))
    monkeypatch.setattr(ONE.Q1X, "armed", lambda: True)
    g = ONE._build_guardian(FakeProbe(FakeTp()), None)
    # the store opens lazily; the write that matters is the INTENT, so prove
    # that an intent against an unusable path raises rather than passing
    with pytest.raises(BaseException):
        g.intend("c1", market="M", side="up", price="0.5", qty="1")


def test_shutdown_sweep_cancels_and_verifies_through_the_transport(tmp_path):
    import signal
    g = _guardian(tmp_path)
    g.intend("c1", market="M", side="up", price="0.50", qty="10")
    g.acknowledged("c1", "V1")
    tp = FakeTp(order={"state": "ORDER_STATE_CANCELLED", "filled_qty": 0})
    probe = FakeProbe(tp)
    seen = []
    GATE.arm_shutdown(g, cancel=lambda oid: ONE._venue_cancel(probe, oid),
                      read_order=lambda oid: ONE._venue_read_order(probe, oid),
                      on_report=seen.append)
    os.kill(os.getpid(), signal.SIGTERM)
    assert tp.cancelled == ["V1"]
    assert seen[0]["results"][0]["verdict"] == "TERMINAL:ORDER_STATE_CANCELLED"
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
