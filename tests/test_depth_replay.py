"""The depth replay harness, validated against the REAL writer.

Not hand-made fixtures: the journal these tests read is produced by
`native_work_window` itself, so the reader and the writer cannot drift
apart without this going red. That is the whole point -- R7's first window
must be replayable the moment it lands, and there is no second chance to
record it.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_session_journal as JN
import scripts.l5_pmus_live_oneshot as EP
import scripts.l5_depth_replay as DR

from tests.test_r6_sweep_on_exit import FakeMP, Feed, Probe


def _record(tmp_path, feed=None, ticks=6):
    """Drive the real writer and hand back the journal it wrote."""
    mp = FakeMP()
    path = tmp_path / "r7_journal.jsonl"
    journal = JN.Journal(str(path))
    t = {"now": 0.0}
    EP.native_work_window(
        Probe(), feed or Feed(), float(ticks) * 2.0, journal=journal,
        clock=lambda: t.__setitem__("now", t["now"] + 1.0) or t["now"],
        sleep=lambda *_a, **_k: None, money_path=mp)
    return str(path)


def test_every_tick_is_replayable_when_depth_was_seen(tmp_path):
    cov = DR.coverage(_record(tmp_path))
    assert cov["ticks"] > 0
    assert cov["depth_rows"] == cov["ticks"]
    assert cov["ticks_without_depth"] == 0
    assert cov["exact_replay_possible"] is True


def test_the_ladder_round_trips_exactly(tmp_path):
    """Decimal in, Decimal out, same numbers. Prices journalled as strings
    precisely so 0.59 does not become 0.5899999999999999."""
    rows = DR.build_sequence(_record(tmp_path))
    books = [r for r in rows if r["kind"] == "book"]
    assert books, "no ladder was rebuilt"
    assert books[0]["bids"] == [[D("0.60"), D("500")], [D("0.59"), D("250")]]
    assert books[0]["offers"] == [[D("0.62"), D("400")]]


def test_depth_precedes_the_tick_it_belongs_to(tmp_path):
    """The frozen walk sets `eng.ladder` from a book row and then decides on
    the next bbo row. Depth arriving after its tick would be read by the
    FOLLOWING decision -- a one-tick lookahead, and a silently wrong replay.
    """
    rows = DR.build_sequence(_record(tmp_path))
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "book"
    for a, b in zip(kinds, kinds[1:]):
        assert not (a == "bbo" and b == "bbo"), (
            "a tick was walked with no ladder of its own: %s" % kinds)


def test_a_window_with_no_depth_is_reported_not_guessed(tmp_path):
    class NoDepth(Feed):
        ladder = None

    cov = DR.coverage(_record(tmp_path, feed=NoDepth()))
    assert cov["ticks"] > 0
    assert cov["depth_rows"] == 0
    assert cov["ticks_without_depth"] == cov["ticks"]
    assert cov["exact_replay_possible"] is False, (
        "a window with no recorded depth was reported as exactly replayable")


def test_replay_runs_the_frozen_engine_and_labels_fills_modelled(tmp_path):
    out = DR.replay(_record(tmp_path), winner="up", clip=D(10))
    assert out["coverage"]["exact_replay_possible"] is True
    assert out["clip"] == "10"
    assert "MODELLED" in out["labels"]
    assert out["net"] is not None


def test_an_unparseable_line_does_not_abort_the_replay(tmp_path):
    p = _record(tmp_path)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{ truncated write from a killed container\n")
    cov = DR.coverage(p)
    assert cov["ticks"] > 0 and cov["exact_replay_possible"] is True
