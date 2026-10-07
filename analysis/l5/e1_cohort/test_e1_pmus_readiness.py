"""READINESS, restored for the PMUS feed.

WHY THIS FILE EXISTS, STATED PLAINLY. The Polymarket.com switchover
deleted `test_e1_ws_readiness.py`, and rightly so: most of what it tested
-- `price_change` frame shapes, two CTF token ids, one token streaming
while the other was silent -- describes a venue E1 does not trade on.

But a differential of the test inventory before and after the switchover
showed that SIX of the removed tests covered concerns that are NOT
venue-specific, and that nothing replaced them:

  1. the wait is bounded EVEN AGAINST A FROZEN CLOCK. A wall-clock
     deadline alone is not a bound when `now_fn` is injected -- against a
     clock that does not advance, the deadline never arrives and the loop
     spins forever. The poll bound is the real bound, and it was
     untested.
  2. the production readiness bound is the one that actually shipped, not
     a test-only value.
  3. a readiness SUCCESS carries its own proof, so the tape records WHY a
     window was considered ready rather than asserting that it was.
  4. a FEED STARTUP FAILURE is TERMINAL FOR THE SWEEP, not a market
     refusal. If it enumerated candidates instead, one dead socket would
     burn every remaining window as "refused".
  5. NO LATCH SLOT IS SPENT WHILE WAITING. A slot consumed during a wait
     that then times out is a permanently lost attempt.
  6. a window is never OPENED by a wait that failed.

Losing coverage of those while replacing the feed would have been a
silent regression, so they are restored here against
`PmusDecisionFeed`/`PmusWindowProvider`.

Run:
    PYTHONPATH=. python3 -m pytest \
        analysis/l5/e1_cohort/test_e1_pmus_readiness.py -q
"""
from __future__ import annotations

import os

import pytest

from analysis.l5.e1_cohort import arming as AR
from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import pmus_provider as PV
from analysis.l5.e1_cohort import pmus_selector as SEL
from analysis.l5.e1_cohort import progress as PG
from analysis.l5.e1_cohort import spec as SP

W = SEL.WINDOW_S
BASE = 1800000000 - (1800000000 % W)


class FrozenClock:
    """A clock that NEVER ADVANCES. This is the adversary: a wall-clock
    deadline is unreachable against it."""

    def __init__(self, t=BASE + 10):
        self.t = float(t)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.t


class NeverReadyFeed:
    def __init__(self, why="NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG"):
        self.why = why
        self.started = False
        self.health_calls = 0

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.started = False

    def health(self, pin):
        self.health_calls += 1
        return {"ok": False, "why": self.why,
                "market": pin.get("slug"),
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS}

    def book(self, pin):
        raise AssertionError("book() must not be read before health is ok")


class ReadyAfterFeed(NeverReadyFeed):
    """Not ready for `after` polls, then ready. Models a socket that
    connects and fills its book a moment after subscription."""

    def __init__(self, after=3, clock=None):
        super().__init__()
        self.after = int(after)
        self.clock = clock or (lambda: BASE + 10)

    def health(self, pin):
        # NOT `super().health()`: the base also increments the counter, so
        # delegating would count every not-ready poll twice. The first
        # version of this fake did, and `polls` read 3 where 4 had
        # happened -- the fake was wrong, not the code.
        self.health_calls += 1
        if self.health_calls <= self.after:
            return {"ok": False, "why": self.why,
                    "market": pin.get("slug"),
                    "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS}
        return {"ok": True, "fresh": True, "market": pin.get("slug"),
                "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                "rest_is_sufficient_timing_authority": False,
                "ctf_token_ids_used": False,
                "source": "authenticated_ws", "stream_age_s": 0.3,
                "proven_for_the_selected_market": True}

    def book(self, pin):
        slug = pin["slug"]
        comp = PBK.complement_of("0.50", "0.52")
        return {"market": slug, "ok": True, "book_unix": self.clock(),
                "first_leg": {"side": "UP", "slug": slug,
                              "outcome_side": pin["outcome_up"],
                              "best_bid": "0.50", "best_ask": "0.52",
                              "size_at_our_price": "4.00"},
                "complement": {"side": "DOWN", "slug": slug,
                               "outcome_side": pin["outcome_down"],
                               "best_bid": str(comp["bid"]),
                               "best_ask": str(comp["ask"])}}


def fake_pin(window_start):
    ws = int(window_start)
    return {"series": SEL.SERIES, "venue": "polymarket_us",
            "slug": PI.slug_for(ws), "market_id": "pmus-market-%d" % ws,
            "outcome_up": PI.outcome_sides({})["up_is"],
            "outcome_down": PI.outcome_sides({})["down_is"],
            "window_start": ws, "window_end": ws + W,
            "identity": "slug_plus_outcome_side",
            "token_ids_in_pin": False,
            "selection_provenance": {"series_pinned": SEL.SERIES}}


def arm(monkeypatch, sid="e1-readiness-test"):
    monkeypatch.setenv("PMUS_E1_LIVE", "YES")
    monkeypatch.setenv("PMUS_E1_SESSION_ID", sid)
    monkeypatch.setenv("PMUS_E1_ARM", sid)
    monkeypatch.setenv("PMUS_E1_GATE_EVIDENCE", AUTH.AUTHORIZED_SERIES)
    monkeypatch.setenv(AR.FINGERPRINT_PIN_ENV, SP.E1_FINGERPRINT)
    for k in AR.Q1_AUTHORIZATION_KEYS:
        monkeypatch.delenv(k, raising=False)
    return sid


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    yield


# ============ 1. THE WAIT IS BOUNDED AGAINST A FROZEN CLOCK =============

def test_the_wait_is_bounded_even_against_a_frozen_clock():
    """THE ADVERSARIAL CASE. `now_fn` never advances, so the wall-clock
    deadline is unreachable. The POLL BOUND must end the wait."""
    clock = FrozenClock()
    feed = NeverReadyFeed()
    slept = []
    with pytest.raises(PV.FeedNotReady) as ei:
        PV.await_pmus_book_ready(feed, fake_pin(BASE), now_fn=clock,
                                 sleep_fn=slept.append,
                                 timeout_s=20.0, poll_s=0.25)
    # it ended, and it ended on the POLL bound
    msg = str(ei.value)
    assert "did not become usable" in msg
    assert feed.health_calls == int(20.0 / 0.25) + 1 == 81
    assert len(slept) == feed.health_calls - 1
    assert all(s == 0.25 for s in slept)
    # and it names the market and the last reason
    assert PI.slug_for(BASE) in msg
    assert "NO_CONNECTED_TWO_SIDED_BOOK_FOR_THIS_SLUG" in msg


def test_a_zero_or_tiny_poll_interval_cannot_make_the_bound_infinite():
    """`int(timeout/poll)` with a poll of 0 would be a division by zero,
    and with a poll of 1e-30 an effectively infinite loop. Neither may
    hang."""
    for poll in (0.0, 1e-30):
        clock = FrozenClock()
        with pytest.raises((PV.FeedNotReady, OverflowError, ValueError)):
            PV.await_pmus_book_ready(
                NeverReadyFeed(), fake_pin(BASE), now_fn=clock,
                sleep_fn=lambda s: None, timeout_s=0.001, poll_s=poll)


def test_a_real_clock_deadline_also_ends_the_wait():
    """With a clock that DOES advance, the wall-clock deadline is what
    ends it -- well before the poll ceiling."""
    t = {"v": float(BASE)}

    def clock():
        t["v"] += 5.0            # five seconds per read
        return t["v"]

    feed = NeverReadyFeed()
    with pytest.raises(PV.FeedNotReady):
        PV.await_pmus_book_ready(feed, fake_pin(BASE), now_fn=clock,
                                 sleep_fn=lambda s: None,
                                 timeout_s=20.0, poll_s=0.25)
    assert feed.health_calls < 81, feed.health_calls


# ============ 2. THE PRODUCTION BOUND IS WHAT SHIPPED ===================

def test_the_production_readiness_bound_is_what_we_shipped():
    """The SHIPPED bound is finite and is the one the image runs with.

    `conftest._fast_ws_readiness` shrinks the bound for every test in
    this package, so the live module attributes read 0.05/0.005 here --
    reading them would assert the TEST value and prove nothing. The
    production values are asserted unpatched by
    `conftest.test_production_defaults_are_what_we_think_they_are`; what
    this adds is that the SOURCE LITERALS are those values, and that the
    patched values are strictly a speed-up of the same shape.
    """
    import re
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pmus_provider.py"), encoding="utf8").read()
    got = dict(re.findall(r"^(WS_READY_\w+) = ([0-9.]+)$", src,
                          re.MULTILINE))
    assert got == {"WS_READY_TIMEOUT_S": "20.0",
                   "WS_READY_POLL_S": "0.25"}, got
    # the test bound is smaller, not looser: still finite, still a poll
    # strictly inside a timeout
    assert 0 < PV.WS_READY_POLL_S < PV.WS_READY_TIMEOUT_S
    # a provider built with no override takes whatever the module says,
    # at CALL time -- not a default argument frozen at import
    class _P:
        pass
    p = PV.PmusWindowProvider(progress=_P(), session_auth=_P(),
                              feed=NeverReadyFeed())
    assert p.ws_ready_timeout_s == PV.WS_READY_TIMEOUT_S
    assert p.ws_ready_poll_s == PV.WS_READY_POLL_S
    # and the book-age bound the health predicate uses is finite too
    assert 0 < PBK.MAX_BOOK_AGE_S < 60


# ============ 3. A SUCCESS CARRIES ITS OWN PROOF ========================

def test_readiness_success_carries_its_own_proof():
    clock = FrozenClock()
    feed = ReadyAfterFeed(after=3, clock=clock)
    out = PV.await_pmus_book_ready(feed, fake_pin(BASE), now_fn=clock,
                                   sleep_fn=lambda s: None,
                                   timeout_s=20.0, poll_s=0.25)
    assert out["ok"] is True
    r = out["readiness"]
    assert r["waited_for_the_pinned_market"] is True
    assert r["polls"] == 4                     # three not-ready, then ready
    assert r["timeout_s"] == 20.0
    assert r["bounded"] is True
    # the proof is ABOUT THE PINNED MARKET, named
    assert out["market"] == PI.slug_for(BASE)
    assert out["proven_for_the_selected_market"] is True
    assert out["timing_authority"] == PBK.TIMING_PMUS_AUTHENTICATED_WS
    assert out["rest_is_sufficient_timing_authority"] is False


def test_an_already_ready_feed_is_not_slept_on():
    """A book that is ready on the first poll must not cost a sleep."""
    clock = FrozenClock()
    slept = []
    feed = ReadyAfterFeed(after=0, clock=clock)
    out = PV.await_pmus_book_ready(feed, fake_pin(BASE), now_fn=clock,
                                   sleep_fn=slept.append)
    assert out["readiness"]["polls"] == 1
    assert slept == []


# ===== 4. A FEED FAILURE IS TERMINAL FOR THE SWEEP, NOT A REFUSAL ======

def test_a_feed_failure_does_not_become_many_market_refusals(monkeypatch,
                                                             tmp_path):
    """THE DEFECT THIS CLOSES. If a dead socket were treated as a MARKET
    refusal, the provider would walk every remaining candidate and burn
    them all. One dead feed must cost ZERO windows."""
    arm(monkeypatch)
    d = str(tmp_path / "e1")
    os.makedirs(d, exist_ok=True)
    clock = FrozenClock()
    feed = NeverReadyFeed()
    prog = PG.CohortProgress(d)
    p = PV.PmusWindowProvider(
        progress=prog, session_auth=AUTH.SessionAuthorization(d),
        feed=feed, selector_fn=lambda ws, **kw: fake_pin(ws),
        now_fn=clock, sleep_fn=lambda s: None,
        window_starts=[BASE + i * W for i in range(12)],
        ws_ready_timeout_s=1.0, ws_ready_poll_s=0.25)

    assert p() is None
    # EXACTLY ONE feed refusal, not twelve
    feed_refusals = [r for r in p.refusals
                     if r["refused"].startswith("PMUS_FEED_NOT_READY")]
    assert len(feed_refusals) == 1, p.refusals
    r = feed_refusals[0]
    assert r["terminal_for_this_sweep"] is True
    assert r["is_a_market_refusal"] is False
    assert r["is_a_feed_startup_failure"] is True
    assert r["candidates_not_enumerated"] is True
    # and NO window was consumed
    assert prog.windows_used() == 0
    assert prog.windows_remaining() == SP.WINDOWS


# ============ 5. NO LATCH SLOT IS SPENT WHILE WAITING ==================

def test_no_latch_slot_is_spent_while_waiting_for_readiness(monkeypatch,
                                                            tmp_path):
    """A slot consumed during a wait that then times out is a
    PERMANENTLY LOST attempt -- the latch has no release path."""
    arm(monkeypatch)
    d = str(tmp_path / "e1")
    os.makedirs(d, exist_ok=True)
    clock = FrozenClock()
    p = PV.PmusWindowProvider(
        progress=PG.CohortProgress(d),
        session_auth=AUTH.SessionAuthorization(d),
        feed=NeverReadyFeed(),
        selector_fn=lambda ws, **kw: fake_pin(ws),
        now_fn=clock, sleep_fn=lambda s: None,
        window_starts=[BASE], ws_ready_timeout_s=1.0,
        ws_ready_poll_s=0.25)
    assert p() is None
    for w in range(SP.WINDOWS):
        assert L.window_state(window=w)["attempts_spent"] == 0, w


def test_a_wait_that_failed_never_opens_the_window(monkeypatch, tmp_path):
    """A timed-out wait must leave NO pinned window behind: a later call
    that found the feed healthy would otherwise inherit a window whose
    book was never proven."""
    arm(monkeypatch)
    d = str(tmp_path / "e1")
    os.makedirs(d, exist_ok=True)
    clock = FrozenClock()
    prog = PG.CohortProgress(d)
    p = PV.PmusWindowProvider(
        progress=prog, session_auth=AUTH.SessionAuthorization(d),
        feed=NeverReadyFeed(),
        selector_fn=lambda ws, **kw: fake_pin(ws),
        now_fn=clock, sleep_fn=lambda s: None,
        window_starts=[BASE], ws_ready_timeout_s=1.0,
        ws_ready_poll_s=0.25)
    assert p() is None
    assert p.current_pin is None
    assert p.current_window_index is None
    assert prog.windows_opened() == []
    assert [e for e in prog.events
            if e["event"] == PG.EV_WINDOW_OPENED] == []


def test_the_book_is_never_read_before_health_is_ok(monkeypatch,
                                                   tmp_path):
    """`NeverReadyFeed.book` RAISES. If anything read a book before its
    health was proven, these tests would error rather than refuse -- so
    the clean refusals above are themselves the proof."""
    arm(monkeypatch)
    d = str(tmp_path / "e1")
    os.makedirs(d, exist_ok=True)
    p = PV.PmusWindowProvider(
        progress=PG.CohortProgress(d),
        session_auth=AUTH.SessionAuthorization(d),
        feed=NeverReadyFeed(),
        selector_fn=lambda ws, **kw: fake_pin(ws),
        now_fn=FrozenClock(), sleep_fn=lambda s: None,
        window_starts=[BASE], ws_ready_timeout_s=1.0,
        ws_ready_poll_s=0.25)
    assert p() is None            # a raise here would be the regression


# ============ 6. THE FEED IS STARTED ONCE, NOT PER WINDOW ==============

def test_the_feed_is_started_not_rebuilt_per_window(monkeypatch,
                                                    tmp_path):
    """One AuthBook, reused. A feed rebuilt per window would resubscribe
    twelve times and pay the readiness race twelve times over."""
    arm(monkeypatch)
    d = str(tmp_path / "e1")
    os.makedirs(d, exist_ok=True)
    clock = FrozenClock()
    feed = ReadyAfterFeed(after=0, clock=clock)
    starts = {"n": 0}
    real_start = feed.start

    def counting_start():
        starts["n"] += 1
        return real_start()
    feed.start = counting_start

    p = PV.PmusWindowProvider(
        progress=PG.CohortProgress(d),
        session_auth=AUTH.SessionAuthorization(d),
        feed=feed, selector_fn=lambda ws, **kw: fake_pin(ws),
        now_fn=clock, sleep_fn=lambda s: None,
        window_starts=[BASE, BASE + W])
    assert p() is not None
    first = p.feed
    clock.t = BASE + W + 10
    assert p() is not None
    assert p.feed is first, "the feed was rebuilt between windows"
    # `start()` is idempotent, so calling it per window is fine; what
    # must not change is the OBJECT
    assert feed.started is True
