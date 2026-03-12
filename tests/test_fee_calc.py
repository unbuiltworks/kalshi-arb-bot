"""
Tests for fee_calc.py — validated against Kalshi's official fee schedule.

The official fee table (Feb 5, 2026) gives exact values for 100 contracts
at various price points. We test against those exact values.
"""

import pytest
from src.fee_calc import taker_fee_cents, maker_fee_cents, compute_edge_cents


class TestTakerFees:
    """Validate taker fees against official fee table for 100 contracts."""

    # (price_cents, expected_fee_cents) from official table
    OFFICIAL_TABLE = [
        (1, 7),      # $0.01 → $0.07
        (5, 34),     # $0.05 → $0.34
        (10, 63),    # $0.10 → $0.63
        (15, 90),    # $0.15 → $0.90
        (20, 112),   # $0.20 → $1.12
        (25, 132),   # $0.25 → $1.32
        (30, 147),   # $0.30 → $1.47
        (35, 160),   # $0.35 → $1.60
        (40, 168),   # $0.40 → $1.68
        (45, 174),   # $0.45 → $1.74
        (50, 175),   # $0.50 → $1.75
        (55, 174),   # $0.55 → $1.74
        (60, 168),   # $0.60 → $1.68
        (65, 160),   # $0.65 → $1.60
        (70, 147),   # $0.70 → $1.47
        (75, 132),   # $0.75 → $1.32
        (80, 112),   # $0.80 → $1.12
        (85, 90),    # $0.85 → $0.90
        (90, 63),    # $0.90 → $0.63
        (95, 34),    # $0.95 → $0.34
        (99, 7),     # $0.99 → $0.07
    ]

    @pytest.mark.parametrize("price_cents,expected_fee_cents", OFFICIAL_TABLE)
    def test_100_contracts_standard(self, price_cents, expected_fee_cents):
        result = taker_fee_cents(100, price_cents)
        assert result == expected_fee_cents, (
            f"At {price_cents}c, expected {expected_fee_cents}c fee, got {result}c"
        )

    def test_single_contract_at_50c(self):
        # 0.07 * 1 * 0.50 * 0.50 = 0.0175 → ceil to 2 cents
        assert taker_fee_cents(1, 50) == 2

    def test_single_contract_at_5c(self):
        # 0.07 * 1 * 0.05 * 0.95 = 0.003325 → ceil to 1 cent
        assert taker_fee_cents(1, 5) == 1

    def test_zero_contracts(self):
        assert taker_fee_cents(0, 50) == 0

    def test_boundary_prices(self):
        assert taker_fee_cents(1, 0) == 0
        assert taker_fee_cents(1, 100) == 0


class TestReducedFees:
    """S&P500 and NASDAQ-100 markets have 0.035 taker rate."""

    INX_TABLE = [
        (5, 17),     # $0.05 → $0.17
        (25, 66),    # $0.25 → $0.66
        (50, 88),    # $0.50 → $0.88
        (75, 66),    # $0.75 → $0.66
        (95, 17),    # $0.95 → $0.17
    ]

    @pytest.mark.parametrize("price_cents,expected_fee_cents", INX_TABLE)
    def test_inx_100_contracts(self, price_cents, expected_fee_cents):
        result = taker_fee_cents(100, price_cents, ticker="INXD-25MAR14-T5800")
        assert result == expected_fee_cents

    def test_nasdaq100_reduced(self):
        # Should also get reduced rate
        result = taker_fee_cents(100, 50, ticker="NASDAQ100W-25MAR14")
        assert result == 88  # 0.035 * 100 * 0.50 * 0.50 = 0.875 → ceil to 88 cents


class TestMakerFees:
    """Maker fees at 0.0175 rate."""

    def test_100_contracts_at_50c(self):
        # 0.0175 * 100 * 0.50 * 0.50 = 0.4375 → ceil to 44 cents
        assert maker_fee_cents(100, 50) == 44

    def test_100_contracts_at_25c(self):
        # 0.0175 * 100 * 0.25 * 0.75 = 0.328125 → ceil to 33 cents
        assert maker_fee_cents(100, 25) == 33

    def test_single_contract_at_50c(self):
        # 0.0175 * 1 * 0.50 * 0.50 = 0.004375 → ceil to 1 cent
        assert maker_fee_cents(1, 50) == 1


class TestEdgeCalculation:
    """Test the arb edge calculator."""

    def test_profitable_arb(self):
        # YES ask = 45c, NO ask = 50c → total cost = 95c + fees
        # At 10 contracts: cost = 450 + 500 = 950 cents
        # Payout = 1000 cents
        # Gross edge = 50 cents before fees
        edge = compute_edge_cents(
            count=10,
            yes_ask_cents=45,
            no_ask_cents=50,
            maker_side="yes",
        )
        # Should be positive (50 cents minus fees)
        assert edge > 0

    def test_no_arb_normal_spread(self):
        # YES ask = 55c, NO ask = 50c → total = 105c per contract (>100)
        edge = compute_edge_cents(
            count=10,
            yes_ask_cents=55,
            no_ask_cents=50,
        )
        assert edge < 0

    def test_exact_100_no_edge(self):
        # YES ask = 50c, NO ask = 50c → total = 100c + fees → negative
        edge = compute_edge_cents(
            count=10,
            yes_ask_cents=50,
            no_ask_cents=50,
        )
        assert edge < 0  # fees make it unprofitable
