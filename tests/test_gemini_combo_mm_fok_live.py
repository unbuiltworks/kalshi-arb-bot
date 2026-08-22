import pytest

from src.gemini_combo_mm_fok_live_v1 import _direction_hedges, _grid_legal_for_q1


def _spec():
    return {
        "combo": "GEMI-CMB-X",
        "active_required_legs": [
            ("A", "YES"),
            ("B", "NO"),
            ("C", "YES"),
        ],
    }


def test_yes_combo_uses_exact_complements():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_YES_PLUS_COMPLEMENTS")
    assert outcome == "YES"
    assert hedges == [
        {"symbol": "A", "outcome": "NO"},
        {"symbol": "B", "outcome": "YES"},
        {"symbol": "C", "outcome": "NO"},
    ]


def test_no_combo_uses_selected_required_outcomes():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_NO_PLUS_SELECTED")
    assert outcome == "NO"
    assert hedges == [
        {"symbol": "A", "outcome": "YES"},
        {"symbol": "B", "outcome": "NO"},
        {"symbol": "C", "outcome": "YES"},
    ]


def test_unknown_direction_fails_closed():
    with pytest.raises(ValueError):
        _direction_hedges(_spec(), "NOT_A_REAL_DIRECTION")


def test_q1_is_legal_on_fractional_increment_for_fok():
    assert _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "0.01",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })


def test_q1_is_legal_on_whole_increment():
    assert _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "1",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })


def test_q1_rejects_minimum_above_one_or_closed_market():
    assert not _grid_legal_for_q1({
        "quantityMinimum": "2",
        "quantityIncrement": "1",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })
    assert not _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "0.01",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "closed",
        "status": "active",
    })
