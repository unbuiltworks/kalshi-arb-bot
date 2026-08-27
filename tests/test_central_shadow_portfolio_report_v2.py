import src.central_shadow_portfolio_report_v2 as report_v2
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


def _verified_audit(_row):
    return {
        "executable_shadow_prereqs_verified": True,
        "selected_side_schema_verified": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def _verified_metric_audit(_row):
    return {
        "weather_portfolio_metric_binding_verified": True,
        "weather_execution_probability_verified": True,
        "weather_execution_episode_basis_verified": True,
        "weather_execution_episode_probability_verified": True,
        "weather_execution_episode_identity_verified": True,
        "weather_execution_episode_set_reconciled": True,
        "derived_net_edge_per_dollar": "0.25",
        "derived_depth_dollars": "4",
        "verified_executable_probability": "0.5",
        "executable_probability_success_episode_count": 10,
        "executable_probability_unique_episode_count": 20,
        "blockers": [],
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def _patch_verified(monkeypatch):
    monkeypatch.setattr(report_v2, "audit_weather_certification_dimensions_v3", _verified_audit)
    monkeypatch.setattr(report_v2, "audit_weather_portfolio_metric_binding_v3", _verified_metric_audit)


def test_uncertified_weather_cannot_be_promoted_or_annualized():
    report = rank_sleeves([_weather_row()])
    sleeve = report["sleeves"][0]
    assert sleeve["capacity_score"] == "0"
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_CANONICAL_AUDIT_REPLAY_UNVERIFIED" in sleeve["hard_blockers"]
    assert "WEATHER_CERTIFICATION_PREREQS_UNVERIFIED" in sleeve["hard_blockers"]
    assert "WEATHER_SELECTED_SIDE_SCHEMA_UNVERIFIED" in sleeve["hard_blockers"]
    assert "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED" in sleeve["hard_blockers"]


def test_producer_summary_booleans_alone_cannot_unlock_projection():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = True
    row["selected_side_schema_verified"] = True
    row["weather_portfolio_metric_binding_verified"] = True
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_CANONICAL_AUDIT_REPLAY_UNVERIFIED" in sleeve["hard_blockers"]
    assert "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED" in sleeve["hard_blockers"]


def test_string_true_weather_certification_fails_closed():
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = "true"
    row["selected_side_schema_verified"] = True
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None


def test_independently_replayed_weather_certification_and_metrics_preserve_shadow_projection(monkeypatch):
    _patch_verified(monkeypatch)
    row = _weather_row()
    row["executable_shadow_prereqs_verified"] = False
    row["selected_side_schema_verified"] = False
    row["net_edge_per_dollar"] = "99"
    row["depth_dollars"] = "999"
    row["executable_probability"] = "1"
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] != "0"
    assert sleeve["observed_shadow_dollars_per_day"] == "80"
    assert sleeve["net_edge_per_dollar"] == "0.25"
    assert sleeve["depth_dollars"] == "4"
    assert sleeve["executable_probability"] == "0.5"
    assert sleeve["capacity_score"] == str((report_v2.D("0.25") * report_v2.D("0.5") * report_v2.D("4")) / report_v2.D("12"))


def test_metric_binding_failure_zeroes_weather_capacity_even_when_canonical_audit_is_green(monkeypatch):
    monkeypatch.setattr(report_v2, "audit_weather_certification_dimensions_v3", _verified_audit)

    def bad_metrics(_row):
        return {
            "weather_portfolio_metric_binding_verified": False,
            "weather_execution_episode_probability_verified": False,
            "weather_execution_episode_identity_verified": False,
            "weather_execution_episode_set_reconciled": False,
            "derived_net_edge_per_dollar": "0.25",
            "derived_depth_dollars": "4",
            "verified_executable_probability": None,
            "blockers": ["WEATHER_EXECUTION_EPISODE_SET_RECONCILIATION_UNVERIFIED"],
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    monkeypatch.setattr(report_v2, "audit_weather_portfolio_metric_binding_v3", bad_metrics)
    row = _weather_row()
    row["net_edge_per_dollar"] = "999"
    row["depth_dollars"] = "999"
    row["executable_probability"] = "1"
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["capacity_score"] == "0"
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED" in sleeve["hard_blockers"]


def test_snapshot_probability_without_unique_episode_verification_cannot_project(monkeypatch):
    monkeypatch.setattr(report_v2, "audit_weather_certification_dimensions_v3", _verified_audit)

    def snapshot_only(_row):
        return {
            "weather_portfolio_metric_binding_verified": True,
            "weather_execution_probability_verified": True,
            "weather_execution_episode_probability_verified": False,
            "weather_execution_episode_identity_verified": False,
            "weather_execution_episode_set_reconciled": False,
            "derived_net_edge_per_dollar": "0.25",
            "derived_depth_dollars": "4",
            "verified_executable_probability": "0.9",
            "blockers": ["WEATHER_EXECUTION_EPISODE_BASIS_UNVERIFIED"],
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    monkeypatch.setattr(report_v2, "audit_weather_portfolio_metric_binding_v3", snapshot_only)
    sleeve = rank_sleeves([_weather_row()])["sleeves"][0]
    assert sleeve["capacity_score"] == "0"
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED" in sleeve["hard_blockers"]


def test_episode_counts_without_explicit_identity_reconciliation_cannot_project(monkeypatch):
    monkeypatch.setattr(report_v2, "audit_weather_certification_dimensions_v3", _verified_audit)

    def counts_only(_row):
        return {
            "weather_portfolio_metric_binding_verified": True,
            "weather_execution_episode_probability_verified": True,
            "weather_execution_episode_identity_verified": False,
            "weather_execution_episode_set_reconciled": False,
            "derived_net_edge_per_dollar": "0.25",
            "derived_depth_dollars": "4",
            "verified_executable_probability": "0.5",
            "blockers": ["WEATHER_EXECUTION_EPISODE_IDENTITY_UNVERIFIED"],
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    monkeypatch.setattr(report_v2, "audit_weather_portfolio_metric_binding_v3", counts_only)
    sleeve = rank_sleeves([_weather_row()])["sleeves"][0]
    assert sleeve["capacity_score"] == "0"
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED" in sleeve["hard_blockers"]


def test_canonical_replay_must_be_literal_true_for_projection(monkeypatch):
    monkeypatch.setattr(report_v2, "audit_weather_portfolio_metric_binding_v3", _verified_metric_audit)
    for selected in (None, False, "true", 1):
        def audit(_row, selected=selected):
            return {
                "executable_shadow_prereqs_verified": True,
                "selected_side_schema_verified": selected,
                "read_only": True,
                "order_entry": False,
                "live_probe_ready": False,
            }

        monkeypatch.setattr(report_v2, "audit_weather_certification_dimensions_v3", audit)
        row = _weather_row()
        sleeve = rank_sleeves([row])["sleeves"][0]
        assert sleeve["promotable_score"] == "0"
        assert sleeve["observed_shadow_dollars_per_day"] is None
        assert "WEATHER_CANONICAL_AUDIT_REPLAY_UNVERIFIED" in sleeve["hard_blockers"]
        assert "WEATHER_SELECTED_SIDE_SCHEMA_UNVERIFIED" in sleeve["hard_blockers"]


def test_certified_weather_with_remaining_hard_blocker_is_not_annualized(monkeypatch):
    _patch_verified(monkeypatch)
    row = _weather_row()
    row["hard_blockers"] = ["TWC_SOURCE_ACCESS_REQUIRED"]
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "TWC_SOURCE_ACCESS_REQUIRED" in sleeve["hard_blockers"]


def test_malformed_hard_blocker_entry_fails_closed_without_stringifying_it(monkeypatch):
    _patch_verified(monkeypatch)
    row = _weather_row()
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


def test_malformed_hard_blockers_also_fail_closed(monkeypatch):
    _patch_verified(monkeypatch)
    row = _weather_row()
    row["hard_blockers"] = ""
    sleeve = rank_sleeves([row])["sleeves"][0]
    assert sleeve["promotable_score"] == "0"
    assert sleeve["observed_shadow_dollars_per_day"] is None
    assert "MALFORMED_HARD_BLOCKER_TELEMETRY" in sleeve["hard_blockers"]
