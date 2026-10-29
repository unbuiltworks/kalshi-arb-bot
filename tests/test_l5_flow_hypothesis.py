"""Tests for the flow-hypothesis analysis.

The properties worth protecting are the ones that would let a weak measure
pass as a strong one, or let the forward gate move after seeing its data.
"""

from src.l5_flow_hypothesis import (FORWARD_GATE, build, cohort_lifetime_stats,
                                    lifetime_separates, quintile_monotonicity,
                                    render_md, slate_coverage)


# ------------------------------ retro: lifetime volume ------------------------------

def test_separability_is_decided_by_the_extremes_not_the_medians():
    """A rule is judged by what it keeps and drops, not by group centres.

    Here the medians differ but the ranges overlap, so no threshold works.
    Goes red if the check is ever rewritten to compare medians.
    """
    stats = cohort_lifetime_stats({
        "FULL": [[25_034, "quiet_but_paired"], [900_000, "loud"]],
        "ZERO": [[10_672, "quietest"], [511_234, "loud_but_stranded"]]})
    out = lifetime_separates(stats)
    assert out["separable"] is False
    assert out["quietest_completed"]["slug"] == "quiet_but_paired"
    assert out["loudest_stranded"]["slug"] == "loud_but_stranded"


def test_separability_is_reported_when_a_threshold_really_does_exist():
    """Companion: the check must be able to say YES, or it proves nothing."""
    stats = cohort_lifetime_stats({"FULL": [[900_000, "a"], [800_000, "b"]],
                                   "ZERO": [[10, "c"], [20, "d"]]})
    assert lifetime_separates(stats)["separable"] is True


def test_separability_refuses_to_answer_on_an_empty_cohort():
    stats = cohort_lifetime_stats({"FULL": [[1, "a"]], "ZERO": []})
    assert lifetime_separates(stats)["separable"] is None


def test_cohort_stats_carry_the_slug_of_each_extreme():
    s = cohort_lifetime_stats({"X": [[5, "lo"], [7, "mid"], [9, "hi"]]})["X"]
    assert (s["min_slug"], s["max_slug"], s["median"], s["n"]) == ("lo", "hi", 7, 3)


# ------------------------------ forward: monotonicity ------------------------------

def test_monotonicity_detects_a_rising_series():
    out = quintile_monotonicity([{"two_sided_share": v}
                                 for v in (0.0, 0.0, 0.0, 0.667, 1.0)])
    assert out["non_decreasing"] is True
    assert out["bottom"] == 0.0 and out["top"] == 1.0


def test_monotonicity_reports_a_broken_series_as_broken():
    """Goes red if the check ever just compares first against last."""
    out = quintile_monotonicity([{"two_sided_share": v}
                                 for v in (0.0, 0.9, 0.1, 0.2, 1.0)])
    assert out["non_decreasing"] is False
    assert out["bottom"] == 0.0 and out["top"] == 1.0


# ------------------------------ coverage: absence vs silence ------------------------------

def test_under_observed_is_not_the_same_as_silent():
    """"We could not see it" and "it did not trade" are different claims."""
    cov = slate_coverage([
        {"prints_seen": 5}, {"prints_seen": 0},
        {"under_observed": True, "prints_seen": 0},
        {"under_observed": True, "prints_seen": 0}])
    assert cov["polled"] == 4
    assert cov["measurable"] == 2
    assert cov["under_observed"] == 2
    assert cov["measured_but_silent"] == 1
    assert cov["share_with_any_flow"] == 0.25


def test_a_slate_where_nothing_trades_reports_zero_not_none():
    cov = slate_coverage([{"prints_seen": 0}, {"prints_seen": 0}])
    assert cov["share_with_any_flow"] == 0.0


# ------------------------------ the pre-registered gate ------------------------------

def test_forward_gate_is_complete_before_any_forward_data_exists():
    """All three outcomes and the baseline must be fixed in advance, so the
    result cannot pick its own success criterion afterwards."""
    for k in ("pass", "fail", "inconclusive", "cohort_rule", "baseline"):
        assert FORWARD_GATE[k], k
    assert FORWARD_GATE["registered_before_data"] is True
    b = FORWARD_GATE["baseline"]
    assert b["stranded"] == 7 and b["markets_with_a_first_side_fill"] == 31


def test_forward_gate_names_the_confound_it_cannot_remove():
    """The entry-band fix landed between baseline and cohort; a forward run
    measures selection AND that fix together. Saying so is the whole point."""
    c = FORWARD_GATE["confound_named_in_advance"]
    assert "a9b0b243" in c and "control" in c


def test_forward_gate_does_not_authorise_scaling():
    assert "not authority" in FORWARD_GATE["not_a_licence"] or \
        "not authority to" in FORWARD_GATE["not_a_licence"]


# ------------------------------ end to end ------------------------------

def test_build_and_render_produce_both_tests_without_conflating_them():
    rep = build(
        {"FULL": [[25_034, "a"], [900_000, "b"]], "ZERO": [[511_234, "c"]],
         "PARTIAL": [[98_471, "d"]]},
        [{"slug": "x", "prints_seen": 3}, {"under_observed": True, "prints_seen": 0}],
        [{"decile": 1, "markets": 2, "min": 0.0, "max": 0.0, "two_sided_share": 0.0},
         {"decile": 2, "markets": 2, "min": 1.0, "max": 9.0, "two_sided_share": 1.0}],
        cycles=41)
    assert rep["retro_lifetime"]["separable"]["separable"] is False
    assert rep["forward_contemporaneous"]["monotonicity"]["non_decreasing"] is True
    md = render_md(rep)
    # the two currencies stay labelled and separate in the output
    assert "Test 1 (retro): lifetime volume" in md
    assert "Test 2 (forward): contemporaneous flow" in md
    assert "FORWARD test only" in md
    assert "registered before the cohort runs" in md
