"""The imbalance-ratio augmentation against its pre-registered bar, its
exposure effect, and L5's own fill economics."""
from decimal import Decimal as D

from src import l5_imbalance_ratio_bar as BAR
from src import l5_imbalance_ratio_eval as E


# --- the bar was frozen first and is not moved ------------------------------

def test_the_bar_predates_and_authorises_nothing():
    assert BAR.AUTHORISES_SHIPPING == ()
    assert BAR.AUTHORISES_MODEL_CHANGE == ()
    assert BAR.BAR_FROZEN_UTC.startswith("2026-09-08")


def test_the_bar_still_demands_four_conditions():
    assert BAR.MIN_LOGLOSS_GAIN == D("0.01")
    assert BAR.MIN_HIGH_IMB_GAIN == D("0.01")
    assert BAR.MAX_EXTRA_BACKOFF == D("0.05")
    assert BAR.MIN_IMPROVED_STRATA == 3


def test_on_fail_forbids_retuning_and_rerunning():
    assert "do not retune" in BAR.ON_FAIL


# --- the result -------------------------------------------------------------

def test_the_augmentation_fails():
    assert E.passes_bar() is False
    assert E.VERDICT == "FAIL"


def test_it_is_worse_overall_out_of_sample():
    assert E.gain("all") < D("0")
    assert E.gain("post_fill") < D("0")


def test_the_high_imbalance_win_is_one_bucket_not_a_regime():
    assert E.improvement_is_one_bucket_not_high_imbalance() is True
    assert E.STRATA_IMPROVED == 1 < BAR.MIN_IMPROVED_STRATA


def test_only_r95_100_improves():
    improving = [k for k, _, g in E.BY_STRATUM if g > 0]
    assert improving == ["r95-100"]


# --- exposure ---------------------------------------------------------------

def test_the_augmentation_would_have_GROWN_positions_not_shrunk_them():
    """Pins the direction so this cannot be sold as a risk control."""
    assert E.augmentation_would_increase_exposure() is True
    assert E.MEAN_EXTEND_CHANGE > D("0.03")


def test_the_growth_is_concentrated_in_the_fully_one_sided_stratum():
    row = [r for r in E.EXPOSURE if r[0] == "r95-100"][0]
    _, _, frozen_p, aug_p = row
    assert aug_p - frozen_p > D("0.15")


def test_our_lane_is_far_more_one_sided_than_l5s():
    assert E.our_one_sided_share_vs_l5() > D("4.0")
    assert E.OUR_RATIO_MIX["r75-95"] == D("0.000")


# --- L5's economics ---------------------------------------------------------

def test_l5s_public_fills_do_not_net_positive():
    assert E.l5_net_on_public_fills() == D("-1050.57")
    assert E.l5_edge_is_not_in_the_fills() is True


def test_l5_pair_profit_is_cancelled_by_residual_loss():
    assert E.L5_PAIR_CONTRIBUTION > D("18000")
    assert E.L5_RESIDUAL_CONTRIBUTION < D("-19000")


def test_the_unknowns_are_listed_not_assumed_away():
    assert len(E.L5_UNKNOWNS) >= 4
    assert any("reward" in u for u in E.L5_UNKNOWNS)


def test_our_residual_unit_cost_is_several_times_l5s():
    assert E.our_residual_cost_ratio_vs_l5() > D("3.0")


def test_l5_almost_never_ends_flat():
    flat, total = E.L5_FLAT_AT_CLOSE
    assert flat / total < 0.02


# --- book capture -----------------------------------------------------------

def test_book_capture_has_no_coverage_and_says_so():
    assert E.BOOK_CAPTURE_ROWS == 0
    assert E.RECORDER_BOOK_ROWS_OTHER_POPULATIONS > 0, \
        "other populations exist and must not be pooled with the new one"


# --- the recommendation -----------------------------------------------------

def test_it_authorises_nothing():
    assert E.AUTHORISES_MODEL_CHANGE == ()
    assert E.AUTHORISES_RESTART == ()
    assert E.RECOMMENDATION == "DO_NOT_PROMOTE_TO_A_LIVE_MODEL_TREATMENT"
