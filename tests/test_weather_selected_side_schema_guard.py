from src.weather_selected_side_schema_guard import normalize_selected_side_schema


def _row():
    return {
        "category": "weather",
        "classification": "SHADOW",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "ev_certified_shadow": True,
        "best_shadow_side": {
            "side": "NO",
            "ask_cents": 36,
            "available_contracts": 5,
            "modeled_taker_fee_cents": 2,
            "modeled_after_fee_ev_cents": "51.0",
            "modeled_after_fee_edge_cents_per_contract": "10.2",
        },
        "modeled_taker_fee_cents": 1,
        "modeled_after_fee_ev_cents": "-4.0",
        "modeled_after_fee_edge_cents_per_contract": "-0.8",
        "side_economics": [
            {
                "side": "YES",
                "ask_cents": 65,
                "available_contracts": 5,
                "modeled_taker_fee_cents": 1,
                "modeled_after_fee_ev_cents": "-4.0",
                "modeled_after_fee_edge_cents_per_contract": "-0.8",
            },
            {
                "side": "NO",
                "ask_cents": 36,
                "available_contracts": 5,
                "modeled_taker_fee_cents": 2,
                "modeled_after_fee_ev_cents": "51.0",
                "modeled_after_fee_edge_cents_per_contract": "10.2",
            },
        ],
        "blockers": [],
    }


def test_normalizes_legacy_object_and_binds_top_level_to_selected_no_side():
    out = normalize_selected_side_schema(_row())
    assert out["selected_side_schema_verified"] is True
    assert out["selected_side_recomputed"] is True
    assert out["best_shadow_side"] == "NO"
    assert out["best_shadow_side_detail"]["side"] == "NO"
    assert out["modeled_taker_fee_cents"] == 2
    assert out["modeled_after_fee_ev_cents"] == "51.0"
    assert out["modeled_after_fee_edge_cents_per_contract"] == "10.2"
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_duplicate_selected_side_records_fail_closed_and_strip_all_economics():
    row = _row()
    row["side_economics"].append(dict(row["side_economics"][1]))
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["best_shadow_side_detail"] is None
    assert out["modeled_taker_fee_cents"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert out["modeled_after_fee_edge_cents_per_contract"] is None
    assert "SIDE_ECONOMICS_IDENTITY_UNVERIFIED" in out["blockers"]


def test_extra_unknown_side_record_fails_closed_instead_of_being_ignored():
    row = _row()
    row["side_economics"].append({
        "side": "MAYBE",
        "modeled_taker_fee_cents": 0,
        "modeled_after_fee_ev_cents": "999",
        "modeled_after_fee_edge_cents_per_contract": "999",
    })
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "SIDE_ECONOMICS_IDENTITY_UNVERIFIED" in out["blockers"]


def test_extra_nondict_side_record_fails_closed_instead_of_being_ignored():
    row = _row()
    row["side_economics"].append("NO=51c")
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "SIDE_ECONOMICS_IDENTITY_UNVERIFIED" in out["blockers"]


def test_nonfinite_selected_ev_fails_closed():
    row = _row()
    row["side_economics"][1]["modeled_after_fee_ev_cents"] = "NaN"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_edge_cents_per_contract"] is None
    assert "SELECTED_SIDE_ECONOMICS_INVALID" in out["blockers"]


def test_nonfinite_selected_edge_fails_closed():
    row = _row()
    row["side_economics"][1]["modeled_after_fee_edge_cents_per_contract"] = "Infinity"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["modeled_after_fee_ev_cents"] is None
    assert out["modeled_after_fee_edge_cents_per_contract"] is None
    assert "SELECTED_SIDE_ECONOMICS_INVALID" in out["blockers"]


def test_missing_selected_side_cannot_leave_stale_dashboard_economics():
    row = _row()
    row["best_shadow_side"] = None
    row["best_shadow_side_detail"] = {"side": "NO"}
    row["modeled_taker_fee_cents"] = "2"
    row["modeled_after_fee_ev_cents"] = "51.0"
    row["modeled_after_fee_edge_cents_per_contract"] = "10.2"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["best_shadow_side_detail"] is None
    assert out["modeled_taker_fee_cents"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert out["modeled_after_fee_edge_cents_per_contract"] is None
    assert "BEST_SHADOW_SIDE_MISSING" in out["blockers"]
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_string_side_is_supported_but_still_rebound_to_exact_side_record():
    row = _row()
    row["best_shadow_side"] = "NO"
    row["modeled_after_fee_ev_cents"] = "999"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is True
    assert out["modeled_after_fee_ev_cents"] == "51.0"


def test_declared_worse_side_fails_closed_instead_of_misranking_portfolio():
    row = _row()
    row["best_shadow_side"] = "YES"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "BEST_SHADOW_SIDE_RECOMPUTE_MISMATCH" in out["blockers"]


def test_equal_yes_no_ev_fails_closed_as_ambiguous_best_side():
    row = _row()
    row["side_economics"][0]["modeled_after_fee_ev_cents"] = "51.0"
    row["side_economics"][0]["modeled_after_fee_edge_cents_per_contract"] = "10.2"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["modeled_after_fee_ev_cents"] is None
    assert "BEST_SHADOW_SIDE_TIE" in out["blockers"]


def test_legacy_best_detail_must_match_recomputed_selected_economics():
    row = _row()
    row["best_shadow_side"]["modeled_after_fee_ev_cents"] = "49.0"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert "LEGACY_BEST_SHADOW_SIDE_DETAIL_MISMATCH" in out["blockers"]


def test_string_blocker_telemetry_fails_closed_instead_of_becoming_characters():
    row = _row()
    row["blockers"] = "TWC_SOURCE_ACCESS_REQUIRED"
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert out["best_shadow_side"] is None
    assert out["blockers"] == ["MALFORMED_BLOCKER_TELEMETRY"]


def test_mixed_malformed_blocker_entries_fail_closed_and_preserve_valid_blocker():
    row = _row()
    row["blockers"] = ["TWC_SOURCE_ACCESS_REQUIRED", None, ""]
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is False
    assert out["ev_certified_shadow"] is False
    assert "MALFORMED_BLOCKER_TELEMETRY" in out["blockers"]
    assert "TWC_SOURCE_ACCESS_REQUIRED" in out["blockers"]


def test_valid_blocker_collection_is_normalized_without_losing_existing_evidence():
    row = _row()
    row["blockers"] = [" TWC_SOURCE_ACCESS_REQUIRED ", "TWC_SOURCE_ACCESS_REQUIRED"]
    out = normalize_selected_side_schema(row)
    assert out["selected_side_schema_verified"] is True
    assert out["blockers"] == ["TWC_SOURCE_ACCESS_REQUIRED"]
