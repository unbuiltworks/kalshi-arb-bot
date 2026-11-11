"""L5's adverse-sequence behaviour, the state aliasing, and the corrected
fill decomposition. These pin conclusions that a later edit must not
silently reverse."""
from decimal import Decimal as D

from src import l5_adverse_extend_sequences as A


# --- the corrections --------------------------------------------------------

def test_the_superseded_asymmetry_is_kept_with_its_defect_named():
    assert "selected on the outcome" in SUPERSEDED_DEFECT()
    assert A.SUPERSEDED_ASYMMETRY["grow"] == D("0.238")


def SUPERSEDED_DEFECT():
    return A.SUPERSEDED_ASYMMETRY["defect"]


def test_on_the_full_harvest_the_ordering_reverses():
    grow = A.rate(A.RESTED_GROW)
    close = A.rate(A.RESTED_CLOSE)
    assert close > grow, "the close side fills BETTER once selection is removed"
    assert A.ALL_ORDERS_N == 2169


def test_extend_fills_better_than_open():
    assert A.rate(A.BY_RELATION["extend"]) > A.rate(A.BY_RELATION["open"])


# --- 1. L5 under adverse accumulation ---------------------------------------

def test_l5_extends_more_not_less_when_the_market_moves_against_him():
    """The premise under test. L5's own history rejects it."""
    assert A.l5_extends_more_under_adverse_movement() is True
    assert A.L5_ADVERSE["extend"] == D("0.725")
    assert A.L5_FAVORABLE["extend"] == D("0.594")


def test_persistence_escalates_with_consecutive_same_side_fills():
    assert A.l5_persistence_escalates_with_run_length() is True
    assert A.L5_BY_RUN_LENGTH[0][2] == D("0.629")
    assert A.L5_BY_RUN_LENGTH[-1][2] == D("0.844")


def test_the_imbalance_ratio_is_the_variable_that_carries_the_decay():
    assert A.l5_persistence_decays_with_imbalance_ratio() is True


def test_failed_complement_attempts_are_declared_unmeasurable():
    assert "failed complement attempts" in A.UNMEASURABLE_ON_L5


def test_the_crypto_to_sports_transfer_limit_is_stated():
    assert "sports" in A.TRANSFER_LIMIT and "magnitudes" in A.TRANSFER_LIMIT


# --- 2. the case vs the analogue -------------------------------------------

def test_our_model_was_less_persistent_than_l5_not_more():
    assert A.CASE_MODEL_EXTEND_P < A.L5_ANALOGUE_EXTEND_P


# --- 3. the aliasing --------------------------------------------------------

def test_imbalance_buckets_are_absolute_contracts_not_a_ratio():
    from src.polymarket_l5.action_model import IMBALANCE_BUCKETS
    edges = [hi for hi, _ in IMBALANCE_BUCKETS]
    assert max(e for e in edges if e < 1e11) > 1.0, \
        "a ratio feature would have all edges <= 1.0; these are contract counts"
    assert A.IMBALANCE_BUCKETS_ARE_ABSOLUTE_CONTRACTS is True


def test_the_frozen_bucket_hides_a_real_extend_difference():
    assert A.ALIASING_MEAN_GAP >= D("0.10")
    assert A.ALIASING_MAX_GAP >= D("0.35")
    assert A.ALIASING_BUCKETS_TESTED >= 30


def test_de_aliasing_would_RAISE_extend_in_the_cases_substate():
    """Guards against anyone adopting this as a loss-reduction measure."""
    lo = A.TARGET_BY_RATIO[0][2]
    hi = A.TARGET_BY_RATIO[-1][2]
    assert hi > lo
    assert A.CORRECTION_WOULD_HAVE_PREVENTED_THE_LOSS is False
    assert A.CORRECTION_IS_FIDELITY_NOT_PROFIT is True


# --- 4. the fill decomposition ---------------------------------------------

def test_grow_and_close_barely_share_a_price_range():
    assert A.GROW_PRICE_IQR[1] < A.CLOSE_PRICE_IQR[0], \
        "no common support: a pooled fill comparison is not identified"


def test_at_equal_price_the_close_side_does_not_fill_worse():
    assert A.close_side_fills_at_least_as_well_at_equal_price() is True


def test_market_structure_is_named_dominant_and_aliasing_is_excluded():
    assert A.ATTRIBUTION["pmus_sports_market_structure"] == "DOMINANT"
    assert A.ATTRIBUTION["state_model_aliasing"].startswith("NOT THIS")
    assert "NOT TESTABLE" in A.ATTRIBUTION["expected_l5_behaviour"]


# --- 5. #93 -----------------------------------------------------------------

def test_accepting_the_rejected_extends_would_have_cost_more():
    assert A.accepting_rejects_would_have_cost() > D("0")
    assert A.rejected_extend_notional() > D("4.0")


def test_extend_is_the_most_rejected_relation():
    assert A.extend_is_the_most_rejected_relation() is True
    assert A.rejection_rate("extend") > D("0.35")


# --- the deliverable --------------------------------------------------------

def test_the_correction_is_shadow_only_and_authorises_nothing():
    assert A.AUTHORISES_MODEL_CHANGE == ()
    assert "ACT ON THE FROZEN" in A.BOUNDED_TEST
    assert A.NOT_A_STOP_LOSS is True


def test_the_correction_is_the_imbalance_ratio():
    assert A.CORRECTION == "add_imbalance_ratio_to_state_representation"
