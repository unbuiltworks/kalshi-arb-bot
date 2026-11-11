"""SHADOW submit-time freshness gate. Measurement only; no order path."""
from src import l5_submit_freshness as F


def _order(**kw):
    o = {"order_id": kw.pop("oid", "X1"), "stage": kw.pop("stage", "spring_complete_2"),
         "filled_qty": kw.pop("filled_qty", "0"),
         "end_reason": kw.pop("end_reason", "deadline")}
    o.update(kw)
    return o


def test_age_uses_receipt_not_the_recorders_own_age():
    """The recorder's own age omits transport, which DOMINATES it (p50 6.3s
    against 0.2s live). Using it alone would understate staleness ~30x."""
    o = _order(recorder_book={"authenticated_age_s": 0.3,
                              "cross_shadow_observation": {
                                  "age_at_probe_receipt_s": 9.4,
                                  "probe_transport_delta_s": 9.1}})
    a = F.age_at_submission(o)
    assert a["age_s"] == 9.4
    assert a["basis"] == "age_at_probe_receipt_s"
    assert a["is_lower_bound"] is True


def test_authenticated_age_alone_is_UNKNOWN_never_an_age_at_submission():
    o = _order(recorder_book={"authenticated_age_s": 0.3})
    a = F.age_at_submission(o)
    assert a["age_s"] is None
    assert "transport" in a["unknown_reason"]
    assert F.gate(o)["decision"] == F.UNKNOWN


def test_missing_evidence_is_UNKNOWN_not_imputed_fresh():
    """A row with no age evidence must never be scored as passing."""
    g = F.gate(_order())
    assert g["decision"] == F.UNKNOWN
    assert g["age_s"] is None


def test_gate_blocks_above_and_passes_below_the_declared_invariant():
    assert F.DECLARED_MAX_QUOTE_AGE_S == 5.0
    below = _order(recorder_book={"cross_shadow_observation":
                                  {"age_at_probe_receipt_s": 4.9}})
    above = _order(recorder_book={"cross_shadow_observation":
                                  {"age_at_probe_receipt_s": 5.1}})
    assert F.gate(below)["decision"] == F.PASS
    assert F.gate(above)["decision"] == F.BLOCK
    # exactly at the threshold is NOT stale
    at = _order(recorder_book={"cross_shadow_observation":
                               {"age_at_probe_receipt_s": 5.0}})
    assert F.gate(at)["decision"] == F.PASS


def test_relation_from_stage_when_field_absent():
    assert F.relation_of({"stage": "spring_open_1"}) == "open"
    assert F.relation_of({"stage": "spring_extend_3"}) == "extend"
    assert F.relation_of({"stage": "spring_complete_2"}) == "complete"
    assert F.relation_of({"relation": "extend", "stage": "x"}) == "extend"


def test_outcome_rejected_beats_filled_qty_zero():
    assert F.outcome_of(_order(end_reason="rejected:ORDER_REJECTED_ZERO_FILL:"
                                          "A:ORDER_STATE_REJECTED")) == "rejected"
    assert F.outcome_of(_order(filled_qty="2.4", end_reason="filled")) == "filled"
    assert F.outcome_of(_order()) == "no_fill_cancelled"


def test_replay_separates_prevented_from_suppressed_from_no_fill():
    """A blocked attempt that never filled forgoes NOTHING; only a blocked
    FILL is a cost. Collapsing the two would overstate the gate's price."""
    stale = {"cross_shadow_observation": {"age_at_probe_receipt_s": 12.0}}
    fresh = {"cross_shadow_observation": {"age_at_probe_receipt_s": 1.0}}
    rows = [
        _order(oid="r1", recorder_book=stale,
               end_reason="rejected:ORDER_REJECTED_ZERO_FILL:r1:X"),
        _order(oid="f1", recorder_book=stale, filled_qty="2", end_reason="filled"),
        _order(oid="n1", recorder_book=stale),
        _order(oid="n2", recorder_book=stale),
        _order(oid="f2", recorder_book=fresh, filled_qty="2", end_reason="filled"),
    ]
    rep = F.replay(rows, thresholds=(5.0,))
    b = rep["thresholds"]["5.0"]["overall"]
    assert b["block"] == 4 and b["pass"] == 1
    assert b["prevented"] == 1
    assert b["suppressed_fills"] == 1
    assert b["blocked_no_fill"] == 2
    assert b["passed_fills"] == 1
    assert rep["evaluable"] == 5 and rep["unknown_no_age_evidence"] == 0


def test_replay_counts_unknown_rows_separately_from_pass():
    rows = [_order(oid="u1"), _order(oid="u2"),
            _order(oid="p1", recorder_book={"cross_shadow_observation":
                                            {"age_at_probe_receipt_s": 1.0}})]
    rep = F.replay(rows, thresholds=(5.0,))
    assert rep["unknown_no_age_evidence"] == 2
    assert rep["evaluable"] == 1
    b = rep["thresholds"]["5.0"]["overall"]
    assert b["unknown"] == 2 and b["pass"] == 1 and b["block"] == 0


def test_dedupe_collapses_by_order_id_and_preserves_raw_count():
    """The emitter prints each rejection 3x. Dedupe is READ-SIDE: history is
    not rewritten, and a rate computed from raw rows is inflated."""
    raw = ([{"order_id": "A"}] * 3) + ([{"order_id": "B"}] * 3) + [{"order_id": "C"}]
    d = F.dedupe_rejections(raw)
    assert d["raw_rows"] == 7
    assert d["distinct_order_ids"] == 3
    assert d["duplicate_rows_dropped"] == 4
    assert d["emissions_per_order"] == [1, 3]


def test_dedupe_ignores_rows_without_an_order_id():
    d = F.dedupe_rejections([{"order_id": "A"}, {"no_id": 1}])
    assert d["distinct_order_ids"] == 1 and d["raw_rows"] == 2


def test_thresholds_bracket_the_declared_invariant_on_both_sides():
    """The sweep must not be one-sided: a gate only ever loosened, or only
    ever tightened, cannot answer whether 5.0 is the right place to stand."""
    assert min(F.THRESHOLDS_S) < F.DECLARED_MAX_QUOTE_AGE_S
    assert max(F.THRESHOLDS_S) > F.DECLARED_MAX_QUOTE_AGE_S
    assert F.DECLARED_MAX_QUOTE_AGE_S in F.THRESHOLDS_S
