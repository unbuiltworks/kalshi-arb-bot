from decimal import Decimal

from src.gemini_sports_ws_validate import _snapshot_prices


def test_snapshot_prices_reads_buy_yes_no():
    contract = {"prices": {"buy": {"yes": "0.34", "no": "0.66"}}}
    assert _snapshot_prices(contract) == (Decimal("0.34"), Decimal("0.66"))


def test_snapshot_prices_fails_closed_on_missing_side():
    assert _snapshot_prices({"prices": {"buy": {"yes": "0.34"}}}) is None


def test_snapshot_prices_rejects_out_of_range():
    assert _snapshot_prices({"prices": {"buy": {"yes": "1.20", "no": "0.20"}}}) is None
