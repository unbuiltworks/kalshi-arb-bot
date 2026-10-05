"""Guards on the FROZEN PMUS BTC15m underdog framework.

These tests exist to make an accidental post-hoc edit of the pre-registration
fail loudly in CI. They assert structure and internal consistency only; they
touch no tape and no outcomes.
"""

import pytest

from src.pmus import btc15m_spec as spec
from src.pmus import fees


def test_checkpoints_are_frozen_exactly():
    assert spec.CHECKPOINTS_MIN_REMAINING == (14, 12, 10, 8, 6, 5, 4, 3, 2, 1)


def test_price_buckets_are_frozen_contiguous_and_stop_at_50c():
    names = [n for n, _, _ in spec.PRICE_BUCKETS]
    assert names == ["<10c", "10-15c", "15-20c", "20-25c", "25-30c",
                     "30-35c", "35-40c", "40-45c", "45-50c"]
    bounds = [(lo, hi) for _, lo, hi in spec.PRICE_BUCKETS]
    for (_, hi), (lo_next, _) in zip(bounds, bounds[1:]):
        assert hi == lo_next
    assert bounds[0][0] == 1 and bounds[-1][1] == 50


def test_states_without_sub_50c_executable_ask_are_kept_separate():
    assert spec.bucket_for_price(None) == spec.NO_UNDERDOG_BUCKET
    assert spec.bucket_for_price(50) == spec.NO_UNDERDOG_BUCKET
    assert spec.bucket_for_price(73) == spec.NO_UNDERDOG_BUCKET
    assert spec.bucket_for_price(49) == "45-50c"
    assert spec.bucket_for_price(1) == "<10c"


@pytest.mark.parametrize("ask", range(1, 50))
def test_sizing_is_smallest_whole_quantity_reaching_five_dollars_net(ask):
    row = spec.size_for_entry(ask)
    assert row.feasible
    assert row.contracts >= spec.MIN_CONTRACTS
    assert row.contracts <= spec.MAX_CONTRACTS_PER_ENTRY
    assert row.net_profit_win_cents >= spec.TARGET_NET_PROFIT_CENTS

    # one contract fewer must NOT reach the target -> minimality
    q = row.contracts - 1
    if q >= spec.MIN_CONTRACTS:
        fee = fees.fee_cents(q, ask, "underdog", spec.ENTRY_LIQUIDITY)
        assert q * 100 - (q * ask + fee) < spec.TARGET_NET_PROFIT_CENTS


@pytest.mark.parametrize("ask", range(1, 50))
def test_row_internal_accounting_and_breakeven(ask):
    row = spec.size_for_entry(ask)
    assert row.spend_cents == row.contracts * ask + row.entry_fee_cents
    assert row.net_profit_win_cents == row.contracts * 100 - row.spend_cents
    assert row.loss_if_loser_cents == row.spend_cents  # a loss is total
    expected_be = row.spend_cents / (row.spend_cents + row.net_profit_win_cents)
    assert row.breakeven_win_prob == pytest.approx(expected_be, abs=1e-9)
    # fees can only push break-even up, never below the raw ask probability
    assert row.breakeven_win_prob >= ask / 100 - 1e-9
    assert row.required_touch_depth >= row.contracts


def test_expectancy_is_zero_at_breakeven_and_monotone_in_win_prob():
    row = spec.size_for_entry(23)
    assert spec.expected_pnl_cents(row, row.breakeven_win_prob) == pytest.approx(0.0, abs=1e-6)
    vals = [spec.expected_pnl_cents(row, p) for p in spec.WIN_PROB_GRID]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_loss_streaks_are_flat_staked_no_martingale():
    row = spec.size_for_entry(30)
    for k in spec.LOSS_STREAKS:
        assert spec.loss_streak_drawdown_cents(row, k) == row.loss_if_loser_cents * k


def test_domain_is_restricted_to_executable_underdog_asks():
    for bad in (0, 50, 99, 100, -1):
        with pytest.raises(ValueError):
            spec.size_for_entry(bad)


def test_provenance_stamps_the_fee_schedule():
    p = spec.provenance()
    assert p["fee_source"] == fees.FEE_SOURCE
    assert p["fee_schedule_fingerprint"] == fees.fee_schedule_fingerprint()
    assert len(p["fee_schedule_fingerprint"]) == 16
