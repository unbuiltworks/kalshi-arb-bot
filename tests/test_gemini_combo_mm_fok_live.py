import asyncio
from itertools import product

import pytest

import src.gemini_combo_mm_fok_entry as entry
from src.gemini_combo_mm_fok_live_v1 import _grid_legal_for_q1
from src.gemini_combo_mm_fok_live_v2 import _direction_hedges
from src.gemini_combo_mm_fok_preflight import _cover_directions


def _spec():
    return {
        "combo": "GEMI-CMB-X",
        "active_required_legs": [
            ("A", "YES"),
            ("B", "NO"),
            ("C", "YES"),
        ],
    }


def test_yes_combo_uses_all_exact_complements():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_YES_PLUS_ALL_COMPLEMENTS")
    assert outcome == "YES"
    assert hedges == [
        {"symbol": "A", "outcome": "NO"},
        {"symbol": "B", "outcome": "YES"},
        {"symbol": "C", "outcome": "NO"},
    ]


def test_no_combo_uses_one_canonical_required_leg_only():
    outcome, hedges = _direction_hedges(_spec(), "COMBO_NO_PLUS_REQUIRED_LEG_1")
    assert outcome == "NO"
    assert hedges == [{"symbol": "B", "outcome": "NO"}]


def test_every_minimal_no_direction_has_exactly_one_hedge():
    for idx, expected in enumerate(_spec()["active_required_legs"]):
        outcome, hedges = _direction_hedges(_spec(), f"COMBO_NO_PLUS_REQUIRED_LEG_{idx}")
        assert outcome == "NO"
        assert hedges == [{"symbol": expected[0], "outcome": expected[1]}]


def test_cover_directions_emit_one_no_cover_per_required_leg():
    covers = _cover_directions(_spec()["active_required_legs"])
    assert [x["name"] for x in covers] == [
        "COMBO_YES_PLUS_ALL_COMPLEMENTS",
        "COMBO_NO_PLUS_REQUIRED_LEG_0",
        "COMBO_NO_PLUS_REQUIRED_LEG_1",
        "COMBO_NO_PLUS_REQUIRED_LEG_2",
    ]
    assert covers[2]["component_specs"] == [("B", "NO")]


def test_minimal_conjunction_cover_truth_table_exhaustive():
    # Each bit means the corresponding required outcome A_i occurred. The Gemini
    # conjunction settles YES iff all required outcomes occur.
    for n in range(1, 6):
        for bits in product((0, 1), repeat=n):
            combo_yes = int(all(bits))
            combo_no = 1 - combo_yes
            assert combo_yes + sum(1 - bit for bit in bits) >= 1
            for idx in range(n):
                assert combo_no + bits[idx] >= 1


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
