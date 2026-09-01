from decimal import Decimal as D

from src.gemini_l5_inventory_replay_v4 import V4Market


def _market() -> V4Market:
    return V4Market(
        ticker="BTC05MTEST",
        symbol="GEMI-BTC05MTEST-UP",
        asset="BTC",
        horizon="5m",
    )


def test_v4_never_adds_same_side_while_residual_exists():
    market = _market()
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=180)
    assert market.tape.quote_side == "YES"
    assert market.tape.quote_price == D("0.64")

    market.on_trade(price=D("0.64"), quantity=D("1"), buyer_maker=True)
    assert market.tape.residual_side == "YES"
    assert market.tape.yes_qty == D("1")

    # Even if YES gets more favored, V4 is no longer allowed to add naked YES.
    market.on_book(bid=D("0.78"), ask=D("0.80"), seconds_to_expiry=150)
    assert market.tape.quote_side != "YES"
    assert market.tape.yes_qty == D("1")


def test_v4_refuses_expensive_complement_that_would_lock_a_loss():
    market = _market()
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=180)
    market.on_trade(price=D("0.64"), quantity=D("1"), buyer_maker=True)

    # NO resting around 59c would make the pair far above $1 after fees.
    market.on_book(bid=D("0.39"), ask=D("0.41"), seconds_to_expiry=140)
    assert market.tape.residual_side == "YES"
    assert market.tape.quote_side is None
    assert market.tape.quote_mode == "WAIT_FOR_COMPLEMENT"
    assert market.tape.pairs == []


def test_v4_completes_only_when_pair_clears_three_cent_floor_after_fees():
    market = _market()
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=180)
    market.on_trade(price=D("0.64"), quantity=D("1"), buyer_maker=True)

    # YES book 70/72 means NO maker can rest around 29c; that is cheap enough
    # to turn the 64c YES residual into a profitable complete set.
    market.on_book(bid=D("0.70"), ask=D("0.72"), seconds_to_expiry=120)
    assert market.tape.quote_side == "NO"
    assert market.tape.quote_price is not None
    assert market.tape.quote_price <= D("0.31")

    yes_trade_equiv = D("1") - market.tape.quote_price
    market.on_trade(price=yes_trade_equiv, quantity=D("1"), buyer_maker=False)

    assert market.tape.residual_side is None
    assert len(market.tape.pairs) == 1
    assert D(market.tape.pairs[0].locked_edge_after_fees) >= D("0.03")


def test_v4_can_start_new_q1_only_after_pair_is_balanced():
    market = _market()
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=180)
    market.on_trade(price=D("0.64"), quantity=D("1"), buyer_maker=True)
    market.on_book(bid=D("0.70"), ask=D("0.72"), seconds_to_expiry=120)
    yes_trade_equiv = D("1") - market.tape.quote_price
    market.on_trade(price=yes_trade_equiv, quantity=D("1"), buyer_maker=False)

    assert market.tape.residual_side is None
    assert len(market.tape.pairs) == 1

    # Once balanced, early enough, V4 may open the next q=1 cycle.
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=90)
    assert market.tape.quote_side == "YES"
    assert market.tape.quote_mode == "ACCUMULATE_Q1"


def test_v4_blocks_very_expensive_fresh_directional_inventory():
    market = _market()
    market.on_book(bid=D("0.92"), ask=D("0.94"), seconds_to_expiry=180)
    assert market.tape.quote_side is None
    assert market.tape.quote_mode == "DIRECTIONAL_PRICE_CAP"


def test_v4_freezes_new_directional_inventory_near_expiry():
    market = _market()
    market.on_book(bid=D("0.64"), ask=D("0.66"), seconds_to_expiry=30)
    assert market.tape.quote_side is None
    assert market.tape.quote_mode == "FREEZE_LATE_FLAT"
