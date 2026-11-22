"""Rate-limit resilience (2026-11-21 audit): a failed account read must not
stop market management, stale account state must never allow NEW exposure,
and shutdown cancels retry instead of leaving orders resting."""
from decimal import Decimal as D

import pytest

from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue


class E429(Exception):
    pass


@pytest.fixture
def eng(tmp_path):
    v = FakeVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._sleep = lambda s: None
    return e, v


def test_failed_account_read_returns_none_and_is_logged(eng, capsys):
    e, v = eng

    def boom():
        raise E429("HTTP Error 429: Too Many Requests")
    v.account = boom
    assert e.read_account() is None
    out = capsys.readouterr().out
    assert "account_read_error" in out and "consecutive" in out
    assert e._acct_ok_at is None


def test_good_read_refreshes_the_snapshot_and_logs_recovery(eng, capsys):
    e, v = eng
    e._acct_fail = 3
    acc = e.read_account()
    assert acc is v.account() or acc == v.account()
    assert e._acct_ok_at is not None and e._acct_fail == 0
    assert "account_read_recovered" in capsys.readouterr().out


def _held_yes(e, v, qty="5"):
    """Book and venue agree: we hold `qty` YES, nothing open."""
    mk = e.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D(qty), D(qty) * D("0.40")
    v.acct = {"positions": {"aec-x": D(qty)}, "open_orders": []}
    return mk


def test_stale_account_refuses_new_exposure(eng, capsys):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None                                 # never read
    _held_yes(e, v)
    v.account = lambda: (_ for _ in ()).throw(E429("HTTP Error 429"))
    e.place("aec-x", "YES", D("0.40"), D("1"), "rn1_open")
    assert v.orders == {}
    assert "place_refused_account_stale" in capsys.readouterr().out


def test_stale_completion_goes_only_on_venue_confirmed_inventory(eng):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None
    _held_yes(e, v)
    e.place("aec-x", "NO", D("0.55"), D("5"), "two_sided_complete")
    assert len(v.orders) == 1                            # confirmed -> goes
    assert e.account_fresh()                             # it used a fresh read


@pytest.mark.parametrize("case,reason", [
    ("unreadable", "account_unreadable"),
    ("venue_differs", "venue_inventory_mismatch"),
    ("no_venue_row", "venue_inventory_mismatch"),
    ("wrong_side", "would_not_reduce_venue_imbalance"),
    ("too_big", "exceeds_venue_imbalance"),
    ("unknown_order", "venue_order_state_mismatch"),
    ("book_order_gone", "venue_order_state_mismatch"),
    ("resting_already", "exceeds_venue_imbalance"),
])
def test_stale_completion_refused_without_confirmation(eng, capsys, case,
                                                       reason):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None
    mk = _held_yes(e, v)
    side, qty = "NO", D("5")
    if case == "unreadable":
        v.account = lambda: (_ for _ in ()).throw(E429("HTTP Error 429"))
    elif case == "venue_differs":
        v.acct["positions"]["aec-x"] = D("3")
    elif case == "no_venue_row":
        v.acct["positions"] = {}
    elif case == "wrong_side":
        side = "YES"                                     # would extend
    elif case == "too_big":
        qty = D("6")                                     # would flip
    elif case == "unknown_order":
        v.acct["open_orders"] = [{"id": "manual1", "marketSlug": "aec-x"}]
    elif case == "book_order_gone":
        mk["orders"]["o9"] = {"side": "NO", "px": "0.5", "qty": "1",
                              "filled": "0", "open": True}
    elif case == "resting_already":
        mk["orders"]["o9"] = {"side": "NO", "px": "0.5", "qty": "1",
                              "filled": "0", "open": True}
        v.acct["open_orders"] = [{"id": "o9", "marketSlug": "aec-x"}]
    e.place("aec-x", side, D("0.55"), qty, "two_sided_complete")
    assert v.orders == {}
    out = capsys.readouterr().out
    assert "place_refused_stale_completion" in out and reason in out


def test_stale_completion_still_obeys_lock_and_halt(eng, monkeypatch):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None
    _held_yes(e, v)
    monkeypatch.setattr(M, "submission_locked", lambda *a, **k: True)
    e.place("aec-x", "NO", D("0.55"), D("5"), "two_sided_complete")
    assert v.orders == {}


def test_stale_completion_reads_at_most_every_retry_interval(eng, capsys):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None
    _held_yes(e, v)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise E429("HTTP Error 429")
    v.account = boom
    for _ in range(5):
        e.place("aec-x", "NO", D("0.55"), D("5"), "two_sided_complete")
    assert calls["n"] == 1 and v.orders == {}
    assert "account_stale_retry_wait" in capsys.readouterr().out


def test_account_freshness_expires(eng, monkeypatch):
    e, v = eng
    e.require_fresh_account = True
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    e.read_account()
    assert e.account_fresh()
    clock["t"] += M.ACCOUNT_FRESH_S + 1
    assert not e.account_fresh()
    e.place("aec-x", "YES", D("0.40"), D("1"), "rn1_open")
    assert v.orders == {}


def test_freshness_not_required_outside_run(eng):
    e, v = eng
    assert e.require_fresh_account is False and e.account_fresh()


def test_shutdown_cancel_retries_a_rate_limited_cancel(eng):
    e, v = eng
    e.b.m("aec-x")
    e.place("aec-x", "YES", D("0.40"), D("1"), "rn1_open")
    oid = next(iter(v.orders))
    real = v.cancel
    calls = {"n": 0}

    def flaky(o):
        calls["n"] += 1
        if calls["n"] < 3:
            raise E429("HTTP Error 429")
        return real(o)
    v.cancel = flaky
    e.cancel_all_own("sigterm")
    assert calls["n"] == 3 and v.orders[oid]["open"] is False
    assert e.b.m("aec-x")["orders"][oid]["open"] is False


def test_shutdown_cancel_reports_an_order_it_could_not_cancel(eng, capsys):
    e, v = eng
    e.b.m("aec-x")
    e.place("aec-x", "YES", D("0.40"), D("1"), "rn1_open")

    def never(o):
        raise E429("HTTP Error 429")
    v.cancel = never
    e.cancel_all_own("sigterm")
    out = capsys.readouterr().out
    assert "cancel_error" in out and '"still_open":["o1"]' in out
    assert e.b.m("aec-x")["orders"]["o1"]["open"] is True   # never assumed


def test_loop_still_manages_markets_when_the_account_read_fails(eng,
                                                                monkeypatch):
    """The 429 used to throw out of the loop before step_market ran."""
    e, v = eng
    monkeypatch.setenv(M.ENABLED_ENV, "YES")

    def boom():
        raise E429("HTTP Error 429")
    v.account = boom
    e.b.m("aec-x")
    stepped = []

    def step(slug):
        stepped.append(slug)
        e.stop = True
    monkeypatch.setattr(e, "step_market", step)
    monkeypatch.setattr(M.signal, "signal", lambda *a, **k: None)
    e.run()
    assert stepped == ["aec-x"]
    assert e.require_fresh_account is True and not e.account_fresh()


# ------------------------------------------- pair cap: simultaneous fills
def test_simultaneous_fill_of_both_openings_stays_within_the_pair_cap(tmp_path,
                                                                     monkeypatch):
    """Both RN1 opening bids rest together; if BOTH fill in one pass the
    locked pair must still cost <= PAIR_CAP (LSU/Tenn 0.985 case)."""
    from src import pmus_rn1_replica as R
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("10"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("20"))
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    v = FakeVenue()
    v.spec = lambda slug: {"tick": D("0.005"), "min_qty": D("1")}
    v.bid, v.ask = D("0.395"), D("0.400")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.b.m("aec-x")
    e.step_market("aec-x")
    open_ = [o for o in v.orders.values() if o["open"]]
    assert {o["side"] for o in open_} == {"YES", "NO"}
    assert sum(o["px"] for o in open_) <= D("0.97")
    for o in open_:
        o.update(filled=o["qty"], open=False)          # both fill at once
    e.step_market("aec-x")
    assert D(e.b.d["pairs"][-1]["pair_cost"]) <= D("0.97")


# ----------------- no unknown or uncancellable orders after cancel failures
def test_failed_cancels_leave_no_unknown_orders(tmp_path, monkeypatch):
    """A rate-limited cancel during a reprice must leave the order OPEN in
    the book (so the registry and watchdog still name it) until the venue
    confirms it closed; once cancels work again nothing is left resting."""
    from src import pmus_rn1_replica as R
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.b.m("aec-x")
    e.step_market("aec-x")
    ids = {k for k, o in v.orders.items() if o["open"]}
    assert len(ids) == 2

    def limited(oid):
        raise E429("HTTP Error 429")
    real = v.cancel
    v.cancel = limited
    v.bid, v.ask = D("0.55"), D("0.57")               # forces a reprice
    with pytest.raises(E429):
        e.step_market("aec-x")                        # engine logs market_error
    book = e.b.m("aec-x")["orders"]
    assert all(book[i]["open"] for i in ids)          # never assumed closed
    listed = {r["venue_order_id"] for r in M.registry_doc(e.b)["orders"]}
    assert ids <= listed                              # watchdog still sees them
    v.cancel = real                                   # venue recovers
    e.step_market("aec-x")
    assert not any(v.orders[i]["open"] for i in ids)
    venue_open = {k for k, o in v.orders.items() if o["open"]}
    book_open = {k for k, o in e.b.m("aec-x")["orders"].items() if o["open"]}
    assert venue_open == book_open                    # nothing unknown
