"""PMUS_RN1_TRADING_FIRST: exposure-adding bids (openings, same-side
additions, non-completion ladder levels) need an independent probability and
a pre-reward edge; a paying reward programme cannot rescue them; completions
are exempt. Enabled-mode fixture: no-fair market (book 0.48/0.50), paying
programme."""
import time
from decimal import Decimal as D

from src import pmus_directional_alloc as DA
from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en, quotes  # noqa: F401
from tests.test_pmus_sports_continuous_controller import SLUG, resting


def _fair(e, monkeypatch, yes):
    monkeypatch.setattr(e, "rn1_directional_prob", lambda slug: (
        None if yes is None else DA.probability(D(yes), D(yes), D(yes),
                                                source="intl")))


def _orders(e):
    mk = e.b.m(SLUG)
    return [(o["side"], D(str(o["px"])), o.get("stage"))
            for o in mk["orders"].values() if o.get("open")]


def test_off_unchanged_no_fair_openings_rest(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", False)
    e.step_market(SLUG)
    assert resting(v), "baseline: reward-carried no-fair openings rest"


def test_on_no_fair_refuses_openings_even_with_paying_rewards(
        en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    _fair(e, monkeypatch, None)
    e.step_market(SLUG)
    assert resting(v) == []
    q = quotes(capsys)[-1]
    assert "tf_no_fair" in (q.get("why") or {}).values()


def test_on_only_the_side_with_edge_rests(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    _fair(e, monkeypatch, "0.60")        # YES fair .60, NO fair .40
    e.step_market(SLUG)
    rs = resting(v)
    assert rs and {s for s, _p, _q in rs} == {"YES"}
    assert all(D("0.60") - D(p) >= M.RN1_TF_EDGE_USD for _s, p, _q in rs)
    tl = e.b.m(SLUG)["_tf_last"]
    assert D(tl["NO"]["trading_edge"]) < 0


def test_on_edge_below_threshold_refused(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.50"))
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    assert resting(v) == []


def test_on_completion_exempt_without_fair(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    _fair(e, monkeypatch, None)
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "0"}
    mk["cost"] = {"YES": "0.80", "NO": "0"}
    e.step_market(SLUG)
    od = _orders(e)
    assert od and all(st == "two_sided_complete" and s == "NO"
                      for s, _p, st in od)
    assert all(px <= D("0.97") - D("0.40") for _s, px, _st in od)


def test_favourite_only_refuses_underdog(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_FAV_ONLY", True)
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.01"))
    _fair(e, monkeypatch, "0.49")        # YES underdog .49, NO fav .51
    e.step_market(SLUG)
    rs = resting(v)
    assert rs and all(s == "NO" for s, _p, _q in rs)


def test_falling_side_refused(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_FALLING_S", 30.0)
    _fair(e, monkeypatch, "0.60")
    mk = e.b.m(SLUG)
    mk["_tf_mid"] = [(time.time() - 5, D("0.53"))]   # YES mid fell 4 ticks
    e.step_market(SLUG)
    assert all(s != "YES" for s, _p, _q in resting(v))


def test_error_fails_closed(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(e, "rn1_directional_prob",
                        lambda slug: {"p": "x"})           # malformed
    e.step_market(SLUG)
    assert resting(v) == []


def test_log_separates_trading_edge_from_reward(en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    tf = quotes(capsys)[-1]["trading_first"]
    assert D(tf["pre_reward_edge"]["YES"]["trading_edge"]) >= D("0.02")
    assert D(tf["pre_reward_edge"]["NO"]["trading_edge"]) < 0
