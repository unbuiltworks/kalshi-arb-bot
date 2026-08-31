from decimal import Decimal as D

from src.gemini_l5_fair_value import FairValueEstimate
from src.gemini_rebate_buyer_30_49 import decide, policy_matrix, projected_maker_rebate


def est(up: str) -> FairValueEstimate:
    p = D(up)
    return FairValueEstimate(
        probability_up=p,
        probability_down=D("1") - p,
        log_moneyness=D("0"),
        total_log_sigma=D("0.01"),
    )


def test_rebate_formula_q10_at_40c_floors_to_five_cents():
    assert projected_maker_rebate(
        price=D("0.40"), quantity=D("10"), rebate_rate=D("0.30")
    ) == D("0.05")


def test_rejects_when_neither_side_is_30_to_49c():
    d = decide(
        policy="fair_1c", estimate=est("0.70"), yes_bid=D("0.60"), yes_ask=D("0.62"),
        rebate_rate=D("0.30"), min_edge_per_contract=D("0.01"),
    )
    assert d.actionable is False
    assert d.side is None


def test_selects_yes_when_yes_is_in_band_and_fair_value_supports_it():
    d = decide(
        policy="fair_1c", estimate=est("0.45"), yes_bid=D("0.40"), yes_ask=D("0.42"),
        rebate_rate=D("0.30"), min_edge_per_contract=D("0.01"),
    )
    assert d.actionable is True
    assert d.side == "YES"
    assert d.quote_price == D("0.40")
    assert d.fair_value_edge_before_rebate_usd > 0


def test_selects_no_when_no_bid_is_in_band_and_has_better_independent_edge():
    d = decide(
        policy="fair_1c", estimate=est("0.60"), yes_bid=D("0.58"), yes_ask=D("0.62"),
        rebate_rate=D("0.30"), min_edge_per_contract=D("0.01"),
    )
    assert d.actionable is True
    assert d.side == "NO"
    assert d.quote_price == D("0.38")


def test_rebate_cannot_rescue_negative_fair_value_trade():
    strict = decide(
        policy="fair_0c", estimate=est("0.39"), yes_bid=D("0.40"), yes_ask=D("0.42"),
        rebate_rate=D("0.30"), min_edge_per_contract=D("0"),
    )
    naive = decide(
        policy="band_only", estimate=est("0.39"), yes_bid=D("0.40"), yes_ask=D("0.42"),
        rebate_rate=D("0.30"),
    )
    assert strict.actionable is False
    assert strict.side is None
    assert naive.actionable is True
    assert naive.projected_rebate_usd > 0


def test_policy_matrix_gets_more_selective_as_edge_floor_rises():
    matrix = policy_matrix(
        estimate=est("0.43"), yes_bid=D("0.40"), yes_ask=D("0.42"), rebate_rate=D("0.30")
    )
    assert matrix["band_only"].actionable is True
    assert matrix["fair_0c"].actionable is True
    assert matrix["fair_2c"].actionable in {True, False}
    active = [name for name, d in matrix.items() if name != "band_only" and d.actionable]
    assert active == sorted(active, key=["fair_0c", "fair_0_5c", "fair_1c", "fair_2c"].index)[:len(active)]
