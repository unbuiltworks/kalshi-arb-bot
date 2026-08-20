from decimal import Decimal as D

import pytest

from src.gemini_kalshi_dynamic_scale_allocator import choose_dynamic_scale


def row(q, gcap, kcap, profit, *, supported=True):
    qd = D(str(q))
    pd = D(str(profit))
    return {
        "quantity": str(qd),
        "supported": supported,
        "gemini_capital": str(D(str(gcap))),
        "kalshi_capital": str(D(str(kcap))),
        "total_capital": str(D(str(gcap)) + D(str(kcap))),
        "cushioned_profit": str(pd),
        "cushioned_profit_per_contract": str(pd / qd),
    }


def test_chooses_largest_supported_funded_quantity():
    result = choose_dynamic_scale(
        [
            row(5, "2.00", "2.20", "0.20"),
            row(10, "4.00", "4.20", "0.35"),
            row(25, "10.00", "10.20", "0.90"),
        ],
        gemini_available=D("20"),
        kalshi_available=D("20"),
        max_total_capital=D("25"),
    )
    assert result is not None
    assert result.quantity == D("25")
    assert result.total_profit == D("0.90")


def test_falls_back_when_gemini_cannot_fund_largest_pair():
    result = choose_dynamic_scale(
        [
            row(5, "2.00", "2.20", "0.20"),
            row(10, "4.00", "4.20", "0.35"),
            row(25, "10.00", "10.20", "0.90"),
        ],
        gemini_available=D("9.99"),
        kalshi_available=D("50"),
        max_total_capital=D("50"),
    )
    assert result is not None
    assert result.quantity == D("10")


def test_falls_back_when_kalshi_cannot_fund_largest_pair():
    result = choose_dynamic_scale(
        [row(5, "2", "2", "0.20"), row(10, "4", "4", "0.35")],
        gemini_available=D("50"),
        kalshi_available=D("3.99"),
        max_total_capital=D("50"),
    )
    assert result is not None
    assert result.quantity == D("5")


def test_falls_back_when_total_capital_cap_blocks_largest_pair():
    result = choose_dynamic_scale(
        [
            row(5, "2.00", "2.20", "0.20"),
            row(10, "4.00", "4.20", "0.35"),
            row(25, "10.00", "10.20", "0.90"),
        ],
        gemini_available=D("50"),
        kalshi_available=D("50"),
        max_total_capital=D("9"),
    )
    assert result is not None
    assert result.quantity == D("10")


def test_reserve_buffer_is_enforced_per_venue():
    result = choose_dynamic_scale(
        [row(5, "2", "2", "0.20"), row(10, "4", "4", "0.35")],
        gemini_available=D("4.50"),
        kalshi_available=D("4.50"),
        max_total_capital=D("20"),
        reserve_buffer_per_venue=D("1"),
    )
    assert result is not None
    assert result.quantity == D("5")


def test_reports_post_reservation_free_balances():
    result = choose_dynamic_scale(
        [row(10, "4.25", "4.00", "0.35")],
        gemini_available=D("10"),
        kalshi_available=D("9"),
        max_total_capital=D("20"),
    )
    assert result is not None
    assert result.gemini_free_after == D("5.75")
    assert result.kalshi_free_after == D("5.00")


def test_unsupported_depth_row_is_never_selected():
    result = choose_dynamic_scale(
        [row(5, "2", "2", "0.20"), row(25, "10", "10", "0.90", supported=False)],
        gemini_available=D("50"),
        kalshi_available=D("50"),
        max_total_capital=D("50"),
    )
    assert result is not None
    assert result.quantity == D("5")


def test_returns_none_when_no_pair_can_be_fully_reserved():
    result = choose_dynamic_scale(
        [row(5, "2", "2", "0.20")],
        gemini_available=D("1.99"),
        kalshi_available=D("50"),
        max_total_capital=D("50"),
    )
    assert result is None


def test_rejects_row_below_three_cents_per_contract():
    with pytest.raises(ValueError, match="3c"):
        choose_dynamic_scale(
            [row(10, "4", "5.75", "0.25")],
            gemini_available=D("50"),
            kalshi_available=D("50"),
            max_total_capital=D("50"),
        )


def test_rejects_duplicate_quantity_rows():
    with pytest.raises(ValueError, match="duplicate"):
        choose_dynamic_scale(
            [row(5, "2", "2", "0.20"), row(5, "2", "2", "0.20")],
            gemini_available=D("50"),
            kalshi_available=D("50"),
            max_total_capital=D("50"),
        )
