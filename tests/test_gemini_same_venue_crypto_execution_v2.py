from decimal import Decimal

from src import gemini_same_venue_crypto_account_gate as account
from src import gemini_same_venue_crypto_taker_q1 as taker

D = Decimal


def test_account_gate_only_matches_same_venue_maker_clients():
    assert account._is_our_maker({"clientOrderId": "crypto-gem-ladder-g4-001-mk"})
    assert not account._is_our_maker({"clientOrderId": "crypto-gem-ladder-g4-001-hg"})
    assert not account._is_our_maker({"clientOrderId": "some-other-bot-mk"})


def test_account_gate_reads_filled_quantity_conservatively():
    assert account._qty({"filledQuantity": "0"}) == D("0")
    assert account._qty({"filledQuantity": "1"}) == D("1")
    assert account._qty({"filledQuantity": "0.37"}) == D("0.37")


def test_taker_pair_profit_includes_both_taker_fees():
    low_yes = D("0.40")
    high_no = D("0.50")
    expected = (
        D("1") - low_yes - high_no
        - taker.gemini_predictions_taker_fee(low_yes, D("1"))
        - taker.gemini_predictions_taker_fee(high_no, D("1"))
    )
    assert taker._taker_profit(low_yes, high_no) == expected


def test_taker_pair_three_cent_gate_has_no_fee_free_shortcut():
    # 0.94 raw cost looks like 6c before fees; the helper must use exact venue fees.
    profit = taker._taker_profit(D("0.47"), D("0.47"))
    assert profit < D("0.06")
