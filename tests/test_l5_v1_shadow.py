"""V1 SHADOW controller: FIDELITY first, then k, and shadow by construction."""
from __future__ import annotations

import pytest

from src import l5_v1_shadow as V


# The REAL v2 artifact shape: full/base/marginal counts with full->base->
# marginal shrinkage. My first fixture invented a {"states": {...}} shape that
# no deployed module uses, and the dry run against the real artifact is what
# caught it -- a fixture that does not match production tests the fixture.
MODEL = {
    "full": {
        "p25-45c|tte>10m|flat|open|q<5": {"open|q5-15": 80, "extend|q5-15": 20},
        "p25-45c|tte>10m|imb1-10|open|q<5": {"extend|q5-15": 67,
                                             "complete|q5-15": 33},
    },
    "base": {
        "p25-45c|tte>10m|flat": {"open|q5-15": 80, "extend|q5-15": 20},
        "p25-45c|tte>10m|imb1-10": {"extend|q5-15": 67, "complete|q5-15": 33},
    },
    "marginal": {"open|q5-15": 1000, "extend|q5-15": 1000, "complete|q5-15": 500},
}
ART = {"size_policy": {"by_relation": {
    "open": {"n": 500, "shares_p50": 10.0},
    "extend": {"n": 500, "shares_p50": 12.0},
    "complete": {"n": 500, "shares_p50": 12.0}}},
    "aggressiveness_policy": {"p_taker_by_tte": {"tte>10m": {"n": 100, "p_taker": 0.03}}}}
BOOK = {"bid": 0.35, "ask": 0.36}


# --------------------------------------------------------------------------
# shadow is structural

def test_the_module_has_no_transport_no_credential_and_no_order_verb():
    src = open(V.__file__, encoding="utf-8").read()
    for bad in ("place_limit_buy", "place_order", "submit_order",
                "cancel_exact", "Transport", "requests", "urllib",
                "PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY", "has_credentials"):
        assert bad not in src, bad


def test_the_authorization_is_recorded_as_shadow_and_not_as_live_headroom():
    assert V.ENVELOPE_A["exposure_ceiling_usd"] == 30.00
    assert "SHADOW ONLY" in V.LABEL
    assert V.RISK_SCALE_K == pytest.approx(0.00619)
    assert V.RISK_SCALE_K >= V.SHAPE_FLOOR_K


# --------------------------------------------------------------------------
# THE state-key lift

def test_our_scaled_inventory_is_lifted_back_into_l5_units_for_the_lookup():
    """Holding 0.06 contracts at k=0.00619 is ~9.7 L5 shares -- 'imb1-10',
    not 'flat'. Without the lift the clone asks the model what L5 does from a
    FLAT book while actually holding inventory."""
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=0.06, tte_s=3600.0)
    assert r["residual_l5_equivalent"] == pytest.approx(9.69, abs=0.05)
    assert r["state_l5_units"] == "p25-45c|tte>10m|imb1-10"
    assert r["state_naive_our_units"] == "p25-45c|tte>10m|flat"
    assert r["state_key_lift_changed_it"] is True
    # and the two states genuinely predict different relations
    assert r["fidelity"]["relation"] == "extend"


def test_without_the_lift_the_clone_would_read_flat_essentially_forever():
    """Every inventory we can hold inside a $3 residual ceiling is far below
    one L5 share, so the naive key is 'flat' across the whole envelope."""
    for inv in (0.01, 0.06, 0.5, 0.99):
        assert V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0,
                         book=BOOK, our_inventory=inv, tte_s=3600.0
                         )["state_naive_our_units"].endswith("flat")


# --------------------------------------------------------------------------
# FIDELITY before ADAPTED

def test_every_row_carries_the_unscaled_fidelity_decision_beside_the_scaled_one():
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=0.0, tte_s=3600.0)
    f, a = r["fidelity"], r["adapted"]
    assert f["raw_desired_qty"] == 10.0            # L5 units, unscaled
    assert f["desired_inventory_l5"] == 10.0
    assert a["scaled_desired_qty"] == pytest.approx(10.0 * V.RISK_SCALE_K)
    assert a["scaled_desired_inventory"] == pytest.approx(10.0 * V.RISK_SCALE_K)
    assert a["quantized_qty"] == 0.06              # 0.0619 -> venue step


def test_a_malformed_model_waits_loudly_rather_than_guessing():
    r = V.observe(model={}, fidelity_art=ART, slug="s", now=0.0,
                  book=BOOK, our_inventory=0.0, tte_s=3600.0)
    assert r["action"] == "WAIT"
    assert r["adapted"]["divergence"] == V.DIV_MODEL
    assert r["wait_reason"].startswith("model_unavailable:")


def test_coverage_means_l5_was_seen_here_not_that_backoff_found_a_prior():
    """The deployed model ALWAYS answers, because shrinkage falls back to the
    marginal. So `observed_by_l5` has to mean "not backed off", or coverage
    would read 100% everywhere and measure nothing."""
    seen = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0,
                     book=BOOK, our_inventory=0.0, tte_s=3600.0)
    assert seen["fidelity"]["backed_off"] is False
    assert seen["fidelity"]["observed_by_l5"] is True

    # a state the toy model has never seen still gets an answer, via the prior
    backed = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0,
                       book={"bid": 0.90, "ask": 0.91}, our_inventory=0.0,
                       tte_s=3600.0)
    assert backed["fidelity"]["backed_off"] is True
    assert backed["fidelity"]["observed_by_l5"] is False
    assert backed["fidelity"]["relation"] is not None   # it still predicts


# --------------------------------------------------------------------------
# divergence attribution is disjoint and correctly ordered

def test_the_residual_ceiling_is_attributed_to_risk_not_to_the_model():
    """Inventory 0.06 lifts to ~9.7 L5 shares ('imb1-10'), a state the model
    DOES have, so the model answers `extend` and only then does the ceiling
    trim it. That ordering is what makes the attribution meaningful."""
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=0.06, tte_s=3600.0,
                  envelope={**V.ENVELOPE_A, "residual_ceiling_usd": 0.025})
    assert r["fidelity"]["relation"] == "extend"          # model spoke first
    assert r["fidelity"]["raw_desired_qty"] == 12.0       # and is unscaled
    assert r["adapted"]["divergence"] == V.DIV_RISK
    assert r["adapted"]["divergence_reason"] == "residual_ceiling"
    assert r["adapted"]["risk_allowed_qty"] < r["adapted"]["scaled_desired_qty"]


def test_a_huge_lifted_inventory_lands_in_a_far_state_and_still_answers():
    """8.0 contracts lifts to ~1292 L5 shares -> 'imb>50'. The toy model has
    never seen it, so the answer comes from the prior and is marked as such."""
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=8.0, tte_s=3600.0)
    assert r["state_l5_units"].endswith("imb>50")
    assert r["fidelity"]["backed_off"] is True


def test_the_exposure_ceiling_is_attributed_to_risk(monkeypatch):
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=0.0, tte_s=3600.0, spent_usd=29.999)
    assert r["adapted"]["divergence"] == V.DIV_RISK
    assert r["adapted"]["divergence_reason"] == "exposure_ceiling"


def test_quantization_is_attributed_to_the_venue_only_when_it_actually_bites():
    """At k=0.00619 a 10-share target lands on 0.06 -- a 3% rounding, not a
    venue distortion. A 0.5-share target pins at the minimum and is."""
    tiny = {"size_policy": {"by_relation": {"open": {"n": 500, "shares_p50": 0.5}}},
            "aggressiveness_policy": {}}
    ok = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                   our_inventory=0.0, tte_s=3600.0)
    assert ok["adapted"]["divergence"] == V.DIV_NONE
    bad = V.observe(model=MODEL, fidelity_art=tiny, slug="s", now=0.0,
                    book=BOOK, our_inventory=0.0, tte_s=3600.0)
    assert bad["adapted"]["divergence"] == V.DIV_VENUE
    assert bad["adapted"]["divergence_reason"] == "pinned_to_venue_minimum"


def test_the_three_divergence_classes_are_disjoint():
    assert len({V.DIV_NONE, V.DIV_MODEL, V.DIV_RISK, V.DIV_VENUE}) == 4


# --------------------------------------------------------------------------
# economics are theoretical and WAITs are scored

def test_economics_never_assume_a_fill():
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0, book=BOOK,
                  our_inventory=0.0, tte_s=3600.0)
    e = r["economics"]
    assert e["maker_feasible"] is True and e["crosses_if_posted_at_touch"] is False
    assert "THEORETICAL" in e["labels"]
    assert "filled" not in json_keys(e)


def json_keys(d):
    return " ".join(d.keys())


def test_a_one_sided_book_is_reported_unavailable_not_guessed():
    r = V.observe(model=MODEL, fidelity_art=ART, slug="s", now=0.0,
                  book={"bid": 0.35, "ask": None}, our_inventory=0.0,
                  tte_s=3600.0)
    assert r["economics"]["available"] is False


def test_summary_counts_waits_and_does_not_score_only_where_we_trade():
    rows = [V.observe(model=MODEL, fidelity_art=ART, slug="s", now=float(i),
                      book=BOOK, our_inventory=0.0, tte_s=3600.0)
            for i in range(3)]
    rows += [V.observe(model={}, fidelity_art=ART, slug="s",
                       now=9.0, book=BOOK, our_inventory=0.0, tte_s=3600.0)]
    s = V.summarize(rows)
    assert s["n"] == 4 and s["wait"] == 1 and s["proposed"] == 3
    assert s["wait_share"] == 0.25
    assert s["divergence_attribution"][V.DIV_MODEL] == 1
    assert list(s["wait_reasons"])[0].startswith("model_unavailable:")
    assert s["state_coverage"] == 0.75
