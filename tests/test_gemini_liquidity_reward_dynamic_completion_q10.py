from decimal import Decimal as D
import pytest

from src import gemini_liquidity_reward_dynamic_completion_q10 as p


def test_time_dependent_profit_floors():
    assert p.time_dependent_total_profit_floor(180) == D('0.50')
    assert p.time_dependent_total_profit_floor(90) == D('0.25')
    assert p.time_dependent_total_profit_floor(30) == D('0.00')
    assert p.time_dependent_total_profit_floor(180, quantity=D('4')) == D('0.20')
    with pytest.raises(ValueError):
        p.time_dependent_total_profit_floor(19.9)


def test_exact_probe_can_spend_edge_as_time_runs_down():
    # First live q10 probe: YES filled 48c, NO began at 39c.
    early = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=180,
    )
    late = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=45,
    )
    assert early['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert late['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert late['target_price'] >= early['target_price']
    assert early['projected_total_profit'] >= D('0.50')
    assert late['projected_total_profit'] >= D('0.00')


def test_never_crosses_maker_ceiling():
    plan = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.55'), yes_ask=D('0.60'), seconds_to_expiry=90,
    )
    assert plan['target_price'] <= plan['passive_ceiling']


def test_completion_can_leave_reward_band_when_risk_reduction_requires_it():
    # Entry quotes must stay in the 20c-80c rebate/reward band, but after a fill
    # the risk-reduction order is governed by maker passivity + pair economics.
    ceiling = p.max_profit_safe_complement_price(
        filled_price=D('0.05'), min_total_profit=D('0.00')
    )
    assert ceiling is not None
    assert ceiling > D('0.80')
    assert ceiling <= D('0.99')


def test_partial_first_fill_is_completed_at_exact_quantity_economics():
    q = D('4')
    plan = p.completion_plan(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=180, quantity=q,
    )
    assert plan['quantity'] == q
    assert plan['projected_total_profit'] >= D('0.20')
    assert p.completed_pair_profit(
        filled_price=D('0.48'), complement_price=plan['target_price'], quantity=q
    ) == plan['projected_total_profit']


def test_completion_quantity_cannot_exceed_initial_q10_cap():
    with pytest.raises(ValueError):
        p.completed_pair_profit(
            filled_price=D('0.48'), complement_price=D('0.40'), quantity=D('11')
        )
