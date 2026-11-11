"""Queue-bound is a diagnosis, and the two easy wrong answers stay excluded."""
import json

import pytest

from analysis.l5.gemini_surface import queue_diagnosis as Q


def _obs(placed, live=2, ahead=("40", "25"), cleared=("0", "0"), past=0,
         name="two_sided_symmetric"):
    return {"kind": "observation", "surfaces": {name: {
        "quotes_placed": placed, "live_quotes": live,
        "queue_ahead_by_side": {
            "BUY": {"median_volume_ahead_at_placement": ahead[0],
                    "median_volume_cleared_since": cleared[0],
                    "n_quotes_past_their_queue": past},
            "SELL": {"median_volume_ahead_at_placement": ahead[1],
                     "median_volume_cleared_since": cleared[1],
                     "n_quotes_past_their_queue": past}}}}}


def _tape(tmp_path, rows):
    p = tmp_path / "gemini_surface_20260917T220000Z.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(tmp_path)


# ------------------------------------------- size is not a queue-priority fix

def test_smaller_size_does_not_change_queue_priority():
    """Reducing our lot does not reduce the volume already ahead of us."""
    s = Q.size_effects()
    assert s["queue_priority_effect"] == "NONE"
    assert "unchanged by our own size" in s["queue_priority_why"]
    assert "never a queue-priority fix" in s["labels"]


def test_size_is_reported_as_risk_and_capacity_instead():
    s = Q.size_effects()
    assert set(s["what_it_does_change"]) == {"risk_per_fill", "capacity",
                                             "partial_fills"}


def test_the_unit_is_compared_to_the_venue_minimum():
    s = Q.size_effects(unit="1", venue_min="0.01")
    assert float(s["ratio"]) == 100.0


# -------------------------------------------- the two answers that are wrong

def test_flow_starved_and_broken_complement_are_explicitly_rejected():
    v = Q.verdict({"per_surface": {}}, {"per_surface_side": {}})
    joined = " ".join(v["explicitly_rejected"])
    assert "FLOW_STARVED" in joined and "flow went through" in joined
    assert "BROKEN_COMPLEMENT" in joined


def test_the_report_names_the_state_as_queue_bound(tmp_path):
    d = _tape(tmp_path, [_obs(10), _obs(12)])
    r = Q.report(d)
    assert r["ok"] is True and r["state"] == "QUEUE_BOUND"
    assert "NOT flow-starved" in " ".join(r["labels"])


# ---------------------------------------------------------- the hypotheses

def test_a_churning_surface_supports_the_persistence_hypothesis():
    """A quote re-placed constantly never accumulates priority."""
    obs = [_obs(0), _obs(5), _obs(10), _obs(15)]
    p = Q.persistence(obs)
    v = Q.verdict(p, Q.queue_by_offset(obs))
    assert v["mean_placements_per_observation"] >= 0.25
    assert "PERSISTENCE" in v["supported_by_evidence"]


def test_a_stable_surface_does_not_support_it():
    obs = [_obs(10) for _ in range(10)]
    p = Q.persistence(obs)
    v = Q.verdict(p, Q.queue_by_offset(obs))
    assert v["mean_placements_per_observation"] == 0.0
    assert "PERSISTENCE" not in v["supported_by_evidence"]


def test_a_queue_that_barely_clears_supports_placement_and_the_model():
    obs = [_obs(10, ahead=("400", "400"), cleared=("5", "5"))]
    o = Q.queue_by_offset(obs)
    v = Q.verdict(Q.persistence(obs), o)
    assert v["median_clear_ratio"] < 0.5
    assert "PLACEMENT" in v["supported_by_evidence"]
    assert "QUEUE_MODEL" in v["supported_by_evidence"]


def test_a_queue_that_clears_does_not():
    obs = [_obs(10, ahead=("10", "10"), cleared=("30", "30"), past=1)]
    v = Q.verdict(Q.persistence(obs), Q.queue_by_offset(obs))
    assert v["median_clear_ratio"] >= 0.5
    assert "PLACEMENT" not in v["supported_by_evidence"]


def test_the_model_headroom_sizes_the_assumption_not_our_real_position():
    obs = [_obs(10, ahead=("100", "100"), cleared=("20", "20"))]
    h = Q.queue_model_headroom(Q.queue_by_offset(obs))
    k = list(h["per_surface_side"].values())[0]
    assert k["priority_factor_needed_to_fill"] == 5.0
    assert "does not measure our real position" in h["labels"]


def test_it_says_what_it_cannot_settle():
    v = Q.verdict({"per_surface": {}}, {"per_surface_side": {}})
    assert "unpublished" in v["cannot_settle"]
    assert "not which is true" in v["cannot_settle"]


# ------------------------------------------------------------ degenerate

def test_no_tape_is_reported_not_crashed():
    assert Q.report("/definitely/not/here")["ok"] is False


def test_a_tape_with_no_observations_is_reported(tmp_path):
    d = _tape(tmp_path, [{"kind": "session_start"}])
    r = Q.report(d)
    assert r["ok"] is False and "no observations" in r["why"]


def test_a_surface_that_reports_no_depth_is_skipped_not_zeroed(tmp_path):
    obs = [{"kind": "observation", "surfaces": {"s": {
        "quotes_placed": 3, "live_quotes": 1,
        "queue_ahead_by_side": {"BUY": {}, "SELL": {}}}}}]
    o = Q.queue_by_offset(obs)
    assert o["per_surface_side"] == {}
