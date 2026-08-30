from decimal import Decimal as D

from src import gemini_q10_rescue_policy as r


def test_same_book_side_routes_are_both_safe_at_unchanged_yes_fill():
    complete, unwind = r.routes_after_fill(
        filled_outcome='YES', filled_price=D('0.48'), quantity=D('10'),
        yes_bid=D('0.48'), yes_bid_size=D('100'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert complete.safe
    assert unwind.safe
    assert complete.current_execution_price == D('0.52')
    assert unwind.current_execution_price == D('0.48')
    assert complete.loss_at_current == unwind.loss_at_current == D('0.23')
    assert complete.limit_price == D('0.52')
    assert unwind.limit_price == D('0.48')


def test_best_route_prefers_unwind_on_economic_tie():
    route = r.best_route_after_fill(
        filled_outcome='YES', filled_price=D('0.48'), quantity=D('10'),
        yes_bid=D('0.48'), yes_bid_size=D('100'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert route.safe
    assert route.action == 'UNWIND'
    assert route.side == 'SELL'
    assert route.outcome == 'YES'


def test_one_adverse_tick_breaks_30c_yes_rescue_bound_at_q10():
    route = r.best_route_after_fill(
        filled_outcome='YES', filled_price=D('0.48'), quantity=D('10'),
        yes_bid=D('0.47'), yes_bid_size=D('100'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert not route.safe
    assert route.loss_at_current == D('0.33')


def test_one_adverse_tick_breaks_30c_no_rescue_bound_at_q10():
    route = r.best_route_after_fill(
        filled_outcome='NO', filled_price=D('0.39'), quantity=D('10'),
        yes_bid=D('0.48'), yes_bid_size=D('100'), yes_ask=D('0.62'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert not route.safe
    assert route.loss_at_current == D('0.32')


def test_full_q10_top_depth_is_a_standing_precondition():
    check = r.standing_rescue_check(
        yes_quote=D('0.48'), no_quote=D('0.39'), quantity=D('10'),
        yes_bid=D('0.48'), yes_bid_size=D('9'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert not check.safe
    assert not check.yes_fill_route.depth_ok
    assert check.no_fill_route.depth_ok


def test_standing_check_requires_both_possible_first_fills_safe():
    good = r.standing_rescue_check(
        yes_quote=D('0.48'), no_quote=D('0.39'), quantity=D('10'),
        yes_bid=D('0.48'), yes_bid_size=D('100'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert good.safe
    assert good.worst_current_loss == D('0.23')

    bad = r.standing_rescue_check(
        yes_quote=D('0.48'), no_quote=D('0.39'), quantity=D('10'),
        yes_bid=D('0.47'), yes_bid_size=D('100'), yes_ask=D('0.61'), yes_ask_size=D('100'),
        max_loss=D('0.30'), increment=D('0.01'),
    )
    assert not bad.safe
    assert not bad.yes_fill_route.safe


def test_toxic_fill_has_zero_maker_grace():
    assert r.maker_grace_seconds(spot_dangerous=True, calm_grace_seconds=1.5) == 0.0
    assert r.maker_grace_seconds(spot_dangerous=False, calm_grace_seconds=1.5) == 1.5
