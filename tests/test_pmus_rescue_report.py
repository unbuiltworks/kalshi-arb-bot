"""The frozen R1/R2/R3 bars for the residual-rescue band."""
import pytest

from src import pmus_rescue_report as rr


def bucket(obs=100, capped=0, rescue=None, only=None):
    only = 0 if only is None else only
    rescue = capped + only if rescue is None else rescue
    return {"observations": obs, "capped_opportunities": capped,
            "rescue_opportunities": rescue,
            "rescue_only_opportunities": only}


def win(**kw):
    return {"buckets": {"a_0_30s": bucket(**kw)}}


def test_constants_match_the_prereg_file():
    """The rule and the document cannot drift apart silently."""
    txt = open("analysis/l5/PREREG_RESIDUAL_RESCUE_BAND.md").read()
    assert "30 capped opportunities" in txt
    assert "1.20" in txt and "**10**" in txt
    assert rr.MIN_CAPPED_OPPORTUNITIES == 30
    assert rr.MIN_LIFT == 1.20
    assert rr.MIN_RESCUE_ONLY == 10


def test_underpowered_computes_no_lift_at_all():
    res = rr.evaluate([win(capped=5, only=50)])
    assert res["verdict"] == "UNDERPOWERED"
    assert res["deficit"] == 25
    assert "lift" not in res          # not computed, not merely hidden


def test_zero_band_opportunities_fails_and_never_passes():
    """A band that never opens is inert, however large the sample."""
    res = rr.evaluate([win(capped=500, only=0)])
    assert res["lift"] == 1.0
    assert res["verdict"] == "FAIL"
    assert res["r2_ci_clears_threshold"] is False


def test_a_thin_sample_that_clears_the_point_bar_still_fails_on_its_interval():
    """The clause pre-entry drift lacked, stated so it cannot go inert.

    lift 1.50 clears the 1.20 point bar, but on 30 vs 15 events the interval
    reaches below 1.20, so the sample cannot tell "meets the bar" from "falls
    short". That must FAIL. Deleting the interval clause has to turn this red.
    """
    res = rr.evaluate([win(capped=50, only=12)])
    assert res["lift"] == pytest.approx(1.24, abs=0.001)   # clears 1.20
    assert res["r2_point_met"] is True
    assert res["r3_met"] is True                            # and the band is populated
    assert res["lift_ci95"][0] < rr.MIN_LIFT                # but the interval is not
    assert res["r2_ci_clears_threshold"] is False
    assert res["verdict"] == "FAIL"
    assert "cannot distinguish" in res["why"]


def test_the_same_effect_passes_once_the_sample_is_big_enough():
    """Same 1.24 lift, four times the sample: now the interval supports it.

    This is the pair that proves the clause is about PRECISION, not about
    disliking the effect size."""
    res = rr.evaluate([win(capped=600, only=144)])
    assert res["lift"] == pytest.approx(1.24, abs=0.001)
    assert res["lift_ci95"][0] > rr.MIN_LIFT
    assert res["verdict"] == "PASS"


def test_clearing_1_00_is_not_enough_because_nesting_makes_it_automatic():
    """Any single band-only opportunity puts the lower bound above 1.00, so
    a guard written against 1.00 would be inert. Pin the real threshold."""
    res = rr.evaluate([win(capped=40, only=1)])
    assert res["lift_ci95"][0] > 1.0          # automatic, proves nothing
    assert res["verdict"] == "FAIL"           # and it must still fail


def test_a_real_effect_passes_all_three():
    res = rr.evaluate([win(capped=100, only=60)])
    assert res["verdict"] == "PASS"
    assert res["lift"] == pytest.approx(1.6, abs=0.001)
    assert res["lift_ci95"][0] > 1.0
    assert "does not authorise" in res["why"]


def test_r3_flags_a_band_that_barely_opens():
    """R3 is belt-and-braces: with R1 at 30, R2's interval clause almost
    always bites first. Pin the flag itself so the bar cannot silently drop."""
    res = rr.evaluate([win(capped=30, only=9)])
    assert res["r3_met"] is False
    assert res["verdict"] == "FAIL"
    thin = rr.evaluate([win(capped=600, only=144)])
    assert thin["r3_met"] is True


def test_nesting_violation_is_a_data_defect_not_a_result():
    bad = {"buckets": {"a": {"observations": 10, "capped_opportunities": 5,
                             "rescue_opportunities": 3,
                             "rescue_only_opportunities": 0}}}
    res = rr.evaluate([bad])
    assert res["verdict"] == "DATA_DEFECT"
    assert "lift" not in res


def test_rows_predating_the_instrument_are_dropped_and_counted():
    old = {"buckets": {"a": {"observations": 50, "opportunities": 3}}}
    res = rr.evaluate([old, win(capped=40, only=20)])
    assert res["dropped_no_rescue_field"] == 1
    assert res["windows_used"] == 1


def test_every_result_carries_the_upper_bound_warning():
    for w in ([win(capped=5)], [win(capped=100, only=60)]):
        assert "not a fill" in rr.evaluate(w)["upper_bound_not_a_fill"]
