from decimal import Decimal

from src import gemini_kalshi_opportunity_matrix_shadow as matrix


D = Decimal


def _snapshot(*, spot: str, strike: str, fresh: bool = True):
    return {
        "spot_mid": spot,
        "kaiko_strike": strike,
        "proxy_fresh_1000ms": fresh,
        "basis_window_end_utc": "2026-08-18T17:00:00+00:00",
    }


def test_double_boundary_ladder_passes_expected_levels():
    out = matrix.augment_double_boundary_ladder(
        _snapshot(spot="100.20", strike="100.00"),
        kalshi_threshold=D("100.05"),
    )
    assert out is not None
    assert out["same_side_of_both_boundaries"] is True
    assert D(out["min_abs_double_boundary_margin_bps"]) > D("14")
    assert out["double_boundary_ladder"] == {
        "5bp": True,
        "10bp": True,
        "15bp": False,
        "20bp": False,
    }


def test_double_boundary_ladder_fails_closed_when_target_missing():
    out = matrix.augment_double_boundary_ladder(
        _snapshot(spot="101", strike="100"),
        kalshi_threshold=None,
    )
    assert out is not None
    assert out["same_side_of_both_boundaries"] is False
    assert not any(out["double_boundary_ladder"].values())


def test_build_matrix_separates_two_cent_and_three_cent_cohorts():
    entry = {"5bp": True, "10bp": True, "15bp": False, "20bp": False}
    whole = dict(entry)
    now = dict(entry)
    out = matrix.build_matrix(
        entry_profit=D("0.025"),
        base_ready=True,
        entry_ladder=entry,
        whole_ladder=whole,
        now_ladder=now,
    )
    assert out["2c"]["5bp"] is True
    assert out["2c"]["10bp"] is True
    assert out["2c"]["15bp"] is False
    assert not any(out["3c"].values())


def test_build_matrix_requires_exact_execution_base_ready():
    gates = {"5bp": True, "10bp": True, "15bp": True, "20bp": True}
    out = matrix.build_matrix(
        entry_profit=D("0.06"),
        base_ready=False,
        entry_ladder=gates,
        whole_ladder=gates,
        now_ladder=gates,
    )
    assert not any(value for row in out.values() for value in row.values())
