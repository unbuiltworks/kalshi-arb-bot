"""Route B: the book asks, the EXISTING path answers.

The hook re-runs `Runner.handle` against the current book, reusing the last
REAL admitted L5 action. These tests prove the properties the owner named:
repeated book evaluations cannot duplicate orders, consume stale
authorization, exceed inventory or cash limits, or submit after expiry --
and that provenance is not misrepresented.

WHY `handle` IS THE THING ASSERTED ON. The hook's whole claim is that it
adds no order path: it re-enters the one reviewed call. So the tests count
`handle` re-entries and read the emitted trigger record, rather than
re-implementing the gates here. A test that mocked the gates would be
asserting its own mock.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_pmus_live_runner as R


class _Store:
    def __init__(self, up="2", dn="0", resting=0, legs=None, live=None):
        self._up, self._dn = D(up), D(dn)
        self._resting = resting
        self._legs = legs or {"UP": D("0.80"), "DOWN": D("0")}
        self._live = live or {}

    def position(self, market, side):
        return self._up if side == "UP" else self._dn

    def still_fillable_order_count(self):
        return self._resting

    def leg_cost(self, market):
        return dict(self._legs)

    def live_orders(self):
        return dict(self._live)


class _Book:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, slug):
        return dict(self._snap)


class _T:
    market_slug = "cpc-btc-updown-15m-2026-10-29-0200z"
    tick_size = "0.01"
    quantity_increment = "0.01"


def _runner(monkeypatch, *, store=None, snap=None, ws=None):
    r = R.Runner.__new__(R.Runner)
    r.transport = _T()
    r.store = store or _Store()
    # A BOOK THAT ACTUALLY YIELDS AN ACCEPTED DECISION. With 0.50/0.51 and
    # a 0.40 first leg the complement quote WOULD_CROSS, so every test
    # would have been asserting the economics rather than the guard it
    # names. Held UP at own 0.40 against a 0.20/0.21 book gives a DOWN
    # complement at own 0.23 and a pair of 0.63, comfortably accepted.
    r.book = _Book(snap or {"bid": "0.20", "ask": "0.21",
                            "stream_age_s": "0.1",
                            "bid_shares": "500", "ask_shares": "500"})
    # THE RESIDUAL BUDGET MUST BE PRESENT, as it is in production.
    # `complement_order` judges a completion on the completion ceiling only
    # when it is given the held inventory AND the owner's approved
    # worst-case deficit; withhold the budget and it reverts to the 0.96
    # OPENING cap, which is the stricter path. A fixture without it was
    # testing the opening cap and calling it a completion.
    _vg = type("VG", (), {"max_worst_case_deficit_usd": D("3")})()
    r.executor = type("E", (), {"validation_gates": _vg})()
    r.stats = __import__("collections").Counter()
    r._book_completion_t = None
    r._last_action_row = {_T.market_slug: {"slug": "x",
                                           "transactionHash": "0xabc"}}
    calls = []
    r.handle = lambda row, now: calls.append((dict(row), now))
    monkeypatch.setattr(R.AB, "slug_start",
                        lambda s: (ws if ws is not None else 1_793_000_000.0))
    monkeypatch.setattr(R, "emit", lambda *a, **k: None)
    monkeypatch.setattr(R.AUTH, "max_submissions", lambda: 72)
    return r, calls


# --------------------------------------------------------------- OFF
def test_the_hook_is_inert_without_the_flag(monkeypatch):
    monkeypatch.delenv(BC.ENABLED_ENV, raising=False)
    r, calls = _runner(monkeypatch)
    assert r.maybe_book_completion(1_793_000_120.0) is None
    assert calls == []


def test_with_the_flag_on_it_can_re_enter(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    out = r.maybe_book_completion(1_793_000_120.0)
    assert out["ok"] is True, out
    assert len(calls) == 1
    assert r.stats["book_completion:REENTERED"] == 1


# ------------------------------------------------- no synthesised action
def test_it_never_invents_an_l5_action(monkeypatch):
    """Before any admitted action there is nothing to reuse, and nothing
    is fabricated to stand in for one."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch)
    r._last_action_row = {}
    assert r.maybe_book_completion(1_793_000_120.0) is None
    assert calls == []
    assert r.stats["book_completion:NO_ACTION_YET"] == 1


def test_the_reused_row_is_the_real_admitted_action(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    r._last_action_row[_T.market_slug] = {"slug": "s", "transactionHash": "0xZ"}
    out = r.maybe_book_completion(1_793_000_120.0)
    assert out["ok"] is True, out
    assert calls[0][0]["transactionHash"] == "0xZ"


# --------------------------------------------------- no duplicate orders
def test_a_resting_order_blocks_re_entry(monkeypatch):
    """One concurrent live order is the limit, and the hook declines BEFORE
    re-entering so the record says why."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", resting=1,
                     legs={"UP": D("0.40"), "DOWN": D("0")}))
    out = r.maybe_book_completion(1_793_000_120.0)
    assert calls == []
    assert out["ok"] is False
    assert out["reason"] == "ORDER_ALREADY_RESTING"


def test_repeated_calls_cannot_re_enter_more_than_the_cadence_allows(
        monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    t0 = 1_793_000_120.0
    for i in range(50):
        r.maybe_book_completion(t0 + i * 0.1)        # 5s of ticks at 0.1s
    # 5 seconds of wall clock at the 5s cadence floor permits at most two
    assert len(calls) <= 2, len(calls)


def test_the_cadence_is_stamped_on_a_refusal_too(monkeypatch):
    """Otherwise a refused decision is retried every single tick."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, _ = _runner(monkeypatch,
                   store=_Store(up="2", dn="0",
                                legs={"UP": D("1.90"), "DOWN": D("0")}))
    r.maybe_book_completion(1_793_000_120.0)
    assert r._book_completion_t == 1_793_000_120.0


# ---------------------------------------------- inventory and cash limits
def test_flat_inventory_never_re_enters(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(up="0", dn="0"))
    r.maybe_book_completion(1_793_000_120.0)
    assert calls == []


def test_matched_inventory_never_re_enters(monkeypatch):
    """Both sides holding is a completed round, not a completion."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(up="2", dn="2"))
    r.maybe_book_completion(1_793_000_120.0)
    assert calls == []
    assert r.stats["book_completion:ALREADY_MATCHED"] == 1


def test_an_exhausted_submission_budget_never_re_enters(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    monkeypatch.setattr(R.AUTH, "max_submissions", lambda: 0)
    r.maybe_book_completion(1_793_000_120.0)
    assert calls == []
    assert r.stats["book_completion:NO_SUBMISSIONS_LEFT"] == 1


def test_an_unreadable_ledger_fails_closed(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")

    class _Bad(_Store):
        def position(self, market, side):
            raise OSError("volume gone")

    r, calls = _runner(monkeypatch, store=_Bad())
    assert r.maybe_book_completion(1_793_000_120.0) is None
    assert calls == []
    assert any(k.startswith("book_completion:LEDGER_UNREADABLE")
               for k in r.stats)


# ------------------------------------------------------- stale and late
def test_a_stale_book_never_re_enters(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}),
        snap={"bid": "0.20", "ask": "0.21", "stream_age_s": "99",
              "bid_shares": "500", "ask_shares": "500"})
    r.maybe_book_completion(1_793_000_120.0)
    assert calls == []
    assert r.stats["book_completion:STALE_BOOK"] == 1


def test_a_late_window_never_re_enters(monkeypatch):
    """After the binding horizon there is nothing to place."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    r.maybe_book_completion(1_793_000_000.0 + 880.0)
    assert calls == []
    assert r.stats["book_completion:PAST_TERMINAL"] == 1


def test_an_unknown_window_start_never_re_enters(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, ws=None)
    monkeypatch.setattr(R.AB, "slug_start", lambda s: None)
    assert r.maybe_book_completion(1_793_000_120.0) is None
    assert calls == []


# -------------------------------------------------- authorization is not
#                                                    re-implemented here
def test_the_hook_contains_no_authorization_or_order_path():
    """It must add no submission site and construct no capability.

    This is the invariant Route B was chosen to preserve: the single-use
    claim stays in the two reviewed files and no module under src/ builds a
    ControllerLiveAuthorization.
    """
    import inspect
    src = inspect.getsource(R.Runner.maybe_book_completion)
    for forbidden in ("ControllerLiveAuthorization", "claim_submission",
                      "transport.submit", "urlopen", "_handle("):
        assert forbidden not in src, forbidden
    # the ONE call that can lead to an order is the reviewed path
    assert "self.handle(row, now)" in src


def test_provenance_is_not_misrepresented(monkeypatch):
    """The tape must say the BOOK asked, naming the reused real action."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    seen = []
    r, _ = _runner(
        monkeypatch,
        store=_Store(up="2", dn="0", legs={"UP": D("0.40"), "DOWN": D("0")}))
    monkeypatch.setattr(R, "emit", lambda label, rec: seen.append((label, rec)))
    out = r.maybe_book_completion(1_793_000_120.0)
    assert out["ok"] is True, out
    if True:
        label, rec = seen[-1]
        assert label == "L5_BOOK_COMPLETION_TRIGGER"
        assert rec["triggered_by"] == "BOOK_NOT_L5_ACTION"
        assert rec["reused_action_key"] == "0xabc"
        assert "no L5 action is synthesised" in rec["labels"]
        assert "leg" not in rec["decision"]


def test_the_tick_calls_the_hook_in_the_completion_slot():
    """Placed where the L5-driven completion already sits, so nothing
    downstream is reordered."""
    import inspect
    src = inspect.getsource(R.Runner.tick)
    i = src.index("self.maybe_ts_completion(now)")
    j = src.index("self.maybe_book_completion(now)")
    k = src.index("self.maybe_ts_deadline(now)")
    assert i < j < k
