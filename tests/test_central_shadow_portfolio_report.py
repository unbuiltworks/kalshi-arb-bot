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
    assert sleeve["realized_paper_dollars_per_day"] is None
    assert "never a forecast" in report["target_note"]


def test_verified_full_observation_window_allows_theoretical_daily_run_rate_only():
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
    assert sleeve["run_rate_status"] == "FULL_OBSERVATION_WINDOW_VERIFIED_THEORETICAL_SHADOW"
    assert sleeve["realized_paper_dollars_per_day"] is None
    assert sleeve["realized_run_rate_status"] == "WITHHELD_UNSETTLED_OR_UNVERIFIED_SETTLEMENT_WINDOW"


def test_realized_daily_run_rate_requires_settled_groups_and_verified_settlement_window():
    base = {
        "name": "sample",
        "category": "mlb",
        "venues": ["polymarket_us", "kalshi"],
        "net_edge_per_dollar": "0.03",
        "executable_probability": "0.1",
        "depth_dollars": "5",
        "capital_lock_hours": "1",
        "settlement_confidence": "1",
        "basis_risk": "0",
        "observed_hours": "12",
        "shadow_profit_dollars": "10",
        "observation_window_verified": True,
        "settled_paper_trade_groups": 4,
        "realized_paper_profit_dollars": "3",
    }
    report = rank_sleeves([base])
    assert report["sleeves"][0]["realized_paper_dollars_per_day"] is None

    base["settlement_window_verified"] = True
    report = rank_sleeves([base])
    sleeve = report["sleeves"][0]
    assert sleeve["observed_shadow_dollars_per_day"] == "20"
    assert sleeve["realized_paper_dollars_per_day"] == "6"
    assert sleeve["realized_run_rate_status"] == "FULL_OBSERVATION_AND_SETTLEMENT_WINDOW_VERIFIED"


def test_zero_settled_groups_never_claims_zero_realized_per_day():
    report = rank_sleeves(
        [{
            "name": "no-settlements-yet",
            "category": "mlb",
            "venues": ["polymarket_us", "kalshi"],
            "net_edge_per_dollar": "0.01",
            "executable_probability": "0.1",
            "depth_dollars": "1",
            "capital_lock_hours": "1",
            "settlement_confidence": "0",
            "basis_risk": "0",
            "observed_hours": "24",
            "observation_window_verified": True,
            "settlement_window_verified": True,
            "settled_paper_trade_groups": 0,
            "realized_paper_profit_dollars": "0",
        }]
    )
    sleeve = report["sleeves"][0]
    assert sleeve["realized_paper_dollars_per_day"] is None
    assert sleeve["realized_run_rate_status"] == "WITHHELD_UNSETTLED_OR_UNVERIFIED_SETTLEMENT_WINDOW"
