from src.sports_kp_combo_discovery import classify_combo_row, explicit_legs


def test_maps_only_structured_two_leg_combo():
    row = {
        "slug": "combo-1",
        "title": "Two pick combo",
        "legs": [
            {"eventId": "E1", "marketId": "M1", "outcome": "YES"},
            {"eventId": "E2", "marketId": "M2", "outcome": "NO"},
        ],
    }
    parsed = classify_combo_row("polymarket", row)
    assert parsed is not None
    assert parsed["mapped"] is True
    assert parsed["leg_count"] == 2
    assert [x["event_id"] for x in parsed["legs"]] == ["E1", "E2"]


def test_text_only_combo_is_never_mapped():
    parsed = classify_combo_row("kalshi", {"ticker": "X", "title": "Team A + Team B parlay"})
    assert parsed is not None
    assert parsed["mapped"] is False
    assert parsed["legs"] == []
    assert parsed["status"] == "TEXT_ONLY_COMBO_CANDIDATE_UNMAPPED"


def test_rejects_one_or_four_leg_structures():
    one = {"legs": [{"eventId": "E1", "marketId": "M1", "outcome": "YES"}]}
    four = {"legs": [
        {"eventId": f"E{i}", "marketId": f"M{i}", "outcome": "YES"} for i in range(4)
    ]}
    assert explicit_legs(one) == []
    assert explicit_legs(four) == []


def test_missing_structured_identity_fails_closed():
    row = {"components": [
        {"eventId": "E1", "marketId": "M1", "outcome": "YES"},
        {"marketId": "M2", "outcome": "YES"},
    ]}
    assert explicit_legs(row) == []
