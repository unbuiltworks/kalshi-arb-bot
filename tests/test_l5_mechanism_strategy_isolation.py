"""A REAL Runner, a REAL feed, and the real tick entry points.

The wiring tests next door drive `mechanism_tick` against a recorder, which
is enough to pin which helpers it calls but NOT enough to prove the strategy
is unreachable: `poll` sat in that method through a live activation and those
tests stayed green because they enumerated the cancellers and never the
strategy.

So this file builds an ACTUAL `Runner` over a temp ledger, hands it an
ACTUAL L5 action feed with rows the strategy would act on, and drives the
real `tick` and `mechanism_tick`. The claim under test is behavioural:

    in mechanism mode, no code path reaches `handle` or `transport.submit`.

And it carries its OWN positive control -- the ordinary `tick` must reach
`handle` from the same feed -- so the file cannot pass against a runner that
has merely lost the ability to trade, which is the way a test like this
usually rots into uselessness.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from unittest import mock

import pytest

from src import l5_pmus_live_runner as R

SLUG = "btc-updown-15m-2026-10-18-1830z"


def action_rows():
    """Two L5 actions in the shape `poll` filters on and `handle` consumes."""
    return [
        {"slug": SLUG, "timestamp": 1792348457, "side": "BUY",
         "outcome": "Up", "price": "0.31", "size": "1.449274",
         "transactionHash": "0x" + "a" * 64, "conditionId": "0x" + "b" * 64},
        {"slug": SLUG, "timestamp": 1792348470, "side": "BUY",
         "outcome": "Down", "price": "0.6", "size": "20",
         "transactionHash": "0x" + "c" * 64, "conditionId": "0x" + "d" * 64},
    ]


class RecordingTransport:
    """Records any submit. Present but never legitimately usable here."""

    market_slug = "cpc-btc-updown-15m-2026-10-18-1830z"
    tick_size = D("0.01")
    quantity_increment = D("0.01")

    def __init__(self):
        self.armed = True
        self.submits = []

    def submit(self, intent):
        self.submits.append(dict(intent))
        return {"order": {"id": "DSHOULD_NEVER_HAPPEN"}}

    def cancel(self, oid, market_slug=None):
        return {"ok": True}

    def order(self, oid):
        return {"id": oid, "state": "ORDER_STATE_OPEN",
                "leavesQuantity": "0.01"}

    def disarm(self, why):
        self.armed = False


def growing_feed():
    """A feed that yields a NEW action each call.

    `poll` dedupes on action_key and treats its first read as history, so a
    feed repeating the same rows can never reach `handle` -- which would make
    the positive control below pass for the wrong reason.
    """
    state = {"n": 0}
    rows = action_rows()

    def feed():
        state["n"] += 1
        return rows[:min(state["n"], len(rows))]

    return feed, state


def build_runner(tmp_path, monkeypatch, feed=None):
    monkeypatch.delenv("L5_ORDER_REGISTRY_PORT", raising=False)
    book = mock.MagicMock()
    book.snapshot.return_value = {
        "bid": D("0.35"), "ask": D("0.36"), "stream_age_s": 0.02,
        "market_state": "MARKET_STATE_OPEN", "source": "authenticated_ws"}
    book.resub_due.return_value = []
    book.stall_due.return_value = False
    if feed is None:
        feed, _ = growing_feed()
    return R.Runner(root=str(tmp_path / "live"), book=book, feed=feed)


def install_recorders(runner, monkeypatch):
    """Guard the two things that must never happen.

    `handle` is watched on the instance. SUBMIT IS GUARDED ON THE CLASS, and
    that detail matters: `roll` builds a fresh `T.PmusTransport` on the first
    tick, so a recorder injected as `runner.transport` is discarded and any
    assertion about it is trivially true. An earlier draft of this file made
    exactly that mistake.
    """
    seen = {"handle": 0, "submit": 0}
    real_handle = runner.handle

    def handle(r, recv):
        seen["handle"] += 1
        return real_handle(r, recv)

    runner.handle = handle

    def submit(self, intent):
        seen["submit"] += 1
        raise AssertionError("transport.submit reached: %r" % (dict(intent),))

    monkeypatch.setattr(R.T.PmusTransport, "submit", submit, raising=True)
    return seen


class Mech:
    state = "RESTING"
    venue_order_id = None

    def __init__(self):
        self.steps = 0

    def step(self, now):
        self.steps += 1
        return {}


# ---------------------------------------------------- the negative claim

def test_mechanism_tick_never_reaches_handle_or_submit(tmp_path, monkeypatch):
    """THE REGRESSION. Many ticks, a live feed, nothing strategic runs."""
    runner = build_runner(tmp_path, monkeypatch)
    seen = install_recorders(runner, monkeypatch)
    mech = Mech()
    now = 1792348400.0
    for i in range(6):
        runner.mechanism_tick(now + i * 3.0, mech)
    assert seen["handle"] == 0, "the L5 strategy ran under mechanism mode"
    assert seen["submit"] == 0, "an order was built outside the mechanism"
    assert mech.steps == 6, "the mechanism itself must still be driven"


def test_mechanism_tick_never_polls_the_action_feed_at_all(tmp_path,
                                                           monkeypatch):
    """Not even the read: the mechanism's order is predetermined."""
    calls = {"n": 0}

    def feed():
        calls["n"] += 1
        return action_rows()

    monkeypatch.delenv("L5_ORDER_REGISTRY_PORT", raising=False)
    book = mock.MagicMock()
    book.snapshot.return_value = {"bid": D("0.35"), "ask": D("0.36")}
    book.resub_due.return_value = []
    book.stall_due.return_value = False
    runner = R.Runner(root=str(tmp_path / "live"), book=book, feed=feed)
    install_recorders(runner, monkeypatch)
    for i in range(4):
        runner.mechanism_tick(1792348400.0 + i * 3.0, Mech())
    assert calls["n"] == 0, "the action feed was polled in mechanism mode"


def test_a_venue_cooloff_in_mechanism_mode_still_reaches_nothing(
        tmp_path, monkeypatch):
    """The cool-off branch had its own poll() call -- that was the second
    copy of the bug."""
    runner = build_runner(tmp_path, monkeypatch)
    seen = install_recorders(runner, monkeypatch)
    monkeypatch.setattr(runner, "venue_cooling", lambda: 5.0)
    for i in range(4):
        runner.mechanism_tick(1792348400.0 + i * 3.0, Mech())
    assert seen["handle"] == 0
    assert seen["submit"] == 0


def test_a_halt_in_mechanism_mode_still_reaches_nothing(tmp_path,
                                                        monkeypatch):
    runner = build_runner(tmp_path, monkeypatch)
    seen = install_recorders(runner, monkeypatch)
    monkeypatch.setattr(runner, "halted", lambda: True)
    monkeypatch.setattr(R.LX, "deadman", lambda *a, **kw: {})
    for i in range(3):
        runner.mechanism_tick(1792348400.0 + i * 3.0, Mech())
    assert seen["handle"] == 0
    assert seen["submit"] == 0
    # the transport under test is whatever `roll` built, not an injected one
    assert getattr(runner.transport, "armed", False) is False, \
        "a halt must leave the live transport disarmed"


# ------------------------------------------------- the positive control

def test_the_ordinary_tick_DOES_reach_handle_from_the_same_feed(
        tmp_path, monkeypatch):
    """Without this the file above would pass against a broken runner.

    `poll` seeds on its first read -- the first batch is history, not signal
    -- so the strategy is reached on the SECOND tick.
    """
    runner = build_runner(tmp_path, monkeypatch)
    seen = install_recorders(runner, monkeypatch)
    now = 1792348400.0
    runner.tick(now)            # seeds
    runner.tick(now + 3.0)      # acts on the newly arrived action
    assert seen["handle"] > 0, (
        "the ordinary tick no longer reaches the strategy, so the isolation "
        "tests above prove nothing")


def test_the_two_ticks_differ_only_by_the_strategy_being_absent(
        tmp_path, monkeypatch):
    """Same runner, same feed, both entry points: one polls, one does not."""
    runner = build_runner(tmp_path, monkeypatch)
    seen = install_recorders(runner, monkeypatch)
    now = 1792348400.0
    runner.mechanism_tick(now, Mech())
    runner.mechanism_tick(now + 3.0, Mech())
    assert seen["handle"] == 0
    runner.tick(now + 6.0)
    runner.tick(now + 9.0)
    assert seen["handle"] > 0


# ------------------------------------- the mechanism is the only submitter

def test_the_mechanism_is_the_only_submit_call_site_reachable_in_this_mode():
    """Source-level companion to the behavioural tests: exactly two call
    sites exist in src/, and in mechanism mode only the mechanism's is
    reachable because mechanism_tick never calls handle."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    sites = []
    for py in sorted((root / "src").rglob("*.py")):
        for n, line in enumerate(py.read_text().splitlines(), 1):
            if re.search(r"\btransport\.submit\(", line):
                sites.append(py.name)
    assert sorted(set(sites)) == ["l5_cancel_mechanism.py",
                                 "l5_live_execution.py"], sites


def test_mechanism_tick_does_not_mention_poll(tmp_path):
    import inspect
    src = inspect.getsource(R.Runner.mechanism_tick)
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "self.poll(" not in code
    assert "self.handle(" not in code
