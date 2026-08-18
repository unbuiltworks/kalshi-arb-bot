from decimal import Decimal

from src.central_shadow_portfolio_report import Sleeve, rank_sleeves


def test_hard_blocker_forces_zero_promotable_score_without_erasing_research_capacity():
    row = Sleeve(
        name="gemini-kalshi-eth-15m",
        category="crypto",
        venues=("gemini", "kalshi"),
        net_edge_per_dollar=Decimal("0.05"),
        executable_probability=Decimal("0.5"),
        depth_dollars=Decimal("400"),
        capital_lock_hours=Decimal("0.25"),
        settlement_confidence=Decimal("0.5"),
        basis_risk=Decimal("0.2"),
        hard_blockers=("INDEX_MISMATCH_RESEARCH_ONLY",),
    )
    assert row.capacity_score == Decimal("40")
    assert row.confidence_adjusted_score == Decimal("16.0")
    assert row.promotable_score == Decimal("0")


def test_unblocked_sleeve_ranks_above_blocked_larger_raw_capacity():
    report = rank_sleeves(
        [
            {
                "name": "blocked-crypto",
                "category": "crypto",
                "venues": ["gemini", "kalshi"],
                "net_edge_per_dollar": "0.08",
                "executable_probability": "0.8",
                "depth_dollars": "1000",
                "capital_lock_hours": "0.25",
                "settlement_confidence": "0.4",
                "basis_risk": "0.3",
                "hard_blockers": ["INDEX_MISMATCH_RESEARCH_ONLY"],
            },
            {
                "name": "audited-sports-shadow",
                "category": "mlb",
                "venues": ["polymarket_us", "kalshi"],
                "net_edge_per_dollar": "0.03",
                "executable_probability": "0.25",
                "depth_dollars": "5",
                "capital_lock_hours": "2",
                "settlement_confidence": "1",
                "basis_risk": "0",
                "hard_blockers": [],
            },
        ]
    )
    assert report["sleeves"][0]["name"] == "audited-sports-shadow"
    assert report["sleeves"][0]["promotable_score"] != "0"
    assert report["sleeves"][1]["name"] == "blocked-crypto"
    assert report["sleeves"][1]["promotable_score"] == "0"


def test_observed_daily_run_rate_requires_verified_full_observation_window():
    report = rank_sleeves(
        [
            {
                "name": "sample",
                "category": "mlb",
                "venues": ["polymarket_us", "kalshi"],
                "net_edge_per_dollar": "0.03",
                "executable_probability": "0.1",
                "depth_dollars": "5",
                "capital_lock_hours": "1",
                "settlement_confidence": "1",
                "basis_risk": "0",
                "observed_hours": "6",
                "shadow_profit_dollars": "12",
            }
        ]
    )
    sleeve = report["sleeves"][0]
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert sleeve["run_rate_status"] == "WITHHELD_UNVERIFIED_OR_ZERO_OBSERVATION_WINDOW"
    assert "never a forecast" in report["target_note"]


def test_verified_full_observation_window_allows_mechanical_daily_run_rate():
    report = rank_sleeves(
        [
            {
                "name": "sample",
                "category": "mlb",
                "venues": ["polymarket_us", "kalshi"],
                "net_edge_per_dollar": "0.03",
                "executable_probability": "0.1",
                "depth_dollars": "5",
                "capital_lock_hours": "1",
                "settlement_confidence": "1",
                "basis_risk": "0",
                "observed_hours": "6",
                "shadow_profit_dollars": "12",
                "observation_window_verified": True,
            }
        ]
    )
    sleeve = report["sleeves"][0]
    assert sleeve["observed_shadow_dollars_per_day"] == "48"
    assert sleeve["run_rate_status"] == "FULL_OBSERVATION_WINDOW_VERIFIED"
