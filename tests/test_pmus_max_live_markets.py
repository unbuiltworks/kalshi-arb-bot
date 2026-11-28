"""PMUS_RN1_MAX_LIVE_MARKETS: a separate cap on LIVE quoting slots, so
pregame coverage can widen (under an authorized MAX_MARKETS) without adding
live exposure. Default 0 = off."""
from src import pmus_sports_two_sided as M
from tests.test_pmus_rn1_live_first_slots import (  # noqa: F401
    DIST, DIST2, INV, LIVE, NEAR, _hold, _setup, eng)


def test_default_off():
    assert M.RN1_MAX_LIVE_MARKETS == 0


def test_off_is_unchanged(eng, monkeypatch):
    e, v = eng
    _setup(e, v, monkeypatch, {LIVE: 0.3})
    _hold(e, v, INV)                                   # a live slot holder
    assert not e.rn1_live_slots_full(LIVE)
    assert not e.rn1_market_slots_full(LIVE, e.b.m(LIVE))


def test_live_cap_blocks_a_second_live_market_not_pregame(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_MAX_LIVE_MARKETS", 1)
    _setup(e, v, monkeypatch, {LIVE: 0.3, DIST: 0.2})
    monkeypatch.setattr(M, "MAX_MARKETS", 4)
    _hold(e, v, INV)                                   # live, holds a slot
    assert e.rn1_live_slots_full(LIVE)                 # second live: no
    assert e.rn1_market_slots_full(LIVE, e.b.m(LIVE))
    assert not e.rn1_live_slots_full(DIST)             # pregame: yes
    assert not e.rn1_market_slots_full(DIST, e.b.m(DIST))


def test_selection_skips_live_and_takes_pregame(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_MAX_LIVE_MARKETS", 1)
    _setup(e, v, monkeypatch, {LIVE: 0.5, DIST: 0.2, DIST2: 0.1},
           targets=[LIVE, DIST, DIST2])
    monkeypatch.setattr(M, "MAX_MARKETS", 4)
    _hold(e, v, INV)
    e.select(v.account())
    act = set(e.active())
    assert LIVE not in act and {DIST, DIST2} <= act


def test_total_cap_still_binds(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_MAX_LIVE_MARKETS", 3)
    _setup(e, v, monkeypatch, {DIST: 0.2})
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    _hold(e, v, INV)
    assert e.rn1_market_slots_full(DIST, e.b.m(DIST))


def test_unknown_phase_fails_closed(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_MAX_LIVE_MARKETS", 1)
    _setup(e, v, monkeypatch, {LIVE: 0.3})

    def boom(_s):
        raise RuntimeError("x")
    monkeypatch.setattr(e, "event_live", boom)
    assert e.rn1_live_slots_full(LIVE)
