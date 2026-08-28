from types import SimpleNamespace

from src.strict_mlb_prop_snapshot_v5 import _paper_probe_flags


def assessment(cleared=True):
    return SimpleNamespace(settlement_equivalent_now=cleared)


def test_q5_three_cents_total_is_positive_but_not_preferred():
    positive, preferred = _paper_probe_flags(
        assessment(),
        {"quantity": 5, "conservative_locked_profit": "0.03"},
    )
    assert positive is True
    assert preferred is False


def test_q5_fifteen_cents_total_is_preferred():
    positive, preferred = _paper_probe_flags(
        assessment(),
        {"quantity": 5, "conservative_locked_profit": "0.15"},
    )
    assert positive is True
    assert preferred is True


def test_q1_three_cents_is_preferred():
    positive, preferred = _paper_probe_flags(
        assessment(),
        {"quantity": 1, "conservative_locked_profit": "0.03"},
    )
    assert positive is True
    assert preferred is True


def test_malformed_quantity_fails_closed_for_preference():
    for quantity in (0, -1, "NaN", "Infinity", "0.5", None):
        positive, preferred = _paper_probe_flags(
            assessment(),
            {"quantity": quantity, "conservative_locked_profit": "0.15"},
        )
        assert positive is True
        assert preferred is False


def test_uncleared_participation_is_not_positive_or_preferred():
    positive, preferred = _paper_probe_flags(
        assessment(False),
        {"quantity": 1, "conservative_locked_profit": "0.10"},
    )
    assert positive is False
    assert preferred is False
