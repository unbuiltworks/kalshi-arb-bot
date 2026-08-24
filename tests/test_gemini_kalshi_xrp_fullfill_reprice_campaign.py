from decimal import Decimal

from src import gemini_kalshi_xrp_fullfill_reprice_campaign as fullfill


def test_fullfill_admission_quantity_is_exactly_one():
    assert fullfill.FULL_FILL_ADMISSION_QTY == Decimal("1.00")
