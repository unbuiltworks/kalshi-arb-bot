from decimal import Decimal

import pytest

from src import sports_nfl_gk_price_scan_efficient as efficient


class Row:
    def __init__(self, name: str) -> None:
        self.name = name


def test_only_priced_gemini_keys_request_kalshi_rows():
    a1, a2, b1, c1 = Row("a1"), Row("a2"), Row("b1"), Row("c1")
    rows = {"a": [a1, a2], "b": [b1], "c": [c1]}
    selected = efficient._kalshi_rows_for_priced_keys(rows, {"c", "a"})
    assert selected == [a1, a2, c1]


def test_no_usable_gemini_books_means_no_kalshi_book_requests():
    assert efficient._kalshi_rows_for_priced_keys({"a": [Row("a")]}, set()) == []


def test_book_rps_defaults_to_ten(monkeypatch):
    monkeypatch.delenv("SPORTS_NFL_KALSHI_BOOK_RPS", raising=False)
    assert efficient._rps() == Decimal("10")


@pytest.mark.parametrize("value", ["0", "0.5", "21", "not-a-number"])
def test_book_rps_fails_closed_outside_safe_range(monkeypatch, value):
    monkeypatch.setenv("SPORTS_NFL_KALSHI_BOOK_RPS", value)
    with pytest.raises(ValueError):
        efficient._rps()
