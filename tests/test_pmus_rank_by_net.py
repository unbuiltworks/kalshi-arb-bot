"""PMUS_RN1_RANK_BY=net_per_hour: positive reward-adjusted economics rank
ahead of the live-first / duration preference (slots bind, not capital)."""
from src import pmus_sports_two_sided as M
from tests.test_pmus_rn1_live_first_slots import (  # noqa: F401
    DIST, LIVE, NEAR, _setup, eng)

RW = {DIST: {"pool_rate_usd_s": 1}, LIVE: {"pool_rate_usd_s": 1},
      NEAR: {"pool_rate_usd_s": 1}}


def _rank(e, slugs):
    return [r[2] for r in e.rank_by_reward_share([(0, 0, s) for s in slugs],
                                                 RW)]


def test_default_is_per_dollar():
    assert M.RN1_RANK_BY == "per_dollar"


def test_live_first_on_prefers_live_by_default(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    _setup(e, v, monkeypatch, {DIST: 0.41, LIVE: 0.20})
    assert _rank(e, [DIST, LIVE]) == [LIVE, DIST]


def test_net_per_hour_ranks_the_better_slot_first(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    monkeypatch.setattr(M, "RN1_RANK_BY", "net_per_hour")
    _setup(e, v, monkeypatch, {DIST: 0.41, LIVE: 0.20})
    assert _rank(e, [LIVE, DIST]) == [DIST, LIVE]


def test_net_per_hour_never_puts_a_negative_ahead(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_RANK_BY", "net_per_hour")
    _setup(e, v, monkeypatch, {DIST: -0.05, LIVE: 0.02, NEAR: 0.0})
    assert _rank(e, [DIST, NEAR, LIVE])[0] == LIVE
