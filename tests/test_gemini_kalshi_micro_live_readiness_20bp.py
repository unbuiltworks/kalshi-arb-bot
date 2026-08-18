from decimal import Decimal

from src.gemini_kalshi_micro_live_readiness_20bp import (
    _KALSHI_TARGETS,
    augment_double_boundary,
    kalshi_target,
    update_target_cache,
)


def _snapshot(spot: str, strike: str = "100", *, fresh: bool = True):
    return {
        "spot_mid": spot,
        "kaiko_strike": strike,
        "proxy_fresh_1000ms": fresh,
        "basis_window_end_utc": "2026-08-18T14:00:00+00:00",
    }


def test_kalshi_target_uses_single_floor_strike():
    assert kalshi_target({"floor_strike": "100.25"}) == Decimal("100.25")


def test_double_boundary_gate_passes_only_when_20bp_from_both_on_same_side():
    row = augment_double_boundary(_snapshot("99.75"), kalshi_threshold=Decimal("100.01"))
    assert row is not None
    assert row["same_side_of_both_boundaries"] is True
    assert row["research_double_boundary_gate_20bp_fresh_1000ms"] is True
    assert Decimal(row["min_abs_double_boundary_margin_bps"]) >= Decimal("20")


def test_double_boundary_gate_fails_between_venue_thresholds():
    row = augment_double_boundary(_snapshot("100.50"), kalshi_threshold=Decimal("101"))
    assert row is not None
    assert row["same_side_of_both_boundaries"] is False
    assert row["research_double_boundary_gate_20bp_fresh_1000ms"] is False


def test_double_boundary_gate_fails_inside_20bp_or_when_stale():
    near = augment_double_boundary(_snapshot("99.81"), kalshi_threshold=Decimal("100"))
    stale = augment_double_boundary(_snapshot("99.70", fresh=False), kalshi_threshold=Decimal("100"))
    assert near is not None and near["research_double_boundary_gate_20bp_fresh_1000ms"] is False
    assert stale is not None and stale["research_double_boundary_gate_20bp_fresh_1000ms"] is False


def test_target_cache_can_fill_late_after_empty_rollover_lookup():
    _KALSHI_TARGETS.clear()
    window = "2026-08-18T14:00:00+00:00"
    assert update_target_cache(window_end_iso=window, found={}) == []
    available = update_target_cache(
        window_end_iso=window,
        found={"BTC": (window, Decimal("64129.12"))},
    )
    assert available == ["BTC"]
    assert _KALSHI_TARGETS["BTC"] == (window, Decimal("64129.12"))


def test_target_cache_drops_stale_window_before_new_target_arrives():
    _KALSHI_TARGETS.clear()
    _KALSHI_TARGETS["BTC"] = ("2026-08-18T13:45:00+00:00", Decimal("64016.41"))
    assert update_target_cache(
        window_end_iso="2026-08-18T14:00:00+00:00", found={}
    ) == []
    assert "BTC" not in _KALSHI_TARGETS
