"""Order-state and shutdown-cancel fixes (RN1 live pilot 2026-11-22).

1. The orphan sweep must not "adopt" an order the book already closed on the
   venue's exact status just because a (cached) open-orders list still shows
   it; it confirms with the exact order status first.
2. SIGTERM must cancel every own open order at once, not after the current
   loop pass (a long audit pass outlived the 30 s drain), and no order may be
   placed after the signal.
"""
import threading
from decimal import Decimal as D

import pytest

from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue


@pytest.fixture
def eng(tmp_path):
    v = FakeVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._sleep = lambda s: None
    e.b.m("aec-x")
    return e, v


def _place(e, side="YES", px="0.40"):
    e.place("aec-x", side, D(px), D("2"), "rn1_open")
    return max(e.b.m("aec-x")["orders"])


def test_stale_snapshot_does_not_adopt_a_closed_order(eng):
    e, v = eng
    oid = _place(e)
    v.orders[oid].update(open=False, filled=D("2"))      # filled at venue
    e.b.m("aec-x")["orders"][oid]["open"] = False        # book closed it
    cancels = []
    real = v.cancel
    v.cancel = lambda o: (cancels.append(o), real(o))[1]
    stale = [{"id": oid, "marketSlug": "aec-x",
              "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}]
    e.orphan_sweep(stale)
    assert cancels == []                                  # not re-cancelled
    assert e.b.m("aec-x")["orders"][oid]["open"] is False
    assert D(e.b.m("aec-x")["pos"]["YES"]) == D("2")      # fill recorded
    assert e.orphan_stale == 1


def test_a_genuinely_open_order_missing_from_the_book_is_adopted(eng, capsys):
    e, v = eng
    oid = _place(e)
    e.b.m("aec-x")["orders"][oid]["open"] = False         # book lost it
    snap = [{"id": oid, "marketSlug": "aec-x",
             "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}]
    e.orphan_sweep(snap)
    assert "ORPHAN_ADOPTED" in capsys.readouterr().out
    assert v.orders[oid]["open"] is False                 # cancelled


def test_manual_orders_are_never_touched(eng):
    e, v = eng
    v.orders["man1"] = {"slug": "aec-x", "side": "YES", "px": D("0.3"),
                        "qty": D("1"), "filled": D(0), "open": True}
    e.orphan_sweep([{"id": "man1", "marketSlug": "aec-x",
                     "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}])
    assert v.orders["man1"]["open"] is True


def test_sigterm_cancels_at_once_while_the_loop_is_busy(eng, capsys):
    e, v = eng
    a, b = _place(e, "YES", "0.40"), _place(e, "NO", "0.55")
    e.on_sigterm()                    # main loop not involved at all
    e._shutdown_thread.join(5)
    assert not v.orders[a]["open"] and not v.orders[b]["open"]
    out = capsys.readouterr().out
    assert '"why":"sigterm_immediate"' in out and '"still_open":[]' in out
    e.on_sigterm()                    # a second signal starts nothing new
    assert e._shutdown_thread is not None


def test_no_placement_after_sigterm(eng, capsys):
    e, v = eng
    e.on_sigterm()
    e._shutdown_thread.join(5)
    n = len(v.orders)
    e.place("aec-x", "YES", D("0.40"), D("2"), "rn1_open")
    e.place("aec-x", "NO", D("0.55"), D("2"), "two_sided_complete")
    assert len(v.orders) == n
    assert "place_refused_shutdown" in capsys.readouterr().out


def test_exit_cancel_after_immediate_cancel_is_idempotent(eng):
    e, v = eng
    oid = _place(e)
    e.on_sigterm()
    e._shutdown_thread.join(5)
    calls = []
    v.cancel = lambda o: calls.append(o)
    e.cancel_all_own("sigterm")        # the loop's own exit cancel
    assert calls == []                 # nothing left open to cancel
    assert not v.orders[oid]["open"]


def test_book_save_is_safe_against_a_concurrent_writer(eng):
    e, v = eng
    stop = threading.Event()

    def mutate():
        i = 0
        while not stop.is_set():
            e.b.m(f"aec-m{i % 50}")["pos"]["YES"] = str(i)
            i += 1
    t = threading.Thread(target=mutate)
    t.start()
    try:
        for _ in range(200):
            e.b.save()
    finally:
        stop.set()
        t.join()
    M.Book(e.b.path)                   # still valid JSON
