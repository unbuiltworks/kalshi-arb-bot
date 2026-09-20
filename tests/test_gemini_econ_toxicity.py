"""TOXICITY_V1: side-specific admission, causal features, walk-forward only."""
import pytest

from src.gemini_execution.econ import prefill as PF
from src.gemini_execution.econ import toxicity as TX


# ------------------------------------------------------------- the ring

def _ring(n=12, spot_step=30.0, no_step=-0.004):
    r = PF.PreFillRing()
    for i in range(n):
        r.observe("S", PF.Sample(
            t=float(i * 8), tte_s=300 - i * 8,
            yes_bid=0.30 + i * 0.005, yes_ask=0.55,
            no_bid=0.33 + i * no_step, no_ask=0.58,
            spot=100000.0 + i * spot_step, strike=100000.0, n_prints=i))
    return r


def test_the_lookback_is_causal_and_reports_the_age_it_actually_used():
    """An 8s cadence cannot serve a true t-1s sample. Saying so is the point."""
    f = _ring().features_before("S", t=88.0, leg="NO")
    for k in ("t-60s", "t-30s", "t-10s", "t-1s"):
        assert f["offsets"][k]["available"] is True
        assert f["offsets"][k]["sample_age_error_s"] is not None
    # every sample used is at or BEFORE the fill instant
    for k, v in f["offsets"].items():
        if v.get("available"):
            assert v["t"] <= 88.0


def test_a_missing_history_is_unavailable_not_a_nearest_guess():
    r = PF.PreFillRing()
    r.observe("S", PF.Sample(t=0.0, tte_s=300, yes_bid=0.3, no_bid=0.33))
    f = r.features_before("S", t=500.0, leg="NO")
    assert f["offsets"]["t-60s"]["available"] is False
    assert "inside tolerance" in f["offsets"]["t-60s"]["why"]


def test_the_held_leg_is_named_so_yes_and_no_rows_read_alike():
    y = _ring().features_before("S", t=88.0, leg="YES")["offsets"]["t-0s"]
    n = _ring().features_before("S", t=88.0, leg="NO")["offsets"]["t-0s"]
    assert y["held_bid"] == y["yes_bid"] and y["complement_bid"] == y["no_bid"]
    assert n["held_bid"] == n["no_bid"] and n["complement_bid"] == n["yes_bid"]


def test_distance_from_strike_is_signed_and_relative():
    f = _ring().features_before("S", t=88.0, leg="NO")
    assert f["distance_from_strike"] > 0          # spot rose above strike


def test_velocity_is_none_without_spot_rather_than_zero():
    r = PF.PreFillRing()
    for i in range(10):
        r.observe("S", PF.Sample(t=float(i * 8), tte_s=300, yes_bid=0.3,
                                 no_bid=0.33))
    assert r.velocity("S", t=72.0, over_s=60.0) is None


# ----------------------------------------------------------- the rule

TOXIC_NO = {"leg": "NO", "held_bid_drift_60s": -0.03,
            "spot_velocity_60s": 5e-5,
            "offsets": {"t-0s": {"available": True, "yes_bid": 0.62,
                                 "no_bid": 0.30},
                        "t-60s": {"available": True, "yes_bid": 0.55,
                                  "no_bid": 0.33}}}
CALM = {"leg": "NO", "held_bid_drift_60s": 0.001, "spot_velocity_60s": 1e-7,
        "offsets": {"t-0s": {"available": True, "yes_bid": 0.60,
                             "no_bid": 0.33},
                    "t-60s": {"available": True, "yes_bid": 0.60,
                              "no_bid": 0.33}}}


def test_the_rule_is_asymmetric_and_blocks_only_the_adverse_side():
    """Blocking NO while allowing YES is the EXPECTED output, not an edge
    case. This is the whole point of a side-specific rule."""
    d = TX.ToxicityV1().decide(TOXIC_NO)
    assert d["verdict"] == TX.ALLOW_YES
    assert d["NO"]["adverse"] is True and d["YES"]["adverse"] is False
    assert TX.ToxicityV1().allows(TOXIC_NO, "YES") is True
    assert TX.ToxicityV1().allows(TOXIC_NO, "NO") is False


def test_a_calm_book_allows_both():
    assert TX.ToxicityV1().decide(CALM)["verdict"] == TX.ALLOW_BOTH


def test_both_sides_adverse_allows_neither():
    both = dict(TOXIC_NO)
    both = {**both, "offsets": {
        "t-0s": {"available": True, "yes_bid": 0.20, "no_bid": 0.20},
        "t-60s": {"available": True, "yes_bid": 0.55, "no_bid": 0.33}}}
    assert TX.ToxicityV1(velocity_threshold=1e9).decide(
        both)["verdict"] == TX.ALLOW_NEITHER


def test_the_rule_does_not_forecast_btc_only_reads_repricing():
    """Spot is a SIGN test against a threshold, never a magnitude forecast."""
    slow = {**TOXIC_NO, "spot_velocity_60s": 1e-9, "held_bid_drift_60s": 0.0,
            "offsets": {"t-0s": {"available": True, "yes_bid": 0.55,
                                 "no_bid": 0.33},
                        "t-60s": {"available": True, "yes_bid": 0.55,
                                  "no_bid": 0.33}}}
    assert TX.ToxicityV1().decide(slow)["verdict"] == TX.ALLOW_BOTH


def test_the_rule_works_without_spot_at_all():
    """Own-bid drift needs no strike, no spot and no model."""
    r = TX.ToxicityV1(use_spot=False)
    assert r.decide(TOXIC_NO)["verdict"] == TX.ALLOW_YES


# ------------------------------------------------- walk-forward discipline

def _row(t, leg, drift, vel, toxic, comp=False, adverse=-10.0):
    return {"t": float(t), "leg": leg,
            "features": {"leg": leg, "held_bid_drift_60s": drift,
                         "spot_velocity_60s": vel, "offsets": {}},
            "outcome": {"toxic": toxic, "markout_60s_ticks":
                        (-40.0 if toxic else 12.0),
                        "complement_filled": comp,
                        "max_adverse_ticks": adverse}}


def test_walk_forward_refuses_an_insufficient_sample_rather_than_fitting():
    out = TX.walk_forward([_row(i, "NO", -0.03, 5e-5, True) for i in range(5)])
    assert out["sufficient"] is False
    assert "below" in out["why"]
    assert out["folds"] == []


def test_a_rule_is_never_scored_on_the_rows_that_chose_it():
    rows = [_row(i, "NO", -0.03, 5e-5, True) for i in range(10)]
    rule = TX._fit(rows)
    got = TX.evaluate(rule, rows, fitted_on=rows)
    assert got["refused"] is True
    assert "manufactured" in got["why"]


def test_walk_forward_fits_on_the_past_and_scores_the_future():
    rows = ([_row(i, "NO", -0.03, 5e-5, True) for i in range(20)]
            + [_row(20 + i, "YES", 0.001, 1e-7, False) for i in range(20)])
    out = TX.walk_forward(rows, folds=4, min_fold=8)
    assert out["sufficient"] is True
    for f in out["folds"]:
        assert max(f["train_span_t"]) <= min(f["test_span_t"])
        assert f["result"]["refused"] is False


def test_the_objective_penalises_blocking_everything():
    """A rule that blocks all fills must not score better than one that
    blocks none, or the fit would always choose the most paranoid grid point."""
    good = [_row(i, "NO", 0.02, -1e-7, False) for i in range(20)]
    rule = TX._fit(good)
    s = TX.score(rule, good)
    assert s["good_blocked"] == 0
    assert s["opportunity_retained"] == 1.0


def test_the_score_reports_both_sides_of_the_trade_off():
    rows = ([_row(i, "NO", -0.03, 5e-5, True) for i in range(10)]
            + [_row(10 + i, "NO", 0.02, -1e-7, False) for i in range(10)])
    s = TX.score(TX.ToxicityV1(), rows)
    assert s["toxic_avoided"] == 10
    assert s["good_blocked"] == 0
    assert s["markout_p50_filtered"] > s["markout_p50_unfiltered"]
    assert s["opportunity_retained"] == 0.5


# --------------------------------------------------------- the dataset

def test_the_outcome_is_a_label_and_never_a_feature():
    ds = PF.ToxicityDataset()
    row = ds.add(features={"held_bid_drift_60s": -0.02}, cohort="BTC5m",
                 arm="PP_F0", symbol="S", leg="NO", price="0.33", t=1.0)
    assert row["outcome"] is None
    PF.ToxicityDataset.label(row, markout_60s=-52.0, complement_filled=False,
                             max_adverse_ticks=-60.0)
    assert row["outcome"]["toxic"] is True
    assert "outcome" not in row["features"]
    assert "markout" not in str(row["features"])


def test_an_unlabelled_row_is_excluded_from_the_sample():
    ds = PF.ToxicityDataset()
    ds.add(features={}, cohort="BTC5m", arm="PP_F0", symbol="S", leg="NO",
           price="0.33", t=1.0)
    assert ds.report()["labelled"] == 0
    assert ds.labelled() == []


def test_legs_are_counted_separately():
    ds = PF.ToxicityDataset()
    for leg, tox in (("NO", -5.0), ("NO", -5.0), ("YES", 5.0)):
        r = ds.add(features={}, cohort="BTC5m", arm="PP_F0", symbol="S",
                   leg=leg, price="0.3", t=1.0)
        PF.ToxicityDataset.label(r, markout_60s=tox, complement_filled=False,
                                 max_adverse_ticks=tox)
    rep = ds.report()
    assert rep["by_leg"]["NO"]["toxic_share"] == 1.0
    assert rep["by_leg"]["YES"]["toxic_share"] == 0.0
