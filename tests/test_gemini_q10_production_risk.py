from decimal import Decimal as D

from src import gemini_q10_production_risk as r


def test_residual_bound_is_q10_hard_cap():
    assert r.residual_safe(D('10'), D('0'))
    assert r.residual_safe(D('10'), D('10'))
    assert not r.residual_safe(D('10.01'), D('0'))
    assert not r.residual_safe(D('11'), D('0'))


def test_spot_momentum_guard_goes_dark_on_fast_move():
    g = r.SpotMomentumGuard(window_seconds=1.0, threshold_bps=D('4'))
    assert g.observe(0.0, D('100000')) is False
    assert g.observe(0.4, D('100020')) is False  # 2 bps
    assert g.observe(0.8, D('100050')) is True   # 5 bps from oldest retained


def test_spot_momentum_guard_forgets_old_move():
    g = r.SpotMomentumGuard(window_seconds=1.0, threshold_bps=D('4'))
    assert g.observe(0.0, D('100000')) is False
    assert g.observe(2.0, D('100050')) is False


def test_exact_live_probe_dynamic_completion_preserves_floor():
    early = r.completion_decision(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=180,
    )
    late = r.completion_decision(
        filled_outcome='YES', filled_price=D('0.48'), current_complement_price=D('0.39'),
        yes_bid=D('0.48'), yes_ask=D('0.61'), seconds_to_expiry=45,
    )
    assert early['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert late['action'] == 'REPRICE_MAKER_COMPLEMENT'
    assert early['projected_total_profit'] >= D('0.50')
    assert late['projected_total_profit'] >= D('0.00')
    assert late['target_price'] >= early['target_price']
