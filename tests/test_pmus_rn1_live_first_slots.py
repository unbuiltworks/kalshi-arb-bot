"""Live-first slot allocation (PMUS_RN1_LIVE_FIRST) and no select/release
loop (PMUS_RN1_SELECT_NO_CHURN), both default off.

LIVE 26-27 Nov (two slots): both went to table-tennis pre-game markets
starting hours later; one slot was held by a market carrying a 1-contract
leg; a third candidate was selected, blocked `max_markets`, released flat
and re-selected every ~2 minutes."""
import json
import os
import subprocess
import sys
import time
from decimal import Decimal as D

import pytest

from src import pmus_duration_rank as DR
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    eng)

LIVE = "aec-atp-aaa-bbb-2026-11-27"          # started 10 min ago
NEAR = "aec-atp-ccc-ddd-2026-11-27"          # starts in 20 min
DIST = "aec-setkameua-eee-fff-2026-11-27"    # starts in 5 h
DIST2 = "aec-setkameua-ggg-hhh-2026-11-27"   # starts in 6 h
INV = "aec-utr-kob-kha-2026-11-27"           # carries a 1-contract leg


# ------------------------------------------------------------------ pure
def test_pure_class_and_score():
    now = 1_000_000.0
    assert DR.start_class(now, None, True, 1800) == "live"
    assert DR.start_class(now, now - 1, False, 1800) == "live"
    assert DR.start_class(now, now + 1800, False, 1800) == "near"
    assert DR.start_class(now, now + 1801, False, 1800) == "distant"
    assert DR.start_class(now, None, False, 1800) == "unknown"
    # settles within the horizon: no penalty; later: divided by R/H
    assert DR.turnover(1200, 3600, 0.5) == 1.0
    assert DR.turnover(7200, 3600, 0.5) == 0.5
    assert DR.turnover(None, 3600, 0.4) == 0.4
    assert DR.turnover(0, 3600, 0.5) == 0.0
    assert DR.live_first_score(0.02, 18000, 3600, 0.5) == pytest.approx(
        0.02 * 1 * 0.2)
    assert DR.live_first_score(0.02, 1800, 7200, 0.5) == pytest.approx(
        0.02 * 2 * 1.0)
    assert DR.live_first_score(-0.1, 100, 3600, 0.5) == -0.1   # no boost


# ------------------------------------------------------------- helpers
def _setup(e, v, monkeypatch, nets, targets=None, inv_slots=True):
    """nets: slug -> net $/h (capital $10). Targets carry starts."""
    now = time.time()
    starts = {LIVE: now - 600, NEAR: now + 1200, DIST: now + 5 * 3600,
              DIST2: now + 6 * 3600, INV: now - 300}
    v.states = {LIVE: {"type": "tennis", "period": "S1"},
                INV: {"type": "tennis", "period": "S1"}}
    e.guard = None
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    monkeypatch.setattr(M, "MAX_TRACKED", 4)
    monkeypatch.setattr(M, "RN1_RANK_SHARE", True)
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 50)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", inv_slots)
    tg = [{"slug": s, "game_start": starts[s],
           "market_meta": {"bestBidQuote": "0.48", "bestAskQuote": "0.50"}}
          for s in (targets or nets)]
    monkeypatch.setattr(e, "slate_targets", lambda: list(tg))
    e._game_start.update({s: starts[s] for s in starts})
    monkeypatch.setattr(e, "reward_programs", lambda slugs: {
        s: {"pool_rate_usd_s": 0.01, "programId": "p", "pool": 1,
            "period": "live", "period_s": 1, "targetSize": 1,
            "maxSpread": None} for s in slugs if s in nets})

    def score(s, _per):
        return {"per_dollar_hour": nets[s] / 10, "net_usd_per_hour": nets[s],
                "usd_per_hour": nets[s] + 0.01, "capital_usd": "10"}
    monkeypatch.setattr(e, "_reward_share_score", score)
    e._share_cache = {}


def _hold(e, v, slug, stage="rn1_open", pos=("0", "0")):
    """A tracked market with a resting bid of `stage` and position."""
    mk = e.b.m(slug)
    mk["game_start"] = e._game_start.get(slug)
    mk["pos"] = {"YES": pos[0], "NO": pos[1]}
    mk["cost"] = {"YES": str(D(pos[0]) * D("0.40")),
                  "NO": str(D(pos[1]) * D("0.40"))}
    oid = "h-" + slug
    side = "NO" if D(pos[0]) > D(pos[1]) else "YES"
    mk["orders"][oid] = {"side": side, "px": "0.40", "qty": "1",
                         "open": True, "stage": stage,
                         "placed_at": time.time()}
    v.orders[oid] = {"slug": slug, "side": side, "px": D("0.40"),
                     "qty": D("1"), "filled": D(0), "open": True}
    return oid


def _rows(capsys, kind):
    pre = "PMUS_TWO_SIDED "
    return [json.loads(x[len(pre):]) for x in
            capsys.readouterr().out.splitlines()
            if x.startswith(pre) and json.loads(x[len(pre):])
            .get("kind") == kind]


# ------------------------------------------------------------ defaults
def test_flags_default_off():
    assert M.RN1_LIVE_FIRST is False and M.RN1_SELECT_NO_CHURN is False
    assert M.RN1_NEAR_START_S == 1800 and M.RN1_DISTANT_MAX_SLOTS == 1


def test_off_the_gate_is_never_consulted(eng, monkeypatch):
    e, v = eng
    _setup(e, v, monkeypatch, {DIST: 0.41, DIST2: 0.30, LIVE: 0.20})
    monkeypatch.setattr(e, "slot_gate", lambda *a: (_ for _ in ())
                        .throw(AssertionError("called while off")))
    monkeypatch.setattr(e, "live_first_info", lambda *a: (_ for _ in ())
                        .throw(AssertionError("called while off")))
    e.select(v.account())
    # today: best per dollar first, both distant pre-game markets
    assert sorted(e.active()) == sorted([DIST, DIST2])


def test_off_the_churn_loop_is_today(eng, monkeypatch):
    """Off: a third market is still selected while both slots are held."""
    e, v = eng
    _setup(e, v, monkeypatch, {LIVE: 0.2})
    _hold(e, v, DIST)
    _hold(e, v, INV, stage="two_sided_complete", pos=("1", "0"))
    e.select(v.account())
    assert LIVE in e.active()
    # and it would be blocked max_markets when it quotes
    assert e.rn1_market_slots_full(LIVE, e.b.m(LIVE))


# ---------------------------------------------------------- live first
def test_live_positive_beats_distant_pregame(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    # DIST has the larger per-hour estimate, but settles in ~5.7 h
    _setup(e, v, monkeypatch, {DIST: 0.41, LIVE: 0.20})
    out = [r[2] for r in e.rank_by_reward_share(
        [(0, 0, DIST), (0, 0, LIVE)], {DIST: {"pool_rate_usd_s": 1},
                                       LIVE: {"pool_rate_usd_s": 1}})]
    assert out == [LIVE, DIST]


def test_pregame_still_wins_when_justified(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    _setup(e, v, monkeypatch, {DIST: 4.0, LIVE: 0.20})
    out = [r[2] for r in e.rank_by_reward_share(
        [(0, 0, DIST), (0, 0, LIVE)], {DIST: {"pool_rate_usd_s": 1},
                                       LIVE: {"pool_rate_usd_s": 1}})]
    assert out == [DIST, LIVE]


def test_near_start_preferred_over_distant(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    _setup(e, v, monkeypatch, {DIST: 0.30, NEAR: 0.20})
    e.select(v.account())
    assert e.live_first_class(NEAR) == "near"
    assert e.live_first_class(DIST) == "distant"
    out = [r[2] for r in e.rank_by_reward_share(
        [(0, 0, DIST), (0, 0, NEAR)], {DIST: {"pool_rate_usd_s": 1},
                                       NEAR: {"pool_rate_usd_s": 1}})]
    assert out == [NEAR, DIST]


def test_distant_limited_to_n_slots(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    _setup(e, v, monkeypatch, {DIST: 0.41, DIST2: 0.30})
    e.select(v.account())
    assert e.active() == [DIST]                     # one distant slot only
    assert _rows(capsys, "RN1_LIVE_FIRST_DISTANT_CAP")
    monkeypatch.setattr(M, "RN1_DISTANT_MAX_SLOTS", 2)
    e.select(v.account())
    assert sorted(e.active()) == sorted([DIST, DIST2])


def test_pregame_selected_when_no_live_or_near_positive(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    _setup(e, v, monkeypatch, {DIST: 0.41, LIVE: -0.105})
    e.select(v.account())
    assert e.active() == [DIST]                     # negative live: never


def test_flat_distant_yields_its_slot_to_live(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_DISTANT_MAX_SLOTS", 2)
    _setup(e, v, monkeypatch, {LIVE: 0.20, DIST: 0.41, DIST2: 0.30})
    oa = _hold(e, v, DIST)
    ob = _hold(e, v, DIST2)
    t = time.monotonic()
    e._share_cache = {DIST: (t, {"per_dollar_hour": 0.041}),
                      DIST2: (t, {"per_dollar_hour": 0.030})}
    e.select(v.account())
    assert LIVE in e.active()
    released = [s for s in (DIST, DIST2) if e.b.m(s)["done"]]
    assert released == [DIST2]                      # the lower score yields
    assert v.orders[ob]["open"] is False            # its opening cancelled
    assert v.orders[oa]["open"] is True             # the other untouched
    assert e.b.m(DIST2)["released_flat"]
    assert not e.rn1_market_slots_full(LIVE, e.b.m(LIVE))   # granted
    row = [r for r in _rows(capsys, "released_flat_market")
           if r.get("why") == "live_first_yield"]
    assert row and row[0]["slug"] == DIST2 and row[0]["for_slug"] == LIVE


def test_inventory_market_is_never_preempted(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    _setup(e, v, monkeypatch, {LIVE: 0.20})
    # distant pre-game markets carrying a leg, with completion and opening
    oc = _hold(e, v, DIST, stage="two_sided_complete", pos=("1", "0"))
    od = _hold(e, v, DIST2, stage="rn1_open", pos=("2", "1"))
    e.select(v.account())
    assert LIVE not in e.active()
    assert not e.b.m(DIST)["done"] and not e.b.m(DIST2)["done"]
    assert v.orders[oc]["open"] and v.orders[od]["open"]   # nothing cancelled


def test_completion_bid_in_a_flat_market_is_never_cancelled(eng,
                                                           monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    monkeypatch.setattr(M, "RN1_DISTANT_MAX_SLOTS", 2)
    _setup(e, v, monkeypatch, {LIVE: 0.20})
    oc = _hold(e, v, DIST, stage="two_sided_complete")   # flat, completion
    oo = _hold(e, v, DIST2, stage="two_sided_complete")
    e.select(v.account())
    assert v.orders[oc]["open"] and v.orders[oo]["open"]
    assert LIVE not in e.active()


def test_a_fill_racing_the_yield_keeps_the_market(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_DISTANT_MAX_SLOTS", 2)
    _setup(e, v, monkeypatch, {LIVE: 0.20})
    oa = _hold(e, v, DIST)        # no estimate either way: DIST yields
    _hold(e, v, DIST2)

    def cancel(oid):                         # the bid filled meanwhile
        v.orders[oid].update(open=False, filled=D("1"))
        return True
    monkeypatch.setattr(v, "cancel", cancel)
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    e.select(v.account())
    mk = e.b.m(DIST)
    assert v.orders[oa]["open"] is False
    assert not mk["done"]                    # kept, now holding inventory
    assert D(mk["pos"]["YES"]) == 1
    assert LIVE not in e.active()            # no slot was freed


# ------------------------------------------------------------- no churn
def test_no_churn_when_every_slot_is_held(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    _setup(e, v, monkeypatch, {LIVE: 0.2})
    _hold(e, v, DIST)
    _hold(e, v, INV, stage="two_sided_complete", pos=("1", "0"))
    for _ in range(3):
        e.select(v.account())
    assert LIVE not in e.active()
    assert len(_rows(capsys, "RN1_SELECT_NO_CHURN")) == 1   # once per change


def test_no_churn_still_fills_free_slots(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    _setup(e, v, monkeypatch, {LIVE: 0.2, NEAR: 0.1, DIST: 0.05})
    e.select(v.account())
    assert len(e.active()) == 2                     # MAX_MARKETS, no more


# ---------------- RN1_SLOTS_COUNT_INVENTORY on (1, default) and off (0)
@pytest.mark.parametrize("inv_slots", [True, False])
def test_inventory_slot_flag_both_ways(eng, monkeypatch, inv_slots):
    e, v = eng
    monkeypatch.setattr(M, "RN1_LIVE_FIRST", True)
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    _setup(e, v, monkeypatch, {LIVE: 0.2}, inv_slots=inv_slots)
    _hold(e, v, DIST)
    # INV: a 1-contract YES leg; only a completion bid resting (managed)
    oc = _hold(e, v, INV, stage="two_sided_complete", pos=("1", "0"))
    e.b.m(INV)["orders"]["filled-open"] = {
        "side": "YES", "px": "0.40", "qty": "1", "filled": "1",
        "open": False, "stage": "rn1_open", "placed_at": time.time()}
    unmatched_before = e.b.total_unmatched()
    e.select(v.account())
    if inv_slots:
        # it holds a slot; DIST is distant and flat, so it yields to LIVE
        assert e.b.m(DIST)["done"] and LIVE in e.active()
    else:
        # it holds no slot: LIVE takes the free one, DIST keeps its own
        assert not e.b.m(DIST)["done"] and LIVE in e.active()
    # the inventory market is always kept, managed and counted
    mk = e.b.m(INV)
    assert not mk["done"] and v.orders[oc]["open"]
    assert INV in e.active()
    assert e.b.unmatched_cost(INV) == D("0.40")
    assert e.b.total_unmatched() == unmatched_before
    assert e.rn1_campaign_exposure(exclude=LIVE)["unmatched"] > 0


@pytest.mark.parametrize("inv_slots", [True, False])
def test_inventory_slot_flag_no_churn_without_live_first(eng, monkeypatch,
                                                         inv_slots):
    e, v = eng
    monkeypatch.setattr(M, "RN1_SELECT_NO_CHURN", True)
    _setup(e, v, monkeypatch, {LIVE: 0.2}, inv_slots=inv_slots)
    _hold(e, v, DIST)
    _hold(e, v, INV, stage="two_sided_complete", pos=("1", "0"))
    e.select(v.account())
    assert (LIVE in e.active()) is (not inv_slots)
    if not inv_slots:
        # the newly selected market is granted a slot when it quotes
        assert not e.rn1_market_slots_full(LIVE, e.b.m(LIVE))


# ------------------------------------------------------------ caps
def _limits(env):
    code = ("from src import pmus_sports_two_sided as M, "
            "pmus_rn1_replica as R; print(repr((M.MAX_MARKETS, M.TOTAL_CAP,"
            " M.MAX_TRACKED, R.PER_MARKET_USD, R.TOTAL_SPEND_USD, "
            "R.RESIDUAL_CAP_USD, R.TOTAL_UNMATCHED_USD, "
            "M.RN1_SLOTS_COUNT_INVENTORY, M.RN1_DURATION_HORIZON_S)))")
    full = {k: v for k, v in os.environ.items()
            if k not in ("PMUS_RN1_LIVE_FIRST", "PMUS_RN1_SELECT_NO_CHURN")}
    full.update(env)
    return subprocess.run([sys.executable, "-c", code], env=full,
                          capture_output=True, text=True, check=True,
                          cwd=os.path.dirname(os.path.dirname(__file__))
                          ).stdout.strip()


def test_caps_and_defaults_unchanged_by_the_flags():
    assert _limits({}) == _limits({"PMUS_RN1_LIVE_FIRST": "1",
                                   "PMUS_RN1_SELECT_NO_CHURN": "1"})
