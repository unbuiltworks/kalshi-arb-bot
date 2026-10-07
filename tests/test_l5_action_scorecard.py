"""Tests for the LANE D action-prediction scorecard.

What these pin, in the words of the lane's own requirement:

  1. the baselines are computed from the SAME training rows and scored on the
     SAME test rows as the model — no baseline gets a different evidence set;
  2. NO LABEL LEAKS FROM THE FUTURE into a prediction — a test-only action
     class is never predicted by a train-fitted model, persistence is strictly
     backward-looking inside a window, and no window straddles the split;
  3. the scored field vocabulary matches the production action model, and the
     unscorable fields stay unscorable.

All fixtures are synthetic and in-memory. No network, no live data.
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from analysis.l5.action_scorecard import score as sc
from src.polymarket_l5 import action_model as am


# ---------------------------------------------------------------- fixtures

COLUMNS = ["schema_version", "window_id", "market_slug", "asset",
           "duration_minutes", "trade_seq", "timestamp", "datetime_utc",
           "seconds_from_window_start", "seconds_to_settlement", "outcome",
           "side", "price", "quantity", "dollars", "maker_taker",
           "cum_up_qty", "cum_up_spend", "cum_down_qty", "cum_down_spend",
           "matched_qty", "matched_pair_unit_cost", "residual_qty_signed",
           "residual_side", "settlement_outcome", "resolution_source",
           "source", "transaction_hash"]


def _row(wid, seq, ts, day, outcome, price, qty, residual, tte=400,
         duration=15, settle="Up"):
    d = {c: "" for c in COLUMNS}
    d.update(schema_version="1", window_id=wid, market_slug=f"m-{wid}",
             asset="BTC", duration_minutes=str(duration), trade_seq=str(seq),
             timestamp=str(ts), datetime_utc=f"{day}T00:00:00Z",
             seconds_to_settlement=str(tte), outcome=outcome, side="BUY",
             price=str(price), quantity=str(qty),
             residual_qty_signed=str(residual), settlement_outcome=settle)
    return d


def _write(tmp_path: Path, rows) -> Path:
    p = tmp_path / "seq.csv.gz"
    with gzip.open(p, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def _window(wid, base_ts, day, outcomes, residual=100.0, qty=10.0):
    """A window whose every post-first fill is on `outcomes`."""
    out = []
    for i, oc in enumerate(outcomes):
        out.append(_row(wid, i + 1, base_ts + 10 * i, day, oc, 0.5, qty,
                        residual))
    return out


# ---------------------------------------------------------------- row build

def test_rows_carry_only_past_features_and_future_truth(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = sc.build_rows(_write(tmp_path, _window("w1", base, "2026-04-01",
                                                  ["Up", "Up", "Down"])))
    assert len(rows) == 2                      # n fills -> n-1 transitions
    # residual on the book is +100 (Up surplus): Up is extend, Down completes
    assert [r["truth_exposure_action"] for r in rows] == ["extend", "complete"]
    assert [r["truth_side"] for r in rows] == ["Up", "Down"]
    assert [r["truth_switch"] for r in rows] == ["stay", "switch"]
    # the state key is the PRODUCTION one, not a second implementation
    assert rows[0]["state"] == am.state_key(0.5, 400.0, 100.0)
    # first transition in a window has no sequential context
    assert rows[0]["prev_rel"] == "open" and rows[0]["prev_size_bucket"] == "q<5"
    # era/regime come from the repo's own boundaries, not new ones
    assert rows[0]["era"] == "E1_PRE_REBATE" and rows[0]["regime"] == "R1"


def test_eras_and_regimes_are_the_repo_definitions():
    assert sc.era_of("2026-04-20") == "E1_PRE_REBATE"
    assert sc.era_of("2026-04-21") == "E3_INCENTIVE_ERA"   # q1.FIRST_REBATE
    for day, label in (("2026-03-19", "R1"), ("2026-04-25", "R2"),
                       ("2026-06-23", "R3"), ("2026-08-31", "R4")):
        assert sc.regime_of(day) == label


# ---------------------------------------------------------------- no leakage

def test_split_never_straddles_a_window(tmp_path):
    rows = []
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    for k in range(10):
        rows += _window(f"w{k}", base + 10_000 * k, "2026-04-01",
                        ["Up"] * 6)
    built = sc.build_rows(_write(tmp_path, rows))
    train, test = sc.chronological_split(built)
    assert train and test
    assert not ({r["window_id"] for r in train} & {r["window_id"] for r in test})
    # and the split is chronological: every train window starts before every
    # test window
    assert max(r["window_start"] for r in train) < \
        min(r["window_start"] for r in test)


def test_future_only_class_is_never_predicted(tmp_path):
    """A label that exists ONLY in the test period cannot reach a prediction."""
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(12):                      # train: all q5-15 sizes
        rows += _window(f"a{k}", base + 10_000 * k, "2026-04-01", ["Up"] * 6,
                        qty=10.0)
    for k in range(8):                       # test: a size bucket unseen before
        rows += _window(f"b{k}", base + 500_000 + 10_000 * k, "2026-04-20",
                        ["Up"] * 6, qty=500.0)
    built = sc.build_rows(_write(tmp_path, rows))
    train, test = sc.chronological_split(built, 0.60)
    assert {r["truth_size_bucket"] for r in test} == {"q>40"}
    assert "q>40" not in {r["truth_size_bucket"] for r in train}
    models = sc.fit_models(train)
    preds = {models["size_bucket"]["M_V1"].predict(r) for r in test}
    preds |= {models["size_bucket"]["M_V1_1"].predict(r) for r in test}
    assert "q>40" not in preds, "a test-only label leaked into the model"
    # and the scorer reports 0 accuracy rather than silently crediting it
    res = sc.score_split(train, test, "leak-probe", confusions=False)
    assert res["fields"]["size_bucket"]["models"]["M_V1"]["accuracy"] == 0.0


def test_persistence_is_strictly_backward_looking(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    built = sc.build_rows(_write(
        tmp_path, _window("w1", base, "2026-04-01",
                          ["Up", "Down", "Up", "Up", "Down"])))
    per = sc.Persistence("exposure_action")
    per.fallback = "FALLBACK"
    preds = per.predict_sequence(built)
    truth = [r["truth_exposure_action"] for r in built]
    assert preds[0] == "FALLBACK"             # nothing precedes the first row
    assert preds[1:] == truth[:-1]            # each prediction = PREVIOUS truth
    # a prediction cannot depend on anything after its own row
    assert sc.Persistence("exposure_action").fit(built) is not None
    head = per.predict_sequence(built[:3])
    assert head == preds[:3]


def test_persistence_does_not_cross_window_boundaries(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = _window("w1", base, "2026-04-01", ["Up", "Up"]) + \
        _window("w2", base + 5_000, "2026-04-01", ["Down", "Down"])
    built = sc.build_rows(_write(tmp_path, rows))
    per = sc.Persistence("side")
    per.fallback = "FALLBACK"
    preds = per.predict_sequence(built)
    assert preds[0] == "FALLBACK"
    assert preds[1] == "FALLBACK", "persistence carried state across windows"


# ------------------------------------------------- same rows for every scorer

def test_baselines_and_models_are_scored_on_identical_rows(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(12):
        rows += _window(f"w{k}", base + 10_000 * k, "2026-04-01",
                        ["Up", "Down", "Up", "Up", "Down", "Up"])
    built = sc.build_rows(_write(tmp_path, rows))
    train, test = sc.chronological_split(built)
    res = sc.score_split(train, test, "same-rows", confusions=False)
    for field, entry in res["fields"].items():
        ns = {entry["baselines"][b]["n_rows"] for b in entry["baselines"]}
        ns |= {m["n_rows"] for m in entry["models"].values()}
        assert ns == {len(test)}, f"{field}: scorers saw different row counts"
    assert res["window_overlap"] == 0
    assert res["n_test_rows"] == len(test)


def test_majority_and_marginal_are_fitted_on_train_only(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(12):                       # train: always extend (Up)
        rows += _window(f"a{k}", base + 10_000 * k, "2026-04-01", ["Up"] * 6)
    for k in range(8):                        # test: always complete (Down)
        rows += _window(f"b{k}", base + 500_000 + 10_000 * k, "2026-04-20",
                        ["Down"] * 6)
    built = sc.build_rows(_write(tmp_path, rows))
    train, test = sc.chronological_split(built, 0.60)
    maj = sc.Majority("exposure_action").fit(train)
    assert maj.cls == "extend"                # the TRAIN modal class
    assert all(maj.predict(r) == "extend" for r in test)
    mar = sc.MarginalRandom("exposure_action").fit(train)
    assert set(mar.classes) <= {"extend", "complete", "open", "sell"}
    assert "complete" not in mar.classes      # unseen in train => never drawn
    assert mar.analytic_accuracy == pytest.approx(1.0)


def test_marginal_random_is_seeded_and_reproducible(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(10):
        rows += _window(f"w{k}", base + 10_000 * k, "2026-04-01",
                        ["Up", "Down", "Up", "Down", "Up", "Down"])
    built = sc.build_rows(_write(tmp_path, rows))
    m1 = sc.MarginalRandom("side").fit(built).predict_sequence(built)
    m2 = sc.MarginalRandom("side").fit(built).predict_sequence(built)
    assert m1 == m2


def test_best_baseline_is_the_maximum_not_the_minimum(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(12):
        rows += _window(f"w{k}", base + 10_000 * k, "2026-04-01",
                        ["Up", "Up", "Down", "Up", "Down", "Down"])
    built = sc.build_rows(_write(tmp_path, rows))
    train, test = sc.chronological_split(built)
    res = sc.score_split(train, test, "best-baseline", confusions=False)
    for entry in res["fields"].values():
        accs = [b["accuracy"] for b in entry["baselines"].values()
                if b["accuracy"] is not None]
        assert entry["best_baseline"]["accuracy"] == max(accs)
        for m in entry["models"].values():
            if m["accuracy"] is not None:
                assert m["lift_over_best_baseline"] == pytest.approx(
                    m["accuracy"] - max(accs), abs=1e-9)
                assert m["beats_best_baseline"] == (m["accuracy"] > max(accs))


# ---------------------------------------------------------------- vocabulary

def test_side_model_is_silent_on_a_flat_book(tmp_path):
    base = am.SYSTEMATIC_REGIME_EPOCH + 1000
    rows = []
    for k in range(10):
        rows += _window(f"w{k}", base + 10_000 * k, "2026-04-01", ["Up"] * 6)
    built = sc.build_rows(_write(tmp_path, rows))
    models = sc.fit_models(built)
    flat = dict(built[0], prev_residual=0.0)
    assert models["side"]["M_V1"].predict(flat) is None
    # and silence is counted, never scored as a hit
    acc = sc._acc([None, "Up"], ["Up", "Up"])
    assert acc == {"n_rows": 2, "n_scored": 1, "n_silent": 1, "accuracy": 1.0}


def test_unscorable_fields_are_declared_and_not_scored():
    assert set(sc.UNSCORABLE) == {"price_offset_ticks_vs_touch", "withdraw",
                                  "wait"}
    for reason in sc.UNSCORABLE.values():
        assert len(reason) > 80
    assert not set(sc.UNSCORABLE) & set(sc.FIELDS)
    # no touch/queue/offset field may ever appear on a scored row
    assert not any("offset" in f or "touch" in f or "queue" in f
                   for f in sc.FIELDS)


def test_buckets_are_the_production_ones_not_new_ones():
    assert sc.GAP_BUCKETS[0][0] == "gap<2s" and sc.ALPHA == 10.0
    for q, label in ((1.0, "q<5"), (10.0, "q5-15"), (20.0, "q15-40"),
                     (100.0, "q>40")):
        assert am._bucket(q, am.SIZE_BUCKETS) == label
    assert sc.gap_bucket(1.5) == "gap<2s" and sc.gap_bucket(None) == "gap>30"
