"""The leg ranker's job is to refuse to average a good leg with a terrible
one, and to keep the terms it cannot measure visibly unmeasured."""
from decimal import Decimal as D

import pytest

from src import kalshi_leg_ranking as R


def _row(ticker, pair_cost, yes_bid, reward=200_000, target=1000.0, **kw):
    mid = 0.5
    r = {"ticker": ticker, "pair_cost": pair_cost, "yes_bid": yes_bid,
         "reward_raw": reward, "target": target}
    for b in ("1", "2", "5", "10"):
        r[f"band{b}_yes"] = kw.get(f"band{b}", 100.0)
        r[f"band{b}_no"] = kw.get(f"band{b}", 100.0)
    return r


# ------------------------------------------------------- the leg identity
def test_the_asset_is_the_leg_for_the_multi_asset_series():
    assert R.leg_key("KXCRYPTOLEAD15M-26SEP082015-BTC") == "KXCRYPTOLEAD15M:BTC"
    assert R.leg_key("KXCRYPTOLEAD15M-26SEP082015-HYPE") == "KXCRYPTOLEAD15M:HYPE"


def test_a_window_suffix_is_not_an_identity():
    """A commodity ticker's trailing segment is the window, which changes
    every 15 minutes. Keying on it would shatter one leg into hundreds."""
    a = R.leg_key("KXGOLD15M-26SEP082015-15")
    b = R.leg_key("KXGOLD15M-26SEP082030-15")
    assert a == b == "KXGOLD15M"
    assert R.leg_key("KXBTC15M-26SEP082030-T3") == "KXBTC15M"


def test_two_legs_of_one_series_never_collapse_together():
    tape = [_row("KXCRYPTOLEAD15M-26SEP082015-BTC", 1.01, 0.50),
            _row("KXCRYPTOLEAD15M-26SEP082015-HYPE", 1.16, 0.50)]
    out = R.rank_legs(tape)
    legs = {L["leg"] for L in out["legs"]}
    assert legs == {"KXCRYPTOLEAD15M:BTC", "KXCRYPTOLEAD15M:HYPE"}
    # and the terrible one must not be averaged into the good one
    by = {L["leg"]: L for L in out["legs"]}
    assert by["KXCRYPTOLEAD15M:BTC"]["pair_cost_median"] == "1.01"
    assert by["KXCRYPTOLEAD15M:HYPE"]["pair_cost_median"] == "1.16"


def test_there_is_no_series_rollup_to_hide_behind():
    out = R.rank_legs([_row("KXCRYPTOLEAD15M-26SEP082015-BTC", 1.01, 0.5)])
    assert out["unit_of_decision"] == "leg"
    assert "series" not in out


# --------------------------------------------------------------- the fees
def test_maker_fee_matches_the_published_table():
    for price, expected in (("0.10", "0.16"), ("0.25", "0.33"),
                            ("0.50", "0.44"), ("0.75", "0.33"),
                            ("0.90", "0.16")):
        assert R.maker_fee_dollars(D(100), D(price)) == D(expected), price


def test_the_fee_is_convex_so_it_separates_legs():
    """Peaks at 0.50. A leg resting at the wings pays materially less, which
    is why fees are a differentiator and not a constant."""
    mid = R.maker_fee_dollars(D(1000), D("0.50"))
    wing = R.maker_fee_dollars(D(1000), D("0.10"))
    assert wing < mid


def test_fees_are_derived_from_venue_metadata_not_assumed():
    assert "DERIVED FROM VENUE METADATA" in R.FEE_STATUS
    assert "not from a paid invoice" in R.FEE_STATUS
    for s, m in R.VENUE_FEE_METADATA.items():
        assert m["fee_type"] == "quadratic"
        assert m["fee_multiplier"] == 1
    assert len(R.VENUE_FEE_METADATA) == 8


def test_the_fee_reward_asymmetry_is_stated():
    """Fee is charged on FILL, reward is scored on RESTING. The equation
    hides that they are not the same event."""
    a = R.FEE_IS_ON_FILL_REWARD_IS_ON_REST
    assert "EXECUTED" in a and "RESTING" in a
    assert "not incurred by the same event" in a


# ------------------------------------------------- the paid_out dead end
def test_paid_out_is_recorded_as_a_lag_flag_not_a_credited_observation():
    p = R.PAID_OUT_IS_A_LAG_FLAG
    assert p["verdict"].startswith("FALSE")
    assert p["by_age_since_end"]["6-12h"] == "0.0%"
    assert p["by_age_since_end"]["24-48h"] == "100.0%"
    assert "n=147 / paid=94" in p["per_leg_tell"]


# --------------------------------------------------- the open denominator
def test_share_is_a_curve_not_an_invented_point():
    curve = R.band_share_curve(D("111"), D("1.01"),
                               {"1": D(0), "2": D(100), "5": D(1000),
                                "10": D(10000)})
    # a wider assumed band means more competition and a smaller share
    vals = [D(curve[b]) for b in ("1", "2", "5", "10")]
    assert vals == sorted(vals, reverse=True)
    assert vals[0] == D(1)          # no competition -> all of it
    assert vals[-1] < D("0.02")     # heavy competition -> nearly none


def test_residual_economics_is_none_and_says_why():
    assert R.RESIDUAL_ECONOMICS is None
    assert "UNMEASURED on this venue" in R.RESIDUAL_NOTE
    assert "not zero" in R.RESIDUAL_NOTE
    assert "-6.458%/$" in R.RESIDUAL_NOTE


def test_credited_incentives_are_zero_and_the_rank_excludes_reward():
    """Folding an unknown-share headline into the rank would launder a pool
    into revenue."""
    out = R.rank_legs([_row("KXWTI15M-26SEP082015-15", 1.01, 0.5)])
    assert R.CREDITED_INCENTIVES_USD == D("0.00")
    assert "never inside it" in out["ranked_on"]
    assert out["realized_pnl"] is None
    assert out["order_entry"] is False


# ------------------------------------------------------------- the ranking
def test_ranking_orders_by_measured_cost_best_first():
    tape = [_row("KXCRYPTOLEAD15M-26SEP082015-HYPE", 1.16, 0.50),
            _row("KXWTI15M-26SEP082015-15", 1.01, 0.50, target=300.0)]
    out = R.rank_legs(tape)
    assert out["legs"][0]["leg"] == "KXWTI15M"
    assert out["legs"][0]["rank_by_measured_cost"] == 1
    assert out["legs"][-1]["leg"] == "KXCRYPTOLEAD15M:HYPE"


def test_reward_density_rewards_a_smaller_target_for_the_same_pool():
    """The commodity legs carry the SAME $20 against target 300 rather than
    1000, so their headline per capital dollar is materially higher."""
    small = R.reward_density(D(20), D(300), D("1.01"))
    big = R.reward_density(D(20), D(1000), D("1.01"))
    assert small > big
    assert (small / big).quantize(D("0.01")) == D("3.33")


def test_a_leg_with_no_program_reports_none_not_zero():
    tape = [_row("KXBTC15M-26SEP082030-T3", 1.01, 0.5, reward=None, target=None)]
    out = R.rank_legs(tape)
    L = out["legs"][0]
    assert L["headline_reward_usd"] is None
    assert L["reward_density_per_capital_dollar"] is None


# -------------------------------------------- rule-independent share result
def _leg(name, d1, d2, d5, d10):
    return {"leg": name, "band_min_side_depth_median":
            {"1": str(d1), "2": str(d2), "5": str(d5), "10": str(d10)}}


def test_dominance_holds_when_one_leg_is_thinner_at_every_band():
    out = R.share_dominance([_leg("THIN", 1, 2, 3, 4),
                             _leg("THICK", 10, 20, 30, 40)])
    assert out["dominates"]["THIN"] == ["THICK"]
    assert out["ordering_depends_on_the_unknown_rule"] == []


def test_a_crossing_pair_is_reported_as_rule_dependent_not_ranked():
    """If A is thinner near the mid but thicker further out, the venue's
    unknown weighting genuinely decides. Say so rather than pick."""
    out = R.share_dominance([_leg("NEAR", 1, 2, 500, 900),
                             _leg("FAR", 10, 20, 30, 40)])
    assert out["dominates"] == {}
    assert out["ordering_depends_on_the_unknown_rule"] == [["FAR", "NEAR"]]


def test_dominance_claims_ordering_only_never_magnitude():
    out = R.share_dominance([_leg("A", 1, 2, 3, 4), _leg("B", 5, 6, 7, 8)])
    assert "non-increasing proximity weighting" in out["basis"]
    assert "a share MAGNITUDE" in out["what_this_does_not_give"]
    assert "Only the ordering is" in out["what_this_does_not_give"]


# ------------------------------------------------------------- the frontier
def test_the_tension_between_drag_and_share_is_stated_not_averaged_away():
    t = R.THE_TENSION
    assert "OPPOSITE DIRECTIONS" in t
    assert "cheap books are cheap precisely because" in t
    assert "no leg that is simply 'best'" in t


def test_frontier_keeps_the_two_axes_separate():
    legs = [
        {"leg": "CHEAP_CROWDED", "drag_per_dollar": "-0.005",
         "maker_fee_per_dollar_if_both_legs_fill": "0.001",
         "band_min_side_depth_median": {"1": "1790", "2": "4424",
                                        "5": "8443", "10": "8760"},
         "headline_reward_usd": "20"},
        {"leg": "WIDE_EMPTY", "drag_per_dollar": "-0.080",
         "maker_fee_per_dollar_if_both_legs_fill": "0.009",
         "band_min_side_depth_median": {"1": "0", "2": "0",
                                        "5": "0", "10": "457"},
         "headline_reward_usd": "20"},
    ]
    f = R.frontier(legs)
    # neither dominates the other, so both are on the frontier
    assert all(p["on_frontier"] for p in f)
    assert f[0]["leg"] == "CHEAP_CROWDED"          # sorted by cost


def test_a_leg_beaten_on_both_axes_is_off_the_frontier():
    legs = [
        {"leg": "GOOD", "drag_per_dollar": "-0.005",
         "maker_fee_per_dollar_if_both_legs_fill": "0.001",
         "band_min_side_depth_median": {"1": "10", "2": "10",
                                        "5": "10", "10": "10"}},
        {"leg": "DOMINATED", "drag_per_dollar": "-0.080",
         "maker_fee_per_dollar_if_both_legs_fill": "0.009",
         "band_min_side_depth_median": {"1": "999", "2": "999",
                                        "5": "999", "10": "999"}},
    ]
    f = {p["leg"]: p for p in R.frontier(legs)}
    assert f["GOOD"]["on_frontier"] is True
    assert f["DOMINATED"]["on_frontier"] is False


# ------------------------------------------------------ the payout floor
def test_the_floor_makes_the_denominator_binary_not_approximate():
    out = R.payout_floor_test(D(20), D(111), D("1.02"),
                              {"2": D("30.19"), "full": D("14804")})
    assert out["share_needed_for_any_payout"] == "0.0500"
    assert out["by_denominator"]["2"]["paid"] is True
    assert out["by_denominator"]["full"]["paid"] is False
    # forfeited in full, not paid small
    assert D(out["by_denominator"]["full"]["forfeited_usd"]) > 0
    assert D(out["by_denominator"]["full"]["gross_usd"]) < R.MIN_PAYOUT_USD


def test_a_sub_floor_payout_is_forfeited_entirely_not_prorated():
    out = R.payout_floor_test(D(20), D(111), D("1.01"), {"x": D("100000")})
    d = out["by_denominator"]["x"]
    assert d["paid"] is False
    assert d["forfeited_usd"] == d["gross_usd"]


def test_the_full_book_verdict_is_zero_on_every_leg():
    v = R.PAYOUT_FLOOR_VERDICT
    assert "EVERY LEG PAYS ZERO" in v["finding"]
    assert "forfeited in full, not paid small" in v["finding"]
    assert "BINARY" in v["consequence"]
    assert "one credited payout on a funded account" in v["what_settles_it"]


def test_the_floor_is_attributed_to_our_bankroll_not_the_venue():
    assert "property of OUR bankroll" in R.PAYOUT_FLOOR_VERDICT["note_on_capital"]
    assert R.MIN_PAYOUT_USD == D("1.00")
    assert "sub-$1" in R.MIN_PAYOUT_SOURCE


# ------------------------------------------------------- the leg verdict
def test_the_answer_names_copper_and_natgas_with_its_reasoning():
    v = R.LEG_VERDICT
    assert v["best_drag_with_meaningful_reward_density"] == ("KXCOPPER15M",
                                                             "KXNATGAS15M")
    assert "MIDDLE of the cost table" in v["why"]
    assert "TOP of the reward-density table" in v["why"]
    assert "3.4x" in v["why"]


def test_the_cheapest_legs_are_the_ones_that_stopped_paying():
    assert "withdrawn on 2026-05-12" in R.LEG_VERDICT["the_irony"]
    assert "stopped paying" in R.LEG_VERDICT["the_irony"]


def test_the_answer_does_not_claim_to_settle_the_venue():
    assert "does not settle the venue" in R.P0_LEG_ANSWER
    assert "below the payout floor on every leg" in R.P0_LEG_ANSWER
