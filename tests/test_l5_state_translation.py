"""Mapping B may re-express L5's own state at another scale; it may not
invent a state, hide its own coverage failures, or be promoted by anything
other than the pre-registered gate (OWNER #167 5583906276 items 1/4/5)."""
from __future__ import annotations

import gzip
import io
import csv
import json

from src import l5_state_translation as st


def _canonical(tmp_path, rows):
    """rows: [(window, duration_minutes, seconds_to_settlement), ...]"""
    buf = io.StringIO()
    cols = ["window_id", "duration_minutes", "seconds_to_settlement"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for wid, dur, tte in rows:
        w.writerow({"window_id": wid, "duration_minutes": dur,
                    "seconds_to_settlement": tte})
    p = tmp_path / "c.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    return str(p)


def test_post_settlement_trades_are_dropped_not_clamped(tmp_path):
    """A trade after settlement has negative time left. Clamping it to zero
    would invent an observation at the boundary and bias the map."""
    path = _canonical(tmp_path, [
        ("w1", 5, 240), ("w1", 5, 20), ("w1", 5, -1), ("w1", 5, -30),
    ])
    m = st.l5_remaining_fraction_map(path)
    assert m["n_rows"] == 4 and m["n_used"] == 2
    assert m["sha256_16"] and len(m["sha256_16"]) == 16
    assert "NOT a seconds-to-settlement claim" in m["not_a_claim"]


def test_an_unknown_event_position_is_never_translated(tmp_path):
    """Missing sport state must stay missing. A default here would put us
    straight back into the degenerate bucket this work exists to remove."""
    m = st.l5_remaining_fraction_map(_canonical(tmp_path, [("w", 5, 100)]))
    bucket, basis = st.translate_tte(None, m)
    assert bucket is None and basis == "no_event_progress_served"
    # and a remaining fraction L5 never occupied is refused, not guessed
    empty = {"per_bin": {str(i): {"modal_tte_bucket": None}
                         for i in range(st.REMAINING_BINS)}}
    bucket, basis = st.translate_tte(0.5, empty)
    assert bucket is None and "l5_has_no_trades" in basis


def test_event_progress_maps_through_l5s_own_clock(tmp_path):
    """Late in the event -> little remaining -> the bucket L5 keyed when
    little of its own window remained."""
    rows = ([("w1", 5, 10)] * 50            # remaining 0.033 -> tte<30s
            + [("w2", 5, 280)] * 50)        # remaining 0.933 -> tte2-5m
    m = st.l5_remaining_fraction_map(_canonical(tmp_path, rows))
    late, basis_late = st.translate_tte(0.98, m)     # 98% through the event
    early, _ = st.translate_tte(0.08, m)             # barely started
    assert late == "tte<30s" and "event_progress->remaining=0.02" in basis_late
    assert early == "tte2-5m"
    # a remaining fraction L5's own tape never visited stays unmapped
    assert st.translate_tte(0.0, m)[0] is None


def test_the_frozen_model_answers_or_says_it_cannot(tmp_path):
    model = {"base": {"p45-55c|tte2-5m|flat": {"open|q<5": 3, "extend|q5-15": 1}}}
    hit = st.lookup(model, "p45-55c|tte2-5m|flat")
    assert hit["state_n"] == 4 and hit["off_distribution"] is False
    assert hit["top_relation"] == "open"
    assert hit["relation_p"] == {"extend": 0.25, "open": 0.75}
    miss = st.lookup(model, "p45-55c|tte<30s|imb>50")
    assert miss["state_n"] == 0 and miss["off_distribution"] is True
    assert miss["top_relation"] is None and miss["relation_p"] == {}


def test_rows_a_mapping_cannot_key_count_against_it(tmp_path):
    """Dropping the decisions B cannot key would score B on its successes
    only and flatter it exactly where it is weakest."""
    rows = [
        {"A": {"state_n": 10, "off_distribution": False,
               "relation_p": {"open": 1.0}},
         "B": {"state_n": 5, "off_distribution": False,
               "relation_p": {"complete": 1.0}}},
        {"A": {"state_n": 20, "off_distribution": False,
               "relation_p": {"open": 1.0}},
         "B": {"state_n": 0, "off_distribution": True, "relation_p": {},
               "unkeyable": True}},
    ]
    a = st.score_mapping(rows, "A", {"open": 1.0})
    b = st.score_mapping(rows, "B", {"open": 1.0})
    assert a["decisions"] == b["decisions"] == 2
    assert b["unkeyable"] == 1 and b["unkeyable_rate"] == 0.5
    assert b["off_distribution_rate"] == 0.5 and a["off_distribution_rate"] == 0.0
    assert a["relation_distance_to_l5"] == 0.0
    assert b["relation_distance_to_l5"] == 1.0


def test_the_promotion_gate_is_frozen_and_refuses_pnl_as_a_criterion():
    g = st.PROMOTION_GATE
    assert g["sha256_16"] and len(g["sha256_16"]) == 16
    # the hash covers the gate's own content, so a silent edit changes it
    import hashlib
    body = {k: v for k, v in g.items() if k != "sha256_16"}
    assert hashlib.sha256(
        json.dumps(body, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16] == g["sha256_16"]
    assert "NOT an input to promotion" in g["criterion"]
    assert "realized P&L" in g["explicitly_not_a_criterion"]
    assert "counterfactual P&L" in g["explicitly_not_a_criterion"]
    assert g["sample_minimums"]["distinct_leagues"] >= 4
    assert g["sample_minimums"]["forward_decisions_with_event_progress"] >= 400
    assert "revert to mapping A immediately" in g["rollback"]["action"]
    # in-sample decisions may never count toward the minimums
    assert "in-sample decisions do not count" in g["sample_minimums"]["note"]


def test_mapping_a_is_read_verbatim_from_the_log(tmp_path):
    """Scoring A against a recomputed key would let us improve the control
    while grading it; A must be exactly what the live key actually was."""
    rep = {"_selections": [], "per_market": {"m": {"rows": [
        {"idx": 0, "t_utc": "2026-09-08T01:00:00+00:00",
         "state": "p45-55c|tte>10m|flat",
         "inventory_before": {"up": "0", "dn": "0", "spent": "0"},
         "order": {}},
    ]}}}
    model = {"base": {"p45-55c|tte>10m|flat": {"open|q<5": 7}}}
    fmap = {"per_bin": {str(i): {"modal_tte_bucket": "tte2-5m",
                                 "modal_purity": 1.0}
                        for i in range(st.REMAINING_BINS)}}
    rows = st.paired_rows(rep, fmap, [0.1, 0.5, 2.0], model)
    assert rows[0]["key_a"] == "p45-55c|tte>10m|flat"
    assert rows[0]["A"]["state_n"] == 7
    # no event progress was served for this market, so B cannot key it
    assert rows[0]["key_b"] is None and rows[0]["B"]["unkeyable"] is True


def _row(t_utc, slug, a_n, b_n, *, b_unkeyable=False, acted="open"):
    """A row is coherent: a state with no support carries no relation
    distribution, exactly as `lookup` returns it."""
    return {"t_utc": t_utc, "slug": slug, "acted": acted,
            "event_proximity": 0.5,
            "A": {"state_n": a_n, "off_distribution": a_n == 0,
                  "relation_p": {"open": 1.0} if a_n else {}},
            "B": {"state_n": b_n, "off_distribution": b_unkeyable or b_n == 0,
                  "relation_p": ({} if (b_unkeyable or not b_n)
                                 else {"complete": 1.0}),
                  **({"unkeyable": True} if b_unkeyable else {})}}


# L5's own relation mix for these fixtures: `complete` is what L5 does
MODEL_COMPLETE = {"base": {"p45-55c|tte2-5m|flat": {"complete|q<5": 10}}}


def test_decisions_from_before_the_freeze_can_never_count_toward_the_gate():
    """The whole point of pre-registering is that already-seen decisions
    cannot promote the thing they were used to build."""
    rows = [_row("2026-09-08T10:00:00+00:00", "aec-atp-a-b", 200, 50),
            _row("2026-09-08T12:00:00+00:00", "aec-atp-a-b", 200, 50)]
    out = st.forward_ab(rows, {"base": {}},
                        since_utc="2026-09-08T11:07:49+00:00")
    assert out["forward_decisions"] == 1
    assert out["sample_minimums_have"]["distinct_markets"] == 1
    assert out["verdict"] == "NOT_YET_ENOUGH_FORWARD_EVIDENCE"
    assert out["gate_sha256_16"] == st.PROMOTION_GATE["sha256_16"]


def test_the_gate_is_scored_as_written_and_still_only_recommends():
    """Even a clean pass hands the decision to the owner; nothing here
    promotes, and nothing here edits the live key."""
    # A is off-distribution on these states and picks the wrong relation
    # where it has any support at all; B keys a supported state and matches
    # L5's own mix
    rows = [_row(f"2026-09-08T12:00:0{i % 10}+00:00", f"aec-l{i % 5}-x-y{i}",
                 0 if i % 10 else 10, 500) for i in range(500)]
    out = st.forward_ab(rows, MODEL_COMPLETE,
                        since_utc="2026-09-08T11:07:49+00:00")
    assert all(out["sample_minimums_met"].values()), out["sample_minimums_have"]
    assert out["gate_checks"]["state_support"] is True
    assert out["gate_checks"]["unkeyable_under_ceiling"] is True
    assert out["verdict"] == "GATE_MET_AWAITING_OWNER_DECISION"
    assert "never promotes" in out["promotion"]


def test_a_mapping_that_cannot_key_its_rows_fails_the_ceiling():
    rows = [_row(f"2026-09-08T12:00:0{i % 10}+00:00", f"aec-l{i % 5}-x-y{i}",
                 10, 0, b_unkeyable=(i % 2 == 0)) for i in range(500)]
    out = st.forward_ab(rows, {"base": {}},
                        since_utc="2026-09-08T11:07:49+00:00")
    assert out["B"]["unkeyable_rate"] == 0.5
    assert out["gate_checks"]["unkeyable_under_ceiling"] is False
    assert out["verdict"] == "GATE_NOT_MET"
