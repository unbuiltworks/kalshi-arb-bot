from decimal import Decimal

from src.mlb_paper_probe_preference import preferred_paper_probe


def probe(quantity, profit):
    return {"quantity": quantity, "conservative_locked_profit": profit}


def test_q5_three_cents_total_is_not_preferred():
    assert preferred_paper_probe(probe(5, "0.03")) is False


def test_q5_fifteen_cents_total_is_preferred():
    assert preferred_paper_probe(probe(5, "0.15")) is True


def test_q1_three_cents_is_preferred():
    assert preferred_paper_probe(probe(1, "0.03")) is True


def test_malformed_quantity_fails_closed():
    for quantity in (0, -1, "NaN", "Infinity", "0.5", None):
        assert preferred_paper_probe(probe(quantity, "0.15")) is False


def test_nonfinite_or_nonpositive_profit_fails_closed():
    for profit in ("NaN", "Infinity", "-0.01", "0", None):
        assert preferred_paper_probe(probe(1, profit)) is False


def test_invalid_threshold_fails_closed():
    assert preferred_paper_probe(probe(1, "0.03"), min_net_per_contract=Decimal("0")) is False
