"""Tests for the COMPLETE-vs-EXTEND mismatch forensic.

What these pin:

  1. RECONCILIATION — the forensic's mismatch cohort is EXACTLY the action
     scorecard's own `complete`-called-`extend` rows, and the count is 3,536
     on the real corpus. If this test fails, the forensic and the scorecard
     are describing different sets of decisions and nothing in FORENSIC.md
     may be read against SCORECARD.md.
  2. NO POST-DECISION FIELD ENTERS THE CHARACTERISATION — the feature list
     and the post-decision field list are disjoint, and every forensic
     observable is computable from fills strictly at or before the decision
     instant (proved by replay on a truncated window: deleting everything
     after the decision must not change a single feature value).
  3. the economic bracket reproduces the scorecard's own two bounds.

The structural tests run on synthetic in-memory fixtures. The two
reconciliation tests need the real corpus and skip if it is absent.
No network, no live data, no git.
"""
from __future__ import annotations

import csv
import gzip
import math
from pathlib import Path

import pytest

from analysis.l5.action_scorecard import score as sc
from analysis.l5.complete_vs_extend import forensic as fx
from src.polymarket_l5 import action_model as am

CORPUS = sc.SEQUENCE_CSV
needs_corpus = pytest.mark.skipif(
    not CORPUS.exists(), reason="canonical sequence not present")

#: The scorecard's published figure, SCORECARD.md §4 / MISMATCHES.md #1.
SCORECARD_COMPLETE_CALLED_EXTEND = 3536
SCORECARD_COMPLETE_DECISIONS = 10035
SCORECARD_TEST_ROWS = 20706
SCORECARD_TRANSITIONS = 69646


# ------------------------------------------------------------ fixtures

COLUMNS = ["schema_version", "window_id", "market_slug", "asset",
           "duration_minutes", "trade_seq", "timestamp", "datetime_utc",
           "seconds_from_window_start", "seconds_to_settlement", "outcome",
           "side", "price", "quantity", "dollars", "maker_taker",
           "cum_up_qty", "cum_up_spend", "cum_down_qty", "cum_down_spend",
           "matched_qty", "matched_pair_unit_cost", "residual_qty_signed",
           "residual_side", "settlement_outcome", "resolution_source",
           "source", "transaction_hash"]


def _window(wid: str, t0: int, legs, settle="Up"):
    """legs: list of (outcome, qty, price). Cumulatives are derived."""
    up_q = up_s = dn_q = dn_s = 0.0
    out = []
    for i, (outcome, qty, price) in enumerate(legs, start=1):
        if outcome == "Up":
            up_q += qty
            up_s += qty * price
        else:
            dn_q += qty
            dn_s += qty * price
        matched = min(up_q, dn_q)
        resid = up_q - dn_q
        ts = t0 + i * 10
        out.append({
            "schema_version": "1", "window_id": wid,
            "market_slug": f"btc-updown-15m-{t0}", "asset": "BTC",
            "duration_minutes": "15", "trade_seq": str(i),
            "timestamp": str(ts),
            "datetime_utc": "2026-08-30T00:00:00Z",
            "seconds_from_window_start": str(i * 10),
            "seconds_to_settlement": str(900 - i * 10),
            "outcome": outcome, "side": "BUY", "price": f"{price}",
            "quantity": f"{qty}", "dollars": f"{qty * price}",
            "maker_taker": "unknown",
            "cum_up_qty": f"{up_q}", "cum_up_spend": f"{up_s}",
            "cum_down_qty": f"{dn_q}", "cum_down_spend": f"{dn_s}",
            "matched_qty": f"{matched}", "matched_pair_unit_cost": "0.99",
            "residual_qty_signed": f"{resid}",
            "residual_side": "Up" if resid > 0 else "Down",
            "settlement_outcome": settle,
            "resolution_source": "test", "source": "test",
            "transaction_hash": f"0x{wid}{i}",
        })
    return out


@pytest.fixture()
def synth(tmp_path: Path) -> Path:
    t0 = am.SYSTEMATIC_REGIME_EPOCH + 100_000
    rows = []
    for k in range(6):
        rows += _window(
            f"w{k}", t0 + k * 2000,
            [("Up", 100, 0.40), ("Up", 100, 0.42), ("Up", 100, 0.45),
             ("Down", 200, 0.56), ("Up", 100, 0.47), ("Down", 100, 0.55)])
    p = tmp_path / "seq.csv.gz"
    with gzip.open(p, "wt", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return p


# ------------------------------------------- 1. RECONCILIATION (real data)

@needs_corpus
def test_forensic_rows_are_positionally_identical_to_scorecard_rows():
    """The forensic must describe the scorecard's rows, not its own."""
    a = sc.build_rows()
    b = fx.build_forensic_rows()
    assert len(a) == len(b) == SCORECARD_TRANSITIONS
    shared = sorted(set(a[0]) & set(b[0]))
    assert "truth_exposure_action" in shared and "state" in shared
    for i, (ra, rb) in enumerate(zip(a, b)):
        for k in shared:
            assert ra[k] == rb[k], f"row {i} field {k}: {ra[k]!r} != {rb[k]!r}"


@needs_corpus
def test_mismatch_cohort_is_exactly_the_scorecard_complete_called_extend():
    """Reconcile the cohort to 3,536 against score.py's own predictions."""
    rows = fx.build_forensic_rows()
    co = fx.cohorts(rows)
    assert len(co["test"]) == SCORECARD_TEST_ROWS
    mis = co["MISMATCH_complete_called_extend"]
    assert len(mis) == SCORECARD_COMPLETE_CALLED_EXTEND

    # independently, from score.py's own row builder and model
    s_rows = sc.build_rows()
    s_tr, s_te = sc.chronological_split(s_rows)
    s_model = sc.fit_models(s_tr)["exposure_action"]["M_V1_1"]
    s_mis = [r for r in s_te
             if r["truth_exposure_action"] == "complete"
             and s_model.predict(r) == "extend"]
    assert len(s_mis) == SCORECARD_COMPLETE_CALLED_EXTEND
    # same decisions, identified by (window_id, timestamp)
    assert ({(r["window_id"], r["ts"]) for r in mis}
            == {(r["window_id"], r["ts"]) for r in s_mis})
    # and the denominator the 35.2% is quoted against
    n_complete = sum(1 for r in co["test"]
                     if r["truth_exposure_action"] == "complete")
    assert n_complete == SCORECARD_COMPLETE_DECISIONS
    assert round(len(mis) / n_complete, 3) == 0.352


@needs_corpus
def test_economic_bracket_reproduces_the_scorecard_bounds():
    rows = fx.build_forensic_rows()
    co = fx.cohorts(rows)
    n_win = len({r["window_id"] for r in co["test"]})
    e = fx.economics(co["MISMATCH_complete_called_extend"], n_win)
    assert e["n_rows"] == SCORECARD_COMPLETE_CALLED_EXTEND
    assert round(e["shares"]) == 86857 or round(e["shares"]) == 86856
    assert abs(e["notional_usd"] - 44787.0) < 1.0
    assert abs(e["lower_bound_usd"] + 1319.0) < 2.0
    assert abs(e["upper_bound_usd"] + 15911.9) < 2.0


# --------------------------------- 2. NO POST-DECISION FIELD IN FEATURES

def test_feature_and_post_decision_lists_are_disjoint():
    fx.assert_no_post_decision_features()
    assert not set(fx.FEATURES) & set(fx.POST_DECISION_FIELDS)
    assert not any(f.startswith("truth_") for f in fx.FEATURES)


def test_every_swept_feature_is_declared_a_feature():
    assert set(fx.SWEEP_BINS) <= set(fx.FEATURES)


def test_features_are_computable_from_the_observable_past_only(synth):
    """Truncating the window AFTER the decision must not move a feature.

    This is the operative proof that no post-decision information enters the
    characterisation: for each transition we rebuild the row set from a
    corpus in which every fill strictly after the decision's own fill has
    been deleted, and require every FEATURE to be bit-identical.
    """
    full = fx.build_forensic_rows(synth)
    assert full, "fixture produced no transitions"

    with gzip.open(synth, "rt", newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))

    for target in full:
        keep = [r for r in raw
                if r["window_id"] != target["window_id"]
                or int(float(r["timestamp"])) <= target["ts"]]
        p = synth.parent / "trunc.csv.gz"
        with gzip.open(p, "wt", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(keep)
        got = [r for r in fx.build_forensic_rows(p)
               if r["window_id"] == target["window_id"]
               and r["ts"] == target["ts"]]
        assert len(got) == 1
        for f in fx.FEATURES:
            if f not in target:
                continue
            a, b = target[f], got[0][f]
            if isinstance(a, float) and math.isnan(a):
                assert isinstance(b, float) and math.isnan(b), f
            else:
                assert a == b, f"{f}: {a!r} != {b!r}"


def test_characterisation_never_reads_a_post_decision_field(synth, monkeypatch):
    """The sweeps must not touch qty/price/settlement at all."""
    rows = fx.build_forensic_rows(synth)
    co = fx.cohorts(rows)
    poisoned = []
    for r in co["test"]:
        for k in ("qty", "price", "settle_pnl_per_share"):
            r[k] = None
        poisoned.append(r)
    # sweeps run clean on rows whose outcome fields have been destroyed
    fx.sweep_numeric(co["MISMATCH_complete_called_extend"],
                     co["RIGHT_complete"], co["RIGHT_extend"])
    fx.sweep_categorical(co["MISMATCH_complete_called_extend"],
                         co["RIGHT_complete"], co["RIGHT_extend"])
    fx.prev_rel_table(co["test"], co["model"])
    fx.state_key_degeneracy(co["test"])
    fx.confound_control(co["test"], co["model"])
    assert poisoned, "fixture produced no test rows"


# --------------------------------------------- 3. structural invariants

def test_relation_labelling_is_sound_on_the_fixture(synth):
    v = fx.verify_labelling(synth)
    per = v["per_relation"]
    # every `extend` strictly increases |residual|
    assert per["extend"]["pct_increased"] == 1.0
    # every `complete` moves toward flat (or overshoots through zero)
    assert per["complete"]["pct_reduced"] > 0.0


@needs_corpus
def test_relation_labelling_is_sound_on_the_real_corpus():
    """The bug check: a `complete` that does not reduce |residual| would
    invalidate the scorecard's cohort and its dollar bracket."""
    v = fx.verify_labelling()
    per = v["per_relation"]
    assert per["extend"]["pct_increased"] == 1.0, "an `extend` moved to flat"
    assert per["complete"]["pct_reduced"] >= 0.95
    # the only non-reducing `complete` rows are clips that overshoot zero
    assert (per["complete"].get("increased_abs_residual", 0)
            <= per["complete"].get("crossed_zero_overshoot", 0))


def test_no_tuning_claim_is_structural(synth):
    """The discovery cell must declare itself unconfirmed."""
    rows = fx.build_forensic_rows(synth)
    co = fx.cohorts(rows)
    d = fx.discovery_candidate(co)
    assert d["confirmation_left_unrun"] is True
    assert "DISCOVERY" in d["status"]
    assert d["held_out_untouched"]
