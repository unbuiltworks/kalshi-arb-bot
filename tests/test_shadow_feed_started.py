"""The shadow runner must START its book subscription.

THE DEFECT THIS PINS. `run_forever` constructed an `AuthenticatedBookFeed`
and never called `start()`. The object existed, `latest()` answered None for
the whole window, and the window was reported with `ticks: 0` -- which reads
as a quiet market and was in fact a collector that never connected. Every
zero-tick shadow window recorded before the fix is that defect, not evidence
about the market.

These tests fail if the start call is removed again.
"""
from __future__ import annotations

import json

import scripts.l5_r7_shadow_runner as R


class FakeJournal:
    def __init__(self):
        self.rows = []

    def write(self, kind, payload):
        self.rows.append((kind, payload))

    def kinds(self):
        return [k for k, _ in self.rows]

    def of(self, kind):
        return [p for k, p in self.rows if k == kind]


class Feed:
    """A feed that answers frames only after start(), like the real one."""

    def __init__(self, ready=True, frames=3):
        self.started = False
        self.waited = False
        self._ready = ready
        self._n = frames
        self._i = 0

    def start(self):
        self.started = True

    def wait_ready(self, sleep=None):
        self.waited = True
        return self._ready and self.started

    def health(self):
        return {"frames": self._n if self.started else 0,
                "connected": self.started}

    def latest(self):
        if not self.started:
            return None                      # the exact zero-tick failure
        if self._i >= self._n:
            return None
        self._i += 1
        return {"t": float(self._i), "bid": "0.40", "ask": "0.42",
                "state": "MARKET_STATE_OPEN",
                "shares_traded_delta_lower_bound": "0"}

    def stop(self):
        pass


def _run(feed, *, windows=1):
    jn = FakeJournal()
    now = [1_000_000.0]

    def clock():
        now[0] += 1.0
        return now[0]

    # a clock that advances past the window end terminates shadow_window
    R.run_forever(clock=clock, sleep=lambda *_a, **_k: None,
                  feed_factory=lambda slug, dl: feed, journal=jn,
                  max_windows=windows)
    return jn


def test_feed_is_started_before_the_window_runs():
    f = Feed()
    _run(f)
    assert f.started, "run_forever never called start(): ticks would be 0"


def test_readiness_is_established_and_journalled():
    f = Feed()
    jn = _run(f)
    assert "shadow_book_feed" in jn.kinds()
    row = jn.of("shadow_book_feed")[0]
    assert row["ready"] is True
    assert f.waited, "readiness was assumed rather than established"


def test_a_feed_that_never_becomes_ready_is_a_failure_not_a_quiet_window():
    f = Feed(ready=False)
    jn = _run(f)
    assert jn.of("shadow_book_feed")[0]["ready"] is False
    fails = jn.of("shadow_window_failed")
    assert fails and fails[0]["error"] == "FeedNotReady"
    # and no window was reported as having run
    assert "shadow_window_done" not in jn.kinds()


def test_frames_actually_reach_the_engine_once_started():
    f = Feed(frames=4)
    jn = _run(f)
    done = jn.of("shadow_window_done")
    assert done, "the window did not complete"
    assert done[0]["ticks"] > 0, "frames did not reach the decision loop"
    json.dumps(done[0], default=str)          # the row must be serialisable


def test_the_shadow_image_installs_the_feeds_third_party_dependency():
    """The readiness check turned the silent zero-tick window into a report,
    and the report named the real cause: the image had no dependencies, so
    `websockets` was missing and the subscription could never connect. The
    COPY-closure test cannot see this -- it walks first-party modules only."""
    df = open("deploy/l5_shadow_signature/Dockerfile").read()
    assert "pip install" in df, "the image installs nothing"
    assert "websockets" in df, (
        "l5_live_book_feed imports websockets at connect time; without it "
        "every shadow window reports ticks: 0")
