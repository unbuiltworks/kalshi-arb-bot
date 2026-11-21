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


def test_stale_account_refuses_new_exposure_but_not_completions(eng, monkeypatch,
                                                               capsys):
    e, v = eng
    e.require_fresh_account = True
    e._acct_ok_at = None                                 # never read
    e.place("aec-x", "YES", D("0.40"), D("1"), "rn1_open")
    assert v.orders == {}
    assert "place_refused_account_stale" in capsys.readouterr().out
    e.place("aec-x", "NO", D("0.55"), D("1"), "two_sided_complete")
    assert len(v.orders) == 1                            # completion goes


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
