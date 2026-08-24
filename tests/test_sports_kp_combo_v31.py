from types import SimpleNamespace

from src import sports_kp_live_combo_supervisor_v31 as v31


def _leg(event_ticker: str, market_ticker: str, side: str):
    return SimpleNamespace(event_ticker=event_ticker, market_ticker=market_ticker, side=side)


def test_spread_yes_total_no_is_hard_contradiction_when_margin_exceeds_total():
    legs = (
        _leg("KXNFLSPREAD-26AUG21GBDEN", "KXNFLSPREAD-26AUG21GBDEN-DEN21", "yes"),
        _leg("KXNFLTOTAL-26AUG21GBDEN", "KXNFLTOTAL-26AUG21GBDEN-20", "no"),
    )
    assert v31._hard_spread_total_contradiction(legs) is True


def test_spread_yes_total_no_not_hard_when_total_threshold_is_higher():
    legs = (
        _leg("KXNFLSPREAD-26AUG21GBDEN", "KXNFLSPREAD-26AUG21GBDEN-DEN8", "yes"),
        _leg("KXNFLTOTAL-26AUG21GBDEN", "KXNFLTOTAL-26AUG21GBDEN-42", "no"),
    )
    assert v31._hard_spread_total_contradiction(legs) is False


def test_opposite_selected_sides_are_not_misclassified():
    legs = (
        _leg("KXNFLSPREAD-26AUG21GBDEN", "KXNFLSPREAD-26AUG21GBDEN-DEN21", "no"),
        _leg("KXNFLTOTAL-26AUG21GBDEN", "KXNFLTOTAL-26AUG21GBDEN-20", "yes"),
    )
    assert v31._hard_spread_total_contradiction(legs) is False


def test_same_game_spread_total_detected_independent_of_selected_sides():
    legs = (
        _leg("KXWNBASPREAD-26AUG21GSCHI", "KXWNBASPREAD-26AUG21GSCHI-CHI4", "no"),
        _leg("KXWNBATOTAL-26AUG21GSCHI", "KXWNBATOTAL-26AUG21GSCHI-161", "yes"),
    )
    assert v31._same_game_spread_total(legs) is True
