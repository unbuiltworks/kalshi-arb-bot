from src.polymarket_us_mlb_prop_supply_audit import audit_payload


def test_prop_like_unknown_type_is_diagnostic_only():
    payload = {
        "events": [
            {
                "id": "evt-1",
                "title": "COL vs WSH",
                "markets": [
                    {
                        "id": "m-1",
                        "type": "new_player_market_shape",
                        "question": "Will CJ Abrams record at least 2 hits in COL vs WSH?",
                    }
                ],
            }
        ]
    }
    out = audit_payload(payload, query="CJ Abrams")
    assert out["prop_like_rows"] == 1
    assert out["prop_like_rows_in_event_markets"] == 1
    assert out["prop_like_rows_outside_event_markets"] == 0
    assert out["strict_parser_compatible_prop_rows"] == 0
    assert out["parser_drift_prop_rows"] == 1
    assert out["parser_drift_reason_counts"]["MISSING_SPORTS_MARKET_TYPE"] == 1
    assert out["parser_drift_reason_counts"]["MISSING_MARKET_SLUG"] == 1
    assert out["interpretation"] == "PARSER_OR_SCHEMA_DRIFT_CONFIRMED_IN_PAYLOAD"
    sample = out["prop_like_samples"][0]
    assert sample["status"] == "PROP_LIKE_PARSER_DRIFT_DIAGNOSTIC_ONLY"
    assert sample["strict_discovery_compatible"] is False
    assert sample["direct_event_market"] is True
    assert "sportsMarketType" not in sample["schema_keys"]
    assert out["execution_eligible"] is False
    assert out["order_entry"] is False


def test_non_prop_sports_markets_do_not_create_false_signal():
    payload = {
        "events": [
            {
                "id": "evt-1",
                "title": "COL vs WSH",
                "markets": [
                    {"id": "m-1", "type": "moneyline", "question": "Will COL win?"},
                    {"id": "m-2", "type": "total", "question": "Will COL vs WSH go over 8.5 runs?"},
                ],
            }
        ]
    }
    out = audit_payload(payload, query="COL WSH")
    assert out["prop_like_rows"] == 0
    assert out["strict_parser_compatible_prop_rows"] == 0
    assert out["parser_drift_prop_rows"] == 0
    assert out["interpretation"] == "NO_PLAYER_PROP_SHAPE_OBSERVED_IN_THIS_PAYLOAD"


def test_recursive_nested_payload_is_inventoried_as_container_parser_drift():
    payload = {
        "data": {
            "results": [
                {
                    "id": "evt-1",
                    "name": "MLB event",
                    "children": [
                        {
                            "instrument_id": "inst-1",
                            "category": "player_prop",
                            "title": "Gerrit Cole 6+ strikeouts",
                        }
                    ],
                }
            ]
        }
    }
    out = audit_payload(payload, query="Gerrit Cole")
    assert out["market_like_rows"] >= 1
    assert out["prop_like_rows"] == 1
    assert out["prop_like_rows_in_event_markets"] == 0
    assert out["prop_like_rows_outside_event_markets"] == 1
    assert out["market_type_counts"]["player_prop"] == 1
    assert out["parser_drift_prop_rows"] == 1
    assert out["parser_drift_reason_counts"]["OUTSIDE_EVENT_MARKETS_CONTAINER"] == 1
    sample = out["prop_like_samples"][0]
    assert sample["direct_event_market"] is False
    assert "children" in sample["container_path"]
    assert out["interpretation"] == "PARSER_OR_SCHEMA_DRIFT_CONFIRMED_IN_PAYLOAD"


def test_nested_data_events_markets_is_not_misclassified_as_strict_search_container():
    payload = {
        "data": {
            "events": [
                {
                    "id": "evt-1",
                    "title": "NYY vs BOS",
                    "markets": [
                        {
                            "id": "m-1",
                            "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                            "sportsMarketType": "baseball_player_strikeouts",
                            "question": "Will Gerrit Cole record at least 6 pitching strikeouts in NYY vs BOS?",
                        }
                    ],
                }
            ]
        }
    }
    out = audit_payload(payload, query="Gerrit Cole")
    assert out["prop_like_rows"] == 1
    assert out["prop_like_rows_in_event_markets"] == 0
    assert out["prop_like_rows_outside_event_markets"] == 1
    assert out["strict_parser_compatible_prop_rows"] == 0
    assert out["parser_drift_prop_rows"] == 1
    assert out["parser_drift_reason_counts"] == {"OUTSIDE_EVENT_MARKETS_CONTAINER": 1}
    sample = out["prop_like_samples"][0]
    assert sample["direct_event_market"] is False
    assert sample["container_path"].startswith("data.events.")
    assert sample["status"] == "PROP_LIKE_PARSER_DRIFT_DIAGNOSTIC_ONLY"


def test_known_sports_market_type_and_slug_are_strict_discovery_compatible():
    payload = {
        "events": [
            {
                "id": "evt-1",
                "title": "NYY vs BOS",
                "markets": [
                    {
                        "id": "m-1",
                        "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                        "sportsMarketType": "baseball_player_strikeouts",
                        "question": "Will Gerrit Cole record at least 6 pitching strikeouts in NYY vs BOS?",
                    }
                ],
            }
        ]
    }
    out = audit_payload(payload, query="Gerrit Cole")
    assert out["prop_like_rows"] == 1
    assert out["prop_like_rows_in_event_markets"] == 1
    assert out["strict_parser_compatible_prop_rows"] == 1
    assert out["parser_drift_prop_rows"] == 0
    assert out["interpretation"] == "STRICT_PARSER_COMPATIBLE_PROP_SUPPLY_OBSERVED"
    sample = out["prop_like_samples"][0]
    assert sample["sports_market_type"] == "baseball_player_strikeouts"
    assert sample["direct_event_market"] is True
    assert sample["strict_discovery_compatible"] is True
    assert sample["strict_discovery_blockers"] == []
    assert sample["status"] == "STRICT_DISCOVERY_SHAPE_PRESENT_DIAGNOSTIC_ONLY"


def test_known_type_outside_event_markets_is_not_strict_discovery_compatible():
    payload = {
        "data": {
            "results": [
                {
                    "id": "m-1",
                    "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                    "sportsMarketType": "baseball_player_strikeouts",
                    "question": "Will Gerrit Cole record at least 6 pitching strikeouts in NYY vs BOS?",
                }
            ]
        }
    }
    out = audit_payload(payload, query="Gerrit Cole")
    assert out["strict_parser_compatible_prop_rows"] == 0
    assert out["parser_drift_prop_rows"] == 1
    assert out["parser_drift_reason_counts"] == {"OUTSIDE_EVENT_MARKETS_CONTAINER": 1}
    sample = out["prop_like_samples"][0]
    assert sample["sports_market_type"] == "baseball_player_strikeouts"
    assert sample["strict_discovery_blockers"] == ["OUTSIDE_EVENT_MARKETS_CONTAINER"]
    assert sample["direct_event_market"] is False


def test_unrecognized_sports_market_type_is_reported_separately():
    payload = {
        "events": [
            {
                "id": "evt-1",
                "title": "SEA vs TEX",
                "markets": [
                    {
                        "id": "m-1",
                        "slug": "mlb-sea-tex-2026-08-27-newprop-player-gte2",
                        "sportsMarketType": "baseball_player_hits_v2",
                        "question": "Will Julio Rodriguez record at least 2 hits in SEA vs TEX?",
                    }
                ],
            }
        ]
    }
    out = audit_payload(payload, query="Julio Rodriguez")
    assert out["parser_drift_prop_rows"] == 1
    assert out["parser_drift_reason_counts"]["UNRECOGNIZED_SPORTS_MARKET_TYPE"] == 1
    assert out["prop_like_samples"][0]["strict_discovery_blockers"] == [
        "UNRECOGNIZED_SPORTS_MARKET_TYPE"
    ]
