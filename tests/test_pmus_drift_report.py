"""The drift evaluator must enforce the prereg, not merely describe it.

Every case here has a hand-computable answer, so a failure means the
evaluator is wrong rather than the data being surprising.
"""

import json

import pytest

from src.pmus_drift_report import (DROP_DRIFT_UNRESOLVABLE,
                                   DROP_NOT_FLOW_RANKED,
                                   DROP_UNRESOLVABLE_BUCKET,
                                   MIN_LOW_SHARE, MIN_OPPORTUNITIES,
                                   MIN_RATE_RATIO, evaluate, parse, usable)


def row(drift, opps, obs=24, selection="flow_ranked", resolvable=True,
        unresolvable_buckets=None):
    d = {"primary_lookback_s": 60.0, "resolvable": resolvable,
         "drift_abs": (None if not resolvable else str(drift)),
         "by_lookback": {}}
    return {"slug": "s", "selection": selection,
            "unresolvable_buckets": unresolvable_buckets or [],
            "pre_entry_drift": d,
            "buckets": {"a_0_30s": {"observations": obs, "opportunities": opps},
                        "b_30_60s": {"observations": 0, "opportunities": 0},
                        "c_60_120s": {"observations": 0, "opportunities": 0}}}


def lines(rows):
    return ["PMUS_PERSISTENCE_WINDOW " + json.dumps(r) for r in rows]


class TestFrozenConstants:
    def test_they_match_the_preregistration(self):
        assert MIN_OPPORTUNITIES == 30
        assert MIN_RATE_RATIO == 1.50
        assert MIN_LOW_SHARE == 0.55

    def test_the_prereg_file_states_the_same_numbers(self):
        txt = open("analysis/l5/PREREG_PRE_ENTRY_DRIFT.md", encoding="utf-8").read()
        assert ">= 30" in txt and ">= 1.50" in txt and ">= 55%" in txt


class TestUnderpoweredCannotLeakTheSplit:
    """The guard that matters most on the fourth read, not the first."""

    def test_no_ratio_is_returned_below_p1(self):
        r = evaluate(lines([row(0.00, 5), row(0.10, 1)]))
        assert r["verdict"] == "UNDERPOWERED"
        assert r["deficit"] == MIN_OPPORTUNITIES - 6
        for k in ("p2_rate_ratio", "p3_low_share", "median_drift_abs",
                  "low_drift", "high_drift"):
            assert k not in r, "%s must not exist on an underpowered result" % k

    def test_the_deficit_is_exact(self):
        r = evaluate(lines([row(0.0, 11)]))
        assert r["opportunities"] == 11
        assert r["deficit"] == 19


class TestDecision:
    def _split(self, low_opps, high_opps, n=8):
        """n calm windows and n moving ones, equal observations."""
        rows = [row(0.00, low_opps) for _ in range(n)]
        rows += [row(0.10, high_opps) for _ in range(n)]
        return evaluate(lines(rows))

    def test_a_clear_pass(self):
        r = self._split(low_opps=4, high_opps=1)      # ratio 4.0, share 0.8
        assert r["verdict"] == "PASS"
        assert r["p2_rate_ratio"] == pytest.approx(4.0)
        assert r["p3_low_share"] == pytest.approx(0.8)

    def test_a_ratio_below_the_bar_fails(self):
        r = self._split(low_opps=3, high_opps=3)      # ratio 1.0
        assert r["verdict"] == "FAIL"
        assert r["p2_met"] is False
        assert "P2" in r["why"]

    def test_a_ratio_just_under_the_bar_still_fails(self):
        """1.49 is not 1.50. The bar does not round in our favour."""
        rows = [row(0.00, 149) for _ in range(1)] + [row(0.10, 100)]
        r = evaluate(lines(rows))
        assert r["p2_rate_ratio"] == pytest.approx(1.49)
        assert r["verdict"] == "FAIL"

    def test_exactly_at_the_bar_passes(self):
        rows = [row(0.00, 150)] + [row(0.10, 100)]
        r = evaluate(lines(rows))
        assert r["p2_rate_ratio"] == pytest.approx(1.50)
        assert r["p2_met"] is True

    def test_p3_can_fail_a_run_that_passes_p2(self):
        """A high ratio built on almost no opportunities in the calm half."""
        rows = [row(0.00, 16, obs=10)] + [row(0.10, 20, obs=1000)]
        r = evaluate(lines(rows))
        assert r["p2_met"] is True
        assert r["p3_low_share"] < MIN_LOW_SHARE
        assert r["verdict"] == "FAIL"
        assert "P3" in r["why"]


class TestExclusions:
    def test_unresolvable_drift_is_excluded_not_counted_as_calm(self):
        """The failure that would put every fresh market in the low half."""
        good = [row(0.10, 1) for _ in range(3)]
        bad = [row(None, 99, resolvable=False) for _ in range(3)]
        r = evaluate(lines(good + bad))
        assert r["windows_used"] == 3
        assert r["dropped"][DROP_DRIFT_UNRESOLVABLE] == 3
        assert r["opportunities"] == 3, "the 99s must not be counted anywhere"

    def test_unranked_rows_are_excluded(self):
        r = evaluate(lines([row(0.0, 5, selection="unranked"), row(0.0, 1)]))
        assert r["dropped"][DROP_NOT_FLOW_RANKED] == 1
        assert r["opportunities"] == 1

    def test_a_window_with_an_unresolvable_bucket_is_excluded(self):
        r = evaluate(lines([row(0.0, 5, unresolvable_buckets=["b_30_60s"]),
                            row(0.0, 1)]))
        assert r["dropped"][DROP_UNRESOLVABLE_BUCKET] == 1
        assert r["opportunities"] == 1

    def test_usable_names_its_reason(self):
        assert usable(row(0.0, 1))[0] is True
        assert usable(row(0.0, 1, selection="unranked"))[1] == DROP_NOT_FLOW_RANKED


class TestParsing:
    def test_malformed_and_unrelated_lines_are_ignored(self):
        assert len(parse(["hi", "PMUS_PERSISTENCE_WINDOW {bad",
                          'PMUS_COMPLETION_SHADOW {"kind":"heartbeat"}']
                         + lines([row(0.0, 1)]))) == 1

    def test_a_heartbeat_under_the_tag_is_not_a_window(self):
        assert parse(["PMUS_PERSISTENCE_WINDOW " +
                      json.dumps({"kind": "heartbeat"})]) == []
