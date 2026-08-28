from src.weather_anchor_valid_local_day_guard import audit_anchor_valid_local_day


def _audit(**overrides):
    params = {
        "market_date": "2026-08-21",
        "independent_anchor_local_date": "2026-08-21",
        "independent_anchor_valid_time": "2026-08-22T03:59:59Z",
        "station_timezone": "America/New_York",
        "timezone_mapping_verified": True,
    }
    params.update(overrides)
    return audit_anchor_valid_local_day(**params)


def test_anchor_valid_time_is_bound_to_exact_contract_day():
    row = _audit()
    assert row["anchor_valid_local_day_verified"] is True
    assert row["derived_anchor_local_date"] == "2026-08-21"
    assert row["blockers"] == []
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_adjacent_station_local_day_fails_closed():
    row = _audit(independent_anchor_valid_time="2026-08-22T04:00:00Z")
    assert row["anchor_valid_local_day_verified"] is False
    assert row["derived_anchor_local_date"] == "2026-08-22"
    assert "INDEPENDENT_ANCHOR_VALID_TIME_MARKET_DAY_MISMATCH" in row["blockers"]
    assert "INDEPENDENT_ANCHOR_VALID_TIME_DECLARED_DAY_MISMATCH" in row["blockers"]


def test_declared_anchor_day_cannot_disagree_with_derived_day():
    row = _audit(independent_anchor_local_date="2026-08-22")
    assert row["anchor_valid_local_day_verified"] is False
    assert row["derived_anchor_local_date"] == "2026-08-21"
    assert "INDEPENDENT_ANCHOR_VALID_TIME_DECLARED_DAY_MISMATCH" in row["blockers"]


def test_missing_or_naive_anchor_valid_time_cannot_certify():
    missing = _audit(independent_anchor_valid_time=None)
    assert missing["anchor_valid_local_day_verified"] is False
    assert "INDEPENDENT_ANCHOR_VALID_TIME_UNVERIFIED" in missing["blockers"]

    naive = _audit(independent_anchor_valid_time="2026-08-21T23:59:59")
    assert naive["anchor_valid_local_day_verified"] is False
    assert "INDEPENDENT_ANCHOR_VALID_TIME_UNVERIFIED" in naive["blockers"]


def test_stringly_timezone_audit_and_invalid_zone_fail_closed():
    stringly = _audit(timezone_mapping_verified="true")
    assert stringly["anchor_valid_local_day_verified"] is False
    assert "STATION_TIMEZONE_MAPPING_UNVERIFIED" in stringly["blockers"]

    invalid = _audit(station_timezone="Mars/Olympus_Mons")
    assert invalid["anchor_valid_local_day_verified"] is False
    assert "STATION_TIMEZONE_INVALID" in invalid["blockers"]
