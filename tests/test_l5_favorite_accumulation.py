"""Favorite-accumulation research gate (issue #194): the arithmetic it rests on.

These pin the parts of `analysis/l5/favorite_accumulation.py` that a verdict
depends on, so the NO-PROMOTION recommendation cannot drift silently:

  * the replay obeys the DEPLOYED caps -- clip 1, $12 cash, the entry and
    completion cutoffs, the residual cap -- because the first version of this
    replay did not, and "earned" +$2,257 over 211 windows on 9,514 pairs by
    matching a minute-2 price to a minute-13 price with no cash limit. A
    replay that can cherry-pick is not evidence;
  * the edge interval is clustered BY WINDOW, because every trade in a window
    shares ONE settlement outcome -- 63,346 trades are ~526 coin flips, and a
    trade-level interval makes noise look like edge;
  * settlement pays a pair $1.00 and a residual $1 or $0, with BOTH outcomes
    reported.

NO NETWORK, NO CORPUS. Every test here builds its own trades, so it runs
without data/l5 and cannot be made to pass by a change in the corpus.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "fa", str(pathlib.Path("analysis/l5/favorite_accumulation.py")))
FA = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(FA)


def _t(px, outcome, t_off, winner, slug="btc-updown-15m-1000", size=1.0):
    return {"slug": slug, "ws": 1000.0, "t_off": t_off, "side": "BUY",
            "outcome": outcome, "px": px, "size": size, "winner": winner}


# ----------------------------------------------- the deployed caps bind
def test_a_pair_redeems_one_dollar_and_the_spend_is_subtracted():
    ts = [_t(0.55, "Up", 10, "Up"), _t(0.40, "Down", 20, "Up")]
    r = FA.policy_paired(ts)
    assert r["pairs"] == 1
    assert r["residual"] == 0
    assert r["spend"] == pytest.approx(0.95)
    assert r["realized"] == pytest.approx(0.05)
    # a matched pair is outcome-independent: both settlements pay the same
    assert r["if_other_outcome"] == pytest.approx(r["realized"])
    assert r["worst_case"] == pytest.approx(0.05)


def test_a_residual_is_reported_on_both_settlement_outcomes():
    ts = [_t(0.60, "Up", 10, "Up")]
    r = FA.policy_favorite(ts, cap=1)
    assert r["residual"] == 1
    assert r["realized"] == pytest.approx(0.40)      # Up won: $1 - $0.60
    assert r["if_other_outcome"] == pytest.approx(-0.60)
    assert r["worst_case"] == pytest.approx(-0.60)


def test_the_entry_cutoff_blocks_exposure_increasing_legs():
    """Past 480s nothing may OPEN. This is the deployed rule, not relaxed."""
    late = [_t(0.60, "Up", FA.ENTRY_CUTOFF_S + 1, "Up")]
    assert FA.policy_favorite(late, cap=3)["residual"] == 0
    early = [_t(0.60, "Up", FA.ENTRY_CUTOFF_S - 1, "Up")]
    assert FA.policy_favorite(early, cap=3)["residual"] == 1


def test_a_reducing_leg_is_allowed_after_entry_but_not_after_completion():
    base = _t(0.55, "Up", 10, "Up")
    ok = [base, _t(0.40, "Down", FA.ENTRY_CUTOFF_S + 10, "Up")]
    assert FA.policy_paired(ok)["pairs"] == 1, (
        "a completion may act after the ENTRY cutoff")
    late = [base, _t(0.40, "Down", FA.COMPLETE_CUTOFF_S + 10, "Up")]
    assert FA.policy_paired(late)["pairs"] == 0, (
        "nothing may complete after the COMPLETION cutoff")


def test_the_cash_cap_binds():
    ts = [_t(0.90, "Up", 10 + i, "Up") for i in range(40)]
    r = FA.policy_favorite(ts, cap=99, max_actions=99)
    assert r["spend"] <= FA.CASH_CAP + 1e-9
    assert r["residual"] * 0.90 <= FA.CASH_CAP + 1e-9


def test_the_residual_cap_binds_on_the_paired_policy():
    ts = [_t(0.60, "Up", 10 + i, "Up") for i in range(10)]
    r = FA.policy_paired(ts, resid_cap=1)
    assert r["residual"] <= 1


def test_the_favorite_policy_never_buys_an_underdog_as_a_first_leg():
    ts = [_t(0.20, "Down", 10, "Up"), _t(0.30, "Down", 20, "Up")]
    assert FA.policy_favorite(ts, cap=3)["residual"] == 0


def test_the_pair_ceiling_refuses_a_dear_completion():
    """Carried basis + the completing price must clear the ceiling."""
    ts = [_t(0.80, "Up", 10, "Up"), _t(0.45, "Down", 20, "Up")]   # 1.25
    assert FA.policy_paired(ts, ceiling=0.96)["pairs"] == 0


def test_participation_can_only_reduce_the_actions_taken():
    ts = [_t(0.60, "Up", 10 + i, "Up") for i in range(8)]
    full = FA.policy_favorite(ts, cap=9, participation=1.0, max_actions=9)
    thin = FA.policy_favorite(ts, cap=9, participation=0.25, max_actions=9)
    assert thin["actions"] <= full["actions"]


def test_max_actions_bounds_the_window():
    ts = [_t(0.60, "Up", 10 + i, "Up") for i in range(50)]
    assert FA.policy_favorite(ts, cap=99, max_actions=4)["actions"] <= 4


# ------------------------------------------- the interval is clustered
def test_the_edge_interval_is_clustered_by_window_not_by_trade():
    """One window is ONE observation. Splitting the same window into more
    trades must not shrink the interval."""
    few = [_t(0.70, "Up", 10, "Up", slug="w1", size=10.0),
           _t(0.70, "Up", 10, "Up", slug="w2", size=10.0),
           _t(0.70, "Up", 10, "Down", slug="w3", size=10.0)]
    many = []
    for w, win in (("w1", "Up"), ("w2", "Up"), ("w3", "Down")):
        many += [_t(0.70, "Up", 10, win, slug=w, size=1.0) for _ in range(10)]
    a = FA.window_clustered_edge(few)
    b = FA.window_clustered_edge(many)
    assert a["windows"] == b["windows"] == 3
    width_a = a["ci95"][1] - a["ci95"][0]
    width_b = b["ci95"][1] - b["ci95"][0]
    assert width_a == pytest.approx(width_b), (
        "splitting one window into ten trades must not narrow the interval")


def test_a_single_window_yields_no_interval():
    assert FA.window_clustered_edge([_t(0.70, "Up", 10, "Up")]) is None


def test_edge_is_realized_minus_implied_and_signs_correctly():
    """Three windows the favorite won at 0.70: edge is +0.30."""
    ts = [_t(0.70, "Up", 10, "Up", slug="w%d" % i) for i in range(3)]
    e = FA.window_clustered_edge(ts)
    assert e["window_mean_edge"] == pytest.approx(0.30)
    ts2 = [_t(0.70, "Up", 10, "Down", slug="w%d" % i) for i in range(3)]
    assert FA.window_clustered_edge(ts2)["window_mean_edge"] == pytest.approx(-0.70)


# ------------------------------------------------------- the stress case
def test_favorite_reversal_is_catastrophic_for_accumulation_and_not_for_pairing():
    """The regime test. If favorites stop winning, B bleeds and A does not."""
    fav_loses = [_t(0.70, "Up", 10, "Down"), _t(0.25, "Down", 20, "Down")]
    b = FA.policy_favorite(fav_loses, cap=1)
    a = FA.policy_paired(fav_loses)
    assert b["realized"] < 0, "accumulating a losing favorite must lose"
    assert a["pairs"] == 1 and a["realized"] > 0, (
        "a matched pair is indifferent to which side wins")
