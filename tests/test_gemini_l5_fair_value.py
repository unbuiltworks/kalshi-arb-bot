from decimal import Decimal as D

from src.gemini_l5_fair_value import estimate_up_probability, fair_value_gate


def test_probability_moves_with_strike_distance_and_time():
    above = estimate_up_probability(
        settlement_proxy_price=D("100.5"),
        strike_price=D("100"),
        seconds_to_expiry=60,
        log_sigma_per_sqrt_second=D("0.0005"),
        settlement_basis_sigma_bps=D("2"),
    )
    below = estimate_up_probability(
        settlement_proxy_price=D("99.5"),
        strike_price=D("100"),
        seconds_to_expiry=60,
        log_sigma_per_sqrt_second=D("0.0005"),
        settlement_basis_sigma_bps=D("2"),
    )
    assert above.probability_up > D("0.5")
    assert below.probability_up < D("0.5")


def test_basis_uncertainty_pulls_extreme_probability_toward_half():
    tight = estimate_up_probability(
        settlement_proxy_price=D("100.2"),
        strike_price=D("100"),
        seconds_to_expiry=10,
        log_sigma_per_sqrt_second=D("0.0001"),
        settlement_basis_sigma_bps=D("0"),
    )
    wide = estimate_up_probability(
        settlement_proxy_price=D("100.2"),
        strike_price=D("100"),
        seconds_to_expiry=10,
        log_sigma_per_sqrt_second=D("0.0001"),
        settlement_basis_sigma_bps=D("20"),
    )
    assert abs(wide.probability_up - D("0.5")) < abs(tight.probability_up - D("0.5"))


def test_fair_value_gate_requires_after_fee_edge_without_counting_rebate():
    estimate = estimate_up_probability(
        settlement_proxy_price=D("101"),
        strike_price=D("100"),
        seconds_to_expiry=30,
        log_sigma_per_sqrt_second=D("0.0005"),
        settlement_basis_sigma_bps=D("2"),
    )
    decision = fair_value_gate(
        estimate=estimate,
        yes_maker_quote=D("0.70"),
        no_maker_quote=D("0.28"),
        min_net_edge=D("0.03"),
    )
    assert decision.actionable is True
    assert decision.side == "YES"
    assert decision.net_edge_before_rebate >= D("0.03")


def test_fair_value_gate_refuses_market_midpoint_without_independent_edge():
    estimate = estimate_up_probability(
        settlement_proxy_price=D("100"),
        strike_price=D("100"),
        seconds_to_expiry=60,
        log_sigma_per_sqrt_second=D("0.001"),
        settlement_basis_sigma_bps=D("5"),
    )
    decision = fair_value_gate(
        estimate=estimate,
        yes_maker_quote=D("0.49"),
        no_maker_quote=D("0.49"),
        min_net_edge=D("0.03"),
    )
    assert decision.actionable is False
    assert decision.side is None
