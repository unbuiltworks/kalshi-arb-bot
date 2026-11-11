"""A window must not leave the universe before its close is recorded.

Measured on 2026-09-27: five of sixteen BTC15m windows (2015z, 2100z, 2130z,
2145z, 2200z -- the last three consecutively) have NO terminal row on the tape,
so the preregistered completeness rule excludes them and the evidence is gone.
The cause is a race, not bad luck: `series_slugs()` drops a `closed` event, so a
window leaves the universe at the first reslate after its boundary (0 to 45s
later, at random), while the venue publishes the terminal bbo state 6s to 30s+
after that boundary. Whichever lands first decides whether the close is seen.

These tests drive `run()` with a fake clock and a fake gateway, reproduce the
loss under the old behaviour, and pin the drain that fixes it. No network.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_book_tape as T                      # noqa: E402

OPEN, CLOSED = "MARKET_STATE_OPEN", "MARKET_STATE_CLOSED"
A, B = "win-a", "win-b"


class Venue:
    """Two consecutive windows. A closes, and its terminal state appears late."""

    def __init__(self, *, close_at, terminal_at, reslate_at):
        self.close_at, self.terminal_at, self.reslate_at = (
            close_at, terminal_at, reslate_at)
        self.now = 0.0

    def universe(self):
        # A leaves the universe at reslate_at, exactly as a `closed` event does
        return [A, B] if self.now < self.reslate_at else [B]

    def snapshot(self, slug):
        st = OPEN
        if slug == A and self.now >= self.terminal_at:
            st = CLOSED
        return {"t": "T%.1f" % self.now, "slug": slug, "kind": "bbo",
                "state": st, "bid": "0.5000", "ask": "0.5100",
                "last": "0.5000", "shares_traded": "1",
                "one_sided": False, "two_sided": True}


def drive(monkeypatch, tmp_path, venue, *, drain_s, steps=200, interval=5.0,
          reslate_s=5.0):
    # A tight reslate grid so the race is exercised deterministically rather
    # than depending on where the 45s production grid happens to land.
    monkeypatch.setattr(T, "series_slugs", lambda series, **k: venue.universe())
    monkeypatch.setattr(T, "snapshot", venue.snapshot)
    monkeypatch.setattr(T, "book_snapshot", lambda s: None)
    monkeypatch.setattr(T, "DRAIN_S", drain_s)
    monkeypatch.setattr(T, "SERIES_RESLATE_S", reslate_s)
    monkeypatch.setattr(T, "HEARTBEAT_S", 30.0)

    monkeypatch.setattr(T.time, "time", lambda: venue.now)

    def sleep(sec):
        venue.now += sec
    monkeypatch.setattr(T.time, "sleep", sleep)

    T.run(tmp_path, minutes=(steps * interval) / 60.0, interval_s=interval,
          series="btc-updown-15m")
    out = list(tmp_path.glob("*.jsonl"))
    assert len(out) == 1
    return [json.loads(x) for x in out[0].read_text().splitlines()]


def rows_for(tape, slug, kind="bbo"):
    return [r for r in tape if r.get("slug") == slug and r.get("kind") == kind]


@pytest.mark.parametrize("terminal_at,reslate_at", [
    (930.0, 905.0),     # the 2130z case: reslate 5s after close, terminal +30s
    (925.0, 901.0),
    (960.0, 900.0),     # terminal a full minute late
])
def test_the_close_is_recorded_even_when_the_slug_left_the_universe_first(
        monkeypatch, tmp_path, terminal_at, reslate_at):
    v = Venue(close_at=900.0, terminal_at=terminal_at, reslate_at=reslate_at)
    tape = drive(monkeypatch, tmp_path, v, drain_s=240.0)
    states = [r["state"] for r in rows_for(tape, A)]
    assert CLOSED in states, (
        "the terminal state was never recorded; the window would be excluded")


def test_without_the_drain_the_close_is_lost(monkeypatch, tmp_path):
    """Negative control: drain_s=0 reproduces the measured loss.

    Without this the test above could pass for reasons unrelated to the drain.
    """
    v = Venue(close_at=900.0, terminal_at=930.0, reslate_at=905.0)
    tape = drive(monkeypatch, tmp_path, v, drain_s=0.0)
    states = [r["state"] for r in rows_for(tape, A)]
    assert states, "A was never polled at all"
    assert CLOSED not in states


def test_the_drain_ends_once_the_terminal_state_is_seen(monkeypatch, tmp_path):
    v = Venue(close_at=900.0, terminal_at=930.0, reslate_at=905.0)
    tape = drive(monkeypatch, tmp_path, v, drain_s=240.0)
    ends = [r for r in tape if r.get("kind") == "drain_end" and r["slug"] == A]
    assert len(ends) == 1
    assert ends[0]["terminal_recorded"] is True
    # and nothing keeps polling A afterwards
    last_a = max(float(r["t"][1:]) for r in rows_for(tape, A))
    assert last_a <= 930.0 + 2 * 5.0


def test_a_drain_that_never_sees_a_close_gives_up_and_says_so(
        monkeypatch, tmp_path):
    """Bounded: a window the venue never closes must not be polled forever."""
    v = Venue(close_at=900.0, terminal_at=1e9, reslate_at=905.0)
    tape = drive(monkeypatch, tmp_path, v, drain_s=60.0)
    ends = [r for r in tape if r.get("kind") == "drain_end" and r["slug"] == A]
    assert len(ends) == 1
    assert ends[0]["terminal_recorded"] is False
    last_a = max(float(r["t"][1:]) for r in rows_for(tape, A))
    assert last_a < 905.0 + 60.0 + 2 * 5.0


def test_the_live_window_is_never_starved_by_a_drain(monkeypatch, tmp_path):
    """The drain adds coverage; it must not cost the window in progress."""
    v = Venue(close_at=900.0, terminal_at=930.0, reslate_at=905.0)
    tape = drive(monkeypatch, tmp_path, v, drain_s=240.0)
    b = rows_for(tape, B)
    assert b, "the live window produced no rows"
    ts = sorted(float(r["t"][1:]) for r in b)
    assert max(y - x for x, y in zip(ts, ts[1:])) <= T.HEARTBEAT_S + 10.0


def test_a_slug_is_not_polled_twice_in_one_cycle(monkeypatch, tmp_path):
    """A draining slug that is still in the universe must not be double-polled."""
    v = Venue(close_at=900.0, terminal_at=1e9, reslate_at=1e9)
    tape = drive(monkeypatch, tmp_path, v, drain_s=240.0, steps=40)
    for slug in (A, B):
        ts = [r["t"] for r in rows_for(tape, slug)]
        assert len(ts) == len(set(ts)), slug
