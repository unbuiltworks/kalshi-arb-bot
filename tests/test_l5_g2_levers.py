"""The G2 lever ranking must not manufacture a result from hindsight.

Two failure modes are specifically guarded: scoring a filter that uses
information which did not exist when the order was placed, and reporting a
group mean that is really one market.
"""

import pytest

from src.l5_g2_levers import (CONCENTRATION_REFUSAL, EX_ANTE, EX_POST,
                              break_even_completion, by_group, completion_rate,
                              counterfactual, load_markets, loss_attribution,
                              outcome)


def mk(slug, net, pairs=1.0, pair_cost=0.95, residual=0.0, shape=None):
    return {"slug": slug, "net_usd": net, "pairs": pairs,
            "pair_cost": pair_cost, "residual_usd": residual, "shape": shape,
            "price_band": None, "category": None, "pair_usd": 0.0,
            "turnover_usd": 1.0, "cohort": "T", "source": "t"}


class TestOutcome:

    def test_zero_pairs_is_no_completion(self):
        assert outcome(mk("a", -1.0, pairs=0.0)) == "no_completion"

    def test_a_book_paired_above_par_is_not_a_success(self):
        """tomvoj-ticjir paired to zero at 1.1107 and lost $3.88.

        Removing the directional risk while locking a loss is not an
        acceptable completion, and counting it as one would make the metric
        reward exactly the behaviour that produced the worst R5F market.
        """
        assert outcome(mk("t", -3.88, pairs=4.11, pair_cost=1.1107)) == \
            "completed_above_par"

    def test_a_good_book_that_still_stranded_is_not_acceptable(self):
        """malden-stoole paired 18.9 at 0.9421 and still lost on residual."""
        assert outcome(mk("m", -0.41, pairs=18.9, pair_cost=0.9421,
                          residual=-1.51)) == "completed_with_residual"

    def test_a_clean_paired_book_is_acceptable(self):
        assert outcome(mk("g", 0.42, pairs=15.8, pair_cost=0.9708)) == \
            "acceptable_completion"

    def test_par_boundary_is_inclusive(self):
        assert outcome(mk("p", 0.0, pairs=1.0, pair_cost=1.0)) == \
            "acceptable_completion"


class TestLookAhead:
    """The property that keeps this from being a fitted story."""

    def test_an_ex_post_filter_is_refused_outright(self):
        ms = [mk("a", 1.0), mk("b", -5.0, pairs=0.0)]
        r = counterfactual(ms, "pairs", ["0.0"], EX_POST)
        assert r["refused"] is True
        assert "did not exist" in r["why"]
        assert "net_after_usd" not in r, \
            "a refused counterfactual must not report a number at all"

    def test_an_ex_ante_filter_is_exact_leave_out_arithmetic(self):
        ms = [mk("a", 1.0, shape="total"), mk("b", -5.0, shape="spread")]
        r = counterfactual(ms, "shape", ["spread"], EX_ANTE)
        assert r["net_before_usd"] == -4.0
        assert r["net_after_usd"] == 1.0
        assert r["delta_usd"] == 5.0
        assert r["markets_removed"] == 1

    def test_a_filter_reports_the_winners_it_would_also_have_removed(self):
        """Dropping a shape drops its good markets too. Say so.

        The two spread markets are wisc-nd (-6.23) and lou-miss (+1.19); a
        spread filter removes one of our best markets, and a counterfactual
        that hid that would oversell the filter.
        """
        ms = [mk("wisc", -6.23, pairs=0.0, shape="spread"),
              mk("lou", 1.19, shape="spread"), mk("k", 0.5, shape="total")]
        r = counterfactual(ms, "shape", ["spread"], EX_ANTE)
        assert r["also_removed_winners"] == ["lou"]


class TestConcentration:

    def test_a_group_whose_loss_is_one_market_is_not_rankable(self):
        """The spread cell: -$5.03 across 2 markets, -$6.23 of it one market."""
        ms = [mk("wisc", -6.23, pairs=0.0, shape="spread"),
              mk("lou", 1.19, shape="spread")]
        g = by_group(ms, "shape")["spread"]
        assert g["rankable"] is False
        assert g["worst_share_of_group_loss"] > CONCENTRATION_REFUSAL

    def test_a_diffuse_group_with_enough_markets_is_rankable(self):
        ms = [mk("m%d" % i, -1.0, shape="s") for i in range(6)]
        g = by_group(ms, "shape")["s"]
        assert g["rankable"] is True
        assert g["why_not"] is None

    def test_a_small_group_is_refused_even_when_diffuse(self):
        ms = [mk("m%d" % i, -1.0, shape="s") for i in range(3)]
        assert by_group(ms, "shape")["s"]["rankable"] is False
        assert "only 3 markets" in by_group(ms, "shape")["s"]["why_not"]


class TestBreakEven:

    def test_the_bar_is_where_the_two_means_balance(self):
        ms = [mk("w%d" % i, 1.0) for i in range(3)] + \
             [mk("l%d" % i, -1.0, pairs=0.0) for i in range(3)]
        b = break_even_completion(ms)
        assert b["p_break_even"] == pytest.approx(0.5, abs=1e-6)
        assert b["p_now"] == pytest.approx(0.5, abs=1e-6)
        assert b["gap_points"] == pytest.approx(0.0, abs=1e-6)

    def test_a_bigger_average_loss_raises_the_bar(self):
        cheap = [mk("w", 1.0), mk("l", -1.0, pairs=0.0)]
        dear = [mk("w", 1.0), mk("l", -9.0, pairs=0.0)]
        assert break_even_completion(dear)["p_break_even"] > \
            break_even_completion(cheap)["p_break_even"]

    def test_it_refuses_when_one_outcome_is_absent(self):
        assert break_even_completion([mk("w", 1.0)])["estimable"] is False

    def test_it_carries_the_concentration_caveat_and_the_worst_market(self):
        ms = [mk("w", 1.0), mk("l", -6.2255, pairs=0.0), mk("l2", -0.1, pairs=0.0)]
        b = break_even_completion(ms)
        assert b["worst_single_loss_usd"] == -6.2255
        assert b["worst_share_of_all_losses"] > 0.9
        assert "dominated by one market" in b["caveat"]


class TestRealRecord:
    """Against the actual settled ledger, not fixtures."""

    def test_every_acceptable_completion_in_the_record_made_money(self):
        ms = load_markets(".")
        if not ms:
            pytest.skip("analysis artifacts not present")
        good = [m for m in ms if outcome(m) == "acceptable_completion"]
        assert good, "the record must contain acceptable completions"
        assert all(m["net_usd"] > 0 for m in good), \
            "an acceptable completion that lost money would break the metric"

    def test_the_record_loads_both_cohorts(self):
        ms = load_markets(".")
        if not ms:
            pytest.skip("analysis artifacts not present")
        assert {m["source"] for m in ms} == {"scoreboard", "r5f"}

    def test_no_completion_dominates_the_losses(self):
        ms = load_markets(".")
        if not ms:
            pytest.skip("analysis artifacts not present")
        a = loss_attribution(ms)
        assert a["share_from_no_completion"] > 0.5, \
            "if this stops being true the ranking has to be redone"

    def test_the_completion_rate_is_below_its_break_even(self):
        ms = load_markets(".")
        if not ms:
            pytest.skip("analysis artifacts not present")
        c = completion_rate(ms)
        b = break_even_completion(ms)
        assert c["p_acceptable_completion_given_first_leg"] < b["p_break_even"], \
            "the campaign is not yet at break-even; if it is, say so loudly"
