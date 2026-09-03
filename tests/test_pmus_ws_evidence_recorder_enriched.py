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
