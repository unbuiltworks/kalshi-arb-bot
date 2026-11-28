"""Trading-first eviction fix (ce0b13510) and reselection backoff (01248d03b):
reward ineligibility alone never evicts a trading-first market; legacy
reward eviction unchanged; negative edge / stale fair still cancel; the
economic idle release still works; bounded backoff boundaries."""
import time
from decimal import Decimal as D

import pytest

from src import pmus_directional_alloc as DA
from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en  # noqa: F401
from tests.test_pmus_sports_continuous_controller import SLUG, resting


def _fair(e, monkeypatch, yes):
    monkeypatch.setattr(e, "rn1_directional_prob", lambda slug: (
        None if yes is None else DA.probability(D(yes), D(yes), D(yes),
                                                source="intl")))


def _unqualified(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_RELEASE_UNQUALIFIED_S", 30.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)


def _tf(monkeypatch):
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_BYPASS_NET", True)


def test_tf_keeps_positive_edge_quote_when_reward_ineligible(en, monkeypatch):
    e, v, _ = en
    _tf(monkeypatch)
    _fair(e, monkeypatch, "0.60")
    _unqualified(e, monkeypatch)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    mk["unqualified_since"] = time.time() - 300
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert not mk.get("done")
    assert "unqualified_since" not in mk
    assert any(s == "YES" for s, _p, _q in resting(v))


# Legacy reward eviction parity: tests/test_pmus_rn1_ladder.py
# test_a_market_that_stops_qualifying_is_released (unchanged, must pass).


def test_tf_negative_edge_still_cancels(en, monkeypatch):
    e, v, _ = en
    _tf(monkeypatch)
    _unqualified(e, monkeypatch)
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    assert any(s == "YES" for s, _p, _q in resting(v))
    _fair(e, monkeypatch, "0.45")            # YES edge now negative
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert not any(s == "YES" for s, _p, _q in resting(v))


def test_tf_stale_fair_still_cancels(en, monkeypatch):
    e, v, _ = en
    _tf(monkeypatch)
    _unqualified(e, monkeypatch)
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    assert resting(v)
    _fair(e, monkeypatch, None)              # fair gone
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []


def test_tf_idle_release_still_works(en, monkeypatch):
    e, v, _ = en
    _tf(monkeypatch)
    _unqualified(e, monkeypatch)
    _fair(e, monkeypatch, None)              # nothing wanted
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    mk["idle_since"] = time.time() - 125
    e.step_market(SLUG)
    assert mk.get("done") and mk.get("released_flat")


def test_tf_lock_still_cancels(en, monkeypatch):
    e, v, _ = en
    _tf(monkeypatch)
    _unqualified(e, monkeypatch)
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    assert resting(v)
    monkeypatch.setattr(M, "submission_locked", lambda: True)
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert not any(s == "YES" for s, _p, _q in resting(v))


def _released(ago, reselected=0):
    return {"done": True, "released_flat": time.time() - ago,
            "pos": {"YES": "0", "NO": "0"}, "orders": {},
            "reselected": reselected}


@pytest.mark.parametrize("repeats,cool", [(0, 60), (1, 120), (2, 240),
                                          (3, 300), (9, 300)])
def test_tf_backoff_boundaries(monkeypatch, repeats, cool):
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    assert not M.Engine.reselectable(_released(cool - 2, repeats))
    assert M.Engine.reselectable(_released(cool + 2, repeats))


def test_backoff_respects_larger_configured_minimum(monkeypatch):
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 900.0)
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    assert not M.Engine.reselectable(_released(600, 0))
    assert M.Engine.reselectable(_released(905, 0))


def test_legacy_reselect_cooldown_unchanged(monkeypatch):
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 10.0)
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", False)
    assert M.Engine.reselectable(_released(15, 5))


def test_never_reselect_with_inventory_or_open_order(monkeypatch):
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    m = _released(1000)
    m["pos"]["YES"] = "1"
    assert not M.Engine.reselectable(m)
    m = _released(1000)
    m["orders"] = {"o": {"open": True}}
    assert not M.Engine.reselectable(m)
