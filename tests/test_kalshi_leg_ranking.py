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
