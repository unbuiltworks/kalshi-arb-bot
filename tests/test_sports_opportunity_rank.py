"""The ranker's load-bearing conventions, pinned.

Each of these encodes a mistake that is easy to make silently and that would
disqualify or mis-rank the whole universe rather than one market.
"""
from __future__ import annotations

import pytest

from src import pmus_sports_opportunity_rank as R


# --------------------------------------------------- the spread IS the margin
def test_maker_pair_cost_is_one_minus_the_spread():
    """bid 0.40 / ask 0.41 -> a maker holding both touches pays 0.99."""
    assert R.maker_pair_cost(0.40, 0.41) == pytest.approx(0.99)
    assert R.maker_pair_cost(0.77, 0.84) == pytest.approx(0.93)


def test_a_wider_spread_is_a_cheaper_pair():
    tight = R.maker_pair_cost(0.50, 0.51)
    wide = R.maker_pair_cost(0.46, 0.55)
    assert wide < tight, "more spread must mean more matched-pair margin"


def test_a_one_sided_book_has_NO_pair_cost_not_a_zero_one():
    """A missing side must not read as a free or perfect pair -- that is how
    an uncompletable market would rank first."""
    assert R.maker_pair_cost(0.40, None) is None
    assert R.maker_pair_cost(None, 0.41) is None


def test_touch_reports_an_absent_side_as_None_never_0():
    assert R.touch([]) == (None, None)
    assert R.touch(None) == (None, None)
    px, qty = R.touch([{"px": {"value": "0.4000"}, "qty": "10502.01"}])
    assert (px, qty) == (0.40, 10502.01)


# ------------------------------------------------------------- flow is real
def test_flow_is_traded_notional_per_minute():
    a = {"notional_traded": "1000", "transact_time": "2026-09-10T18:00:00Z"}
    b = {"notional_traded": "3000", "transact_time": "2026-09-10T18:10:00Z"}
    assert R.flow_usd_per_min(a, b) == pytest.approx(200.0)


def test_unmeasurable_flow_is_None_not_zero():
    """'we could not establish it' and 'nothing traded' must never collapse:
    the first is ignorance, the second is evidence."""
    a = {"transact_time": "2026-09-10T18:00:00Z"}
    b = {"notional_traded": "3000", "transact_time": "2026-09-10T18:10:00Z"}
    assert R.flow_usd_per_min(a, b) is None
    same = {"notional_traded": "1", "transact_time": "2026-09-10T18:00:00Z"}
    assert R.flow_usd_per_min(same, same) is None, "a zero window is not a rate"


def test_a_genuinely_quiet_market_reports_zero_flow():
    a = {"notional_traded": "500", "transact_time": "2026-09-10T18:00:00Z"}
    b = {"notional_traded": "500", "transact_time": "2026-09-10T18:10:00Z"}
    assert R.flow_usd_per_min(a, b) == 0.0


# -------------------------------------------------------- hour-shape and band
def _hist(pairs):
    return [{"longPrice": a, "shortPrice": b} for a, b in pairs]


def test_hour_shape_reads_the_venues_long_short_convention():
    """long = YES ask, short = 1 - YES bid. bid 0.35 / ask 0.355 -> mid
    0.3525 and spread 0.005."""
    out = R.hour_shape(_hist([(0.355, 0.65)] * 4))
    assert out["median_spread"] == pytest.approx(0.005)
    assert out["in_band_fraction"] == 1.0


def test_band_occupancy_counts_only_the_R5F_band():
    out = R.hour_shape(_hist([(0.90, 0.11), (0.50, 0.51),
                              (0.50, 0.51), (0.05, 0.96)]))
    assert out["in_band_fraction"] == pytest.approx(0.5)


def test_empty_history_yields_None_shape_not_a_confident_zero():
    out = R.hour_shape([])
    assert out["points"] == 0
    assert out["in_band_fraction"] is None and out["mid_stdev"] is None


# ------------------------------------------------------- games, not futures
def test_futures_are_excluded_by_TYPE_not_by_date():
    """A season future carries a gameStartTime like any game, so a date-based
    filter would let it through."""
    fut = {"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE",
           "gameStartTime": "2026-09-10T00:00:00Z"}
    game = {"sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
            "gameStartTime": "2027-01-01T00:00:00Z"}
    assert R.is_game(fut) is False
    assert R.is_game(game) is True


# ------------------------------------------------------------- eligibility
def _row(**kw):
    base = {"state": "MARKET_STATE_OPEN", "bid": 0.40, "ask": 0.41,
            "maker_pair_cost": 0.99, "in_band_fraction": 1.0,
            "flow_usd_per_min": 100.0}
    base.update(kw)
    return base


def test_an_eligible_row_passes_every_gate():
    out = R.eligibility(_row())
    assert out["eligible"] and out["failed"] == []


@pytest.mark.parametrize("kw,expected", [
    ({"state": "MARKET_STATE_EXPIRED"}, {"state_open"}),
    # a missing side fails BOTH gates, and should: there is no pair cost
    # either. Asserting one name here would have been asserting a bug.
    ({"ask": None, "maker_pair_cost": None},
     {"two_sided", "pair_at_or_below_ceiling"}),
    ({"maker_pair_cost": 1.02}, {"pair_at_or_below_ceiling"}),
    ({"in_band_fraction": 0.1}, {"in_band"}),
    ({"flow_usd_per_min": 0.0}, {"flow_observed"}),
])
def test_each_gate_names_itself_when_it_fails(kw, expected):
    """An empty ranking must say WHICH gate emptied it."""
    out = R.eligibility(_row(**kw))
    assert not out["eligible"]
    assert set(out["failed"]) == expected


def test_unmeasured_flow_is_not_eligible():
    assert not R.eligibility(_row(flow_usd_per_min=None))["eligible"]


# ------------------------------------------------------------------ ranking
def test_rank_orders_by_contemporaneous_flow_and_drops_ineligible():
    rows = [_row(slug="quiet", flow_usd_per_min=5.0),
            _row(slug="busy", flow_usd_per_min=5000.0),
            _row(slug="out_of_band", flow_usd_per_min=9e9, in_band_fraction=0.0)]
    got = [r["slug"] for r in R.rank(rows)]
    assert got == ["busy", "quiet"], "out_of_band is unenterable however busy"


def test_ranking_ignores_incentive_program_entirely():
    """OWNER: do not optimize for liquidity rewards. Carrying the flag must
    not move a market."""
    a = _row(slug="a", flow_usd_per_min=10.0, incentive_program=True)
    b = _row(slug="b", flow_usd_per_min=20.0, incentive_program=False)
    assert [r["slug"] for r in R.rank([a, b])] == ["b", "a"]


def test_ranking_does_not_reward_attempt_count():
    """Nothing in the ordering key counts sessions or attempts."""
    import inspect
    src = inspect.getsource(R.rank)
    for token in ("session", "attempt", "count", "reward", "pool"):
        assert token not in src.split('"""')[2], f"{token} leaked into ordering"
