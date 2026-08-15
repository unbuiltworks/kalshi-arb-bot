from src.gemini_kalshi_btc_ws_confidence import (
    confidence_overlay,
    enrich_scenario,
    wilson_upper_95,
)


def _scenario(*, n=10, adverse=0, favorable=0):
    return {
        "settled_episode_candidates_matched": n,
        "unresolved_episode_candidates": 0,
        "excluded_unknown_gemini_margin_candidates": 0,
        "excluded_inside_margin_candidates": 0,
        "consumable_episodes": n,
        "consumable_contracts": str(n),
        "apparent_locked_profit_dollars": "1.00",
        "settlement_adjusted_pnl_dollars": "1.00",
        "basis_effect_dollars": "0.00",
        "adverse_mismatch_consumable_episodes": adverse,
        "favorable_mismatch_consumable_episodes": favorable,
    }


def test_wilson_zero_mismatch_small_sample_is_not_zero_risk():
    assert wilson_upper_95(0, 0) is None
    bound = wilson_upper_95(0, 10)
    assert bound is not None
    assert 0.27 < bound < 0.28


def test_enrich_scenario_counts_both_mismatch_directions():
    row = enrich_scenario(_scenario(n=20, adverse=2, favorable=1))
    assert row["observed_mismatch_consumable_episodes"] == 3
    assert row["observed_mismatch_rate"] == 0.15
    assert row["mismatch_rate_wilson_upper_95"] > 0.15
    assert row["confidence_warning"] == "SMALL_SAMPLE_BOUND_NOT_A_FORECAST"


def test_confidence_overlay_enriches_latency_and_margin_cohorts():
    base = {
        "entry": _scenario(n=10),
        "survival": {"50": _scenario(n=8, adverse=1)},
        "basis_margin_sensitivity": {
            "2": {
                "min_abs_gemini_margin_bps": "2",
                "entry": _scenario(n=6),
                "survival": {"50": _scenario(n=5)},
            }
        },
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }
    out = confidence_overlay(base)
    assert out["entry"]["observed_mismatch_rate"] == 0
    assert out["entry"]["mismatch_rate_wilson_upper_95"] > 0
    assert out["survival"]["50"]["observed_mismatch_rate"] == 0.125
    assert out["basis_margin_sensitivity"]["2"]["entry"]["confidence_warning"] == (
        "ZERO_OBSERVED_MISMATCHES_DOES_NOT_IMPLY_ZERO_BASIS_RISK"
    )
    assert out["read_only"] is True
    assert out["order_entry"] is False
