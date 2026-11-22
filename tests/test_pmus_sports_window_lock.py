"""Submission window (owner 2026-11-22): the only unlock is an exact UTC
window in the lock variable; it relocks by itself, fails closed on any
malformed value, and acts on orders already resting."""
from decimal import Decimal as D

import pytest

from src import pmus_sports_two_sided as M
from src import pmus_rn1_replica as R
from tests.test_pmus_sports_two_sided import FakeVenue

START, END = 1795320000.0, 1795325400.0           # 90 minutes
WIN = "window:%s/%s" % (M._iso(START), M._iso(END))


def setlock(monkeypatch, v):
    if v is None:
        monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    else:
        monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, v)


def at(monkeypatch, t):
    monkeypatch.setattr(M.time, "time", lambda: t)


# ------------------------------------------------------------ pure state
def test_one_is_locked_and_legacy_values_are_unchanged(monkeypatch):
    setlock(monkeypatch, "1")
    assert M.lock_state() == {"new": True, "all": True, "why": "locked"}
    for v in (None, "", "0"):
        setlock(monkeypatch, v)
        assert M.lock_state()["new"] is False and not M.submission_locked()


def test_window_opens_and_relocks_by_itself(monkeypatch):
    setlock(monkeypatch, WIN)
    assert M.lock_window() == (START, END)
    assert M.lock_state(START - 1)["why"] == "window_not_started"
    assert M.lock_state(START - 1)["all"]
    assert M.lock_state(START) == {"new": False, "all": False, "why": None,
                                   "start": START, "end": END}
    assert M.lock_state(END - 0.001)["new"] is False
    assert M.lock_state(END)["why"] == "window_ended"
    assert M.lock_state(END)["all"] and M.lock_state(END + 86400)["all"]


@pytest.mark.parametrize("bad", [
    "2", "yes", "off", "unlock", "window", "window:", "window:now/later",
    "window:2026-11-22T05:00:00Z",                      # no end
    "window:2026-11-22T06:00:00Z/2026-11-22T05:00:00Z",  # reversed
    "window:2026-11-22T05:00:00Z/2026-11-22T05:00:00Z",  # empty
    "window:2026-11-22T05:00:00/2026-11-22T06:00:00",    # no timezone
    "window:2026-11-22T05:00:00Z/2026-11-23T05:00:00Z",  # 24 h > max 6 h
    "window:2026-11-22T05:00:00Z/2026-11-22T06:00:00Z/x",
])
def test_anything_malformed_is_locked(monkeypatch, bad):
    setlock(monkeypatch, bad)
    st = M.lock_state(1795322000.0)
    assert st["new"] and st["all"] and st["why"] in (
        "window_invalid", "lock_value_unrecognized")
    assert M.submission_locked() and M.fully_locked()
    assert M.submission_locked("two_sided_complete")


def test_completion_grace_is_off_by_default_and_maker_only(monkeypatch):
    setlock(monkeypatch, WIN)
    at(monkeypatch, END + 10)
    assert M.WINDOW_COMPLETE_GRACE_S == 0
    assert M.submission_locked("two_sided_complete")     # default: all stops
    monkeypatch.setattr(M, "WINDOW_COMPLETE_GRACE_S", 1800.0)
    assert M.lock_state()["why"] == "window_completions_only"
    assert not M.submission_locked("two_sided_complete")
    assert M.submission_locked("two_sided_complete", post_only=False)
    for stage in ("rn1_open", "rn1_extend", "two_sided_open",
                  "two_sided_complete_taker", "two_sided_exit", None):
        assert M.submission_locked(stage)
    assert not M.fully_locked()                # registry: still managing
    at(monkeypatch, END + 1800)
    assert M.submission_locked("two_sided_complete") and M.fully_locked()


def _pilot_since_in_fresh_process(lock, since):
    """PILOT_SINCE is fixed at import, so read it from a fresh interpreter
    (reloading the engine module here would disturb other tests)."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, **{M.SUBMISSION_LOCK_ENV: lock,
                              "PMUS_RN1_PILOT_SINCE": since})
    out = subprocess.run(
        [sys.executable, "-c",
         "from src import pmus_sports_two_sided as M; print(M.PILOT_SINCE)"],
        env=env, capture_output=True, text=True, check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return float(out.stdout.strip().splitlines()[-1])


def test_window_start_is_the_campaign_marker():
    """A stale PMUS_RN1_PILOT_SINCE must not leak last night's markets
    (their $9.65 of filled cost) into the new campaign's $16 cap."""
    stale = "2026-11-22T01:15:00Z"
    assert _pilot_since_in_fresh_process(WIN, stale) == START
    assert _pilot_since_in_fresh_process(WIN, M._iso(START + 600)) == \
        START + 600                                   # a later marker: kept
    assert _pilot_since_in_fresh_process("1", stale) == 1795310100.0


# ---------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e


def open_sides(e):
    return sorted((o["side"], o.get("stage")) for mk in
                  e.b.d["markets"].values() for o in mk["orders"].values()
                  if o.get("open"))


def test_no_order_before_the_window_then_orders_inside_it(eng, monkeypatch):
    setlock(monkeypatch, WIN)
    # as a process started with this window does (the window start is the
    # campaign marker; a marker later than the start blocks new exposure)
    eng.since = M.adopt_campaign_marker(eng.b)
    assert eng.since == START
    at(monkeypatch, START - 5)
    eng.b.m("aec-x")
    eng.step_market("aec-x")
    assert eng.v.orders == {}
    at(monkeypatch, START + 5)
    eng.step_market("aec-x")
    assert len(eng.v.orders) == 2 and len(open_sides(eng)) == 2


def test_at_the_deadline_resting_orders_are_cancelled_and_none_placed(
        eng, monkeypatch):
    setlock(monkeypatch, WIN)
    # as a process started with this window does (the window start is the
    # campaign marker; a marker later than the start blocks new exposure)
    eng.since = M.adopt_campaign_marker(eng.b)
    assert eng.since == START
    at(monkeypatch, START + 5)
    eng.b.m("aec-x")
    eng.step_market("aec-x")
    assert len(open_sides(eng)) == 2
    at(monkeypatch, END + 1)
    assert eng.enforce_lock() == "window_ended"
    assert open_sides(eng) == []
    assert all(not o["open"] for o in eng.v.orders.values())
    n = len(eng.v.orders)
    eng.step_market("aec-x")
    eng.place("aec-x", "YES", D("0.40"), D("2"), "rn1_open")
    eng.place("aec-x", "NO", D("0.40"), D("2"), "two_sided_complete")
    assert len(eng.v.orders) == n                    # nothing new, ever


def test_lock_one_cancels_anything_left_resting(eng, monkeypatch):
    setlock(monkeypatch, None)
    eng.b.m("aec-x")
    eng.step_market("aec-x")
    assert len(open_sides(eng)) == 2
    setlock(monkeypatch, "1")
    assert eng.enforce_lock() == "locked"
    assert open_sides(eng) == []


def test_grace_keeps_only_the_maker_completion(eng, monkeypatch):
    monkeypatch.setattr(M, "WINDOW_COMPLETE_GRACE_S", 1800.0)
    setlock(monkeypatch, WIN)
    at(monkeypatch, START + 5)
    mk = eng.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.88")   # lone 0.44
    eng.step_market("aec-x")
    before = open_sides(eng)
    assert ("NO", "two_sided_complete") in before
    at(monkeypatch, END + 60)
    assert eng.enforce_lock() == "window_completions_only"
    eng.step_market("aec-x")
    assert open_sides(eng) == [("NO", "two_sided_complete")]
    at(monkeypatch, END + 1800)
    assert eng.enforce_lock() == "window_ended"
    assert open_sides(eng) == []


def test_enforce_lock_is_quiet_when_nothing_rests(eng, monkeypatch):
    setlock(monkeypatch, "1")
    calls = []
    monkeypatch.setattr(eng, "cancel_all_own",
                        lambda *a, **k: calls.append(a))
    eng.enforce_lock()
    eng.enforce_lock()
    assert calls == []


def test_registry_reports_disabled_only_when_fully_locked(eng, monkeypatch):
    setlock(monkeypatch, WIN)
    at(monkeypatch, START + 5)
    assert M.registry_doc(eng.b)["submission_disabled"] is False
    at(monkeypatch, END)
    assert M.registry_doc(eng.b)["submission_disabled"] is True


def test_book_worst_case_blocks_between_audits(eng, monkeypatch):
    """5 lone YES @0.85 can lose 4.25 >= the $4 stop: blocked at once, from
    the book, without waiting for the hourly venue audit."""
    from src import pmus_pilot_pnl as PP
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("4"))
    eng.pilot_meter([], {})                        # a clean, current meter
    assert eng.pilot_block() is None
    mk = eng.b.m("aec-w")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("5"), D("4.25")
    mk["orders"]["o1"] = {"side": "YES", "qty": "5", "filled": "5",
                          "open": False, "stage": "rn1_open",
                          "placed_at": 2000.0}
    assert PP.book_worst_case(eng.b.d["markets"]) == D("-4.25")
    assert eng.pilot_block() == "pilot_loss_at_risk_book"
    mk["pos"]["NO"], mk["cost"]["NO"] = D("5"), D("0.50")   # paired 0.95
    assert PP.book_worst_case(eng.b.d["markets"]) == D("0.25")
    assert eng.pilot_block() is None
