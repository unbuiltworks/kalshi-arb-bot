"""A refused OPENING is named, not only counted.

THE BLINDNESS THIS REMOVES. `controller_open:*` was a counter and nothing
more, so the 1315z-1400z campaign could report
`controller_open:PAIR_COST_ABOVE_CEILING` 683 times -- its largest opening
refusal -- and could not say whether one of those 683 gave up a pair worth
having. The executor's refusal dict carries `effective_pair_cost` and
`ceiling` and neither ever reached the tape; the completion layer has
carried `L5_MISSED_PAIR` since the 1400z work, the opening layer did not.

WHAT THE RECONSTRUCTION FOUND, over the 501 `L5_CONTROLLER_OPEN_TRIGGER`
books preserved for 1400z/1415z/1430z. Both maker legs quote `d` ticks
behind their own touch, so the best pair a book can yield is

    (ask - d*t) + (1 - bid - d*t) = 1 + (ask - bid) - 2*d*t

The spread was ONE TICK at the median (p90 2, max 4), so at the deployed
`depth_ticks=2` the achievable pair was 0.97 in every one of the 501
samples -- min 0.97, p90 0.98. Against the 0.96 entry ceiling that is:

    genuinely unprofitable (pair > 1.00)            0 of 501
    profitable but above the ceiling (0.96 < p <= 1)  501 of 501

So none of those refusals was a bad bargain being declined. Every one was
a pair below the $1.00 redemption, refused by the ceiling alone.

AND THE CEILING IS ARITHMETICALLY UNREACHABLE AT DEPTH 2. pair <= 0.96
needs 1 + spread - 0.04 <= 0.96, i.e. spread <= 0.00 -- a locked or
crossed book, which `BOOK_CROSSED_OR_LOCKED` independently refuses. This
is why the count is 683 and not six.

This file pins the record, not the policy. No ceiling, depth or price
changes here.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_missed_pair_blocker as MPB
from src import l5_pmus_live_runner as R


class _Stats(dict):
    def __missing__(self, k):            # a defaultdict the tests can read
        self[k] = 0
        return 0


class _Runner:
    """Only what `_note_open_missed` touches."""
    def __init__(self):
        self.stats = _Stats()
        self.emitted = []

    _note_open_missed = R.Runner._note_open_missed


def _run(monkeypatch, rec, book, tick="0.01"):
    out = []
    monkeypatch.setattr(R, "emit", lambda label, payload: out.append(
        (label, payload)))
    r = _Runner()
    r._note_open_missed("cpc-btc-updown-15m-2026-11-02-1400z", rec, book,
                        tick)
    return r, out


# --------------------------------------------------- the measured case
ONE_TICK = {"bid": "0.4700", "ask": "0.4800", "stream_age_s": "0.05"}


def test_a_one_tick_book_records_an_achievable_pair_of_0_97(monkeypatch):
    """The 501-sample median, and the whole point of the record."""
    _, out = _run(monkeypatch,
                  {"refused_reason": "PAIR_COST_ABOVE_CEILING"}, ONE_TICK)
    assert len(out) == 1
    label, p = out[0]
    assert label == "L5_MISSED_PAIR"
    assert p["stage"] == "OPENING"
    assert p["reason"] == "PAIR_COST_ABOVE_CEILING"
    assert D(p["pair"]["pair_own_cost"]) == D("0.97")
    # below redemption, so it IS a pair worth having -- and refused
    assert p["pair"]["locks_gain_vs_redemption"] == "YES"
    assert p["pair"]["inside_entry_ceiling"] == "NO"
    assert p["missed_profitable_pair"] == "YES"


def test_the_ceiling_cannot_be_reached_at_depth_two_on_any_real_book():
    """The arithmetic behind the 683, stated as a test.

    `pair = 1 + spread - 2*d*t`. At d=2, t=0.01 the depth term is 0.04,
    so pair <= 0.96 demands spread <= 0.00 -- which is a locked or
    crossed book and refused elsewhere. Every TRADEABLE book is above
    the ceiling at this depth.
    """
    tick, d, cap = D("0.01"), D("2"), D("0.96")
    for spread_ticks in (1, 2, 3, 4):
        spread = tick * spread_ticks
        pair = D(1) + spread - (D(2) * d * tick)
        assert pair > cap, (spread_ticks, pair)
        assert pair <= D(1)              # and still profitable if filled
    # only a zero spread reaches it, and that book is not tradeable
    assert D(1) + D(0) - (D(2) * d * tick) == cap


def test_a_wider_spread_is_recorded_as_a_dearer_pair(monkeypatch):
    """p90 of the sample was a 2-tick spread -> 0.98."""
    _, out = _run(monkeypatch, {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                  {"bid": "0.4700", "ask": "0.4900", "stream_age_s": "0.05"})
    assert D(out[0][1]["pair"]["pair_own_cost"]) == D("0.98")


# --------------------------------------------------- it stays honest
def test_a_successful_open_emits_nothing(monkeypatch):
    """NOT_REFUSED is not a missed pair."""
    _, out = _run(monkeypatch, {"submitted": True}, ONE_TICK)
    assert out == []
    _, out2 = _run(monkeypatch, {"refused_reason": None}, ONE_TICK)
    assert out2 == []


def test_an_unreadable_book_records_UNKNOWN_not_a_guess(monkeypatch):
    """A refusal that fired before the book could be priced."""
    for bad in ({"bid": None, "ask": None},
                {"bid": "0", "ask": "0"},
                {"bid": "0.5000", "ask": "0.4900"},     # crossed
                {}):
        _, out = _run(monkeypatch, {"refused_reason": "BOOK_UNCERTAIN"}, bad)
        assert len(out) == 1, bad
        p = out[0][1]
        assert p["pair"]["pair_own_cost"] is None, bad
        assert p["missed_profitable_pair"] in ("UNKNOWN", "N/A"), bad


def test_an_unreadable_tick_records_UNKNOWN_rather_than_assuming_0_01(
        monkeypatch):
    """A guessed tick rescales the depth term and invents the pair."""
    for bad_tick in (None, "", "0", "nonsense"):
        _, out = _run(monkeypatch,
                      {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                      ONE_TICK, bad_tick)
        assert out[0][1]["pair"]["pair_own_cost"] is None, bad_tick


def test_it_can_never_raise_into_the_decision_path(monkeypatch):
    """Observability must not change the refusal it describes."""
    def boom(*a, **k):
        raise RuntimeError("telemetry is down")
    monkeypatch.setattr(MPB, "record", boom)
    monkeypatch.setattr(R, "emit", lambda *a, **k: None)
    r = _Runner()
    r._note_open_missed("m", {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                        ONE_TICK, "0.01")                  # must not raise
    assert r.stats["open_missed_emit_failed:RuntimeError"] == 1


def test_the_refusal_record_names_the_blocker_class(monkeypatch):
    """So the histogram is actionable rather than merely large."""
    seen = {}
    for reason in ("PAIR_COST_ABOVE_CEILING", "ORDER_ALREADY_RESTING",
                   "PAST_TERMINAL", "PAST_ENTRY_CUTOFF"):
        _, out = _run(monkeypatch, {"refused_reason": reason}, ONE_TICK)
        seen[reason] = out[0][1]["blocker_class"]
    assert seen["PAIR_COST_ABOVE_CEILING"] == MPB.PAIR_CEILING
    assert seen["ORDER_ALREADY_RESTING"] == MPB.ORDER_CONCURRENCY
    # a refusal past the window is a CORRECT refusal, never a missed pair
    assert seen["PAST_TERMINAL"] == MPB.NOT_AN_OPPORTUNITY
