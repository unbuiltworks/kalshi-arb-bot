from decimal import Decimal as D
from types import SimpleNamespace

from src.sports_kgp_micro_guard import RuleApproval
from src.sports_kgp_micro_live_v2 import _apply_gemini_top, _approved_gemini_symbols


def approval():
    return RuleApproval(
        settlement_key="k", venue_a="gemini", market_a="GEMI-X", source_side_a="YES",
        venue_b="kalshi", market_b="KX-X", source_side_b="YES",
        rule_fingerprint_sha256="a" * 64, approved_for_execution=True,
    )


def test_approved_symbols_are_scoped_by_exact_settlement_key():
    assert _approved_gemini_symbols({"k": [approval()]}) == {"k": {"GEMI-X"}}


def test_fresh_yes_oriented_depth_replaces_discovery_economics():
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
    assert out["depth_source"] == "gemini_depth10_100ms"
    assert D(out["all_in_yes_cost"]) >= D("0.43")


def test_complement_source_side_swaps_native_depth_into_canonical_space():
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
