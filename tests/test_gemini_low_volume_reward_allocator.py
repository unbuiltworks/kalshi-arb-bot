from decimal import Decimal as D

from src.gemini_low_volume_reward_allocator import Candidate, allocate, eligible, reward_proxy_usd


def c(ticker='BTC05M-A', asset='BTC', pool='250', makers=0, yes='0.32', no='0.62', uptime='0.60', pair='0.54'):
    return Candidate(
        ticker=ticker, symbol='GEMI-'+ticker+'-UP', asset=asset, window_minutes=5,
        yes_quote=D(yes), no_quote=D(no), daily_pool_usd=D(pool),
        qualifying_makers=makers, planned_uptime=D(uptime), modeled_pair_profit=D(pair),
    )


def test_rejects_planned_uptime_below_lr_threshold():
    ok, reason = eligible(c(uptime='0.49'))
    assert ok is False
    assert reason == 'UPTIME_BELOW_50_PERCENT'


def test_scarcity_prefers_fewer_makers_all_else_equal():
    zero = c(ticker='A', makers=0)
    five = c(ticker='B', makers=5)
    assert reward_proxy_usd(zero) > reward_proxy_usd(five)


def test_allocator_reserves_both_outcomes_and_keeps_safety_cash():
    out = allocate([c()], D('57.1455'), safety_cash=D('12'))
    assert len(out['selected']) == 1
    assert D(out['selected'][0]['reserve_usd']) == D('9.40')
    assert D(out['reserved_usd']) == D('9.40')
    assert D(out['unreserved_allocatable_usd']) == D('35.7455')
    assert out['reward_proxy_is_realized'] is False
    assert out['selected'][0]['reward_proxy_is_realized'] is False


def test_correlated_asset_window_cap_prevents_stacking_btc():
    a = c(ticker='BTC05M-A', pool='250')
    b = c(ticker='BTC15M-B', pool='500')
    out = allocate([a,b], D('100'), max_same_asset_windows=1)
    assert len(out['selected']) == 1
    assert any(r['reason']=='CORRELATED_ASSET_WINDOW_CAP' for r in out['rejected'])


def test_allocator_can_parallelize_distinct_assets_under_budget():
    btc = c(ticker='BTC05M-A', asset='BTC', pool='250')
    eth = c(ticker='ETH05M-A', asset='ETH', pool='200', yes='0.40', no='0.50')
    out = allocate([btc,eth], D('57.1455'), safety_cash=D('12'))
    assert {x['asset'] for x in out['selected']} == {'BTC','ETH'}


def test_outside_rebate_band_is_rejected():
    out = allocate([c(yes='0.19', no='0.70')], D('100'))
    assert out['selected'] == []
    assert out['rejected'][0]['reason'] == 'OUTSIDE_MAKER_REBATE_PRICE_BAND'
