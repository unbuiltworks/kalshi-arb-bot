"""Regression tests for read-only spring-watch completion visibility."""
from src import pmus_ws_evidence_recorder_enriched as enriched


def _row(counts):
    return {
        "bid": "0.50",
        "ask": "0.52",
        "bid_qty": "10",
        "ask_qty": "8",
        "both_sides_served": True,
        "trades_1h": 20,
        "two_sided_trades": True,
        "gate_status": "SHADOW_CANDIDATE",
        "slot_counts_theoretical": counts,
        "l5_shadow": {"off_distribution": False},
    }


def test_spring_watch_surfaces_repeated_completion():
    rows = {"candidate": _row({"MATCHED": 2, "NO_FILL": 1})}
    out = enriched.spring_watch(rows, l5_alloc={"candidate"})
    assert len(out) == 1
    row = out[0]
    assert row["slot_counts_theoretical"] == {"MATCHED": 2, "NO_FILL": 1}
    assert row["matched_slots"] == 2
    assert row["one_sided_holds"] == 0
    assert row["no_fill_slots"] == 1
    assert row["repeated_completion"] is True
    assert row["l5_allocated"] is True


def test_spring_watch_does_not_promote_one_sided_history():
    rows = {
        "candidate": _row({"MATCHED": 1, "ONE_SIDED_HOLD": 2, "NO_FILL": 3})
    }
    out = enriched.spring_watch(rows)
    assert len(out) == 1
    row = out[0]
    assert row["matched_slots"] == 1
    assert row["one_sided_holds"] == 2
    assert row["no_fill_slots"] == 3
    assert row["repeated_completion"] is False
    # Advisory visibility must not rewrite the canonical gate verdict.
    assert row["gate_status"] == "SHADOW_CANDIDATE"


def test_spring_watch_preserves_band_and_two_sided_prefilter():
    outside = _row({"MATCHED": 5})
    outside["bid"] = "0.60"
    one_sided = _row({"MATCHED": 5})
    one_sided["both_sides_served"] = False
    assert enriched.spring_watch({"outside": outside, "one": one_sided}) == []


# ---- probe-evidence fields + advisory status (issue #167 5525966568) ----

def _evidenced_row(matched=3, missing=(), off=False):
    r = _row({"MATCHED": matched})
    r["spring_slot_counts_theoretical"] = {"SPRING_MATCHED": matched,
                                           "SPRING_NO_FILL": 1}
    r["spring_evaluable_slots"] = matched + 1
    r["spring_matched_fraction"] = round(matched / (matched + 1), 4)
    r["spring_latest_slot"] = {"status": "SPRING_MATCHED", "age_s": 42.0}
    r["spring_completion_latency_s"] = {"p50": 30.0, "p95": 90.0,
                                        "n": matched}
    r["spring_pair_costs"] = ["0.96"] * matched
    r["spring_le_096_completions"] = matched
    r["spring_fee_safe_completions"] = matched + 1
    r["spring_evidence_missing"] = list(missing)
    r["l5_shadow"] = {"off_distribution": off,
                      "v2": {"off_distribution": off}}
    return r


def test_probe_evidence_ready_for_review_with_repeated_completions():
    rows = {"cand": _evidenced_row(matched=3)}
    out = enriched.spring_watch(rows, l5_alloc={"cand"})
    row = out[0]
    # every requested evidence field rides on the row, verbatim
    assert row["spring_evaluable_slots"] == 4
    assert row["spring_matched_fraction"] == 0.75
    assert row["spring_latest_slot"]["status"] == "SPRING_MATCHED"
    assert row["spring_completion_latency_s"]["p95"] == 90.0
    assert row["spring_pair_costs"] == ["0.96"] * 3
    assert row["spring_fee_safe_completions"] == 4
    pe = row["probe_evidence"]
    assert pe["status"] == "READY_FOR_REVIEW"
    assert pe["reasons"] == []
    assert pe["min_completions_required"] == 3
    assert "ADVISORY ONLY" in pe["labels"]
    # advisory only: the canonical gate verdict is untouched
    assert row["gate_status"] == "SHADOW_CANDIDATE"


def test_probe_evidence_insufficient_reports_reasons_and_raw_counts():
    thin = _evidenced_row(matched=1)
    thin["trades_1h"] = 4
    thin["two_sided_trades"] = False
    rows = {"thin": thin}
    row = enriched.spring_watch(rows)[0]
    pe = row["probe_evidence"]
    assert pe["status"] == "INSUFFICIENT_EVIDENCE"
    assert "thin_flow:4" in pe["reasons"]
    assert "one_directional_flow" in pe["reasons"]
    assert "insufficient_completions:1<3" in pe["reasons"]
    assert row["spring_matched_slots"] == 1        # raw counts still shown


def test_probe_evidence_min_completions_is_configurable(monkeypatch):
    monkeypatch.setenv(enriched.PROBE_MIN_ENV, "5")
    row = enriched.spring_watch({"c": _evidenced_row(matched=4)})[0]
    pe = row["probe_evidence"]
    assert pe["status"] == "INSUFFICIENT_EVIDENCE"
    assert "insufficient_completions:4<5" in pe["reasons"]
    assert pe["min_completions_required"] == 5


def test_probe_evidence_surfaces_missingness_and_unknown_l5():
    bare = _row({})
    row = enriched.spring_watch({"bare": bare})[0]
    pe = row["probe_evidence"]
    assert pe["status"] == "INSUFFICIENT_EVIDENCE"
    assert "l5_state_unknown" not in pe["reasons"]  # v1 off=False known
    unknown = _row({})
    unknown["l5_shadow"] = {}
    unknown["spring_evidence_missing"] = ["no_bbo_history"]
    row2 = enriched.spring_watch({"u": unknown})[0]
    assert "l5_state_unknown" in row2["probe_evidence"]["reasons"]
    assert "no_bbo_history" in row2["probe_evidence"]["reasons"]
