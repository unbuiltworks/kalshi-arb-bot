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


def test_underdog_needs_extra_edge_favourite_does_not(en, monkeypatch):
    """Favourite bias without a ban: book 0.48/0.50, YES fair 0.52
    (favourite) edge .04 at 0.48; NO fair 0.48 (underdog) edge -.02."""
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.01"))
    monkeypatch.setattr(M, "RN1_TF_DOG_EXTRA_USD", D("0.02"))
    _fair(e, monkeypatch, "0.52")
    e.step_market(SLUG)
    tl = e.b.m(SLUG)["_tf_last"]
    assert tl["YES"]["favourite"] and not tl["NO"]["favourite"]
    assert D(tl["NO"]["required"]) == D("0.03")
    assert D(tl["YES"]["required"]) >= D("0.01")
    assert {s for s, _p, _q in resting(v)} <= {"YES"}


def test_value_underdog_still_admissible(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.01"))
    monkeypatch.setattr(M, "RN1_TF_DOG_EXTRA_USD", D("0.02"))
    _fair(e, monkeypatch, "0.45")        # NO fav .55; YES dog .45
    v.bid, v.ask = D("0.40"), D("0.62")   # YES touch .40: dog edge .05
    e.step_market(SLUG)
    assert "YES" in {s for s, _p, _q in resting(v)}


def test_concentration_stops_replenishment(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_CONC_USD", D("0.02"))
    _fair(e, monkeypatch, "0.60")         # YES edge at .48 = .12
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "4", "NO": "0"}
    mk["cost"] = {"YES": "1.80", "NO": "0"}
    e.step_market(SLUG)
    # 4 held + 2 more -> 6 unpaired: required .02 + .12 = .14 > .12
    assert all(not (s == "YES" and st != "two_sided_complete")
               for s, _p, st in _orders(e))


def test_weakening_favourite_refused(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_WEAKEN_USD", D("0.02"))
    _fair(e, monkeypatch, "0.60")
    mk = e.b.m(SLUG)
    mk["_tf_fair"] = [(time.time() - 60, D("0.66"))]   # YES fair fell 6c
    e.step_market(SLUG)
    assert all(s != "YES" for s, _p, _q in resting(v))
    assert e.b.m(SLUG)["_tf_fair"][-1][1] == D("0.60")


def test_fair_coverage_logged_only_with_coverage_flag(en, monkeypatch,
                                                      capsys):
    e, v, _ = en
    monkeypatch.setenv("PMUS_SHARP_COVERAGE_LOG", "1")
    e.step_market(SLUG)
    fc = quotes(capsys)[-1]["fair_coverage"]
    assert fc["any"] is False and "guard_configured" in fc


def test_fair_coverage_absent_by_default(en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.delenv("PMUS_SHARP_COVERAGE_LOG", raising=False)
    e.step_market(SLUG)
    assert "fair_coverage" not in quotes(capsys)[-1]


class _G:
    def __init__(self, mapped):
        self.mapped = mapped

    def token(self, slug):
        return "tok" if slug in self.mapped else None


def test_select_fair_first_and_only(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    sc = [(D("0.01"), D("0"), "a"), (D("0.02"), D("0"), "b"),
          (D("0.03"), D("0"), "c")]
    monkeypatch.setattr(e, "guard", _G({"c"}))
    monkeypatch.setattr(M, "RN1_TF_SELECT", "first")
    assert [r[2] for r in e.rn1_tf_select(list(sc))] == ["c", "a", "b"]
    monkeypatch.setattr(M, "RN1_TF_SELECT", "only")
    assert [r[2] for r in e.rn1_tf_select(list(sc))] == ["c"]


def test_select_only_fails_closed(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TF_SELECT", "only")
    monkeypatch.setattr(e, "rn1_has_independent_fair",
                        lambda slug: 1 / 0)
    assert e.rn1_tf_select([(D("0"), D("0"), "a")]) == []


def test_sportsbook_fair_counts_only_when_fresh(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(e, "guard", None)
    monkeypatch.setattr(M, "RN1_DIR_SHARP", True)
    e.b.d["sharp_shadow"] = {"x": {"fair_yes": "0.6", "t": time.time(),
                                   "odds_age_s": 30},
                             "y": {"fair_yes": "0.6", "t": time.time() - 5000,
                                   "odds_age_s": 30}}
    assert e.rn1_has_independent_fair("x")
    assert not e.rn1_has_independent_fair("y")


def test_hedge_repriced_to_fair_plus_give(en, monkeypatch):
    """YES held at 0.40; NO completion would rest at the 0.50 touch (pair
    0.90 <= 0.97). NO fair .45 + give 0 -> the completion rests at <= .45."""
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0"))
    _fair(e, monkeypatch, "0.55")
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "0"}
    mk["cost"] = {"YES": "0.80", "NO": "0"}
    e.step_market(SLUG)
    comp = [px for s, px, st in _orders(e) if st == "two_sided_complete"]
    assert comp and all(px <= D("0.45") for px in comp)


def test_hedge_unchanged_without_fair_or_flag(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0"))
    _fair(e, monkeypatch, None)
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "0"}
    mk["cost"] = {"YES": "0.80", "NO": "0"}
    e.step_market(SLUG)
    comp = [px for s, px, st in _orders(e) if st == "two_sided_complete"]
    assert comp and max(comp) > D("0.45")      # pair cap alone governs
