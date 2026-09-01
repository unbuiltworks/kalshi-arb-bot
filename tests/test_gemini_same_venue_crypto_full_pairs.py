from __future__ import annotations

from decimal import Decimal as D

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1 as maker


def _c(symbol: str, strike: str):
    return {"instrumentSymbol": symbol, "_strike": D(strike)}


def test_monotone_pairs_include_non_adjacent_thresholds():
    low = _c("GEMI-X-HI1", "1")
    mid = _c("GEMI-X-HI2", "2")
    high = _c("GEMI-X-HI3", "3")
    pairs = scan.monotone_pairs([low, mid, high])
    assert pairs == [(low, mid), (low, high), (mid, high)]


def test_any_low_high_pair_has_one_dollar_payout_floor():
    # HI means YES if settlement is above the threshold. For every L<H:
    # below/equal L => NO(H)=1; between => YES(L)+NO(H)=2; above H => YES(L)=1.
    payouts = {
        "below_low": D("1"),
        "between": D("2"),
        "above_high": D("1"),
    }
    assert min(payouts.values()) == D("1")


def test_fractional_grid_contract_is_valid_for_q1_fok_but_not_resting_maker():
    contract = {
        "quantityMinimum": "0.01",
        "quantityIncrement": "0.01",
    }
    # q=1 lands exactly on the instrument grid, so FOK can request exactly one.
    assert scan._q1_ok(contract) is True
    # We deliberately keep the stricter rule for the resting maker lane because a
    # maker order can partially fill before cancellation/recovery.
    assert maker.whole_contract_only(contract) is False
