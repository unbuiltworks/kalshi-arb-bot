"""RN1_PAUSE_DEPTH / RN1_PAUSE_ROTATE_S on the release activation config
(enabled-mode fixture: no-fair market, paying programme)."""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from src import pmus_sports_phase as PH
from tests.test_pmus_enabled_mode_safety import (  # noqa: F401
    en, full_ladder, quotes, worst)
from tests.test_pmus_sports_continuous_controller import SLUG, resting


def _vol_pause(e, monkeypatch, rng="0.04"):
    monkeypatch.setattr(PH, "pause_until",
                        lambda *a, **k: time.time() + 60.0)
    monkeypatch.setattr(e, "_vol", lambda slug, bbo, now: D(rng))
    e._vol_hist = getattr(e, "_vol_hist", None) or {}


def test_pause_depth_off_vol_pause_withdraws_openings(en, monkeypatch,
                                                      capsys):
    e, v, _ = en
    _vol_pause(e, monkeypatch)
    e.step_market(SLUG)
    assert resting(v) == []
    assert quotes(capsys)[-1]["block"] == "vol_pause"


def test_pause_depth_on_rests_below_the_move_never_at_the_touch(
        en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_PAUSE_DEPTH", True)
    _vol_pause(e, monkeypatch)
    e.step_market(SLUG)
    q = quotes(capsys)[-1]
    assert q["block"] is None
    assert q["pause_depth"] == {"reason": "vol_pause", "ticks": 5}
    # book 0.48 / 0.50: YES touch 0.48, NO touch 0.50; 5 ticks behind
    assert resting(v), "pause depth must let reward-carried bids rest"
    for side, px, _q in resting(v):
        touch = D("0.48") if side == "YES" else D("0.50")
        assert touch - D(px) >= D("0.05")
    c = worst(e)
    assert c["unmatched"] <= R.RESIDUAL_CAP_USD
    assert c["spend"] <= R.PER_MARKET_USD


def test_pause_depth_bids_still_need_their_own_reward(en, monkeypatch,
                                                      capsys):
    """The economic gate still decides: with the day's projection under the
    minimum payout nothing rests, paused or not."""
    e, v, feed = en
    monkeypatch.setattr(M, "RN1_PAUSE_DEPTH", True)
    _vol_pause(e, monkeypatch)
    feed.projected_usd_h = 0.2
    e.step_market(SLUG)
    assert resting(v) == []


def test_pause_depth_ticks():
    assert M.Engine.rn1_pause_depth(D("0.04"), D("0.01")) == 5
    assert M.Engine.rn1_pause_depth(D("0"), D("0.01")) == \
        M.JUMP_TICKS + M.RN1_PAUSE_DEPTH_EXTRA_TICKS
    assert M.Engine.rn1_pause_depth(D("0.035"), D("0.01")) == 5


def test_paused_flat_market_rotates_sooner(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_PAUSE_ROTATE_S", 20.0)
    _vol_pause(e, monkeypatch)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    mk["idle_since"] = time.time() - 25
    e.step_market(SLUG)
    assert mk.get("done") and mk.get("released_flat")


def test_paused_flat_market_without_rotation_holds_its_slot(en,
                                                            monkeypatch):
    e, v, _ = en
    _vol_pause(e, monkeypatch)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    mk["idle_since"] = time.time() - 25
    e.step_market(SLUG)
    assert not mk.get("done")
