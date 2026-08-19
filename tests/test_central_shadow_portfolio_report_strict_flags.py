from src.central_shadow_portfolio_report import rank_sleeves


def _base_row():
    return {
        "name": "mlb-paper",
        "category": "mlb",
        "venues": ["polymarket_us", "kalshi"],
        "net_edge_per_dollar": "0.03",
        "executable_probability": "0.2",
        "depth_dollars": "5",
        "capital_lock_hours": "1",
        "settlement_confidence": "1",
        "basis_risk": "0",
        "observed_hours": "12",
        "shadow_profit_dollars": "6",
        "settled_paper_trade_groups": 2,
        "realized_paper_profit_dollars": "1",
    }


def test_string_false_cannot_verify_observation_window():
    row = _base_row()
    row["observation_window_verified"] = "false"
    report = rank_sleeves([row])
    sleeve = report["sleeves"][0]
    assert sleeve["observation_window_verified"] is False
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert sleeve["run_rate_status"] == "WITHHELD_UNVERIFIED_OR_ZERO_OBSERVATION_WINDOW"


def test_string_true_cannot_verify_observation_window():
    row = _base_row()
    row["observation_window_verified"] = "true"
    report = rank_sleeves([row])
    sleeve = report["sleeves"][0]
    assert sleeve["observation_window_verified"] is False
    assert sleeve["observed_shadow_dollars_per_day"] is None


def test_string_settlement_flag_cannot_unlock_realized_daily_rate():
    row = _base_row()
    row["observation_window_verified"] = True
    row["settlement_window_verified"] = "false"
    report = rank_sleeves([row])
    sleeve = report["sleeves"][0]
    assert sleeve["settlement_window_verified"] is False
    assert sleeve["observed_shadow_dollars_per_day"] == "12"
    assert sleeve["realized_paper_dollars_per_day"] is None
    assert sleeve["realized_run_rate_status"] == "WITHHELD_UNSETTLED_OR_UNVERIFIED_SETTLEMENT_WINDOW"


def test_actual_boolean_true_still_verifies_both_windows():
    row = _base_row()
    row["observation_window_verified"] = True
    row["settlement_window_verified"] = True
    report = rank_sleeves([row])
    sleeve = report["sleeves"][0]
    assert sleeve["observation_window_verified"] is True
    assert sleeve["settlement_window_verified"] is True
    assert sleeve["observed_shadow_dollars_per_day"] == "12"
    assert sleeve["realized_paper_dollars_per_day"] == "2"
