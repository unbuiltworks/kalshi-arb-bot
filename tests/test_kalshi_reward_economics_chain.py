"""The chain's job is to keep three statuses apart and to leave open links
open. These tests pin that, not the arithmetic."""
from decimal import Decimal as D

from src import kalshi_reward_economics_chain as K


# ------------------------------------------------- the three statuses
def test_headline_estimated_and_credited_are_three_separate_statuses():
    ch = K.chain()
    # headline is measured
    assert ch["1_headline_pool_usd_per_market_period"]["KXCRYPTOLEAD15M"] == "20.00"
    # estimated obtainable is refused
    assert ch["4_estimated_obtainable_usd"] is None
    assert K.ESTIMATED_OBTAINABLE_USD is None
    # credited is zero because no order was ever placed
    assert ch["credited_reward_usd"] == "0.00"
    assert K.CREDITED_REWARD_USD == D("0.00")


def test_credited_is_zero_because_order_entry_is_off_not_because_we_lost():
    assert K.ORDER_ENTRY is False
    assert K.SHADOW_ONLY is True
    assert K.chain()["realized_pnl"] is None


def test_the_module_never_states_an_expected_personal_revenue():
    """The owner asked explicitly not to convert the pool into revenue yet."""
    ch = K.chain()
    assert ch["4_estimated_obtainable_usd"] is None
    assert ch["6_net_shadow_economics"] is None
    assert K.NET_SHADOW_ECONOMICS is None
    assert K.P0_VERDICT == "UNDECIDED"


# ------------------------------------------------------- link 1, measured
def test_the_reward_period_is_fifteen_minutes_with_its_evidence():
    assert K.REWARD_PERIOD_MINUTES == 15
    assert "23:45:00Z" in K.REWARD_PERIOD_EVIDENCE
    assert "00:00:00Z" in K.REWARD_PERIOD_EVIDENCE


def test_headline_per_hour_scales_by_concurrent_markets_and_periods():
    # 5 concurrent legs x $20 x 4 periods an hour
    assert K.headline_usd_per_hour("KXCRYPTOLEAD15M") == D("400.00")
    assert K.headline_usd_per_hour("KXGOLD15M") == D("80.00")


def test_the_withdrawn_control_pair_returns_none_not_zero():
    """A withdrawn subsidy and an absent one are different facts."""
    for s in ("KXBTC15M", "KXETH15M"):
        assert K.headline_usd_per_hour(s) is None
        assert K.HEADLINE[s]["reward_usd"] is None
        assert "WITHDRAWN" in K.HEADLINE[s]["incentive_type"]


# --------------------------------------------- link 2, and the correction
def test_every_observed_active_program_clears_target_size():
    assert K.qualifying_fraction() == D(1)
    for t, v in K.QUALIFYING.items():
        assert v["min_full"] >= v["target"], t


def test_the_touch_based_reading_is_recorded_as_wrong_not_deleted():
    w = K.TOUCH_READING_WAS_WRONG
    assert "TOUCH" in w
    assert "10-16 contracts" in w
    assert "measurement mistake" in w


# ------------------------------------------------------------ link 3 and 4
def test_the_band_is_labelled_a_proxy_for_a_rule_we_do_not_have():
    assert "PROXY" in K.BAND_PROXY
    assert K.DISCOUNT_FACTOR_MEANING.startswith("UNKNOWN")


def test_the_refusal_to_divide_is_argued_not_merely_asserted():
    w = K.WHY_NOT_JUST_DIVIDE
    assert "absurd quotient" in w
    assert "denominator is wrong" in w
    assert len(K.OBTAINABLE_BLOCKERS) >= 4


# ---------------------------------------------------- link 5, measurable
def test_drag_is_fee_independent_and_signed_negative():
    for t in K.PAIR_COST:
        assert K.drag_per_turnover_dollar(t) < 0, t
    assert K.drag_per_turnover_dollar("not a ticker") is None


def test_the_wide_cryptolead_legs_are_worse_than_our_own_failed_lane():
    hype = K.drag_per_turnover_dollar("KXCRYPTOLEAD15M-26SEP082015-HYPE")
    sol = K.drag_per_turnover_dollar("KXCRYPTOLEAD15M-26SEP082015-SOL")
    assert hype < K.PMUS_OUR_TRADING_DRAG_PER_DOLLAR
    assert sol < K.PMUS_OUR_TRADING_DRAG_PER_DOLLAR


def test_the_tight_legs_are_still_far_worse_than_l5s_own_drag():
    best = K.drag_range()["best"]
    assert best < K.L5_TRADING_DRAG_PER_DOLLAR
    assert best > K.PMUS_OUR_TRADING_DRAG_PER_DOLLAR


# --------------------------------------------------------- link 6, open
def test_net_is_open_because_two_named_terms_are_open():
    assert K.NET_SHADOW_ECONOMICS is None
    joined = " ".join(K.NET_OPEN_TERMS)
    assert "no maker schedule is certified" in joined
    assert "$0.00 credited" in joined


def test_undecided_names_an_experiment_that_costs_no_money():
    a = K.P0_ANSWER
    assert a["verdict"].startswith("UNDECIDED")
    assert "not REJECTED" in a["verdict"]
    assert "without an order" in a["next_step_costs_no_money"]


def test_the_venue_wide_lead_is_flagged_incidental():
    v = K.VENUE_WIDE_LEAD
    assert "NOT measured" in v["note"]
    assert "NOT a recommendation" in v["note"]
    assert v["KXDEEPSHARE"]["reward_usd"] == D("200.00")


def test_provenance_names_the_deployment_and_the_artifact():
    assert "e63f3f58" in K.SOURCE["structure"]
    assert K.SOURCE["snapshot_artifact"].endswith("qualifying_snapshot.json")
    assert "centi-cents" in K.SOURCE["reward_unit"]
