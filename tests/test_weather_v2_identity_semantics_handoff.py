from src.weather_settlement_certified_v2 import _identity_semantics_evidence


def test_identity_semantics_handoff_preserves_literal_verified_evidence():
    result = _identity_semantics_evidence(
        market={"local_day_verified": True, "bracket_semantics_verified": True},
        station={"identity_verified": True},
    )

    assert result == {
        "station_identity_verified": True,
        "local_day_verified": True,
        "bracket_semantics_verified": True,
    }


def test_identity_semantics_handoff_rejects_stringly_truthy_evidence():
    result = _identity_semantics_evidence(
        market={"local_day_verified": "true", "bracket_semantics_verified": 1},
        station={"identity_verified": "true"},
    )

    assert result == {
        "station_identity_verified": False,
        "local_day_verified": False,
        "bracket_semantics_verified": False,
    }


def test_identity_semantics_handoff_fails_closed_when_missing():
    result = _identity_semantics_evidence(market={}, station={})

    assert result == {
        "station_identity_verified": False,
        "local_day_verified": False,
        "bracket_semantics_verified": False,
    }
