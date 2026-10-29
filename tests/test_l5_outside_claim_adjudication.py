"""Outside 'I figured out L5' claims are hypotheses; the tape adjudicates.
These pin the discipline as much as the verdicts."""
from decimal import Decimal as D

from src import l5_outside_claim_adjudication as A


# --- discipline -------------------------------------------------------------

def test_the_article_is_not_evidence_and_nothing_is_implemented():
    assert A.SOURCE_IS_EVIDENCE is False
    assert A.IMPLEMENTS_OUTSIDE_STRATEGY is False
    assert A.CHANGES_ANYTHING_OPERATIONAL is False
    assert A.AUTHORISES_MODEL_CHANGE == ()
    assert A.AUTHORISES_RESTART == ()


def test_identity_is_self_asserted_not_established():
    assert A.SOURCE_SELF_ASSERTS_WALLET is True
    assert A.IDENTITY_ESTABLISHED is False


def test_promotional_provenance_is_recorded():
    assert A.SOURCE_IS_PROMOTIONAL is True


# --- scope honesty ----------------------------------------------------------

def test_our_evidence_is_declared_a_subset():
    assert A.TAPE_IS_A_SUBSET_OF_THE_WALLET is True
    assert all("updown" in s for s in A.TAPE_MARKET_SHAPES)


def test_our_subset_net_is_not_offered_as_a_pnl_rebuttal():
    assert A.net_on_our_subset() == A.OUR_SUBSET_NET
    assert A.VERDICTS["7_66k_pnl"] == "OUT OF SCOPE"
    assert "66k" in A.DO_NOT_CITE_AS_REBUTTAL_OF


# --- the verdicts that rest on measurements ---------------------------------

def test_simultaneous_arb_is_contradicted_by_leg_timing():
    assert A.VERDICTS["1_simultaneous_pair_arb"] == "CONTRADICTED"
    assert A.MEDIAN_LEG_GAP_S > D("30")
    assert A.SAME_SECOND_PAIR_SHARE < D("0.05")
    assert A.MOVEMENT_DOMINATED_SHARE > D("0.5")


def test_the_no_naked_legs_claim_is_contradicted():
    assert A.VERDICTS["2_never_hold_naked_legs"] == "CONTRADICTED"
    assert A.windows_ending_flat_share() < D("0.02")
    assert A.RESIDUAL_CONTRIBUTION < D("0")


def test_millisecond_latency_is_not_supported_at_a_47_second_timescale():
    assert A.VERDICTS["3_millisecond_latency_decisive"] == "NOT SUPPORTED"


def test_the_maker_preference_claim_is_the_one_that_holds():
    assert A.VERDICTS["4_gtc_maker_preference"] == "CONSISTENT"
    assert A.MAKER_SHARE > D("0.7")
    assert "4_gtc_maker_preference" in A.supported_claims()


def test_the_document_contradicts_itself_on_ask_vs_resting():
    assert A.VERDICTS["5_buys_at_the_ask"] == "INTERNALLY INCONSISTENT"


def test_merge_is_untestable_and_economically_neutral():
    assert A.VERDICTS["6_immediate_merge"] == "UNTESTABLE"


def test_untested_claims_stay_untested():
    assert A.VERDICTS["8_combinatorial_arb"] == "UNTESTED"


def test_three_load_bearing_claims_fail():
    assert len(A.contradicted_claims()) >= 3


def test_the_ingestion_gap_is_written_down():
    assert "combinatorial" in A.OPEN_INGESTION_GAP
