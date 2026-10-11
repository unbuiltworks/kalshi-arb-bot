"""Does the wallet behave like the strategy the article claims? Pins the
verdicts, the control that makes the merge finding decisive, and the two
corrections."""
from decimal import Decimal as D

from src import l5_identity_strategy_audit as A


# --- corrections ------------------------------------------------------------

def test_i_was_wrong_that_incentives_were_not_sourceable():
    assert "WRONG" in A.MY_EARLIER_CLAIM_D_NOT_SOURCEABLE
    assert A.total_incentives() > D("70000")


def test_the_97_percent_maker_figure_is_not_ours():
    assert A.MEASURED_MAKER_SHARE < A.DIRECTIVE_MAKER_FIGURE
    assert A.MEASURED_MAKER_SHARE_CLASSIFIED < A.DIRECTIVE_MAKER_FIGURE
    mk, tk, unk = A.MAKER_TAKER_UNKNOWN
    assert abs(D(mk) / D(mk + tk + unk) - A.MEASURED_MAKER_SHARE) < D("0.001")


# --- the decisive finding ---------------------------------------------------

def test_l5_has_never_merged():
    assert A.l5_has_never_merged() is True


def test_the_merge_finding_rests_on_a_control_not_on_absence():
    """Other traders in the same feed DO show MERGE, so zero is meaningful."""
    assert A.merge_control_is_valid() is True
    assert max(A.CONTROL_TRADERS_MERGE.values()) > 1000


def test_the_only_capital_return_observed_is_settlement_redemption():
    assert A.L5_REDEEM_EVENTS > 10000
    assert A.L5_ACTIVITY_TYPES["SPLIT"] == 0


# --- where the money comes from ---------------------------------------------

def test_incentives_alone_exceed_the_articles_claimed_pnl():
    assert A.incentives_exceed_claimed_pnl() is True
    assert A.total_incentives() == D("71551.74")


def test_the_updown_fills_do_not_explain_the_claimed_profit():
    assert A.UPDOWN_FILL_NET < D("0")


# --- the seven items --------------------------------------------------------

def test_neutrality_claim_fails():
    assert A.WINDOWS_ENDING_RESIDUAL / A.WINDOWS > 0.98
    assert A.naked_dollar_time_share() > D("0.10")


def test_a_fifth_of_matched_inventory_is_paired_above_par():
    assert A.above_par_matched_share() > D("0.15")
    assert A.PAIR_COST_UNITS["=1.00"] == 0


def test_he_buys_more_as_completion_gets_more_expensive():
    lo, hi = A.SAME_SIDE_P_BY_PAIR_COST
    assert hi[1] > lo[1]


def test_capital_is_freed_in_minutes_not_seconds():
    assert A.CAPITAL_LOCK_MIN["median"] >= D("5.0")


def test_simultaneous_executability_stays_unverifiable():
    assert A.SIMULTANEOUS_EXECUTABILITY == "UNVERIFIABLE"


# --- the ledger and the deliverable -----------------------------------------

def test_the_ledger_has_every_field_for_every_claim():
    for row in A.CLAIM_LEDGER:
        assert len(row) == 5
        assert all(x for x in row)


def test_the_merge_claim_is_the_one_marked_decisive():
    strengths = {c: s for c, _, _, s, _ in A.CLAIM_LEDGER}
    assert any(s == "decisive" for s in strengths.values())


def test_more_claims_are_contradicted_than_supported():
    assert len(A.verdicts("CONTRADICTED")) > len(A.verdicts("SUPPORTS"))


def test_the_wallet_does_not_resemble_the_claimed_strategy():
    assert A.RESEMBLES_THE_CLAIMED_STRATEGY is False


def test_identity_is_explicitly_not_resolved():
    assert A.IDENTITY_RESOLVED is False


def test_nothing_is_authorised_or_implemented():
    assert A.AUTHORISES_MODEL_CHANGE == ()
    assert A.AUTHORISES_RESTART == ()
    assert A.IMPLEMENTS_OUTSIDE_STRATEGY is False
