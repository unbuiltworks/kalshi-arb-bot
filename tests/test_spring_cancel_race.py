"""The cancel race that lost 4.34 units on aec-setkameua-vervas-kohboh
(OWNER GO #167 5584132560 item 2).

Order CC1E3Y7TPQ45 rested 5.676 s, we asked for a cancel, discarded the
answer, read the status once, saw nothing filled, and never looked again.
The order stayed live and filled 4.34 units 5.7 s later. These tests hold
the fixed behaviour: the cancel's verdict is honoured, an unconfirmed
cancel is re-read after a settle delay, and the order stays OPEN in the
fill ledger until the venue itself reports a terminal state."""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_fill_ledger as fl
from src import pmus_spring_l5_session as spring


class _Venue:
    """Scripted order statuses, one per read, and a cancel that reports
    whether the venue actually confirmed it."""

    def __init__(self, statuses, cancel_confirms):
        self.statuses = list(statuses)
        self.cancel_confirms = cancel_confirms
        self.reads = 0
        self.cancels = 0

    def status(self, _t, oid, **_kw):
        self.reads += 1
        return self.statuses[min(self.reads - 1, len(self.statuses) - 1)]

    def cancel(self, _t, _oid):
        self.cancels += 1
        return self.cancel_confirms


def _install(monkeypatch, venue):
    monkeypatch.setattr(spring.q1, "_order_status", venue.status)
    monkeypatch.setattr(spring.q1, "cancel_exact", venue.cancel)
    monkeypatch.setattr(spring.q1, "POLL_S", 0)


def _order(cum, state="ORDER_STATE_OPEN", px="0.44"):
    return {"cumQuantity": str(cum), "state": state, "avgPx": px}


def test_a_fill_inside_the_cancel_race_is_recovered(monkeypatch):
    """Unconfirmed cancel, then the venue reveals 4.34 filled. Before the
    fix this returned 0 and the units vanished."""
    venue = _Venue(
        statuses=[_order("0"),            # resting poll
                  _order("0"),            # first read after the cancel
                  _order("4.34", "ORDER_STATE_CANCELLED")],   # after settle
        cancel_confirms=False)
    _install(monkeypatch, venue)
    monkeypatch.setattr(fl, "CANCEL_SETTLE_S", 0)
    led = fl.FillLedger()
    clock = iter([0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    got, _rest, end = spring._await_order(
        object(), "CC1E3Y7TPQ45", D("4.54"), until=0.5,
        clock=lambda: next(clock), sleep=lambda *_a: None, ledger=led)
    assert got == D("4.34")
    assert end == "filled_in_cancel_race"
    assert led.booked("CC1E3Y7TPQ45") == D("4.34")


def test_an_unconfirmed_cancel_with_no_fill_is_named_and_stays_open(monkeypatch):
    """No units were lost here, but the order is not proven finished. It
    must stay OPEN so a later reconcile still reads it."""
    venue = _Venue(statuses=[_order("0")], cancel_confirms=False)
    _install(monkeypatch, venue)
    monkeypatch.setattr(fl, "CANCEL_SETTLE_S", 0)
    led = fl.FillLedger()
    clock = iter([0.0, 0.0, 1.0, 1.0, 1.0, 1.0])
    got, _rest, end = spring._await_order(
        object(), "o", D("4.54"), until=0.5,
        clock=lambda: next(clock), sleep=lambda *_a: None, ledger=led)
    assert got == D("0") and end == "unconfirmed_cancel"
    assert led.open_orders() == ["o"]


def test_a_confirmed_cancel_closes_the_order(monkeypatch):
    venue = _Venue(statuses=[_order("0"),
                             _order("0", "ORDER_STATE_CANCELLED"),
                             _order("0", "ORDER_STATE_CANCELLED")],
                   cancel_confirms=True)
    _install(monkeypatch, venue)
    led = fl.FillLedger()
    clock = iter([0.0, 0.0, 1.0, 1.0, 1.0, 1.0])
    got, _rest, end = spring._await_order(
        object(), "o", D("4.54"), until=0.5,
        clock=lambda: next(clock), sleep=lambda *_a: None, ledger=led)
    assert got == D("0") and end == "deadline"
    assert led.open_orders() == []


def test_a_full_fill_while_resting_is_unchanged(monkeypatch):
    """The fix must not disturb the ordinary path."""
    venue = _Venue(statuses=[_order("4.54", "ORDER_STATE_FILLED")],
                   cancel_confirms=True)
    _install(monkeypatch, venue)
    led = fl.FillLedger()
    clock = iter([0.0, 0.0, 0.1, 0.1, 0.1])
    got, _rest, end = spring._await_order(
        object(), "o", D("4.54"), until=5.0,
        clock=lambda: next(clock), sleep=lambda *_a: None, ledger=led)
    assert got == D("4.54") and end == "filled"
    assert venue.cancels == 0
    assert led.booked("o") == D("4.54")


def test_a_refused_or_halted_session_still_reconciles_its_fills(monkeypatch):
    """LIVE 2026-09-08 12:07Z: the reconciliation sat at the end of the
    action loop, so a session ending in SPRING_SAFE_REFUSAL returned
    flat_verified null and never re-read its own orders -- and a refusal
    mid-sequence is exactly the shape that abandoned CC1E3Y7TPQ45. Every
    exit path must reconcile."""
    calls = []

    def _body(*_a, **_kw):
        led = fl.FillLedger()
        led.track("o")
        return {"slug": "m", "status": "SPRING_SAFE_REFUSAL",
                "reason": "POST_ONLY_STALE_REFUSE:spring_complete_1",
                "_fill_ledger": led, "_inventory_obj": spring.Inventory()}

    def _recon(_t, slug, ledger, inv):
        calls.append(slug)
        return {"reconciled": {"recovered_qty": "4.34"},
                "venue_vs_ledger": {"agrees": True, "venue_qty": "4.34",
                                    "ledger_qty": "4.34"},
                "may_assert_flat": True}

    monkeypatch.setattr(spring, "_run_session_body", _body)
    monkeypatch.setattr(spring, "_reconcile_fills", _recon)
    rep = spring.run_session(object())
    assert calls == ["m"], "a refused session must still reconcile"
    assert rep["flat_verified"] is True
    assert rep["fill_reconciliation"]["reconciled"]["recovered_qty"] == "4.34"
    # the private handles never leak into the reported session
    assert "_fill_ledger" not in rep and "_inventory_obj" not in rep


def test_a_session_that_never_ordered_has_nothing_to_reconcile(monkeypatch):
    calls = []
    monkeypatch.setattr(spring, "_run_session_body",
                        lambda *a, **k: {"slug": "m",
                                         "status": "SPRING_SKIP_NOT_QUALIFYING"})
    monkeypatch.setattr(spring, "_reconcile_fills",
                        lambda *a, **k: calls.append(1) or {})
    rep = spring.run_session(object())
    assert calls == [] and "fill_reconciliation" not in rep


def test_a_mismatch_on_a_refusal_path_still_alarms(monkeypatch, capsys):
    monkeypatch.setattr(spring, "_run_session_body",
                        lambda *a, **k: {"slug": "m", "status": "HALT",
                                         "_fill_ledger": fl.FillLedger(),
                                         "_inventory_obj": spring.Inventory()})
    monkeypatch.setattr(spring, "_reconcile_fills", lambda *a, **k: {
        "venue_vs_ledger": {"agrees": False, "venue_qty": "13.8200",
                            "ledger_qty": "9.48", "delta": "4.3400"},
        "may_assert_flat": False})
    rep = spring.run_session(object())
    assert rep["flat_verified"] is False
    out = capsys.readouterr().out
    assert "venue_ledger_quantity_mismatch" in out and "13.8200" in out
