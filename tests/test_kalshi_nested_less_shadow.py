from decimal import Decimal

from src.kalshi_nested_less_shadow import guaranteed_less_payout

D = Decimal


def test_no_lower_plus_yes_higher_never_pays_less_than_one():
    lower, higher = D("0.10"), D("0.20")
    assert guaranteed_less_payout(lower, higher, D("0.05")) == D("1")
    assert guaranteed_less_payout(lower, higher, D("0.10")) == D("2")
    assert guaranteed_less_payout(lower, higher, D("0.15")) == D("2")
    assert guaranteed_less_payout(lower, higher, D("0.20")) == D("1")
    assert guaranteed_less_payout(lower, higher, D("0.25")) == D("1")


def test_reversed_less_strikes_fail_closed():
    try:
        guaranteed_less_payout(D("0.2"), D("0.1"), D("0.15"))
    except ValueError as exc:
        assert "lower" in str(exc)
    else:
        raise AssertionError("reversed strikes must fail closed")
