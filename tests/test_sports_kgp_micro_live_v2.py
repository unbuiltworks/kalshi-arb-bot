from decimal import Decimal as D
from types import SimpleNamespace

from src.sports_kgp_micro_guard import RuleApproval
from src.sports_kgp_micro_live_v2 import (
    _apply_gemini_top,
    _apply_kalshi_book,
    _apply_poly_book,
    _approved_markets,
)
from src.strict_mlb_prop_snapshot import TopBook


def approval():
    return RuleApproval(
        settlement_key="k", venue_a="gemini", market_a="GEMI-X", source_side_a="YES",
        venue_b="kalshi", market_b="KX-X", source_side_b="YES",
        rule_fingerprint_sha256="a" * 64, approved_for_execution=True,
    )


def test_approved_markets_are_scoped_by_exact_settlement_key_and_venue():
    assert _approved_markets({"k": [approval()]}) == {
        ("k", "gemini"): {"GEMI-X"},
        ("k", "kalshi"): {"KX-X"},
    }


def test_fresh_yes_oriented_gemini_depth_replaces_discovery_economics():
    row = {
        "venue": "gemini", "market_id": "GEMI-X#CONTRACT", "settlement_key": "k",
        "source_side": "CONTRACT", "depth_source": "event_prices_q1_probe_only",
    }
    top = SimpleNamespace(
        yes_bid=D("0.40"), yes_bid_size=D("7"), yes_ask=D("0.43"), yes_ask_size=D("5"), received_ms=123,
    )
    out = _apply_gemini_top(row, top)
    assert D(out["raw_yes_ask"]) == D("0.43")
    assert D(out["raw_no_ask"]) == D("0.60")
    assert out["yes_depth"] == "1"
    assert out["no_depth"] == "1"
    assert out["received_ms"] == 123
    assert out["depth_source"] == "gemini_depth10_100ms_final"
    assert D(out["all_in_yes_cost"]) >= D("0.43")


def test_complement_source_side_swaps_native_gemini_depth_into_canonical_space():
    row = {
        "venue": "gemini", "market_id": "GEMI-X#NO", "settlement_key": "k",
        "source_side": "NO", "depth_source": "event_prices_q1_probe_only",
    }
    top = SimpleNamespace(
        yes_bid=D("0.40"), yes_bid_size=D("3"), yes_ask=D("0.43"), yes_ask_size=D("4"), received_ms=123,
    )
    out = _apply_gemini_top(row, top)
    assert D(out["raw_yes_ask"]) == D("0.60")
    assert D(out["raw_no_ask"]) == D("0.43")


def test_polymarket_final_book_recomputes_q1_fee_and_marks_final_source():
    row = {"source_side": "YES"}
    book = TopBook(yes_ask=D("0.42"), yes_depth=D("5"), no_ask=D("0.59"), no_depth=D("7"))
    out = _apply_poly_book(row, book, 222)
    assert D(out["raw_yes_ask"]) == D("0.42")
    assert D(out["raw_no_ask"]) == D("0.59")
    assert D(out["all_in_yes_cost"]) >= D("0.42")
    assert out["depth_source"] == "polymarket_public_book_final"
    assert out["received_ms"] == 222


def test_kalshi_final_book_uses_conservative_fee_multiplier():
    row = {"source_side": "YES"}
    book = TopBook(yes_ask=D("0.40"), yes_depth=D("5"), no_ask=D("0.61"), no_depth=D("7"))
    out = _apply_kalshi_book(row, book, 333, D("1"))
    assert D(out["raw_yes_ask"]) == D("0.40")
    assert D(out["raw_no_ask"]) == D("0.61")
    assert D(out["yes_fee"]) >= D("0")
    assert out["depth_source"] == "kalshi_public_orderbook_depth1_final"
    assert out["received_ms"] == 333
