"""The Sep 8 stop reconciliation and the kaique-annrog case study.

These tests pin the CONCLUSIONS, so a later edit that quietly reverses one
fails here rather than in a report nobody re-reads.
"""
from decimal import Decimal as D

from src import l5_daily_stop_forensics as F


# --- the stop was correct ---------------------------------------------------

def test_sep8_reconciles_to_the_number_that_fired_the_stop():
    assert F.sep8_total() == D("-18.5873")
    assert F.stop_was_correct() is True


def test_ten_rows_not_three():
    assert len(F.SEP8_REALIZED) == 10


def test_no_duplicate_market_in_the_days_realized_rows():
    slugs = [s for _, s, _ in F.SEP8_REALIZED]
    assert len(slugs) == len(set(slugs)), "a duplicate realized row would double-count"


def test_the_stop_was_not_armed_until_the_last_row():
    running = F.sep8_running()
    assert [fired for *_, fired in running[:-1]] == [False] * 9
    assert running[-1][3] is True
    assert running[-1][1] == F.CASE


def test_before_the_case_market_the_day_was_above_the_threshold():
    running = F.sep8_running()
    assert running[-2][2] == D("-13.2704")
    assert running[-2][2] > F.DAILY_LOSS_STOP_R5F


def test_the_verdict_survives_the_log_coverage_gaps():
    # hidden rows would have to be net POSITIVE by more than this to overturn
    assert F.margin_needed_to_overturn() == D("3.5873")
    positives = [D(r) for _, _, r in F.SEP8_REALIZED if D(r) > 0]
    assert sum(positives, D("0")) == D("1.4610") < F.margin_needed_to_overturn()


def test_coverage_gaps_are_declared_not_hidden():
    assert len(F.LOG_COVERAGE_GAPS_UTC) == 2


# --- the case: the model built the position, not the shell ------------------

def test_the_position_was_one_sided_and_never_paired():
    assert F.CASE_YES_UNITS == D("0")
    assert F.CASE_MATCHED_PAIRS == D("0")
    assert F.CASE_NO_UNITS == D("15.74")


def test_realized_loss_is_exactly_the_cost_basis():
    # the NO side expired worthless: the venue took the whole basis
    assert F.CASE_REALIZED_USD == -F.CASE_COST_BASIS_USD


def test_the_cap_bound_only_after_the_position_was_fully_built():
    """The load-bearing finding: the shell capped this position, it did not
    cause it. If this ever fails the causal story has changed."""
    assert F.residual_was_built_before_cap_bound() is True


def test_the_shell_recorded_its_own_override_on_both_conversions():
    for c in F.CAP_CONVERSIONS:
        assert c["sampled"] == "extend"
        assert c["acted"] == "complete"
        assert c["would_act_without_safety_cap"] == "extend"
        assert c["cap_binding"] == ("spend_cap",)


def test_the_model_favoured_extend_in_this_state():
    ranked = dict(F.CASE_RELATION_RANKED)
    assert ranked["extend"] > ranked["complete"]
    assert ranked["extend"] == D("0.6786")
    assert F.CASE_STATE_N == 298


def test_every_complement_attempt_failed_and_most_were_above_par():
    assert F.complete_fill_count() == 0
    assert len(F.COMPLETE_ATTEMPTS) == 4
    assert F.below_par_attempts() == 1
    assert F.complete_rest_seconds() > 100.0


def test_the_venue_rejected_non_crossing_orders_inside_this_loss():
    # issue #93, still open, observed here
    assert len(F.VENUE_REJECTED_NONCROSSING_ORDERS) == 3


def test_the_halt_closed_the_window_before_resolution():
    assert F.HALT["not_two_sided_utc"] > F.HALT["last_two_sided_utc"]
    assert F.HALT["halted_from_utc"] > F.HALT["not_two_sided_utc"]
    assert F.HALT["venue_resolution_utc"] > F.HALT["halted_until_utc"]


# --- the counterfactual: more capacity is monotonically worse here ---------

def test_more_capacity_buys_more_of_the_losing_side():
    losses = [F.counterfactual_loss(D(c))
              for c in ("7.50", "10.00", "12.50", "15.00")]
    assert losses == [D("-5.3169"), D("-7.8529"), D("-10.3889"), D("-12.9249")]
    assert losses == sorted(losses, reverse=True), "loss must worsen with cap"


def test_the_fill_rate_bracket_has_the_same_sign_and_slope():
    a = [F.counterfactual_loss(D(c), True) for c in ("10.00", "12.50", "15.00")]
    assert all(x < F.CASE_REALIZED_USD for x in a)
    assert a == sorted(a, reverse=True)


def test_the_current_cap_permits_no_further_extend():
    assert F.extends_permitted(D("7.50")) == 0


def test_the_taker_exit_is_labelled_unverifiable_and_not_proposed():
    assert F.TAKER_EXIT["verifiable"] is False
    assert "maker-only" in F.TAKER_EXIT["requires"]
    assert F.TAKER_EXIT["loss_usd"] > F.CASE_REALIZED_USD  # smaller loss


# --- the population ---------------------------------------------------------

def test_the_chain_is_most_of_the_loss_dollars_but_not_most_of_the_cause():
    assert F.chain_share_of_loss_dollars() > D("0.7")
    assert (F.CHAIN_CAP_BOUND_AFTER_RESIDUAL_BUILT
            > F.CHAIN_RESIDUAL_GREW_AFTER_CONVERSION)
    assert (F.CHAIN_CAP_BOUND_AFTER_RESIDUAL_BUILT
            + F.CHAIN_RESIDUAL_GREW_AFTER_CONVERSION) == F.CHAIN_MARKETS_N


def test_the_largest_loss_had_no_cap_involvement_at_all():
    slug, usd = F.LARGEST_LOSS_HAD_NO_CAP_CONVERSION
    assert usd < F.CASE_REALIZED_USD


def test_the_grow_side_fills_far_more_easily_than_the_close_side():
    assert F.fill_asymmetry() > D("2.0")


# --- this module authorises nothing ----------------------------------------

def test_module_recommends_but_cannot_authorise_a_restart():
    assert F.RECOMMENDATION == "DO_NOT_RESTART_YET"
    assert F.AUTHORISES_RESTART == ()
    assert F.RECOMMENDATION_IS_ADVISORY_ONLY is True


def test_cause_names_the_model_first_and_clears_the_shell():
    assert "model behaviour" in F.CAUSE
    assert "reduced the" in F.CAUSE
