from src.weather_episode_survival_analyze import analyze_closed_episode_survival


def _close(lower: int, upper: int) -> dict:
    return {
        "event": "CLOSE",
        "duration_lower_bound_ms": lower,
        "duration_upper_bound_ms": upper,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def test_survival_uses_lower_bound_for_confirmation_and_upper_for_possible():
    result = analyze_closed_episode_survival([
        _close(40, 80),
        _close(120, 180),
        _close(600, 650),
    ])

    t50 = result["thresholds"]["50"]
    assert t50["confirmed_survival_episodes"] == 2
    assert t50["possible_survival_episodes"] == 3
    assert t50["unresolved_due_to_polling_censoring"] == 1

    t100 = result["thresholds"]["100"]
    assert t100["confirmed_survival_episodes"] == 2
    assert t100["possible_survival_episodes"] == 2

    t250 = result["thresholds"]["250"]
    assert t250["confirmed_survival_episodes"] == 1
    assert t250["possible_survival_episodes"] == 1

    t500 = result["thresholds"]["500"]
    assert t500["confirmed_survival_episodes"] == 1
    assert t500["possible_survival_episodes"] == 1


def test_invalid_or_non_read_only_close_rows_fail_closed():
    rows = [
        _close(100, 50),
        {**_close(100, 200), "read_only": "true"},
        {**_close(100, 200), "order_entry": True},
        {**_close(100, 200), "live_probe_ready": True},
        {"event": "MOVE"},
    ]
    result = analyze_closed_episode_survival(rows)
    assert result["closed_episodes_valid"] == 0
    assert result["closed_episodes_invalid"] == 4
    assert result["thresholds"]["50"]["confirmed_survival_rate"] is None


def test_duplicate_unsorted_thresholds_are_normalized():
    result = analyze_closed_episode_survival([_close(1000, 1000)], thresholds_ms=(500, 50, 500))
    assert list(result["thresholds"]) == ["50", "500"]


def test_bad_thresholds_fail_closed():
    try:
        analyze_closed_episode_survival([], thresholds_ms=(0,))
    except ValueError as exc:
        assert str(exc) == "SURVIVAL_THRESHOLDS_MUST_BE_POSITIVE_INTEGERS"
    else:
        raise AssertionError("expected ValueError")
