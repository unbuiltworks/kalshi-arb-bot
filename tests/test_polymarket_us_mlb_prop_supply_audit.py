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
    assert out["interpretation"] == "PARSER_OR_SCHEMA_DRIFT_POSSIBLE"
    assert out["prop_like_samples"][0]["status"] == "PROP_LIKE_DIAGNOSTIC_ONLY"
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
    assert out["interpretation"] == "NO_PLAYER_PROP_SHAPE_OBSERVED_IN_THIS_PAYLOAD"


def test_recursive_nested_payload_is_inventoried():
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
    assert out["market_type_counts"]["player_prop"] == 1
