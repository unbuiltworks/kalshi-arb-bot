from dataclasses import dataclass
from decimal import Decimal as D

from src.gemini_incentive_mm_shadow_v2 import OnlineMarket, build_summary


@dataclass
class FakeEvent:
    ticker: str = 'BTC05MTEST'
    symbol: str = 'GEMI-BTC05MTEST-UP'
    asset: str = 'BTC'
    horizon: str = '5m'


def market() -> OnlineMarket:
    return OnlineMarket.create(FakeEvent())


def test_online_book_emits_plan_and_strict_fill_only_on_trade_through():
    state = market()
    rows = state.on_book(D('0.49'), D('0.51'), 't0')
    assert rows[-1]['type'] == 'INCENTIVE_MM_SHADOW_PLAN'
    assert not [r for r in rows if r['type'] == 'INCENTIVE_MM_SHADOW_FILL']

    rows = state.on_book(D('0.47'), D('0.48'), 't1')
    fills = [r for r in rows if r['type'] == 'INCENTIVE_MM_SHADOW_FILL']
    assert len(fills) == 1
    assert fills[0]['ledger'] == 'STRICT_TRADE_THROUGH'
    assert fills[0]['side'] == 'YES'
    assert fills[0]['quantity'] == '10'


def test_online_tape_partial_fill_does_not_touch_strict_ledger():
    state = market()
    state.on_book(D('0.49'), D('0.51'), 't0')
    rows = state.on_trade(D('0.49'), D('4.2'), True, 't1')
    assert len(rows) == 1
    assert rows[0]['ledger'] == 'TAPE_TOUCH_OPTIMISTIC'
    assert rows[0]['quantity'] == '4.2'
    assert state.policy.strict_trade_through.residual_qty == D('0')


def test_summary_keeps_projected_rewards_separate_from_realized_pnl():
    state = market()
    state.on_book(D('0.49'), D('0.51'), 't0')
    state.on_trade(D('0.49'), D('10'), True, 't1')
    summary = build_summary(
        stats={'messages': 1, 'book_tickers': 1, 'trades': 1, 'errors': 0,
               'discoveries': 1, 'subscriptions': 2, 'rest_seeds': 1,
               'control_messages': 0, 'subscription_errors': 0,
               'ws_connections': 1, 'ws_disconnects': 0, 'ws_reconnects': 0,
               'checkpoints': 1},
        states={state.event.symbol: state},
        seconds=120,
        discover_s=10,
        status='RUNNING',
    )
    assert summary['order_entry'] is False
    assert summary['credentials_loaded'] is False
    assert summary['realized_pnl'] is None
    assert D(summary['tape_touch']['projected_maker_rebates_not_realized']) >= D('0')
    assert summary['strict_trade_through']['fill_count'] == 0
