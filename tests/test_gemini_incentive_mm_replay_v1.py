from decimal import Decimal as D

from src.gemini_incentive_mm_replay_v1 import Ledger, projected_maker_rebate


def test_projected_rebate_respects_price_band_and_rounding():
    assert projected_maker_rebate(D('0.50'), D('10')) == D('0.05')
    assert projected_maker_rebate(D('0.10'), D('10')) == D('0')


def test_flat_book_quotes_both_sides_at_reward_minimum_size():
    ledger = Ledger()
    ledger.on_book(D('0.49'), D('0.51'))
    assert ledger.yes_quote == D('0.49')
    assert ledger.no_quote == D('0.49')
    assert ledger.quote_size == D('10')
    assert ledger.eligible_book_updates == 1


def test_one_side_fill_stops_same_side_accumulation():
    ledger = Ledger()
    ledger.on_book(D('0.49'), D('0.51'))
    ledger.on_trade(D('0.49'), D('10'), True)
    assert ledger.residual_side == 'YES'
    assert ledger.residual_qty == D('10')
    ledger.on_book(D('0.49'), D('0.51'))
    assert ledger.yes_quote is None


def test_profitable_completion_creates_trade_edge_without_rebate_credit():
    ledger = Ledger()
    ledger.on_book(D('0.40'), D('0.42'))
    ledger.on_trade(D('0.40'), D('10'), True)  # YES maker fill
    # Market moves: NO best maker bid = 0.57. Combined raw price 0.97.
    ledger.on_book(D('0.41'), D('0.43'))
    assert ledger.no_quote == D('0.57')
    ledger.on_trade(D('0.43'), D('10'), False)  # seller maker condition for NO bid
    summary = ledger.summary()
    assert D(summary['locked_trade_edge_after_fees']) > D('0')
    assert D(summary['projected_maker_rebates_not_realized']) >= D('0')
    assert summary['residual_side'] is None


def test_unprofitable_complement_is_blocked_even_if_rebate_exists():
    ledger = Ledger(min_trade_edge_per_pair=D('0.00'))
    ledger.on_book(D('0.60'), D('0.62'))
    ledger.on_trade(D('0.60'), D('10'), True)
    # NO bid = .40; raw pair is exactly par before maker fees, therefore negative after fees.
    ledger.on_book(D('0.59'), D('0.60'))
    assert ledger.no_quote is None
    assert ledger.blocked_unprofitable_completion >= 1
