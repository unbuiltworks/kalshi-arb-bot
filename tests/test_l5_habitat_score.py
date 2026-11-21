"""Pre-action by construction, L5-derived thresholds, and no favourable imputation."""
import pytest

from src import l5_habitat_score as hs


def ref(**over):
    """A reference shaped like a derived one: values 0..100 on the grid."""
    base = {g: 100.0 * g for g in hs.GRID}
    cal = {s["name"]: {"n": 1893, "q": {str(k): v for k, v in base.items()}}
           for s in hs.CALIBRATED_FEATURES}
    cal.update(over)
    return {"calibrated": cal, "l5_demand": {}, "windows": 1893}


def cand(**kw):
    d = {s["name"]: 50.0 for s in hs.CALIBRATED_FEATURES}
    d.update(kw)
    return d


# ---------- pre-action by construction

@pytest.mark.parametrize("field", hs.FORBIDDEN_INPUTS)
def test_post_action_fields_are_refused_not_ignored(field):
    with pytest.raises(ValueError, match="post-action"):
        hs.score({**cand(), field: 1}, ref())


def test_settlement_and_our_fills_are_both_on_the_forbidden_list():
    for f in ("settlement_outcome", "realized_usd", "filled_qty",
              "matched_qty", "residual"):
        assert f in hs.FORBIDDEN_INPUTS


def test_no_calibrated_feature_reads_a_post_action_quantity():
    names = {s["name"] for s in hs.CALIBRATED_FEATURES}
    assert not (names & set(hs.FORBIDDEN_INPUTS))


# ---------- the thresholds are L5's

def test_reaching_l5s_median_scores_one_half():
    r = hs.score(cand(), ref())
    assert r["per_feature"]["price_changes_per_min"]["score"] == \
        pytest.approx(0.5)


def test_reaching_l5s_p90_scores_zero_point_nine():
    r = hs.score(cand(price_changes_per_min=90.0), ref())
    assert r["per_feature"]["price_changes_per_min"]["score"] == \
        pytest.approx(0.9)


def test_a_capacity_feature_saturates_rather_than_rewarding_excess():
    hot = hs.score(cand(price_changes_per_min=10_000.0), ref())
    top = hs.score(cand(price_changes_per_min=100.0), ref())
    assert hot["per_feature"]["price_changes_per_min"]["score"] == 1.0
    assert top["per_feature"]["price_changes_per_min"]["score"] == 1.0


def test_a_regime_feature_penalises_difference_in_both_directions():
    lo = hs.score(cand(tte_p50_s=0.0), ref())["per_feature"]["tte_p50_s"]
    mid = hs.score(cand(tte_p50_s=50.0), ref())["per_feature"]["tte_p50_s"]
    hi = hs.score(cand(tte_p50_s=100.0), ref())["per_feature"]["tte_p50_s"]
    assert mid["score"] == pytest.approx(1.0)
    assert lo["score"] == pytest.approx(0.0)
    assert hi["score"] == pytest.approx(0.0)


def test_a_reference_needs_at_least_thirty_windows():
    assert hs.quantiles(range(29)) is None
    assert hs.quantiles(range(30))["n"] == 30


def test_derive_reference_emits_uncalibrated_for_a_thin_feature():
    r = hs.derive_reference([{"price_changes_per_min": 1.0}] * 5)
    assert r["calibrated"]["price_changes_per_min"] is None
    out = hs.score(cand(), r)
    assert "price_changes_per_min" in out["uncalibrated_features"]


# ---------- three kinds of not-knowing, never imputed favourably

def test_a_missing_value_is_unknown_and_leaves_the_denominator():
    c = cand()
    c.pop("price_span")
    r = hs.score(c, ref())
    assert "price_span" in r["unknown_features"]
    assert r["features_scored"] == len(hs.CALIBRATED_FEATURES) - 1
    assert r["coverage"] < 1.0


def test_a_missing_value_never_scores_as_if_it_were_good():
    full = hs.score(cand(), ref())
    c = cand()
    c["price_span"] = None
    thin = hs.score(c, ref())
    assert thin["coverage_discounted_score"] < full["coverage_discounted_score"]


def test_a_thinly_observed_market_cannot_reach_a_perfect_discounted_score():
    c = {s["name"]: 100.0 for s in hs.CALIBRATED_FEATURES[:2]}
    r = hs.score(c, ref())
    assert r["habitat_score_on_observed"] == pytest.approx(1.0)
    assert r["coverage_discounted_score"] < 0.35


def test_uncalibrated_features_are_reported_but_never_scored():
    r = hs.score({**cand(), "spread_ticks_p50": 1.0,
                  "depth_at_touch_p50": 500.0}, ref())
    assert r["uncalibrated_reported_not_scored"]["spread_ticks_p50"] == 1.0
    assert r["features_total"] == len(hs.CALIBRATED_FEATURES)
    for n in hs.UNCALIBRATED_FEATURES:
        assert n not in {s["name"] for s in hs.CALIBRATED_FEATURES}


def test_an_uncalibrated_reference_is_distinguished_from_a_missing_value():
    r = hs.score(cand(), ref(price_span=None))
    assert "price_span" in r["uncalibrated_features"]
    assert "price_span" not in r["unknown_features"]


# ---------- the demand side is reported, not folded in

def test_l5_demand_features_are_not_part_of_the_score():
    for n in hs.L5_DEMAND_FEATURES:
        assert n not in {s["name"] for s in hs.CALIBRATED_FEATURES}
    r = hs.score(cand(), ref())
    assert set(r["l5_demand_side"]) == set(hs.L5_DEMAND_FEATURES)
    assert all(v["status"] == hs.UNKNOWN for v in r["l5_demand_side"].values())


def test_the_one_directional_reading_is_carried_on_every_result():
    r = hs.score(cand(), ref())
    i = r["interpretation"]
    assert "NOT conclusive sufficiency" in i["direction"]
    assert "CEILING" in i["ceiling"]
    assert "does not predict profit" in i["not_profit"]


# ---------- window features

def test_window_features_need_two_observations_and_a_span():
    assert hs.window_features([{"t": 1, "price": 0.5}])["usable"] is False
    assert hs.window_features([{"t": 1, "price": 0.5},
                               {"t": 1, "price": 0.6}])["usable"] is False


def test_window_features_measure_movement_per_minute():
    obs = [{"t": 0, "price": 0.50}, {"t": 30, "price": 0.52},
           {"t": 60, "price": 0.52}, {"t": 90, "price": 0.60}]
    w = hs.window_features(obs)
    assert w["price_changes_per_min"] == pytest.approx(2 / 1.5, abs=1e-4)
    assert w["share_move_ge_1c"] == pytest.approx(2 / 3, abs=1e-4)
    assert w["share_move_ge_5c"] == pytest.approx(1 / 3, abs=1e-4)
    assert w["price_span"] == pytest.approx(0.10, abs=1e-4)


def test_bands_visited_counts_distinct_price_bands():
    obs = [{"t": 0, "price": 0.05}, {"t": 10, "price": 0.50},
           {"t": 20, "price": 0.95}]
    assert hs.window_features(obs)["bands_visited"] == 3


def test_absent_optional_fields_come_back_none_not_zero():
    w = hs.window_features([{"t": 0, "price": 0.5}, {"t": 60, "price": 0.6}])
    assert w["spread_ticks_p50"] is None
    assert w["depth_at_touch_p50"] is None
    assert w["tte_p50_s"] is None


# ---------- venue agnostic

def test_the_same_call_serves_any_venue():
    r = ref()
    a = hs.score(cand(), r)
    b = hs.score(cand(), r)
    assert a["habitat_score_on_observed"] == b["habitat_score_on_observed"]
    src = open("src/l5_habitat_score.py").read()
    for venue in ("pmus", "polymarket", "gemini", "kalshi", "btc", "mlb"):
        assert venue not in src.lower().replace("l5_", "")


def test_it_reads_and_never_acts():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(hs))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code = ast.unparse(tree)
    for tok in ("place_limit", "submit", "cancel_order", "requests.",
                "httpx", "os.environ", "open("):
        assert tok not in code, tok
