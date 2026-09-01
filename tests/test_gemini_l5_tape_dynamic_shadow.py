from decimal import Decimal as D
import time

from src.gemini_l5_tape_dynamic_shadow import Ledger, MarketState, _parse_trade
from src.gemini_l5_threshold_shadow import Event


def event() -> Event:
    return Event(
        asset="BTC",
        horizon="5m",
        ticker="BTC05M9999999999",
        symbol="GEMI-BTC05M9999999999-UP",
        expiry_ms=int(time.time() * 1000) + 300_000,
        price_increment=D("0.01"),
    )


def test_trade_parser_requires_boolean_maker_flag():
    assert _parse_trade({"p": "0.62", "q": "2", "m": True}) == (D("0.62"), D("2"), True)
    assert _parse_trade({"p": "0.62", "q": "2", "m": "false"}) is None


def test_yes_tape_fill_uses_buyer_maker_trade_and_scales_to_target():
    state = MarketState(event())
    state.on_book(D("0.60"), D("0.64"), "t0")
    assert state.favored_side == "YES"
    quote = state.yes_quote
    assert quote is not None
    assert state.tape.yes_qty == 0

    state.on_trade(quote, D("5"), True, "t1")
    assert state.tape.yes_qty == D("1")

    state.on_book(D("0.69"), D("0.73"), "t2")
    assert state.target_units == D("2")
    quote2 = state.yes_quote
    assert quote2 is not None
    state.on_trade(quote2, D("1"), True, "t3")
    assert state.tape.yes_qty == D("2")


def test_side_flip_keeps_yes_inventory_and_adds_no():
    state = MarketState(event())
    state.on_book(D("0.60"), D("0.64"), "t0")
    yes_quote = state.yes_quote
    assert yes_quote is not None
    state.on_trade(yes_quote, D("1"), True, "t1")
    assert state.tape.yes_qty == D("1")

    state.on_book(D("0.33"), D("0.37"), "t2")
    assert state.favored_side == "NO"
    assert state.side_switches == 1
    no_quote = state.no_quote
    assert no_quote is not None

    yes_equiv = D("1") - no_quote
    state.on_trade(yes_equiv, D("1"), False, "t3")
    assert state.tape.yes_qty == D("1")
    assert state.tape.no_qty == D("1")
    assert state.tape.matched_qty == D("1")
    assert state.tape.matched_pair_cost is not None


def test_ledger_never_exceeds_three_units_per_outcome():
    ledger = Ledger("test")
    ledger.fill("YES", D("0.4"), D("10"), "t", "x")
    ledger.fill("NO", D("0.4"), D("10"), "t", "x")
    assert ledger.yes_qty == D("3")
    assert ledger.no_qty == D("3")
