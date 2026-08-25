from src.central_shadow_portfolio_report_v2 import rank_sleeves


def _weather_row():
    return {
        "name": "kalshi-weather-miami",
        "category": "weather",
        "venues": ["kalshi"],
        "net_edge_per_dollar": "0.40",
        "executable_probability": "0.9",
        "depth_dollars": "5",
        "capital_lock_hours": "12",
        "settlement_confidence": "1",
        "basis_risk": "0",
        "observed_hours": "6",
        "shadow_profit_dollars": "20",
        "observation_window_verified": True,
        "hard_blockers": [],
    }


def test_uncertified_weather_cannot_be_promoted_or_annualized():
    report = rank_sleeves([_weather_row()])
    sleeve = report["sleeves"][0]
    assert sleeve["capacity_score"] != "0"
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_CERTIFICATION_PREREQS_UNVERIFIED" in sleeve["hard_blockers"]


def test_string_true_weather_certification_fails_closed():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = "true"
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None


def test_literal_true_weather_certification_preserves_verified_shadow_path():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = True
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] != "0"
    assert sleeve["observed_shadow_dollars_per_day"] == "80"


def test_certified_weather_with_remaining_hard_blocker_is_not_annualized():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = True
    row["hard_blockers"] = ["TWC_SOURCE_ACCESS_REQUIRED"]
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "TWC_SOURCE_ACCESS_REQUIRED" in sleeve["hard_blockers"]


def test_malformed_hard_blocker_entry_fails_closed_without_stringifying_it():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = True
    row["hard_blockers"] = [None, ""]
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert sleeve["hard_blockers"] == ["MALFORMED_HARD_BLOCKER_TELEMETRY"]


def test_non_weather_behavior_is_unchanged():
    row = _weather_row()
    row["category"] = "mlb"
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] != "0"
    assert sleeve["observed_shadow_dollars_per_day"] == "80"


def test_malformed_hard_blockers_also_fail_closed():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = True
    row["hard_blockers"] = ""
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "MALFORMED_HARD_BLOCKER_TELEMETRY" in sleeve["hard_blockers"]
