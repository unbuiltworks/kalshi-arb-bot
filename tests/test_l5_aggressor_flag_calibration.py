"""Every `venue_order_id_not_ours` verdict depends on isAggressor. Measure it.

THE CLAIM THAT NEEDED A FOUNDATION. With the nesting fix in place the
2026-11-03T16:42:28Z production audit resolved all 76 fills of the 51
unknown-basis legs and returned 75 OWNER_MANUAL / venue_order_id_not_ours
and 1 L5_OWNED. "75 of these fills were somebody else's orders" is a strong
claim about a SHARED account, and it rests entirely on `isAggressor`
selecting our execution out of the row's two.

A POSITIVE match needs no such assumption: an id in our own tracked set
cannot belong to the counterparty. A NEGATIVE one needs it completely. So
the assumption is calibrated against rows already known to be ours by the
other, independent route -- a client order id in our provenance -- and the
audit reports the verdict beside the counts instead of letting a reader
assume it.

READ-ONLY, counts only. No identifier, price or quantity is reported.
"""
from __future__ import annotations

from src import l5_cost_basis_audit as CBA


def frow(*, coid=None, agg="VO-A", pas="VO-P", flag=True,
         market="cpc-btc-updown-15m-2026-10-16-0000z"):
    body = {"marketSlug": market, "qty": "1", "price": "0.5", "cost": "0.5",
            "outcomeSide": "OUTCOME_SIDE_YES", "tradeId": "T1"}
    if coid:
        body["clientOrderId"] = coid
    if flag is not None:
        body["isAggressor"] = flag
    if agg is not None:
        body["aggressorExecution"] = {"order": agg}
    if pas is not None:
        body["passiveExecution"] = {"order": pas}
    return {"type": "activity_type_trade", "trade": body}


def cal(rows, *, coids=("l5f-mine",), vids=()):
    return CBA.aggressor_flag_calibration(rows, our_coids=set(coids),
                                          our_venue_orders=set(vids))


def test_the_flag_agreeing_everywhere_reads_CALIBRATED():
    out = cal([frow(coid="l5f-mine", agg="VO-OURS", pas="VO-THEIRS",
                    flag=True)], vids=["VO-OURS"])
    assert out["counts"]["flag_picked_our_side"] == 1
    assert out["counts"].get("flag_picked_the_other_side", 0) == 0
    assert out["verdict"].startswith("CALIBRATED")


def test_a_false_flag_selects_the_passive_execution():
    out = cal([frow(coid="l5f-mine", agg="VO-THEIRS", pas="VO-OURS",
                    flag=False)], vids=["VO-OURS"])
    assert out["counts"]["flag_picked_our_side"] == 1
    assert out["verdict"].startswith("CALIBRATED")


def test_a_fully_inverted_flag_is_named_INVERTED():
    """If this ever fires in production, every negative verdict in the
    audit is unsafe and must not be read as external activity."""
    out = cal([frow(coid="l5f-mine", agg="VO-THEIRS", pas="VO-OURS",
                    flag=True)], vids=["VO-OURS"])
    assert out["counts"]["flag_picked_the_other_side"] == 1
    assert out["verdict"].startswith("INVERTED")
    assert "unsafe" in out["verdict"]


def test_a_mixed_flag_is_named_AMBIGUOUS_with_the_counts():
    rows = [frow(coid="l5f-mine", agg="VO-OURS", pas="VO-X", flag=True),
            frow(coid="l5f-mine", agg="VO-Y", pas="VO-OURS", flag=True)]
    out = cal(rows, vids=["VO-OURS"])
    assert out["decided_rows"] == 2
    assert out["verdict"].startswith("AMBIGUOUS")
    assert "1 of 2" in out["verdict"]


def test_no_decidable_row_is_NOT_CALIBRATED_and_says_what_that_costs():
    """The honest state. It must say the negative verdicts are unverified
    rather than report a silent pass."""
    out = cal([frow(coid="l5f-mine", agg="VO-A", pas="VO-B")],
              vids=["VO-NOT-PRESENT"])
    assert out["decided_rows"] == 0
    assert out["verdict"].startswith("NOT_CALIBRATED")
    assert "UNVERIFIED" in out["verdict"]
    assert out["counts"]["neither_side_in_our_set"] == 1


def test_only_known_ours_rows_calibrate():
    """A row we cannot already vouch for proves nothing about the flag."""
    rows = [frow(coid="someone-else", agg="VO-OURS", flag=True),
            frow(coid=None, agg="VO-OURS", flag=True)]
    out = cal(rows, vids=["VO-OURS"])
    assert out["counts"].get("known_ours_by_client_order_id", 0) == 0
    assert out["decided_rows"] == 0


def test_a_missing_flag_is_counted_and_never_guessed():
    out = cal([frow(coid="l5f-mine", flag=None)], vids=["VO-A"])
    assert out["counts"]["flag_absent_or_unreadable"] == 1
    assert out["decided_rows"] == 0


def test_a_row_with_no_order_id_on_either_execution_is_its_own_count():
    out = cal([frow(coid="l5f-mine", agg=None, pas=None, flag=True)],
              vids=["VO-A"])
    assert out["counts"]["no_order_id_on_either_execution"] == 1


def test_both_sides_ours_is_not_counted_as_agreement():
    """Self-trade or a reused id: it cannot discriminate, so it must not
    inflate the agreement count."""
    out = cal([frow(coid="l5f-mine", agg="VO-1", pas="VO-2", flag=True)],
              vids=["VO-1", "VO-2"])
    assert out["counts"]["both_sides_in_our_set"] == 1
    assert out["decided_rows"] == 0


def test_the_calibration_reports_counts_only():
    out = cal([frow(coid="l5f-mine", agg="VO-OURS", flag=True)],
              vids=["VO-OURS"])
    blob = repr(out)
    for leaked in ("VO-OURS", "VO-THEIRS", "l5f-mine", "0.5"):
        assert leaked not in blob, leaked


def test_the_audit_carries_the_calibration_beside_the_verdicts():
    import inspect
    src = inspect.getsource(CBA.audit)
    assert "aggressor_flag_calibration" in src
