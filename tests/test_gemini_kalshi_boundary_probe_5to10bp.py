from decimal import Decimal

from src import gemini_kalshi_boundary_probe_5to10bp as probe

D = Decimal


def test_strict_gate_preserves_q1_profit_50ms_and_skew_limits():
    base = dict(
        delay_ms=50,
        readiness_20bp=True,
        quantity=D("1"),
        locked_profit=D("0.03"),
        entry_quote_skew_ms=50,
        current_quote_skew_ms=50,
    )
    assert probe.strict_gate_50ms(**base)
    for key, value in [
        ("delay_ms", 100),
        ("readiness_20bp", False),
        ("quantity", D("2")),
        ("locked_profit", D("0.029")),
        ("entry_quote_skew_ms", 51),
        ("current_quote_skew_ms", 51),
    ]:
        case = dict(base)
        case[key] = value
        assert not probe.strict_gate_50ms(**case)


def test_boundary_band_is_mutually_exclusive_with_10bp_core(monkeypatch):
    def fake(snapshot, *, kalshi_threshold):
        return dict(snapshot)

    monkeypatch.setattr(probe, "_original_augment", fake)
    expected = {
        "4.99": False,
        "5": True,
        "7.5": True,
        "9.99": True,
        "10": False,
        "20": False,
    }
    for margin, want in expected.items():
        out = probe.augment_5to10_band(
            {
                "proxy_fresh_1000ms": True,
                "same_side_of_both_boundaries": True,
                "min_abs_double_boundary_margin_bps": margin,
            },
            kalshi_threshold=D("1"),
        )
        assert out["research_double_boundary_gate_20bp_fresh_1000ms"] is want
        assert out["experimental_boundary_band"] == "5_to_lt_10_bp"


def test_boundary_band_fails_closed_on_freshness_or_side(monkeypatch):
    monkeypatch.setattr(probe, "_original_augment", lambda snapshot, *, kalshi_threshold: dict(snapshot))
    for snapshot in [
        {"proxy_fresh_1000ms": False, "same_side_of_both_boundaries": True, "min_abs_double_boundary_margin_bps": "7"},
        {"proxy_fresh_1000ms": True, "same_side_of_both_boundaries": False, "min_abs_double_boundary_margin_bps": "7"},
        {"proxy_fresh_1000ms": True, "same_side_of_both_boundaries": True, "min_abs_double_boundary_margin_bps": None},
    ]:
        out = probe.augment_5to10_band(snapshot, kalshi_threshold=D("1"))
        assert not out["research_double_boundary_gate_20bp_fresh_1000ms"]
