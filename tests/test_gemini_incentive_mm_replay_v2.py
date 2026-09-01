from decimal import Decimal as D

from src.gemini_incentive_mm_replay_v2 import Market


def test_strict_does_not_fill_on_touch_only():
    market = Market('X')
    market.on_book(D('0.49'), D('0.51'))
    market.on_book(D('0.48'), D('0.49'))  # ask equals prior YES quote, not through
    assert market.strict_trade_through.residual_side is None


def test_strict_yes_trade_through_opens_bounded_q10_residual():
    market = Market('X')
    market.on_book(D('0.49'), D('0.51'))
    market.on_book(D('0.47'), D('0.48'))  # ask below prior YES quote .49
    strict = market.strict_trade_through
    assert strict.residual_side == 'YES'
    assert strict.residual_qty == D('10')
    assert strict.yes_quote is None


def test_strict_no_trade_through_opens_bounded_q10_residual():
    market = Market('X')
    market.on_book(D('0.49'), D('0.51'))  # NO bid .49, YES equivalent .51
    market.on_book(D('0.52'), D('0.54'))  # bid above prior .51 equivalent
    strict = market.strict_trade_through
    assert strict.residual_side == 'NO'
    assert strict.residual_qty == D('10')
    assert strict.no_quote is None


def test_strict_profitable_complement_can_complete_pair():
    market = Market('X')
    # Rest YES .40 / NO .58, then strict YES fill.
    market.on_book(D('0.40'), D('0.42'))
    market.on_book(D('0.37'), D('0.39'))
    strict = market.strict_trade_through
    assert strict.residual_side == 'YES'

    # New plan works the missing NO side only if the pair is non-negative.
    # With held YES .40, book ask .43 gives NO maker bid .57.
    market.on_book(D('0.41'), D('0.43'))
    assert strict.no_quote == D('0.57')
    # NO quote .57 is YES-equivalent .43; bid moving above .43 is trade-through.
    market.on_book(D('0.44'), D('0.46'))
    assert strict.residual_side is None
    assert len(strict.pairs) == 1
    assert strict.pairs[0].trade_edge > D('0')


def test_strict_blocks_negative_completion_even_with_projected_rebate():
    market = Market('X')
    market.on_book(D('0.60'), D('0.62'))
    market.on_book(D('0.57'), D('0.59'))  # strict YES fill at .60
    strict = market.strict_trade_through
    assert strict.residual_side == 'YES'
    # A 40c NO would make the raw pair exactly $1; maker fees make it negative.
    market.on_book(D('0.58'), D('0.60'))
    assert strict.no_quote is None
    assert strict.blocked_unprofitable_completion >= 1
