"""Guards on the completion-predictor split."""

from src.l5_completion_predictors import (ADVERSE, OBSERVED, STABLE,
                                          classify_window, opportunity_rate,
                                          split_by_movement)


def win(det, obs=6, opp=1, unresolvable=None):
    return {"slug": "s", "unresolvable_buckets": unresolvable or [],
            "buckets": {"a_0_30s": {"observations": obs, "opportunities": opp,
                                    "deterioration_vs_entry": det}}}


def test_adverse_means_the_complement_got_more_expensive():
    assert classify_window(win("0.0100")) == ADVERSE
    assert classify_window(win("-0.0100")) == STABLE
    assert classify_window(win("0.0000")) == STABLE


def test_an_unresolvable_window_is_not_classified():
    assert classify_window(win("0.01", unresolvable=["b_30_60s"])) is None


def test_a_malformed_row_is_not_classified():
    assert classify_window({"kind": "heartbeat"}) is None
    assert classify_window({}) is None


def test_the_split_recomputes_rather_than_reciting():
    wins = [win("0.01", obs=100, opp=1)] * 2 + [win("-0.01", obs=100, opp=10)] * 2
    r = split_by_movement(wins)
    assert r[ADVERSE]["rate"] == 0.01
    assert r[STABLE]["rate"] == 0.1
    assert r["ratio_stable_over_adverse"] == 10.0


def test_the_split_carries_the_mechanism_caveat():
    r = split_by_movement([win("0.01"), win("-0.01")])
    assert "not that movement is predictable before entry" in \
        r["labels"]["mechanism_not_lever"]


def test_the_recorded_observation_keeps_the_negative_result_too():
    """The within-roster null must travel with the positive finding."""
    assert OBSERVED["within_roster_flow_score"]["ratio"] < 1.2
    assert OBSERVED["within_roster_flow_score"]["verdict"] == "does not rank"
    assert OBSERVED["adverse_complement_movement"]["ratio"] > 4.0
