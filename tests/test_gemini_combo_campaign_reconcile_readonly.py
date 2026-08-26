from __future__ import annotations

import pytest

from src import gemini_combo_campaign_reconcile_readonly as r


def test_rows_requires_object_and_list() -> None:
    with pytest.raises(RuntimeError):
        r._rows([], "orders")
    with pytest.raises(RuntimeError):
        r._rows({"orders": {}}, "orders")
    assert r._rows({}, "orders") == []
    assert r._rows({"orders": [{"symbol": r.COMBO}, "bad"]}, "orders") == [{"symbol": r.COMBO}]


def test_filter_is_exact_symbol_and_campaign_scoped() -> None:
    campaign = "gcombo-2608221328-torclb-rest1"
    rows = [
        {"symbol": r.COMBO, "clientOrderId": campaign + "-c"},
        {"symbol": r.HEDGES[0], "clientOrderId": campaign + "-h1"},
        {"symbol": r.HEDGES[1], "clientOrderId": "other-h2"},
        {"symbol": "OTHER", "clientOrderId": campaign + "-x"},
    ]
    out = r._filter(rows, campaign)
    assert len(out) == 2
    assert {x["symbol"] for x in out} == {r.COMBO, r.HEDGES[0]}


def test_filter_without_campaign_still_limits_symbols() -> None:
    rows = [
        {"symbol": r.COMBO, "clientOrderId": "a"},
        {"symbol": "OTHER", "clientOrderId": "b"},
    ]
    assert r._filter(rows, "") == [rows[0]]
