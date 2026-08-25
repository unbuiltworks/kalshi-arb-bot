from src.kp_census_decision_stats import CensusDecisionStats, CensusObservation


def obs(*, key="m1", t=0.0, edge=0.03, depth=1.0, skew=20.0, latency=5.0, quality="REST_SNAPSHOT_NOT_WS_LIFETIME"):
    return CensusObservation(
        semantic_key=key,
        observed_ms=t,
        net_edge_usd=edge,
        q1_depth=depth,
        quote_skew_ms=skew,
        process_latency_ms=latency,
        source_quality=quality,
    )


def test_explicit_edge_bins_are_cumulative():
    stats = CensusDecisionStats()
    stats.observe(obs(edge=0.009))
    stats.observe(obs(key="m2", edge=0.015))
    stats.observe(obs(key="m3", edge=0.025))
    stats.observe(obs(key="m4", edge=0.035))
    assert stats.summary()["edge_bins"] == {">=1c": 3, ">=2c": 2, ">=3c": 1}


def test_realistic_capture_requires_survival_and_q1_depth():
    stats = CensusDecisionStats(capture_min_ms=80.0)
    stats.observe(obs(t=0, edge=0.04, depth=2))
    stats.observe(obs(t=79, edge=0.04, depth=2))
    assert stats.summary()["realistic_capture_bins"][">=3c"] == 0
    stats.observe(obs(t=80, edge=0.04, depth=2))
    assert stats.summary()["realistic_capture_bins"][">=3c"] == 1

    shallow = CensusDecisionStats(capture_min_ms=80.0)
    shallow.observe(obs(t=0, edge=0.04, depth=0.5))
    shallow.observe(obs(t=100, edge=0.04, depth=0.5))
    assert shallow.summary()["realistic_capture_bins"][">=3c"] == 0


def test_semantic_key_dedupes_signal_lifecycle_and_records_lifetime():
    stats = CensusDecisionStats(capture_min_ms=80.0)
    stats.observe(obs(t=100, edge=0.03))
    stats.observe(obs(t=150, edge=0.04))
    assert stats.summary()["open_signal_count"] == 1
    assert stats.summary()["unique_semantic_keys"] == 1

    stats.observe(obs(t=220, edge=0.0))
    summary = stats.summary()
    assert summary["open_signal_count"] == 0
    assert summary["closed_signal_count"] == 1
    assert summary["max_closed_lifetime_ms"] == 120


def test_close_missing_ends_signal_without_fabricating_edge():
    stats = CensusDecisionStats()
    stats.observe(obs(key="a", t=0, edge=0.03))
    stats.observe(obs(key="b", t=0, edge=0.03))
    stats.close_missing(100, {"a"})
    summary = stats.summary()
    assert summary["open_signal_count"] == 1
    assert summary["closed_signal_count"] == 1


def test_rest_quality_is_preserved_and_not_mislabeled_as_ws():
    stats = CensusDecisionStats()
    stats.observe(obs(t=0, quality="REST_SNAPSHOT_NOT_WS_LIFETIME"))
    stats.observe(obs(t=100, quality="REST_SNAPSHOT_NOT_WS_LIFETIME"))
    stats.observe(obs(t=150, edge=0.0, quality="REST_SNAPSHOT_NOT_WS_LIFETIME"))
    counts = stats.summary()["source_quality_counts"]
    assert counts == {"REST_SNAPSHOT_NOT_WS_LIFETIME": 1}


def test_invalid_negative_transport_metrics_fail_closed():
    stats = CensusDecisionStats()
    bad = obs(skew=-1)
    try:
        stats.observe(bad)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative skew must fail")
