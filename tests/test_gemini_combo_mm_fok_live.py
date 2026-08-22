import asyncio

import pytest

import src.gemini_combo_mm_fok_entry as entry
from src.gemini_combo_mm_fok_live_v1 import _direction_hedges, _grid_legal_for_q1


def _spec():
    return {
        "combo": "GEMI-CMB-X",
        "active_required_legs": [
            ("A", "YES"),
            ("B", "NO"),
            ("C", "YES"),
        ],
    }


def test_yes_combo_uses_exact_complements():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_YES_PLUS_COMPLEMENTS")
    assert outcome == "YES"
    assert hedges == [
        {"symbol": "A", "outcome": "NO"},
        {"symbol": "B", "outcome": "YES"},
        {"symbol": "C", "outcome": "NO"},
    ]


def test_no_combo_uses_selected_required_outcomes():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_NO_PLUS_SELECTED")
    assert outcome == "NO"
    assert hedges == [
        {"symbol": "A", "outcome": "YES"},
        {"symbol": "B", "outcome": "NO"},
        {"symbol": "C", "outcome": "YES"},
    ]


def test_unknown_direction_fails_closed():
    with pytest.raises(ValueError):
        _direction_hedges(_spec(), "NOT_A_REAL_DIRECTION")


def test_q1_is_legal_on_fractional_increment_for_fok():
    assert _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "0.01",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })


def test_q1_is_legal_on_whole_increment():
    assert _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "1",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })


def test_q1_rejects_minimum_above_one_or_closed_market():
    assert not _grid_legal_for_q1({
        "quantityMinimum": "2",
        "quantityIncrement": "1",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "open",
        "status": "active",
    })
    assert not _grid_legal_for_q1({
        "quantityMinimum": "1",
        "quantityIncrement": "0.01",
        "priceMinimum": "0.01",
        "priceIncrement": "0.01",
        "marketState": "closed",
        "status": "active",
    })


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


def test_active_order_audit_uses_one_unfiltered_private_call(monkeypatch):
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return _Response({
            "orders": [
                {"symbol": "A", "status": "open"},
                {"symbol": "UNRELATED", "status": "open"},
            ],
            "pagination": {"limit": 100, "offset": 0, "count": 2},
        })

    monkeypatch.setattr(entry, "private_rest_request", fake_request)
    ok, rows = asyncio.run(entry._active_orders_for_symbols_single_call({"A", "B"}))
    assert ok is True
    assert [x["symbol"] for x in rows] == ["A"]
    assert len(calls) == 1
    assert "symbol" not in calls[0][2]["params"]


def test_active_order_audit_fails_closed_on_full_page(monkeypatch):
    async def fake_request(method, path, **kwargs):
        return _Response({"orders": [{"symbol": f"X{i}"} for i in range(100)]})

    monkeypatch.setattr(entry, "private_rest_request", fake_request)
    ok, rows = asyncio.run(entry._active_orders_for_symbols_single_call({"A"}))
    assert ok is False
    assert rows == []


def test_disarmed_entry_skips_private_preflight(monkeypatch):
    monkeypatch.delenv("GEMINI_COMBO_FOK_CAMPAIGN", raising=False)
    monkeypatch.delenv("GEMINI_COMBO_FOK_ARM_CAMPAIGN", raising=False)
    monkeypatch.delenv("ENABLE_GEMINI_COMBO_FOK_LIVE", raising=False)
    monkeypatch.delenv("GEMINI_COMBO_FOK_ARM_NOT_AFTER_UTC", raising=False)

    async def fake_load_candidates(_http):
        return {"http": 200, "catalog_rows": 1}, [{"combo": "GEMI-CMB-X"}]

    async def forbidden_private(*args, **kwargs):
        raise AssertionError("disarmed entry must not call private Gemini REST")

    monkeypatch.setattr(entry.live, "_load_candidates", fake_load_candidates)
    monkeypatch.setattr(entry, "private_rest_request", forbidden_private)
    out = asyncio.run(entry.run())
    assert out["armed"] is False
    assert out["private_preflight"] == "SKIPPED_DISARMED"
    assert out["classification"] == "DISARMED_PUBLIC_CATALOG_READY"
    assert out["order_entry"] is False
