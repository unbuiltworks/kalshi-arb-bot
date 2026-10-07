"""THE DEFECT THAT KILLED THE FIRST LIVE COHORT, and its fix, from main().

The live run (deployment b23699af) selected 13 BTC15m windows, authorized
all 13, and refused all 13 inside 400ms with

    WEBSOCKET_NOT_PROVEN: BOTH_TOKENS_NOT_STREAMING;
    TOKEN_NEVER_SEEN:token_up; TOKEN_NEVER_SEEN:token_down

then stopped at NO_CANDIDATE_WINDOW_COULD_BE_OPENED. A websocket cannot
connect, subscribe and deliver a first frame in the microseconds before the
single health call, so the proof could never be satisfied and the cohort
could not have opened a window in ANY market condition. It failed CLOSED,
and it placed nothing -- but a transport that had not initialised was
recorded as thirteen independent market refusals.

These tests drive the REAL `runner.main()` on its ARMED branch with a fake
transport, and they are written so the suite never waits the production
20s bound: the readiness bound is injected per test, and `sleep_fn`
advances the injected clock, so the SEQUENCING is real while the waiting is
not.

NO REAL POST IS POSSIBLE HERE. Fake transport, fake selector, fake feed,
fake credentials, `PMUS_E1_*` set only inside `monkeypatch`.

Run:
    PYTHONPATH=. python3 -m pytest \
        analysis/l5/e1_cohort/test_e1_ws_readiness.py -q
"""
from __future__ import annotations

import os

import pytest

from analysis.l5.e1_cohort import authorization as AUTH
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import progress as PG
from analysis.l5.e1_cohort import provider as PV
from analysis.l5.e1_cohort import recorder as R
from analysis.l5.e1_cohort import runner as RUN
from analysis.l5.e1_cohort import selector as SEL

# the proven harness, reused rather than re-invented
from analysis.l5.e1_cohort.test_e1_live_path import (
    BASE, Clock, FakeTransport, arm, fake_book, fake_pin, fake_selector)

W = SEL.WINDOW_S

READY_TIMEOUT_S = 2.0        # realistic shape, 2s not 20s
READY_POLL_S = 0.25          # the production poll interval


# --------------------------------------------------------------- the feed

class StagedFeed:
    """A feed that becomes ready on a SCHEDULE, like a real socket.

    `stages` is one health dict per poll; the last one repeats forever, so
    "never ready" is just a last stage that is not ready.
    """

    def __init__(self, stages):
        self.stages = list(stages)
        self.polls = 0

    def __call__(self, pin):
        i = min(self.polls, len(self.stages) - 1)
        self.polls += 1
        return self.stages[i](pin)


def _h(pin, *, clock, frames, up=False, down=False, up_age=0.0,
       down_age=0.0, market=None, up_token=None, down_token=None):
    """One health report, exactly the shape `live_book.health()` returns."""
    seen = {}
    if up:
        seen[str(up_token or pin["token_up"])] = clock() - up_age
    if down:
        seen[str(down_token or pin["token_down"])] = clock() - down_age
    return {"market": market or pin["slug"],
            "timing_authority": "websocket_strict",
            "both_tokens_streaming": bool(up and down),
            "tokens_last_seen_unix": seen,
            "tokens_last_book_update_unix": dict(seen),
            "tokens_last_frame_unix": dict(seen),
            "frames": frames,
            "frames_applied": frames,
            "frames_refused_other_market": 0,
            "frames_unrecognised": 0,
            "ws_url": PV.WS.DEFAULT_WS_URL,
            "derived_complement": False,
            "rest_role": "rest_same_second_reachability"}


def connecting(clock):
    """Stage 0: socket opening. NO frames at all."""
    return lambda pin: _h(pin, clock=clock, frames=0)


def up_only(clock):
    return lambda pin: _h(pin, clock=clock, frames=6, up=True)


def both_fresh(clock):
    return lambda pin: _h(pin, clock=clock, frames=42, up=True, down=True)


def both_stale(clock):
    age = PV.MAX_TOKEN_SILENCE_S + 5.0
    return lambda pin: _h(pin, clock=clock, frames=42, up=True, down=True,
                          up_age=age, down_age=age)


def wrong_market(clock):
    return lambda pin: _h(pin, clock=clock, frames=42, up=True, down=True,
                          market="btc-updown-15m-999999999")


def wrong_tokens(clock):
    return lambda pin: _h(pin, clock=clock, frames=42, up=True, down=True,
                          up_token="not-our-up", down_token="not-our-down")


# -------------------------------------------------------------- the driver

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    yield


def drive(monkeypatch, tmp_path, *, feed, starts, clock=None,
          timeout_s=READY_TIMEOUT_S):
    """The REAL armed `main()`, with a staged feed and a fake transport."""
    arm(monkeypatch, "e1-readiness-test")
    clock = clock or Clock(BASE + 10)
    captured = {}

    # the wait is real sequencing; only the CLOCK is fast. `sleep_fn`
    # advances it, so the timeout is reached in zero wall-clock time.
    def sleep_fn(dt):
        clock.advance(dt)

    def provider_factory(*, progress, session_auth, recorder):
        p = PV.LiveWindowProvider(
            progress=progress, session_auth=session_auth,
            book_fn=lambda pin: fake_book(pin, now_fn=clock),
            health_fn=feed,
            recorder=recorder, selector_fn=fake_selector,
            now_fn=clock, window_starts=list(starts),
            sleep_fn=sleep_fn,
            ws_ready_timeout_s=timeout_s,
            ws_ready_poll_s=READY_POLL_S)
        captured["provider"] = p
        captured["progress"] = progress
        return p

    rc = RUN.main(
        [], provider_factory=provider_factory,
        progress_factory=lambda d: PG.CohortProgress(d),
        auth_factory=lambda d: AUTH.SessionAuthorization(d),
        recorder_factory=lambda d: R.Recorder(os.path.join(d, "tape")),
        transport_factory=FakeTransport)
    captured["rc"] = rc
    captured["clock"] = clock
    captured["latch_dir"] = str(tmp_path / "e1_cohort")
    return captured


def slots_on_disk(latch_dir):
    """Every latch slot file, at any depth. Spending one leaves a file."""
    out = []
    for d, _ds, fs in os.walk(latch_dir):
        for f in fs:
            if f.endswith(".submitted") or "slot" in f:
                out.append(os.path.join(d, f))
    return out


# ------------------------------------------------- THE HEADLINE TEST
def test_delayed_startup_then_up_then_down_reaches_the_fake_transport(
        monkeypatch, tmp_path):
    """The exact sequence the owner specified, through production main().

    delayed websocket startup -> UP frame -> DOWN frame -> both-token proof
    -> eligible -> fake order reaches the fake transport.
    """
    clock = Clock(BASE + 10)
    feed = StagedFeed([connecting(clock),   # poll 1: socket opening
                       connecting(clock),   # poll 2: still nothing
                       up_only(clock),      # poll 3: UP arrives
                       both_fresh(clock)])  # poll 4: DOWN arrives
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert got["rc"] == 0
    # it WAITED rather than refusing on the first look
    assert feed.polls >= 4, "readiness did not wait for the staged feed"
    # and an order actually reached the transport
    assert FakeTransport.posts, (
        "no POST reached the fake transport: readiness never succeeded")
    first = FakeTransport.posts[0]
    assert first["slug"] == "btc-updown-15m-%d" % BASE
    assert first["post_only"] is True, "first leg must stay maker"


# --------------------------------------------- the four failure modes
def test_no_frames_times_out_with_connect_failure_and_zero_transport(
        monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    feed = StagedFeed([connecting(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts, "a POST escaped an unready feed"
    refusals = got["provider"].refusals
    assert refusals, "no refusal recorded"
    why = refusals[-1]["refused"]
    assert PV.WS_READY_CONNECT_FAILED in why, why
    assert refusals[-1]["is_a_feed_startup_failure"] is True
    assert refusals[-1]["is_a_market_refusal"] is False


def test_up_token_only_times_out_naming_down_and_zero_transport(
        monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    feed = StagedFeed([up_only(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts
    why = got["provider"].refusals[-1]["refused"]
    assert PV.WS_READY_DOWN_NEVER_SEEN in why, why


def test_down_token_only_times_out_naming_up(monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    feed = StagedFeed([
        lambda pin: _h(pin, clock=clock, frames=6, down=True)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts
    why = got["provider"].refusals[-1]["refused"]
    assert PV.WS_READY_UP_NEVER_SEEN in why, why


def test_stale_frames_never_satisfy_readiness(monkeypatch, tmp_path):
    """Both tokens seen, both older than the EXISTING freshness bound."""
    clock = Clock(BASE + 10)
    feed = StagedFeed([both_stale(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts, "a stale book produced an order"
    why = got["provider"].refusals[-1]["refused"]
    assert PV.WS_READY_STALE in why, why


def test_frames_for_another_market_never_satisfy_readiness(
        monkeypatch, tmp_path):
    clock = Clock(BASE + 10)
    feed = StagedFeed([wrong_market(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts, "another market's frames opened a window"
    assert got["provider"].refusals, "wrong market was not refused"


def test_frames_for_other_token_ids_never_satisfy_readiness(
        monkeypatch, tmp_path):
    """`both_tokens_streaming` is true, but not for OUR two token ids."""
    clock = Clock(BASE + 10)
    feed = StagedFeed([wrong_tokens(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts, "wrong token ids opened a window"
    why = got["provider"].refusals[-1]["refused"]
    assert (PV.WS_READY_UP_NEVER_SEEN in why
            or PV.WS_READY_DOWN_NEVER_SEEN in why), why


# -------------------------------------- the ms-sweep regression itself
def test_a_feed_failure_does_not_become_thirteen_market_refusals(
        monkeypatch, tmp_path):
    """The live failure mode, asserted as a bound on the sweep.

    13 candidate starts, a feed that never initialises. Before the fix this
    produced 13 WEBSOCKET_NOT_PROVEN refusals in milliseconds and a
    NO_CANDIDATE_WINDOW_COULD_BE_OPENED. Now the first feed failure is
    terminal for the sweep and says so.
    """
    clock = Clock(BASE + 10)
    starts = [BASE + i * W for i in range(13)]
    feed = StagedFeed([connecting(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=starts, clock=clock)

    refusals = got["provider"].refusals
    feed_failures = [r for r in refusals
                     if "WEBSOCKET_FEED_NOT_READY" in r["refused"]]
    market_refusals = [r for r in refusals
                       if "WEBSOCKET_NOT_PROVEN" in r["refused"]]

    assert len(feed_failures) == 1, (
        "expected ONE terminal feed failure, got %d refusals: %r"
        % (len(refusals), [r["refused"][:60] for r in refusals]))
    assert not market_refusals, (
        "a transport failure was recorded as a market refusal: %r"
        % market_refusals)
    assert feed_failures[0]["candidates_not_enumerated"] is True
    assert not FakeTransport.posts


def test_no_latch_slot_is_spent_while_waiting_for_readiness(
        monkeypatch, tmp_path):
    """Readiness runs before any latch. A timeout must leave it untouched."""
    clock = Clock(BASE + 10)
    feed = StagedFeed([connecting(clock)])
    got = drive(monkeypatch, tmp_path, feed=feed, starts=[BASE], clock=clock)

    assert not FakeTransport.posts
    assert slots_on_disk(got["latch_dir"]) == [], (
        "a latch slot was spent while the feed was not even ready")


# ------------------------------------------------------- the bound itself
def test_the_wait_is_bounded_even_against_a_frozen_clock():
    """A wall-clock deadline alone is not a bound.

    `now_fn` is injected, so a deterministic caller can supply a clock that
    never advances. This hung the suite during development: the deadline was
    never reached and the loop spun forever. The poll count bounds it
    independently, so termination does not depend on the clock at all.
    """
    pin = fake_pin(BASE)
    frozen = Clock(BASE + 10)        # never advanced

    calls = {"n": 0}

    def health(_pin):
        calls["n"] += 1
        return _h(_pin, clock=frozen, frames=0)

    with pytest.raises(PV.FeedNotReady):
        PV.await_ws_both_tokens_ready(
            health, pin, now_fn=frozen, sleep_fn=lambda _dt: None,
            timeout_s=1.0, poll_s=0.1)

    # bounded by polls, and it really did poll more than once
    assert 1 < calls["n"] <= 20, calls["n"]


def test_production_readiness_bound_is_what_we_shipped():
    """Guard the shipped values; the suite shrinks them per test."""
    import analysis.l5.e1_cohort.conftest as C
    assert C.PRODUCTION_TIMEOUT_S == 20.0
    assert C.PRODUCTION_POLL_S == 0.25


def test_readiness_success_carries_its_own_proof():
    """A success says it waited, and stays inside the existing contract."""
    clock = Clock(BASE + 10)
    pin = fake_pin(BASE)
    feed = StagedFeed([connecting(clock), up_only(clock), both_fresh(clock)])

    proof = PV.await_ws_both_tokens_ready(
        feed, pin, now_fn=clock, sleep_fn=lambda dt: clock.advance(dt),
        timeout_s=READY_TIMEOUT_S, poll_s=READY_POLL_S)

    assert proof["ok"] is True
    assert proof["both_tokens_streaming"] is True
    assert proof["proven_for_the_selected_market"] is True
    assert proof["rest_is_sufficient_timing_authority"] is False
    assert proof["readiness"]["waited_for_both_tokens"] is True
    assert proof["readiness"]["bounded"] is True
    assert proof["readiness"]["polls"] >= 3
