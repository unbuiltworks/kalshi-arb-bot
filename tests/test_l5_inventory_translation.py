"""The inventory translation must be derived from L5 alone, must be causal,
and must never let a unit mismatch or a shell ceiling be reported as a
behavioural finding (OWNER #167 5583906276 item 3)."""
from __future__ import annotations

import gzip
import io
import csv

from src import l5_inventory_translation as inv


def _l5_rows(spec):
    """spec: [(window, up_qty, up_spend, dn_qty, dn_spend), ...] cumulative
    AFTER each trade, in order."""
    buf = io.StringIO()
    cols = ["window_id", "timestamp", "cum_up_qty", "cum_up_spend",
            "cum_down_qty", "cum_down_spend", "asset", "duration_minutes"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for i, (wid, uq, us, dq, ds) in enumerate(spec):
        w.writerow({"window_id": wid, "timestamp": 1000 + i,
                    "cum_up_qty": uq, "cum_up_spend": us,
                    "cum_down_qty": dq, "cum_down_spend": ds,
                    "asset": "BTC", "duration_minutes": "5"})
    return buf.getvalue()


def _write_csv(tmp_path, text, name="canon.csv.gz"):
    p = tmp_path / name
    with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return str(p)


def test_the_state_a_decision_sees_is_the_one_before_its_own_action(tmp_path):
    """Every row must carry the inventory BEFORE its trade. A row that read
    its own fill would let the model see the future."""
    path = _write_csv(tmp_path, _l5_rows([
        ("w1", 10, 5.0, 0, 0.0),      # first trade: book was empty
        ("w1", 10, 5.0, 4, 2.0),      # second: book was 10 up
        ("w2", 3, 1.5, 0, 0.0),       # new window: back to empty
    ]))
    rows = inv.l5_decision_rows(path)
    assert [r["residual_qty_before"] for r in rows] == [0.0, 10.0, 0.0]
    # and the dollar imbalance is the surplus side's own average cost
    assert rows[1]["imbalance_usd_before"] == 5.0
    assert rows[2]["sequence"] == "w2"


def test_the_capital_scale_never_uses_the_sequence_it_scales(tmp_path):
    """The trailing median is the operator's own history. Using the current
    sequence would leak its outcome into its own decisions."""
    rows = [{"sequence": f"s{i}", "deployed_usd_before": float(i)}
            for i in range(1, 9) for _ in range(2)]
    scale = inv.trailing_scale(rows, window=50, min_sequences=3)
    # the first three sequences have no history and are refused a scale
    assert scale["s1"] is None and scale["s2"] is None and scale["s3"] is None
    # s4 sees exactly s1..s3 -> median(1,2,3) = 2, and NOT its own 4
    assert scale["s4"] == 2.0
    assert scale["s5"] == 2.5


def test_rows_without_a_scale_are_excluded_never_imputed(tmp_path):
    rows = [{"sequence": "s1", "deployed_usd_before": 2.0,
             "imbalance_usd_before": 1.0, "residual_qty_before": 1.0}]
    out = inv.attach_ratio(rows, min_sequences=5)
    assert out[0]["capital_scale_usd"] is None
    assert out[0]["imbalance_ratio"] is None
    assert inv.normalized_bucket(None, [0.1, 0.5, 1.0]) is None


def test_edges_come_from_l5_alone_and_reproduce_its_own_bucket_shares(tmp_path):
    """The translation must not be tunable against our data: the edges are
    quantiles of L5's ratio distribution at L5's own bucket shares."""
    # the windows must VARY: a degenerate tape puts every decision on a
    # handful of tied ratios, and no quantile can split a tie
    spec = []
    for w in range(60):
        g = 1.0 + w * 0.37
        spec += [(f"w{w}", 0.4 * g, 0.2 * g, 0, 0.0),
                 (f"w{w}", 6 * g, 3.0 * g, 0, 0.0),
                 (f"w{w}", 6 * g, 3.0 * g, 2 * g, 1.0 * g),
                 (f"w{w}", 60 * g, 30.0 * g, 2 * g, 1.0 * g)]
    rows = inv.attach_ratio(inv.l5_decision_rows(_write_csv(tmp_path, _l5_rows(spec))))
    spec_edges = inv.derive_ratio_edges(rows)
    assert spec_edges["id"] == "L5_INVENTORY_RATIO_EDGES_v1"
    assert "L5 rows only" in spec_edges["derived_from"]
    assert len(spec_edges["edges"]) == len(inv.BUCKET_LABELS) - 1
    assert spec_edges["sha256_16"] and len(spec_edges["sha256_16"]) == 16
    usable = [r for r in rows if r["imbalance_ratio"] is not None]
    got = inv._shares(inv.normalized_bucket(r["imbalance_ratio"],
                                            spec_edges["edges"]) for r in usable)
    for label, want in spec_edges["l5_absolute_share"].items():
        assert abs(got[label] - want) <= 0.03, (label, got[label], want)


def test_the_flat_key_is_split_by_cause_not_lumped(tmp_path):
    """`flat` has three different causes and only one of them is a scale
    artefact; a single number would hide that."""
    rep = {"per_market": {"m": {"rows": [
        {"state": "p45-55c|tte>10m|flat",
         "inventory_before": {"up": "0", "dn": "0"}},
        {"state": "p45-55c|tte>10m|flat",
         "inventory_before": {"up": "5", "dn": "5"}},
        {"state": "p45-55c|tte>10m|flat",
         "inventory_before": {"up": "5.4", "dn": "5.0"}},
        {"state": "p45-55c|tte>10m|imb10-50",
         "inventory_before": {"up": "30", "dn": "0"}},
    ]}}}
    out = inv.flat_decomposition(rep)
    assert out["n_decisions"] == 4 and out["n_flat"] == 3
    assert out["causes"] == {"empty_book_no_position_yet": 1,
                             "matched_pair_book_flat": 1,
                             "residual_below_one_share": 1}


def test_the_unit_control_restricts_us_to_l5s_own_unit(tmp_path):
    """L5's tape is one row per FILL. Counting our unfilled attempts as flat
    observations would invent a behavioural gap out of a unit mismatch."""
    ours = [{"sequence": "m1", "idx": 0, "residual_qty_before": 0.0},
            {"sequence": "m1", "idx": 1, "residual_qty_before": 0.0},
            {"sequence": "m1", "idx": 2, "residual_qty_before": 4.0},
            {"sequence": "m1", "idx": 3, "residual_qty_before": 4.0}]
    l5 = [{"sequence": "w1", "residual_qty_before": 0.0},
          {"sequence": "w1", "residual_qty_before": 3.0},
          {"sequence": "w1", "residual_qty_before": 6.0},
          {"sequence": "w1", "residual_qty_before": 9.0}]
    out = inv.like_for_like(ours, l5, [("m1", 0), ("m1", 2)])
    assert out["r5f_all_decisions"]["flat_share"] == 0.5
    assert out["r5f_filled_decisions_only"]["n"] == 2
    assert out["r5f_filled_decisions_only"]["flat_share"] == 0.5
    assert out["l5_fills"]["flat_share"] == 0.25
    assert out["fills_per_sequence"] == {"l5": 4.0, "r5f": 2.0}
    assert "no state-key change can manufacture fills" in out["reading"]


def test_the_shell_ceiling_is_reported_as_the_shells_not_the_keys():
    """If the cap makes a bucket unreachable, that is an arithmetic fact
    about the shell and no representation change removes it."""
    ours = [{"sequence": "m", "capital_scale_usd": 2.5, "imbalance_ratio": 0.4,
             "residual_qty_before": 1.0}]
    l5 = [{"sequence": "w", "imbalance_ratio": r, "residual_qty_before": 1.0}
          for r in (0.1, 1.0, 5.0, 9.0)]
    out = inv.reachability(ours, l5, [0.2, 0.8, 2.0], cap_usd=7.5)
    assert out["computable"] is True
    assert out["max_reachable_ratio"] == 3.0
    assert out["highest_reachable_bucket"] == "imb>50"
    assert out["l5_decisions_above_our_ceiling"] == 2
    assert out["l5_share_above_our_ceiling"] == 0.5
    # with no completed sequence there is no scale, and nothing is guessed
    assert inv.reachability([{"sequence": "m", "capital_scale_usd": None}],
                            l5, [0.2])["computable"] is False


def test_the_translated_bucket_keeps_the_frozen_models_own_labels():
    """Mapping B must key the SAME frozen model, so it may change which
    decisions land in a bucket but never invent a new bucket name."""
    edges = [0.1, 0.5, 2.0]
    assert inv.normalized_bucket(0.05, edges) == "flat"
    assert inv.normalized_bucket(0.1, edges) == "imb1-10"
    assert inv.normalized_bucket(1.9, edges) == "imb10-50"
    assert inv.normalized_bucket(2.0, edges) == "imb>50"
    assert set(inv.BUCKET_LABELS) == {"flat", "imb1-10", "imb10-50", "imb>50"}
