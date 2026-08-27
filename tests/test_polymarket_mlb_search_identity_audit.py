from src.polymarket_mlb_search_identity_audit import audit_search_identity


def test_exact_event_with_strict_prop_supply_is_diagnostic_only():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "startTime": "2026-08-27T23:05:00Z",
                "markets": [
                    {
                        "id": "m1",
                        "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                        "sportsMarketType": "baseball_player_strikeouts",
                        "question": "Will Gerrit Cole record at least 6 strikeouts in NYY vs BOS?",
                    }
                ],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00Z",
        query="NYY vs BOS",
    )
    assert out["status"] == "EXACT_EVENT_WITH_STRICT_PROP_SUPPLY"
    assert out["exact_event_match_count"] == 1
    assert out["start_time_verified"] is True
    assert out["start_time_delta_seconds"] == 0
    assert out["strict_parser_compatible_prop_rows"] == 1
    assert out["execution_eligible"] is False
    assert out["order_entry"] is False


def test_exact_slug_with_materially_different_start_time_fails_closed():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "startTime": "2026-08-28T00:05:00Z",
                "markets": [
                    {
                        "id": "m1",
                        "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                        "sportsMarketType": "baseball_player_strikeouts",
                        "question": "Will Gerrit Cole record at least 6 strikeouts in NYY vs BOS?",
                    }
                ],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00Z",
    )
    assert out["status"] == "EXACT_EVENT_START_TIME_MISMATCH"
    assert out["start_time_verified"] is False
    assert out["start_time_delta_seconds"] == 3600
    assert out["execution_eligible"] is False


def test_exact_slug_missing_search_start_time_fails_closed_when_league_time_known():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "markets": [],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00Z",
    )
    assert out["status"] == "EXACT_EVENT_START_TIME_MISSING_OR_INVALID"
    assert out["start_time_verified"] is False
    assert out["execution_eligible"] is False


def test_naive_search_start_time_never_inherits_runtime_timezone():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "startTime": "2026-08-27T23:05:00",
                "markets": [],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00Z",
    )
    assert out["status"] == "EXACT_EVENT_START_TIME_MISSING_OR_INVALID"
    assert out["start_time_verified"] is False
    assert out["start_time_delta_seconds"] is None
    assert out["execution_eligible"] is False


def test_naive_expected_league_start_time_fails_closed():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "startTime": "2026-08-27T23:05:00Z",
                "markets": [],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00",
    )
    assert out["status"] == "EXPECTED_EVENT_START_TIME_INVALID_OR_NAIVE"
    assert out["start_time_verified"] is False
    assert out["execution_eligible"] is False


def test_small_search_time_skew_remains_diagnostic_match():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-27",
                "title": "NYY vs BOS",
                "startTime": "2026-08-27T23:08:00Z",
                "markets": [],
            }
        ]
    }
    out = audit_search_identity(
        payload,
        expected_event_slug="mlb-nyy-bos-2026-08-27",
        expected_event_start_time="2026-08-27T23:05:00Z",
    )
    assert out["status"] == "EXACT_EVENT_NO_PLAYER_PROP_SUPPLY"
    assert out["start_time_verified"] is True
    assert out["start_time_delta_seconds"] == 180


def test_exact_event_without_props_is_distinct_from_parser_drift():
    payload = {
        "events": [
            {
                "slug": "mlb-sea-tex-2026-08-27",
                "title": "SEA vs TEX",
                "markets": [
                    {"id": "m1", "slug": "mlb-sea-tex-winner", "question": "Will SEA win?"}
                ],
            }
        ]
    }
    out = audit_search_identity(payload, expected_event_slug="mlb-sea-tex-2026-08-27")
    assert out["status"] == "EXACT_EVENT_NO_PLAYER_PROP_SUPPLY"
    assert out["prop_like_rows"] == 0


def test_exact_event_prop_schema_drift_is_reported_without_fuzzy_fallback():
    payload = {
        "events": [
            {
                "slug": "mlb-bal-tor-2026-08-27",
                "title": "BAL vs TOR",
                "markets": [
                    {
                        "id": "m1",
                        "slug": "new-shape-hits",
                        "sportsMarketType": "baseball_player_hits_v2",
                        "question": "Will Gunnar Henderson record at least 2 hits in BAL vs TOR?",
                    }
                ],
            }
        ]
    }
    out = audit_search_identity(payload, expected_event_slug="mlb-bal-tor-2026-08-27")
    assert out["status"] == "EXACT_EVENT_PROP_SCHEMA_DRIFT"
    assert out["strict_parser_compatible_prop_rows"] == 0
    assert out["parser_drift_prop_rows"] == 1


def test_similar_search_event_never_substitutes_for_expected_slug():
    payload = {
        "events": [
            {
                "slug": "mlb-nyy-bos-2026-08-28",
                "title": "NYY vs BOS",
                "markets": [
                    {
                        "id": "m1",
                        "slug": "astatc-mlb-nyy-bos-2026-08-28-k-gercole-gte6",
                        "sportsMarketType": "baseball_player_strikeouts",
                        "question": "Will Gerrit Cole record at least 6 strikeouts in NYY vs BOS?",
                    }
                ],
            }
        ]
    }
    out = audit_search_identity(payload, expected_event_slug="mlb-nyy-bos-2026-08-27")
    assert out["status"] == "SEARCH_EVENT_SLUG_DRIFT_OR_MISSING_EXACT_EVENT"
    assert out["exact_event_match_count"] == 0
    assert "strict_parser_compatible_prop_rows" not in out
    assert out["execution_eligible"] is False


def test_duplicate_exact_slug_is_ambiguous_and_fails_closed():
    event = {"slug": "mlb-chc-stl-2026-08-27", "title": "CHC vs STL", "markets": []}
    out = audit_search_identity(
        {"events": [event, dict(event)]}, expected_event_slug="mlb-chc-stl-2026-08-27"
    )
    assert out["status"] == "DUPLICATE_EXACT_EVENT_SLUG_AMBIGUOUS"
    assert out["exact_event_match_count"] == 2
    assert out["execution_eligible"] is False
