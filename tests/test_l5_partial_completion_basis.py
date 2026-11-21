"""One clip of a several-clip leg can be completed (default off).

WHAT HAPPENED, 2026-11-11 04:00Z. Three DOWN clips filled by T+25 (1.18 at
an average 0.4668) while their three UP siblings rested at 0.45 to 0.47.
At T+50 the venue offered UP at 0.51: a pair of 0.977. The engine could
not take it, and could not have at ANY price:

  * `completion_ceiling` returned None for a one-clip completion, because
    it required a test that is not in force (`positive_both`) which a
    partial completion can never pass -- 68 requotes refused
    NO_COMPLETION_CEILING and 154 complements COMPLEMENT_PAIR_ABOVE_CAP;
  * the submission gate divided the WHOLE leg's cost by the one clip being
    matched -- (0.5508 + 0.39p) / 0.39 is above 1.41 for every p -- 211
    refusals PAIR_COST_ABOVE_CEILING.

`L5_PARTIAL_COMPLETION_BASIS=PER_CONTRACT` prices the pairs an order
creates at what those contracts cost: the held leg's average plus this
price. No ceiling moves. A full completion is the same number either way.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_ladder_runner as LAD
from src import l5_two_sided_probe as TS

ENV = TS.PARTIAL_COMPLETION_BASIS_ENV
REST = TS.COMPLETION_RESTS_AT_CEILING_ENV
BASE = dict(CC.FOUR, PMUS_RESIDUAL_CAP_K="4", **CC.STEER)
ONE = D("1.00")


def ceiling(qty, **over):
    kw = dict(held_side="DOWN", held_qty=D("1.18"),
              held_cost_usd=D("0.5508"), deficient_qty=D(0),
              deficient_cost_usd=D(0), complete_qty=D(qty),
              residual_budget_usd=D("1.00"), tick=D("0.01"))
    kw.update(over)
    return TS.completion_ceiling(**kw)


class TestTheSwitch:

    @pytest.mark.parametrize("raw", [None, "", "ON", "per_contract", "1",
                                     "PER_CONTRACT "])
    def test_only_the_exact_token(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, raw)
        assert TS.partial_completion_per_contract() is False

    def test_the_exact_token(self, monkeypatch):
        monkeypatch.setenv(ENV, "PER_CONTRACT")
        assert TS.partial_completion_per_contract() is True


class TestTheCompletionCeiling:

    def test_off_one_clip_of_three_has_no_ceiling_at_all(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        assert ceiling("0.39") is None            # the 0400z refusal
        assert ceiling("1.18") is not None        # the whole leg always had

    def test_on_one_clip_gets_the_ceilings_in_force(self, monkeypatch):
        monkeypatch.setenv(ENV, "PER_CONTRACT")
        assert ceiling("0.39") == D("0.95")
        assert ceiling("0.78") == D("0.95")

    def test_the_whole_leg_is_the_same_either_way(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        off = ceiling("1.18")
        monkeypatch.setenv(ENV, "PER_CONTRACT")
        assert ceiling("1.18") == off

    @pytest.mark.parametrize("bad", [dict(residual_budget_usd=None),
                                     dict(held_qty=D(0)),
                                     dict(held_cost_usd=None),
                                     dict(complete_qty=D(0)),
                                     dict(residual_budget_usd=D("0.05"))])
    def test_on_what_cannot_be_computed_is_still_none(self, monkeypatch,
                                                      bad):
        monkeypatch.setenv(ENV, "PER_CONTRACT")
        assert ceiling("0.39", **bad) is None


def stranded_three(tmp_path, monkeypatch, on, rest=False, away=("0.55",
                                                               "0.56")):
    """Three of four DOWN clips fill; every UP sibling still rests."""
    env = dict(BASE)
    if on:
        env[ENV] = "PER_CONTRACT"
    if rest:
        env[REST] = "ON"
    h = CC.steered(CC.harness(tmp_path, monkeypatch, env))
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(4):
        off += 1.0
        h.tick(off)
    for oid in LAD.resting_ids(h, "DOWN")[:3]:
        h.venue.fill(oid)
    off += 3.0
    h.tick(off)
    n0 = len(h.venue.sent)
    h.book.set(*away)
    for _ in range(8):
        off += 3.0
        h.tick(off)
    return h, off, n0


def avg_down(h):
    return h.r.store.leg_cost(CC.SLUG)["DOWN"] / h.pos("DOWN")


class TestInTheRunner:

    def test_off_reproduces_0400z_the_stale_siblings_never_move(
            self, tmp_path, monkeypatch):
        h, _off, n0 = stranded_three(tmp_path, monkeypatch, on=False)
        assert h.pos("DOWN") > ONE and h.pos("UP") == 0
        assert len(h.venue.sent) == n0
        assert max(p for _s, p in LAD.resting(h, "UP")) <= D("0.48")
        assert h.r.stats["requote_refused:NO_COMPLETION_CEILING"] >= 5

    def test_on_a_stale_sibling_is_re_placed_inside_the_ceiling(
            self, tmp_path, monkeypatch):
        h, _off, n0 = stranded_three(tmp_path, monkeypatch, on=True)
        new = h.sent()[n0:]
        assert new and all(s == "UP" for s, _p, _q in new)
        for _s, p, q in new:
            assert avg_down(h) + p <= ONE          # the owner's ceiling
            assert p <= D("0.55")                  # never through the touch
            assert q <= h.pos("DOWN")              # never past the strand
        assert max(p for _s, p in LAD.resting(h, "UP")) >= D("0.50")
        assert h.r.stats.get("requote_refused:NO_COMPLETION_CEILING", 0) == 0
        assert h.r.store.orders_uncertain() is None
        assert CC.worst_loss(h) <= CC.DEFICIT + CC.CENT

    def test_on_the_clip_completes_when_the_offer_comes_to_it(
            self, tmp_path, monkeypatch):
        h, off, _n0 = stranded_three(tmp_path, monkeypatch, on=True)
        h.book.set("0.50", "0.51")
        moved = [oid for oid, (_s, p) in zip(LAD.resting_ids(h, "UP"),
                                              LAD.resting(h, "UP"))
                 if p >= D("0.51")]
        assert moved
        for oid in moved:
            h.venue.fill(oid)
        for _ in range(4):
            off += 3.0
            h.tick(off)
        up, dn = h.pos("UP"), h.pos("DOWN")
        assert D(0) < up <= dn
        lc = h.r.store.leg_cost(CC.SLUG)
        assert lc["UP"] / up + lc["DOWN"] / dn <= ONE
        assert h.r.store.orders_uncertain() is None

    def test_on_nothing_is_ever_sent_above_one_dollar_a_pair(
            self, tmp_path, monkeypatch):
        """The market runs far away: with the leg resting at the ceiling
        the quote stops there; without it nothing is sent at all."""
        for i, rest in enumerate((False, True)):
            h, _off, n0 = stranded_three(tmp_path / str(i), monkeypatch,
                                         on=True, rest=rest,
                                         away=("0.88", "0.89"))
            for s, p, _q in h.sent()[n0:]:
                assert s == "UP" and avg_down(h) + p <= ONE
            assert h.pos("UP") == 0

    def test_a_single_clip_window_is_identical_with_and_without_it(
            self, tmp_path, monkeypatch):
        seen = []
        for i, on in enumerate((False, True)):
            env = dict(BASE, PMUS_RESIDUAL_CAP_K="1",
                       L5_TS_CONCURRENT_ROUNDS="1")
            if on:
                env[ENV] = "PER_CONTRACT"
            h = CC.steered(CC.harness(tmp_path / str(i), monkeypatch, env))
            h.at(6.5).l5_action()
            off = 6.8
            for _ in range(4):
                off += 1.0
                h.tick(off)
            for oid in LAD.resting_ids(h, "DOWN"):
                h.venue.fill(oid)
            for _ in range(12):
                off += 3.0
                h.tick(off)
            seen.append([(s, p, q) for s, p, q in h.sent()])
        assert seen[0] == seen[1]
