from decimal import Decimal

import pytest

from src.kalshi_nested_live_candidate import candidate_rank, row_to_spec

D = Decimal


def _row(**overrides):
    row = {
        "event_ticker": "KXETHD-26AUG1620",
        "strike_type": "greater",
        "lower_strike": "1754.99",
        "higher_strike": "1819.99",
        "maker_ticker": "KXETHD-26AUG1620-T1754.99",
        "maker_side": "yes",
        "shadow_quote": "0.95",
        "current_best_bid": "0.55",
        "quote_improvement": "0.40",
        "distance_below_ask": "0.05",
        "hedge_ticker": "KXETHD-26AUG1620-T1819.99",
        "hedge_side": "no",
        "hedge_top_ask": "0.01",
    }
    row.update(overrides)
    return row


def _spec(row):
    return row_to_spec(
        row,
        fee_type="quadratic",
        fee_multiplier=D("1"),
        scheduled_fee_changes=(),
        target_profit=D("0.03"),
        maker_rest_seconds=10,
    )


def test_greater_yes_maker_maps_maker_to_lower_strike():
    spec = _spec(_row())
    assert spec.lower_ticker.endswith("T1754.99")
    assert spec.higher_ticker.endswith("T1819.99")
    assert spec.maker_side == "yes"
    assert spec.hedge_side == "no"


def test_greater_no_maker_maps_maker_to_higher_strike():
    spec = _spec(_row(
        maker_ticker="KXETHD-26AUG1620-T1819.99",
        maker_side="no",
        shadow_quote="0.94",
        hedge_ticker="KXETHD-26AUG1620-T1754.99",
        hedge_side="yes",
        hedge_top_ask="0.02",
    ))
    assert spec.lower_ticker.endswith("T1754.99")
    assert spec.higher_ticker.endswith("T1819.99")


def test_less_orientation_is_mirrored():
    spec = _spec(_row(
        strike_type="less",
        maker_ticker="KXETHD-26AUG1620-T1754.99",
        maker_side="no",
        shadow_quote="0.20",
        hedge_ticker="KXETHD-26AUG1620-T1819.99",
        hedge_side="yes",
        hedge_top_ask="0.74",
    ))
    assert spec.lower_ticker.endswith("T1754.99")
    assert spec.higher_ticker.endswith("T1819.99")


def test_impossible_side_combination_fails_closed():
    with pytest.raises(ValueError):
        _spec(_row(hedge_side="yes"))


def test_candidate_rank_prefers_smaller_bid_jump_after_same_distance():
    a = _row(distance_below_ask="0.05", quote_improvement="0.40", hedge_top_ask="0.01")
    b = _row(distance_below_ask="0.05", quote_improvement="0.10", hedge_top_ask="0.02")
    assert candidate_rank(b) < candidate_rank(a)
