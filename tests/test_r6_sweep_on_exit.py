"""R6 orphan-order regression: the sweep runs on EVERY exit path.

THE FAILURE THIS FILE EXISTS FOR. R6, 2026-09-29 1815z. A
`MandateRefusal(max_spend_per_market_exhausted)` was raised inside
`native_work_window`'s tick loop while order `CSWG9ZB98YCA` was resting at
the venue. `mp.sweep_all()` sat on the straight-line path AFTER the loop, so
the exception went straight past it. `probe.close_out()` in run()'s own
`finally` could not compensate -- it cancels what it finds in
`store.live_orders_for_session(...)` and the native lane holds no
InventoryStore -- so it reported `cancelled: []`. The order was re-adopted
from the venue with `session_id None`, rested UNMANAGED for 12.5 minutes on
a market no process was watching, and filled at the close.

It made money that time. That is not the same as being safe.

These tests assert the MECHANISM, not the outcome: that sweep_all is
reached, that a tracked resting order cannot be left silently unmanaged, and
that a sweep which cannot confirm terminality fails CLOSED rather than
looking like a clean shutdown.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D

import pytest

from src import l5_session_journal as JN
import scripts.l5_pmus_live_oneshot as EP


MARKET = "cpc-btc-updown-15m-2026-09-29-1815z"


class MandateRefusal(Exception):
    """Stands in for pmus_live_mandate.MandateRefusal, same shape."""


class Eng:
    clip = D("10")
    locked_at = None
    lock_via = None
    lock_floor = None


class FakeMP:
    """A money path whose only job is to record what happened to it."""

    def __init__(self, *, raise_on_tick=None, sweep_raises=None,
                 resting=("CSWG9ZB98YCA",)):
        self.eng = Eng()
        self.feed = None
        self.submitted = []
        self.lock_transactions = []
        self.remainders = []
        self.ws_observer = None
        self.decision_shadow = None
        self._raise_on_tick = raise_on_tick
        self._sweep_raises = sweep_raises
        #: what the venue would still be holding if nobody swept.
        self.registry_open = list(resting)
        self.sweep_calls = 0
        self.ticks = 0

    def on_book_state(self, book, ladder):
        self.ticks += 1
        if self._raise_on_tick is not None and self.ticks >= self._raise_on_tick:
            raise self._raise_on_tick_exc()
        return {"submitted": [], "verdict": "NO_INTENT"}

    def _raise_on_tick_exc(self):
        return self._exc

    def sweep_all(self, *, rounds=3):
        self.sweep_calls += 1
        if self._sweep_raises is not None:
            raise self._sweep_raises
        cancelled = [{"oid": o, "state": "CANCELLED_NO_FILL"}
                     for o in self.registry_open]
        self.registry_open = []
        return {"cancelled": cancelled, "open_orders_after": 0,
                "nonterminal_after": 0, "clean": True}


class Feed:
    ladder = {"bids": [["0.60", "500"], ["0.59", "250"]],
              "offers": [["0.62", "400"]]}

    def __init__(self, ticks=3):
        self.n = 0
        self._ticks = ticks

    def fresh(self):
        return True

    def age_s(self):
        return 0.01

    def latest(self):
        self.n += 1
        return {"state": "MARKET_STATE_OPEN",
                "t": "2026-09-29T18:17:%02dZ" % min(self.n + 20, 59),
                "bid": "0.60", "ask": "0.62"}


class Probe:
    market = MARKET

    def __init__(self):
        self.tp = None
        self.native = None


def _run(mp, journal_path, *, deadline_ticks=3):
    journal = JN.Journal(str(journal_path))
    t = {"now": 0.0}

    def clock():
        t["now"] += 1.0
        return t["now"]

    return EP.native_work_window(
        Probe(), Feed(), float(deadline_ticks) * 2.0, journal=journal,
        clock=clock, sleep=lambda *_a, **_k: None, money_path=mp)


def _kinds(path):
    return [json.loads(l)["kind"]
            for l in open(str(path)).read().splitlines() if l.strip()]


def _rows(path, kind):
    out = []
    for l in open(str(path)).read().splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("kind") == kind:
            out.append(r)
    return out


# ------------------------------------------------ THE EXACT R6 FAILURE

def test_mandate_refusal_mid_loop_still_sweeps_the_resting_order(tmp_path):
    """THE R6 CASE. Order resting, mandate refuses mid-loop, shutdown.

    Before the fix: sweep_calls == 0 and CSWG9ZB98YCA stayed at the venue.
    """
    mp = FakeMP(raise_on_tick=2)
    mp._exc = MandateRefusal("max_spend_per_market_exhausted")
    j = tmp_path / "j.jsonl"

    with pytest.raises(MandateRefusal):
        _run(mp, j)

    assert mp.sweep_calls == 1, (
        "the R6 defect: a mandate refusal walked straight past sweep_all")
    assert mp.registry_open == [], (
        "CSWG9ZB98YCA was left resting at the venue, unmanaged")


def test_the_refusal_itself_is_not_swallowed(tmp_path):
    """The sweep must not turn a refusal into a silent success.

    run()'s own handler is what journals L5_PROBE_ERROR and spends the
    authorization; a finally that ate the exception would skip all of it.
    """
    mp = FakeMP(raise_on_tick=2)
    mp._exc = MandateRefusal("max_spend_per_market_exhausted")
    with pytest.raises(MandateRefusal, match="max_spend_per_market_exhausted"):
        _run(mp, tmp_path / "j.jsonl")


def test_window_done_is_journalled_even_on_the_refusal_path(tmp_path):
    """R4's crash-free window reported zero. A window that dies must not
    also lose its record of what it swept."""
    mp = FakeMP(raise_on_tick=2)
    mp._exc = MandateRefusal("max_spend_per_market_exhausted")
    j = tmp_path / "j.jsonl"
    with pytest.raises(MandateRefusal):
        _run(mp, j)
    assert "native_window_done" in _kinds(j)
    done = _rows(j, "native_window_done")[-1]
    assert done["sweep"]["clean"] is True
    assert done["sweep"]["open_orders_after"] == 0


# ------------------------------------------------ OTHER EXIT PATHS

def test_an_arbitrary_exception_also_sweeps(tmp_path):
    """Not special-cased to MandateRefusal. Any way out sweeps."""
    mp = FakeMP(raise_on_tick=2)
    mp._exc = RuntimeError("socket died")
    with pytest.raises(RuntimeError):
        _run(mp, tmp_path / "j.jsonl")
    assert mp.sweep_calls == 1
    assert mp.registry_open == []


def test_normal_exit_sweeps_exactly_once(tmp_path):
    """The fix must not double-sweep the ordinary path."""
    mp = FakeMP()
    out = _run(mp, tmp_path / "j.jsonl")
    assert mp.sweep_calls == 1, "normal exit swept %d times" % mp.sweep_calls
    assert out is mp
    assert mp.registry_open == []


def test_keyboard_interrupt_sweeps_and_still_propagates(tmp_path):
    mp = FakeMP(raise_on_tick=2)
    mp._exc = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        _run(mp, tmp_path / "j.jsonl")
    assert mp.sweep_calls == 1


# ------------------------------------------------ FAIL-CLOSED ON A BAD SWEEP

def test_a_failing_sweep_fails_closed_and_says_so(tmp_path):
    """If the venue cannot confirm terminality, that is NOT a clean close.

    The record must say the orders may still be resting, rather than
    reporting an empty, tidy sweep.
    """
    mp = FakeMP(sweep_raises=RuntimeError("venue 503"))
    j = tmp_path / "j.jsonl"
    _run(mp, j)

    failed = _rows(j, "native_sweep_failed")
    assert failed, "a sweep that raised was not recorded"
    assert failed[-1]["swept"] is False
    assert "NOT confirmed" in failed[-1]["labels"]

    done = _rows(j, "native_window_done")[-1]
    assert done["sweep"]["swept"] is False, (
        "a failed sweep must not be reported as a clean one")
    assert mp.registry_open == ["CSWG9ZB98YCA"], (
        "the order really is still out there; the record must not pretend "
        "otherwise")


def test_a_failing_sweep_does_not_mask_the_original_exception(tmp_path):
    """The refusal is the diagnosis. A secondary sweep error must not
    replace it."""
    mp = FakeMP(raise_on_tick=2, sweep_raises=RuntimeError("venue 503"))
    mp._exc = MandateRefusal("max_spend_per_market_exhausted")
    with pytest.raises(MandateRefusal, match="max_spend_per_market_exhausted"):
        _run(mp, tmp_path / "j.jsonl")


# ------------------------------------------------ DEPTH PERSISTENCE (OBSERVATIONAL)

def test_the_depth_the_decision_saw_is_persisted(tmp_path):
    """R6's counterfactual was unanswerable because the full-depth frames
    were seen at 14ms and thrown away."""
    mp = FakeMP()
    j = tmp_path / "j.jsonl"
    _run(mp, j)
    rows = _rows(j, "native_book_depth")
    assert rows, "no depth was persisted"
    assert rows[0]["bids"] == [["0.60", "500"], ["0.59", "250"]]
    assert rows[0]["offers"] == [["0.62", "400"]]


def test_depth_is_never_synthesized_when_the_venue_showed_none(tmp_path):
    class NoDepthFeed(Feed):
        ladder = None

    mp = FakeMP()
    journal = JN.Journal(str(tmp_path / "j.jsonl"))
    t = {"now": 0.0}
    EP.native_work_window(
        Probe(), NoDepthFeed(), 6.0, journal=journal,
        clock=lambda: t.__setitem__("now", t["now"] + 1.0) or t["now"],
        sleep=lambda *_a, **_k: None, money_path=mp)
    assert not _rows(tmp_path / "j.jsonl", "native_book_depth"), (
        "a ladder was invented for a book the venue never sent")


def test_depth_recording_cannot_break_the_window(tmp_path):
    class ExplodingLadder:
        @property
        def ladder(self):
            raise RuntimeError("depth blew up")

        def fresh(self):
            return True

        def age_s(self):
            return 0.01

        def latest(self):
            return {"state": "MARKET_STATE_OPEN", "t": "2026-09-29T18:20:00Z",
                    "bid": "0.60", "ask": "0.62"}

    mp = FakeMP()
    journal = JN.Journal(str(tmp_path / "j.jsonl"))
    t = {"now": 0.0}
    # NOTE, measured rather than assumed: `getattr(feed, "ladder", None)`
    # does NOT swallow an exception raised INSIDE the property -- the
    # default only covers a missing attribute. That was already true before
    # this fix, on the same call, so it is pre-existing behaviour and not
    # something the depth journalling introduced.
    #
    # What the fix changes is the consequence. A feed that explodes now
    # takes the same route as a mandate refusal: out through the finally,
    # sweeping on the way.
    with pytest.raises(RuntimeError, match="depth blew up"):
        EP.native_work_window(
            Probe(), ExplodingLadder(), 4.0, journal=journal,
            clock=lambda: t.__setitem__("now", t["now"] + 1.0) or t["now"],
            sleep=lambda *_a, **_k: None, money_path=mp)
    assert mp.sweep_calls == 1, (
        "a feed-side failure left orders resting")
    assert mp.registry_open == []


# ------------------------------------------------ STRUCTURAL

def test_sweep_all_is_called_from_a_finally(tmp_path):
    """Guards the control flow itself, so the straight-line form cannot
    come back without this test going red."""
    src = open(EP.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "native_work_window")

    in_finally = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.finalbody:
            for sub in ast.walk(stmt):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "sweep_all"):
                    in_finally.append(sub)
    assert in_finally, "sweep_all() is not reached from a finally block"

    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "sweep_all"]
    assert len(calls) == len(in_finally), (
        "a sweep_all() call exists outside the finally; that is the R6 shape")
