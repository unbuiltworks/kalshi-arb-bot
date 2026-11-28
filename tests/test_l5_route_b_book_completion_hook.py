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
    # names.
    #
    # CORRECTED 2026-10-31. This fixture used 0.20/0.21 and described it as
    # "a DOWN complement at own 0.23 and a pair of 0.63". It is neither.
    # DOWN's touch in own terms is `1 - ask`, so on 0.20/0.21 the DOWN
    # complement costs own 0.79 -- UP is the long shot there, which makes
    # DOWN the dear side -- and the pair is 0.40 + 0.79 = 1.19, a certain
    # loss that must be refused. The quoted 0.23/0.63 was the
    # DOUBLE-CONVERTED value produced by the `own = 1 - px` defect in
    # `l5_book_completion.decide`, so this fixture encoded that bug and is
    # why it survived the suite.
    #
    # 0.79/0.80 is the state the comment intended: DOWN's touch is own 0.20
    # and the pair is a genuine 0.60, comfortably accepted on arithmetic
    # that holds.
    r.book = _Book(snap or {"bid": "0.79", "ask": "0.80",
                            "stream_age_s": "0.1",
                            "bid_shares": "500", "ask_shares": "500"})
    # THE RESIDUAL BUDGET MUST BE PRESENT, as it is in production.
    # `complement_order` judges a completion on the completion ceiling only
    # when it is given the held inventory AND the owner's approved
    # worst-case deficit; withhold the budget and it reverts to the 0.96
    # OPENING cap, which is the stricter path. A fixture without it was
    # testing the opening cap and calling it a completion.
    _vg = type("VG", (), {"max_worst_case_deficit_usd": D("3")})()
    # THE ROW-FREE RE-ENTRY IS A REAL ROUTE NOW, so the fake executor has to
    # offer the same `handle` the controller interface uses. It records
    # instead of submitting; `controller_calls` is how a test tells the two
    # routes apart.
    controller_calls = []
    r.executor = type("E", (), {
        "validation_gates": _vg,
        "handle": lambda self, row, book, **kw: controller_calls.append(
            (row, dict(kw))),
    })()
    r.controller_calls = controller_calls
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
    """RESTATED 2026-10-30. This asserted that NO ROW means no route at all.
    That was the defect: 0230z held 1.91 contracts the controller had opened,
    had no L5 row, and this route declined 263 times while they went
    unmatched.

    The invariant it exists to protect is intact and is what is asserted
    here: no L5 action is invented. The row re-entry is NOT used, no action
    key is reused, and the re-entry declares itself CONTROLLER_GENERATED.
    """
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch)        # default store holds 2 UP
    r._last_action_row = {}
    rec = r.maybe_book_completion(1_793_000_120.0)
    assert rec is not None and rec.get("ok") is True
    assert calls == [], "the L5 row route must not be used"
    assert r.stats["book_completion:NO_ACTION_YET"] == 0
    assert r.stats["book_completion:REENTERED:CONTROLLER"] == 1
    assert len(r.controller_calls) == 1
    row, kw = r.controller_calls[0]
    assert row is None, "no L5 row is synthesised"
    ctl = kw["controller"]
    assert ctl["provenance"] == R.LX.CONTROLLER_GENERATED
    assert ctl["decision"]["relation"] == "complete"


def test_with_no_row_and_a_flat_book_there_is_still_nothing_to_do(monkeypatch):
    """The case the original guard was written for survives untouched."""
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(up="0", dn="0"))
    r._last_action_row = {}
    assert r.maybe_book_completion(1_793_000_120.0) is None
    assert calls == []
    assert r.controller_calls == []
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
        # SELECTED BY LABEL, NOT BY POSITION. This read `seen[-1]` and so
        # silently depended on the trigger being the LAST event emitted.
        # `L5_BOOK_COMPLETION_RESULT` now follows it -- pairing the decision
        # with what the order path actually did -- so position is no longer
        # identity. Every assertion about the trigger's CONTENT below is
        # unchanged; only the selector moved.
        trig = [(l, r) for l, r in seen
                if l == "L5_BOOK_COMPLETION_TRIGGER"]
        assert len(trig) == 1, [l for l, _ in seen]
        label, rec = trig[0]
        assert label == "L5_BOOK_COMPLETION_TRIGGER"
        # and the outcome event must FOLLOW the decision, so the tape still
        # reads as a book-triggered re-evaluation and then its result
        labels = [l for l, _ in seen]
        assert labels.index("L5_BOOK_COMPLETION_TRIGGER") < \
            labels.index("L5_BOOK_COMPLETION_RESULT")
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


# ===================================================== INERTNESS, PINNED
#
# The promotion ships this code with L5_BOOK_COMPLETION unset. "Inert" has
# to mean more than "returns early": it must touch no state and emit no
# counter, so the deployed engine is byte-identical in behaviour to the
# commit that produced the +$3.8861 settled result.


def test_the_hook_touches_nothing_at_all_with_the_flag_off(monkeypatch):
    """A Runner carrying ONLY `stats` must survive the call.

    Giving it nothing else is the point: if the hook read `self.transport`,
    `self.store`, `self.book` or `self._last_action_row` before checking the
    flag, this raises AttributeError instead of returning None. That is a
    stronger statement than asserting the return value.
    """
    import collections
    monkeypatch.delenv(BC.ENABLED_ENV, raising=False)
    r = R.Runner.__new__(R.Runner)
    r.stats = collections.Counter()
    assert r.maybe_book_completion(1.0) is None
    assert dict(r.stats) == {}
    assert [k for k in vars(r) if k != "stats"] == []


def test_the_flag_check_is_the_first_statement(monkeypatch):
    """Pinned on the source too, so a later edit that moves a read above
    the flag check fails here rather than in production."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_book_completion)
    body = src[src.index('"""', src.index('"""') + 3) + 3:]
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()
             and not ln.strip().startswith("#")]
    assert lines[0] == "from src import l5_book_completion as BC"
    assert lines[1] == "if not BC.enabled():"
    assert lines[2] == "return None"
