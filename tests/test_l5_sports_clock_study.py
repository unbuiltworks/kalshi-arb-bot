"""Tests for the L5 sports clock study (#167 comment 5577894093).

Real assertions over synthetic fixtures: episode segmentation, each of the
three clocks, the causal-at-decision-time guarantee (including a deliberately
non-causal clock that MUST be caught), the frozen-mapping hash, the
information test, and the venue-coverage reader with an injected gateway.
No network, no log dumps, nothing frozen is read.
"""
from __future__ import annotations

import json
import math

import pytest

from src import l5_sports_clock_study as S


# ------------------------------ fixtures ----------------------------------

def _row(slug, t, ye, **kw):
    d = {"slug": slug, "t": float(t), "t_utc": None, "yes_equiv": ye,
         "acted": kw.pop("acted", "open"), "filled_qty": kw.pop("filled_qty", 0.0),
         "residual_after": kw.pop("residual_after", None),
         "relation": kw.pop("relation", "open"), "px": kw.pop("px", 0.5),
         "idx": kw.pop("idx", 0), "state": "p45-55c|tte>10m|flat",
         "tte_bucket": "tte>10m", "price_bucket": "p45-55c",
         "inventory_bucket": "flat", "l5_sampled": None, "refusal_class": None,
         "outcome": None, "placed_order": True,
         "progress_series": kw.pop("progress_series", [])}
    d.update(kw)
    return d


def _tape():
    """One market: quiet start, a 3c jump at t=30, quiet again, then a
    5-minute gap, then two more decisions."""
    return [
        _row("m", 0, 0.50, idx=0),
        _row("m", 10, 0.505, idx=1),          # 0.5c move: same episode
        _row("m", 20, 0.51, idx=2),           # 1c from anchor: same episode
        _row("m", 30, 0.53, idx=3),           # 3c from anchor: NEW episode
        _row("m", 40, 0.535, idx=4),
        _row("m", 400, 0.535, idx=5),         # 360s gap: NEW episode
        _row("m", 410, 0.535, idx=6),
    ]


# --------------------------- episode segmentation -------------------------

def test_episode_boundaries_are_where_the_definition_says():
    out = S.clocks_for_market(_tape(), k_cents=2.0, gap_s=300.0)
    assert [r["c3_episode"] for r in out] == [0, 0, 0, 1, 1, 2, 2]
    assert [r["c3_episode_opened_by"] for r in out] == [
        "first_observation", None, None, "move>=2c", None, "gap>=300s", None]
    assert [r["c3_regime_age_s"] for r in out] == [0.0, 10.0, 20.0, 0.0, 10.0,
                                                   0.0, 10.0]


def test_episode_threshold_is_measured_from_the_anchor_not_the_last_row():
    """Three 1.5c steps in a row must trigger once k is crossed against the
    ANCHOR, not never (which is what a step-to-step rule would do)."""
    rows = [_row("m", 0, 0.50), _row("m", 5, 0.515), _row("m", 10, 0.53)]
    out = S.clocks_for_market(rows, k_cents=2.0, gap_s=300.0)
    assert [r["c3_episode"] for r in out] == [0, 0, 1]


def test_k_sensitivity_monotone_in_episode_count():
    counts = []
    for k in (1.0, 2.0, 3.0, 5.0):
        out = S.clocks_for_market(_tape(), k_cents=k, gap_s=300.0)
        counts.append(len({r["c3_episode"] for r in out}))
    assert counts == sorted(counts, reverse=True)


def test_rows_without_a_touch_do_not_open_or_move_an_episode():
    rows = [_row("m", 0, 0.50), _row("m", 5, None), _row("m", 10, 0.505)]
    out = S.clocks_for_market(rows, k_cents=2.0, gap_s=300.0)
    assert [r["c3_episode"] for r in out] == [0, 0, 0]
    assert out[1]["c3_regime_age_s"] == 5.0


# ------------------------------- the clocks -------------------------------

def test_c1_event_phase_buckets_and_missing_policy():
    assert S.event_phase_bucket(None) == "ev_unknown"
    assert S.event_phase_bucket(0.0) == "ev_early(<0.34)"
    assert S.event_phase_bucket(0.33) == "ev_early(<0.34)"
    assert S.event_phase_bucket(0.34) == "ev_mid(0.34-0.67)"
    assert S.event_phase_bucket(0.66) == "ev_mid(0.34-0.67)"
    assert S.event_phase_bucket(0.67) == "ev_late(>=0.67)"
    assert S.event_phase_bucket(1.0) == "ev_late(>=0.67)"


def test_c1_uses_only_observations_at_or_before_the_decision():
    series = [(0.0, {"period": "S1", "proximity": 0.20, "basis": "b", "clock": None}),
              (100.0, {"period": "S5", "proximity": 0.95, "basis": "b", "clock": None})]
    rows = [_row("m", 50, 0.50, progress_series=series),
            _row("m", 150, 0.50, progress_series=series)]
    out = S.clocks_for_market(rows, k_cents=2.0, gap_s=300.0)
    assert out[0]["c1_event_phase"] == "ev_early(<0.34)"     # the S5 row is future
    assert out[1]["c1_event_phase"] == "ev_late(>=0.67)"
    assert out[0]["c1_observation_age_s"] == 50.0
    assert out[1]["c1_observation_age_s"] == 50.0


def test_c1_before_any_observation_is_unknown_not_guessed():
    series = [(100.0, {"period": "S5", "proximity": 0.95, "basis": "b"})]
    out = S.clocks_for_market([_row("m", 10, 0.5, progress_series=series)])
    assert out[0]["c1_event_phase"] == "ev_unknown"
    assert out[0]["c1_proximity"] is None
    assert out[0]["c1_observation_age_s"] is None


def test_c2_never_manufactures_a_remaining_life():
    out = S.clocks_for_market(_tape())
    for r in out:
        assert r["c2_remaining_tradeable_life_s"] is None
        assert "endDate" in r["c2_remaining_basis"]
    assert [r["c2_since_first_decision_s"] for r in out][:4] == [0.0, 10.0,
                                                                 20.0, 30.0]


def test_c3_trailing_drift_is_measured_over_the_stated_window():
    rows = [_row("m", 0, 0.50), _row("m", 60, 0.50), _row("m", 130, 0.56)]
    out = S.clocks_for_market(rows, k_cents=100.0, gap_s=1e9,
                              drift_window_s=120.0)
    assert out[0]["c3_trailing_drift_cents"] is None      # nothing 120s back
    assert out[1]["c3_trailing_drift_cents"] is None
    assert out[2]["c3_trailing_drift_cents"] == pytest.approx(6.0)
    assert out[2]["c3_drift_bucket"] == "drift>=5c"


def test_drift_and_phase_bucket_edges():
    assert S.drift_bucket(None) == "drift_unknown"
    assert S.drift_bucket(1.9) == "drift<2c"
    assert S.drift_bucket(-4.9) == "drift2-5c"            # magnitude, signed input
    assert S.drift_bucket(5.0) == "drift>=5c"
    assert S.phase_bucket(None) == "ep_unknown"
    assert S.phase_bucket(0.0) == "ep<15s"
    assert S.phase_bucket(15.0) == "ep15-60s"
    assert S.phase_bucket(60.0) == "ep>=60s"


# --------------------------- causality guarantee --------------------------

def test_causal_audit_passes_on_the_real_clocks():
    rows = _tape() + [_row("n", 0, 0.30), _row("n", 20, 0.40)]
    a = S.causal_audit(rows, k_cents=2.0, gap_s=300.0)
    assert a["mismatches"] == 0
    assert a["fields_checked"] == len(rows) * len(S.CLOCK_FIELDS)


def test_causal_audit_catches_a_clock_that_peeks_at_the_future():
    """The audit is only worth anything if it fails on a non-causal feature."""
    def peeking(rows, **kw):
        rows = sorted(rows, key=lambda r: r["t"])
        final = rows[-1]["yes_equiv"]
        return [dict(r, c3_regime_age_s=(final - (r["yes_equiv"] or 0.0)))
                for r in rows]

    with pytest.raises(AssertionError) as e:
        S.causal_audit(_tape(), clock_fn=peeking, fields=("c3_regime_age_s",),
                       k_cents=2.0, gap_s=300.0)
    assert "non-causal" in str(e.value)


def test_no_outcome_field_is_a_clock_field():
    for f in S.CLOCK_FIELDS:
        assert not any(w in f for w in ("completed", "latency", "realized",
                                        "settle", "residual_at_settlement"))


# ------------------------------ frozen mapping ----------------------------

def test_frozen_mapping_hash_is_stable():
    """If this literal changes, the mapping changed and the in-sample and
    out-of-sample numbers no longer refer to the same object."""
    assert S.mapping_hash() == "df3408c843ee7c25"
    assert S.FROZEN_MAPPING["id"] == "L5_SPORTS_CLOCK_v1"


def test_frozen_mapping_excludes_future_information():
    nu = S.FROZEN_MAPPING["not_used"]
    assert "settlement outcome" in nu and "realized pnl" in nu
    assert any("endDate" in x for x in nu)
    assert S.FROZEN_MAPPING["event_phase"]["missing_policy"].startswith(
        "ev_unknown")


def test_mapping_hash_changes_when_the_definition_changes():
    a = S.mapping_hash()
    b = S.mapping_hash(dict(S.FROZEN_MAPPING, id="something-else"))
    assert a != b


# ---------------------------- outcome machinery ---------------------------

def test_fill_sequences_marks_completion_only_from_later_fills():
    rows = [
        _row("m", 0, 0.50, filled_qty=5.0, residual_after=5.0, relation="open"),
        _row("m", 30, 0.51, filled_qty=5.0, residual_after=0.0,
             relation="complete"),
        _row("m", 60, 0.52, filled_qty=4.0, residual_after=4.0,
             relation="extend"),
    ]
    out = S.clocks_for_market(rows, k_cents=100.0, gap_s=1e9)
    fs = S.fill_sequences(out)
    assert [f["completed"] for f in fs] == [True, False]
    assert fs[0]["latency_s"] == 30.0
    assert fs[1]["latency_s"] is None


def test_rate_table_and_cluster_bootstrap_are_deterministic():
    rows = ([{"slug": "a", "k": "x", "hit": True} for _ in range(6)]
            + [{"slug": "b", "k": "y", "hit": False} for _ in range(6)])
    t = S.rate_table(rows, "k", lambda r: r["hit"])
    assert t["x"]["rate"] == 1.0 and t["y"]["rate"] == 0.0
    f = lambda s: S._diff_rate(s, "k", ("x",), ("y",), lambda r: r["hit"])  # noqa: E731
    a = S.cluster_bootstrap(rows, f, b=200)
    b = S.cluster_bootstrap(rows, f, b=200)
    assert a == b
    assert a["point"] == 1.0 and a["clusters"] == 2


def test_cluster_bootstrap_uncertainty_comes_from_markets():
    """Heterogeneous markets must produce a WIDE interval even though each
    market contributes many rows: the uncertainty is over markets, so adding
    rows inside a market must not shrink it."""
    def make(reps):
        rows = []
        for slug, hx, hy in (("a", True, False), ("b", True, True),
                             ("c", False, False), ("d", True, False)):
            rows += [{"slug": slug, "k": "x", "hit": hx} for _ in range(reps)]
            rows += [{"slug": slug, "k": "y", "hit": hy} for _ in range(reps)]
        return rows
    f = lambda s: S._diff_rate(s, "k", ("x",), ("y",), lambda r: r["hit"])  # noqa: E731
    small = S.cluster_bootstrap(make(4), f, b=800)
    big = S.cluster_bootstrap(make(40), f, b=800)
    assert small["ci95"][0] < small["ci95"][1]
    # ten times the rows, same four markets -> the same interval
    assert small["ci95"] == big["ci95"]
    assert small["clusters"] == big["clusters"] == 4


# --------------------------- information test -----------------------------

def test_information_test_prefers_the_feature_that_actually_predicts():
    # r = (price, tte, inv, age, relation); relation is a pure function of inv
    recs = []
    for i in range(400):
        inv = "flat" if i % 2 else "imb>50"
        recs.append(("p45-55c", "tte<30s" if i % 3 else "tte>10m", inv,
                     "ep<15s" if i % 5 else "ep>=60s",
                     "open" if inv == "flat" else "complete"))
    out = S.information_test(recs, folds=4)
    by = out["by_feature"]
    assert by["inventory"]["held_out_log_loss_bits"] < \
        by["tte"]["held_out_log_loss_bits"]
    assert by["inventory"]["information_gain_bits"] == pytest.approx(
        out["H_relation_bits"], abs=1e-6)


def test_relation_derivation_matches_the_canonical_definition():
    assert S._relation(0.0, "Up", "BUY") == "open"
    assert S._relation(5.0, "Up", "BUY") == "extend"
    assert S._relation(5.0, "Down", "BUY") == "complete"
    assert S._relation(-5.0, "Down", "BUY") == "extend"
    assert S._relation(5.0, "Up", "SELL") == "sell"


# ---------------------------- venue coverage ------------------------------

def _fake_gateway(path):
    if path.startswith("/v2/leagues/mlb/"):
        return {"events": [{"ticker": "mlb-a-b", "live": True, "period": "Bot 7th",
                            "eventState": {"type": "baseball", "score": "2-3",
                                           "period": "Bot 7th", "live": True,
                                           "baseballState": {"outs": 1,
                                                             "inningHalf": "B",
                                                             "balls": 1}}}]}
    if path.startswith("/v2/leagues/nhl/"):
        return {"events": [{"ticker": "nhl-a-b", "live": False}]}
    if path.startswith("/v1/market/slug/"):
        return {"market": {"gameStartTime": "2026-09-07T23:30:00Z",
                           "endDate": "2026-09-21T23:30:00Z",
                           "startDate": "2026-09-01T00:00:00Z",
                           "sportsMarketType": "moneyline"}}
    raise AssertionError("unexpected path " + path)


def test_gateway_coverage_reports_missing_coverage_as_not_observed():
    out = S.gateway_coverage(("mlb", "nhl"), get_json=_fake_gateway,
                             sample_market_slugs=("aec-cfb-x",))
    mlb = out["by_league"]["mlb"]
    assert mlb["observed"] == "yes" and mlb["live_at_probe"] == 1
    assert mlb["substates"] == {"baseballState": 1}
    assert "baseballState.outs" in mlb["populated_fields"]
    nhl = out["by_league"]["nhl"]
    assert nhl["observed"] == "not_observed" and nhl["substates"] == {}
    md = out["market_detail"]["aec-cfb-x"]
    assert md["endDate_minus_gameStart_days"] == 14.0
    assert md["endDate_before_gameStartTime"] is False
    assert out["auth"].startswith("none")


def test_gateway_coverage_flags_endDate_before_gameStart():
    def gw(path):
        if path.startswith("/v1/market/slug/"):
            return {"market": {"gameStartTime": "2026-09-07T19:45:00Z",
                               "endDate": "2026-09-07T16:02:28Z"}}
        return {"events": []}
    out = S.gateway_coverage((), get_json=gw, sample_market_slugs=("x",))
    assert out["market_detail"]["x"]["endDate_before_gameStartTime"] is True
    assert out["market_detail"]["x"]["endDate_minus_gameStart_days"] < 0


# ------------------------------ misc / io ---------------------------------

def test_iso_epoch_tolerates_nine_digit_fractions():
    a = S.iso_epoch("2026-09-07T19:21:36.425733064Z")
    b = S.iso_epoch("2026-09-07T19:21:36.425733+00:00")
    assert a is not None and abs(a - b) < 1e-3
    assert S.iso_epoch(None) is None
    assert S.iso_epoch("not-a-time") is None


def test_live_state_distribution_shares_sum_to_one():
    rows = [{"tte_bucket": "tte>10m", "price_bucket": "p45-55c",
             "inventory_bucket": "flat"} for _ in range(4)]
    d = S.live_state_distribution(rows)
    for k in d:
        assert math.isclose(sum(d[k]["share"].values()), 1.0, abs_tol=1e-9)


def test_market_level_event_phase_counts_markets_not_fills():
    fills = [{"slug": "m", "c1_event_phase": "ev_late(>=0.67)",
              "c1_period": "S5", "completed": False, "cohort": "oos"}
             for _ in range(9)]
    fills += [{"slug": "n", "c1_event_phase": "ev_early(<0.34)",
               "c1_period": "Q1", "completed": True, "cohort": "oos"}]
    out = S.market_level_event_phase(fills)
    tab = out["markets_fully_completed_by_event_phase"]
    assert tab["ev_late(>=0.67)"] == {"markets_fully_completed": 0, "markets": 1}
    assert tab["ev_early(<0.34)"] == {"markets_fully_completed": 1, "markets": 1}


def test_module_is_structurally_read_only_for_the_venue():
    """No authenticated endpoint, no order path, GET only."""
    src = open(S.__file__, encoding="utf-8").read()
    assert "gateway.polymarket.us" in src
    for banned in ("/portfolio/", "POST", "api_key", "PMUS_API", "signature"):
        assert banned not in src, banned


def test_render_md_and_json_round_trip_on_a_minimal_report():
    rep = {
        "question": "q", "owner_ref": "r", "labels": S.LABELS,
        "frozen_mapping": S.FROZEN_MAPPING,
        "frozen_mapping_sha256_16": S.mapping_hash(),
        "parameters": {"k_cents": 2.0, "gap_s": 300.0},
        "sample": {"markets": 1, "decisions": 1, "in_sample_decisions": 1,
                   "oos_decisions": 0, "fills": 0,
                   "residual_creating_fills": 0, "warning": "small"},
        "causal_audit": {"fields_checked": 1, "mismatches": 0},
        "tte_degeneracy": {"live_r5f": S.live_state_distribution(
            [{"tte_bucket": "tte>10m", "price_bucket": "p45-55c",
              "inventory_bucket": "flat"}]), "why": "because"},
        "clock_diagnostics": {"all": S.clock_diagnostics(
            S.clocks_for_market([_row("m", 0, 0.5)]))},
        "regime_clock_sensitivity": {"k=2.0c": {"episodes": 1,
                                                "phase_share": {"ep<15s": 1.0}}},
        "outcome_linkage": {k: S.outcome_linkage([], []) for k in
                            ("all", "in_sample_EXPLORATORY", "oos_VALIDATION")},
        "event_phase_market_level": {"all": S.market_level_event_phase([])},
        "market_runs": [], "loss_partition_from_ledger": {},
        "realized_usd_venue_sum": "0", "losing_markets": [],
    }
    md = S.render_md(rep)
    assert "sports clock study" in md and S.mapping_hash() in md
    json.dumps(rep, default=str)


# ------------------- partial completion / robustness ----------------------

def test_residual_reduced_is_a_softer_outcome_than_completed():
    """A market that pairs most of its size but never reaches zero must be
    scored `residual_reduced` True and `completed` False -- otherwise a
    well-paired market reads as a total miss."""
    rows = [
        _row("m", 0, 0.50, filled_qty=10.0, residual_after=10.0,
             relation="open"),
        _row("m", 30, 0.50, filled_qty=6.0, residual_after=4.0,
             relation="complete"),
    ]
    out = S.clocks_for_market(rows, k_cents=100.0, gap_s=1e9)
    fs = S.fill_sequences(out)
    assert len(fs) == 2
    assert fs[0]["completed"] is False and fs[0]["residual_reduced"] is True
    assert fs[0]["reduction_latency_s"] == 30.0
    assert fs[1]["completed"] is False and fs[1]["residual_reduced"] is False


def test_outcome_linkage_reports_both_outcomes():
    rows = [
        _row("m", 0, 0.50, filled_qty=10.0, residual_after=10.0),
        _row("m", 30, 0.50, filled_qty=6.0, residual_after=4.0),
    ]
    out = S.clocks_for_market(rows, k_cents=100.0, gap_s=1e9)
    ol = S.outcome_linkage(out, S.fill_sequences(out))
    assert "completion_by_event_phase" in ol
    assert "residual_reduced_by_event_phase" in ol


def test_market_level_table_carries_reduced_fills():
    fills = [{"slug": "m", "c1_event_phase": "ev_late(>=0.67)",
              "c1_period": "S5", "completed": False, "residual_reduced": True,
              "cohort": "oos"}]
    out = S.market_level_event_phase(fills)
    assert out["markets"][0]["reduced_fills"] == 1
    assert out["markets"][0]["completed_fills"] == 0


def test_oos_gate_forbids_pnl_only_promotion():
    names = {c["name"] for c in S.OOS_GATE["must_all_pass"]}
    assert {"sample", "effect", "coherence", "coverage",
            "no_pnl_only_promotion"} <= names
    coh = next(c for c in S.OOS_GATE["must_all_pass"]
               if c["name"] == "coherence")
    assert "fidelity" in coh["requirement"]


def test_shadow_implementation_changes_nothing_behavioural():
    impl = S.SMALLEST_SHADOW_IMPLEMENTATION
    joined = " ".join(impl["changes"]).lower()
    assert "no change to `state`" in joined
    for frozen in ("frozen action model", "safety shell", "fidelity gate"):
        assert any(frozen in x for x in impl["explicitly_not_changed"])
    assert "P&L" in impl["coherence_argument_not_pnl"]


def test_input_manifest_hashes_every_file(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text('{"deploy": []}')
    m = S.input_manifest([str(a)])
    assert m["n_files"] == 1 and m["files"][0]["file"] == "a.txt"
    assert len(m["files"][0]["sha256_16"]) == 16
    a.write_text('{"deploy": [1]}')
    assert S.input_manifest([str(a)])["input_set_sha256_16"] != \
        m["input_set_sha256_16"]


def test_loss_partition_audit_excludes_non_reconciling_markets():
    rep = {"per_market": {
        "ok": {"loss_partition": {
            "partition_check": {"reconciles": True,
                                "residual_contribution_usd": "-2.0",
                                "sum_usd": "-2.0"},
            "partition_of_residual_loss": {
                "execution_queue_failure": "-2.0",
                "faithful_l5_behavior": "0", "market_selection_transfer": "0",
                "remaining_shell_distortion": "0"}}},
        "bad": {"loss_partition": {
            "partition_check": {"reconciles": False,
                                "residual_contribution_usd": "-1.5",
                                "sum_usd": "-7.5"},
            "partition_of_residual_loss": {
                "execution_queue_failure": "-7.5",
                "faithful_l5_behavior": "0", "market_selection_transfer": "0",
                "remaining_shell_distortion": "0"}}}}}
    out = S.loss_partition_audit(rep)
    assert out["reconciling_markets"] == ["ok"]
    assert out["reconciling_totals_usd"]["execution_queue_failure"] == -2.0
    assert out["non_reconciling_markets"][0]["slug"] == "bad"
