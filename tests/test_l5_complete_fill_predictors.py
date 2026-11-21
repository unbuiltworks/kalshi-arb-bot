"""The predictor study must not sell an outcome back as its own cause.

rest_s is the trap: filled completes rest 7.9s and unfilled 30.7s, which
looks like overwhelming predictive power and is just the deadline. These
tests pin that rest time is never offered as a predictor, that the two
unavailable strata are declared unavailable rather than quietly substituted,
and that the headline finding cannot be computed from the wrong denominator.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_complete_fill_predictors as fp

_SRC = open(fp.__file__).read()
_LOGS = "../r5f_logs"
_have = os.path.isdir(_LOGS)
_paths = ([os.path.join(_LOGS, f) for f in sorted(os.listdir(_LOGS))]
          if _have else [])


def _r(filled, **kw):
    base = {"filled": filled, "attempt": 1, "price_band": "p45-55c",
            "tte_bucket": "tte>10m", "imbalance": "flat",
            "residual_units": D("3"), "pair_at_touch": D("0.98"),
            "px": D("0.50"), "min_opp_ask": D("0.52"), "rest_s": 30.0,
            "gateway_ask_at_placement": None, "authenticated_ask": None,
            "max_touch": D("0.50"), "slug": "m"}
    base.update(kw)
    return base


# ------------------------------------------------------- the excluded trap
def test_rest_time_is_never_a_predictor_feature():
    assert "rest_s" not in fp.PLACEMENT
    assert "rest_s" not in fp.REALIZED
    for name in list(fp.PLACEMENT) + list(fp.REALIZED):
        assert "rest" not in name, name


def test_the_report_says_why_rest_time_is_excluded():
    rep = {"unavailable_strata": fp.report(_paths)["unavailable_strata"]} \
        if _have else None
    if rep is None:
        pytest.skip("harvested logs not present")
    t = rep["unavailable_strata"]["rest_time"]
    assert "NOT A PREDICTOR" in t
    assert "determined by the outcome" in t


def test_placement_and_realized_families_do_not_overlap():
    assert not set(fp.PLACEMENT) & set(fp.REALIZED)
    assert "closest_book_gap_ticks" in fp.REALIZED


# ------------------------------------------------------------- arithmetic
def test_stratify_counts_fills_over_n():
    # at or above MIN_STRATUM_N, so the rate is published
    rs = [_r(True)] * 6 + [_r(False)] * 3
    got = fp.stratify(rs, lambda r: "one")
    assert got["one"] == {"n": 9, "filled": 6, "fill_rate": round(6 / 9, 4)}


def test_a_thin_stratum_reports_counts_and_no_rate():
    rs = [_r(True)] * (fp.MIN_STRATUM_N - 1)
    row = fp.stratify(rs, lambda r: "thin")["thin"]
    assert row["fill_rate"] is None
    assert row["suppressed_below_min_n"] == fp.MIN_STRATUM_N


def test_pair_bucket_cuts_exactly_at_par():
    assert fp.pair_bucket(_r(True, pair_at_touch=D("0.9999"))).startswith("b")
    assert fp.pair_bucket(_r(True, pair_at_touch=D("1.00"))).startswith("c")
    assert fp.pair_bucket(_r(True, pair_at_touch=D("1.05"))).startswith("d")


def test_book_gap_is_measured_from_our_own_price():
    assert fp.book_gap_bucket(_r(True, px=D("0.50"),
                                 min_opp_ask=D("0.50"))) == "a <=0"
    assert fp.book_gap_bucket(_r(True, px=D("0.50"),
                                 min_opp_ask=D("0.51"))) == "b 1"
    assert fp.book_gap_bucket(_r(True, min_opp_ask=None)) is None


def test_spread_needs_two_usable_buckets():
    assert fp._spread({"a": {"fill_rate": 0.1}, "b": {"fill_rate": 0.5}}) == 0.4
    assert fp._spread({"a": {"fill_rate": 0.1}}) is None
    assert fp._spread({"a": {"fill_rate": None},
                       "b": {"fill_rate": 0.5}}) is None


# --------------------------------------------------- against the real tape
@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_rejections_are_excluded_from_the_denominator():
    """A rejection is a failure to transmit, not a failure to fill."""
    from src import l5_completion_economics as ce
    rs = fp.rows(_paths)
    assert len(rs) == sum(1 for o in ce.all_orders(_paths)
                          if o.get("relation") == "complete"
                          and not str(o.get("end_reason") or ""
                                      ).startswith("rejected"))


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_pair_at_touch_is_the_strongest_measured_predictor():
    rep = fp.report(_paths)
    ranked = rep["ranked_by_spread"]
    assert ranked[0]["feature"] == "pair_at_touch"
    assert ranked[0]["fill_rate_spread"] > 0.30


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_cliff_is_at_par_not_somewhere_convenient():
    t = fp.report(_paths)["placement_features"]["pair_at_touch"]
    below = [v["fill_rate"] for k, v in t.items() if k.startswith(("a", "b"))]
    above = [v["fill_rate"] for k, v in t.items() if k.startswith(("c", "d"))]
    assert min(below) > max(above) * 2, (below, above)


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_tte_is_reported_degenerate_rather_than_dropped():
    u = fp.report(_paths)["unavailable_strata"]["event_progress_tte"]
    assert u.startswith("DEGENERATE")
    assert "tte>10m" in u


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_authenticated_stratum_is_declared_unavailable_with_its_coverage():
    rep = fp.report(_paths)
    u = rep["unavailable_strata"]["distance_from_authenticated_touch"]
    assert "UNAVAILABLE" in u
    assert rep["coverage"]["authenticated_ask"]["share"] < 0.05


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_inventory_comes_from_the_decision_not_a_reconstruction():
    """349 of 497 sessions resume a held position; a running total would
    read residual zero on exactly the orders that matter."""
    assert "inventory_before" in _SRC
    rs = fp.rows(_paths)
    withr = [r for r in rs if r["residual_units"] is not None]
    assert any(r["residual_units"] > 5 for r in withr), \
        "a reconstruction from in-session fills would never see a large residual"


# ------------------------------------------------------------- the honesty
def test_it_corrects_the_earlier_requote_proposal_rather_than_defending_it():
    rc = fp.REQUOTE_CROSSCHECK
    assert "weakens" in rc["claim"]
    assert "too generous" in rc["why"]
    assert "lowers the expected magnitude, not the sign" in rc[
        "what_it_does_not_overturn"]


def test_it_proposes_no_cap_change():
    cd = fp.CANDIDATE
    assert "proposes no change to any of them" in cd["what_this_does_not_claim"]
    blob = _SRC.lower()
    for banned in ("raise the cap", "loosen the cap", "widen the cap",
                   "increase the cap", "lift the limit"):
        assert banned not in blob, banned


def test_the_confound_is_named_before_the_remedy():
    assert "the_confound_to_rule_out_first" in fp.CANDIDATE
    c = fp.CANDIDATE["the_confound_to_rule_out_first"]
    assert "far side was empty" in fp.CANDIDATE["second_candidate"]["why"]
    assert "the market's shape rather than" in c


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "subprocess",
                      "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2
    assert "pmus_spring_l5_session" not in _SRC
