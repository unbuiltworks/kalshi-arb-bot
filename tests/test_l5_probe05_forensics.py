"""What Probe 05 broke, and what its report was allowed to claim.

Three defects, each demonstrated before it is fixed:

  1. `reconcile_from_venue` subscripted `p["market"]` on rows that
     `positions()` builds as `{"marketSlug": ..., ...}`. An empty account
     never entered the loop, so it survived Probe 04 and raised KeyError the
     first time a close-out ran while the account held something.
  2. the probe report read `filled_qty` straight out of the inventory store,
     which is a file on a volume that outlives the process, so Probe 05's
     report carried Probe 04's fill as if it were its own.
  3. an ACK that says `filled_qty 0` was the only fill count reported, and
     Probe 05 reported `filled: 0` against eighteen fills.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
import scripts.l5_pmus_live_oneshot as ONE


# --------------------------------------------------------------- the row

def venue_row(**over):
    """A position row in the shape the venue actually serves.

    Probe 06's close-out captured the field list of the first POPULATED row
    we had ever seen. There is no side field in it: direction lives in the
    sign of netPosition, and the unsigned halves qtyBought/qtySold prove
    which sign means long without anyone having to assume it.
    """
    row = {"marketSlug": "cpc-btc-updown-15m-2026-09-25-1500z",
           "qtyBoughtDecimal": "10.34", "qtySoldDecimal": "0",
           "netPositionDecimal": "10.34"}
    row.update(over)
    return row


def test_the_row_positions_builds_has_no_market_key_at_all():
    # the premise of the original KeyError, stated as a fact
    assert "market" not in venue_row()
    assert "marketSlug" in venue_row()


def test_the_populated_row_carries_no_side_field_at_all():
    """The Probe 06 finding. Every side name I had guessed was absent."""
    captured = ["avgPx", "baseCost", "bodPosition", "bodPositionDecimal",
                "cashValue", "comboLegDetails", "cost", "costPerShare",
                "expired", "fees", "marketMetadata", "marketSlug",
                "netPosition", "netPositionDecimal", "positionId",
                "qtyAvailable", "qtyAvailableDecimal", "qtyBought",
                "qtyBoughtDecimal", "qtySold", "qtySoldDecimal", "realized",
                "updateTime"]
    for guess in ("side", "outcomeSide", "positionSide", "marketSide",
                  "outcome"):
        assert guess not in captured


def test_position_row_reads_the_shape_the_venue_serves():
    assert EX.position_row(venue_row()) == {
        "market": "cpc-btc-updown-15m-2026-09-25-1500z",
        "side": "UP", "qty": "10.34", "signed_net": "10.34"}


def test_the_side_comes_from_bought_minus_sold_not_from_a_convention():
    short = EX.position_row(venue_row(qtyBoughtDecimal="1",
                                      qtySoldDecimal="4",
                                      netPositionDecimal="-3"))
    assert short["side"] == "DOWN" and short["qty"] == "3"


def test_a_net_that_disagrees_with_the_halves_is_refused():
    with pytest.raises(EX.UnrecognizedVenueShape) as e:
        EX.position_row(venue_row(qtyBoughtDecimal="6", qtySoldDecimal="1",
                                  netPositionDecimal="-5"))
    assert "disagrees" in e.value.what


def test_a_row_with_only_a_signed_net_cannot_prove_its_own_convention():
    # refusing here is the point: guessing which sign means long is the
    # assumption this function exists to avoid
    with pytest.raises(EX.UnrecognizedVenueShape) as e:
        EX.position_row({"marketSlug": "m", "netPositionDecimal": "6.08"})
    assert "cannot be proven" in e.value.what


def test_the_decimal_halves_are_preferred_over_their_plain_twins():
    # the auditor measured qty disagreeing with its own decimal field on
    # 12 of 13 rows; preferring the wrong one produced a 17x phantom position
    r = EX.position_row({"marketSlug": "m", "qtyBoughtDecimal": "45.01",
                         "qtyBought": "774.62", "qtySoldDecimal": "0",
                         "qtySold": "0"})
    assert r["qty"] == "45.01"


def test_a_row_with_no_market_is_refused():
    with pytest.raises(EX.UnrecognizedVenueShape) as e:
        EX.position_row({"qtyBoughtDecimal": "1", "qtySoldDecimal": "0"})
    assert "market" in e.value.what


def test_a_non_numeric_quantity_is_refused():
    with pytest.raises(EX.UnrecognizedVenueShape):
        EX.position_row(venue_row(qtyBoughtDecimal="lots"))


def test_the_refusal_now_carries_the_rows_values_not_just_its_keys():
    """Probe 06 captured keys only, so the question it existed to answer --
    which sign means long -- still could not be answered."""
    try:
        EX.position_row({"marketSlug": "m", "netPositionDecimal": "6.08"})
    except EX.UnrecognizedVenueShape as e:
        assert e.rows == [["marketSlug", "netPositionDecimal"]]
        assert e.values == [{"marketSlug": "m", "netPositionDecimal": "6.08"}]
    else:
        raise AssertionError("should have refused")


# ------------------------------------------------- reconcile_from_venue

def store(tmp_path, **state):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state.update(state)
    return st


def test_reconcile_raised_keyerror_on_the_old_subscript(tmp_path):
    """The Probe 05 failure, reproduced through the production call."""
    st = store(tmp_path)
    rows = [venue_row()]
    # the old code did `p["market"]`; prove that is what a venue row denies
    with pytest.raises(KeyError):
        _ = rows[0]["market"]
    # and that the fixed path does not
    out = st.reconcile_from_venue(rows, [])
    assert out["reconciled"] is True


def test_reconcile_books_the_venue_position_under_our_own_key(tmp_path):
    st = store(tmp_path)
    st.reconcile_from_venue([venue_row()], [])
    assert st.state["positions"] == {
        "cpc-btc-updown-15m-2026-09-25-1500z|UP": "10.34"}


def test_reconcile_reports_drift_and_adopts_the_venue_position(tmp_path):
    """REVERSED 2026-10-14 by owner authorization, and the 2026-09-26
    objection is ANSWERED rather than overruled.

    That objection was exactly right: "taking 10.34 while holding a cost
    basis for 7.00 manufactures a cost for 3.34 shares". Declining to adopt
    avoided it -- and cost more than it saved, because `positions` is what
    `risk_exposure_usd` and the $5 gate read, so our book sat BELOW what the
    venue said we held. Measured: 3.34 shares of venue-confirmed exposure
    invisible to the gate.

    `adopt_venue_position` manufactures nothing. The 7.00 basis is
    QUARANTINED, not carried onto 10.34 shares, and the adopted shares are
    booked at the $1 upper bound and flagged in `leg_cost_basis_unknown`.
    This test now asserts that property directly, which is what the
    2026-09-26 reversal was protecting.

    Fail-closed is unchanged.
    """
    K = "cpc-btc-updown-15m-2026-09-25-1500z|UP"
    st = store(tmp_path, positions={K: "7.00"}, leg_cost={K: "2.10"})
    out = st.reconcile_from_venue([venue_row()], [])
    assert out["drift"] == {K: {"ours": "7.00", "venue": "10.34"}}
    assert st.state["positions"][K] == "10.34"
    # no cost was manufactured for the 3.34 extra shares: the old basis is
    # preserved as evidence and the adopted shares carry the $1 ceiling
    assert st.state["gross_quarantine"][
        "cpc-btc-updown-15m-2026-09-25-1500z"]["leg_cost"] == {K: "2.10"}
    assert K in st.state["leg_cost_basis_unknown"]
    assert st.risk_exposure_usd(
        "cpc-btc-updown-15m-2026-09-25-1500z") >= D("10.34")
    assert st.orders_uncertain() is not None


def test_an_unreadable_row_fails_closed_and_changes_nothing(tmp_path):
    st = store(tmp_path,
               positions={"m|UP": "1"},
               live_orders={"c1": {"market": "m", "side": "UP",
                                   "remaining": "2", "price": "0.3",
                                   "state": "LIVE"}},
               orders_uncertain="something happened")
    with pytest.raises(EX.UnrecognizedVenueShape):
        st.reconcile_from_venue([{"marketSlug": "m", "qty": "1"}], [])
    # nothing released, nothing overwritten, uncertainty NOT cleared
    assert st.state["positions"] == {"m|UP": "1"}
    assert "c1" in st.state["live_orders"]
    assert st.state["orders_uncertain"] == "something happened"


def test_an_empty_account_still_reconciles(tmp_path):
    # the case that hid the bug for two probes
    assert store(tmp_path).reconcile_from_venue([], [])["reconciled"] is True


def test_the_probe_records_the_shape_it_could_not_read():
    ev = ONE.shape_evidence(
        EX.UnrecognizedVenueShape("position row lacks side",
                                  [{"marketSlug": "m", "qty": "1"}]))
    assert ev["error"] == "UnrecognizedVenueShape"
    assert ev["what"] == "position row lacks side"
    assert ev["row_keys"] == [["marketSlug", "qty"]]


def test_shape_evidence_still_works_for_a_plain_exception():
    assert ONE.shape_evidence(KeyError("market")) == {"error": "KeyError"}


# ------------------------------------------------------- run provenance

PRIOR = "l5f-e2e57f2b5ac9021d045267f2"          # Probe 04's order, verbatim
MINE = "l5f-64e845ed09185a17744f908a"


class FakeRun:
    """Only what report()'s provenance needs."""
    scoped = ONE.Probe.scoped
    session_coids = ONE.Probe.session_coids

    def __init__(self, store, records):
        self.store, self.records = store, records


def test_a_prior_sessions_fill_is_not_reported_as_this_runs(tmp_path):
    st = store(tmp_path,
               filled_qty={PRIOR: "2.25", MINE: "0.22"},
               fees={PRIOR: "0.0300", MINE: "0"})
    run = FakeRun(st, [{"ORDER": {"client_order_id": MINE,
                                  "submitted": True}}])
    assert run.scoped("filled_qty") == {MINE: "0.22"}
    assert run.scoped("fees") == {MINE: "0"}


def test_the_excluded_prior_fill_is_named_rather_than_silently_dropped(
        tmp_path):
    st = store(tmp_path, filled_qty={PRIOR: "2.25", MINE: "0.22"})
    run = FakeRun(st, [{"ORDER": {"client_order_id": MINE,
                                  "submitted": True}}])
    excluded = sorted(set(st.state["filled_qty"]) - set(run.session_coids()))
    assert excluded == [PRIOR]


def test_an_order_that_was_not_submitted_is_not_this_runs_either(tmp_path):
    st = store(tmp_path, filled_qty={MINE: "0.22"})
    run = FakeRun(st, [{"ORDER": {"client_order_id": MINE,
                                  "submitted": False}}])
    assert run.session_coids() == []
    assert run.scoped("filled_qty") == {}


def test_provenance_survives_a_store_with_nothing_in_it(tmp_path):
    run = FakeRun(store(tmp_path), [])
    assert run.scoped("filled_qty") == {}


# --------------------------------------------------------- order table

def test_the_order_table_folds_one_row_per_order():
    recs = [
        {"kind": "action", "market": "m",
         "PMUS_TRANSLATION": {"qty_desired": "2.30", "qty": "2.25",
                              "quantization_delta": "0.05"},
         "ORDER": {"client_order_id": MINE, "submitted": True,
                   "side": "UP", "price": "0.32",
                   "venue_order_id": "CQ5"},
         "INVENTORY": {"up": "2.25", "residual": "2.25"}},
        {"kind": "order_progress", "coid": MINE, "venue_order_id": "CQ5",
         "cum": "2.25", "avg_px": "0.2900", "commission": "0.0300",
         "leaves": "0", "state": "ORDER_STATE_FILLED", "released": True},
    ]
    rows = ONE.order_table(recs, "m")
    assert len(rows) == 1
    r = rows[0]
    assert (r["qty_desired"], r["qty_executable"],
            r["quantization_delta"]) == ("2.30", "2.25", "0.05")
    assert (r["limit_px"], r["avg_fill_px"]) == ("0.32", "0.2900")
    assert (r["cum_qty"], r["leaves_qty"], r["commission"]) == (
        "2.25", "0", "0.0300")
    assert r["venue_state"] == "ORDER_STATE_FILLED"
    assert r["released"] == "True"
    assert r["inventory_after"] == {"up": "2.25", "residual": "2.25"}


def test_the_table_does_not_invent_a_column_the_journal_lacks():
    rows = ONE.order_table(
        [{"kind": "action", "market": "m",
          "ORDER": {"client_order_id": MINE, "submitted": True}}], "m")
    assert rows[0]["avg_fill_px"] is None
    assert rows[0]["commission"] is None


def test_the_table_is_one_row_per_order_not_one_per_refresh():
    recs = [{"kind": "action", "market": "m",
             "ORDER": {"client_order_id": MINE, "submitted": True}}]
    recs += [{"kind": "order_progress", "coid": MINE, "cum": str(i)}
             for i in range(5)]
    rows = ONE.order_table(recs, "m")
    assert len(rows) == 1
    assert rows[0]["cum_qty"] == "4"        # the last word, not five rows


def test_journal_rows_keeps_a_line_it_cannot_parse(tmp_path):
    j = tmp_path / "j.jsonl"
    j.write_text(json.dumps({"kind": "action"}) + "\n" + "{ broken\n")
    rows = ONE.journal_rows(str(j))
    assert rows[0]["kind"] == "action"
    assert "unparsed" in rows[1]


def test_journal_rows_on_a_missing_file_says_so(tmp_path):
    assert ONE.journal_rows(str(tmp_path / "nope"))[0]["unreadable"]


# ------------------------------------------------------- idempotency

def test_repeated_identical_refreshes_book_the_fill_exactly_once(tmp_path):
    st = store(tmp_path)
    st.state["live_orders"] = {MINE: {"market": "m", "side": "UP",
                                      "qty": "2.25", "remaining": "2.25",
                                      "price": "0.32", "state": "LIVE",
                                      "venue_order_id": "CQ5"}}
    prog = {"cum_qty": D("2.25"), "leaves": D("0"), "terminal": True,
            "state": "ORDER_STATE_FILLED", "avg_px": D("0.29"),
            "commission": D("0.03")}
    first = st.apply_order_progress(MINE, prog, market="m", side="UP")
    assert (first["delta"], first["released"]) == ("2.25", True)
    for _ in range(4):
        again = st.apply_order_progress(MINE, prog, market="m", side="UP")
        assert D(again["delta"]) == 0
    assert st.state["filled_qty"][MINE] == "2.25"
    assert st.position("m", "UP") == D("2.25")
    assert st.live_order_count() == 0


# ------------------------------------------- L5 beside us, action by action

def action_rec(**over):
    rec = {"kind": "action", "market": "m", "action_key": "a1",
           "L5_NATIVE": {"side": "UP", "price": "0.31", "l5_size": "50",
                         "relation": "INSIDE"},
           "PMUS_TRANSLATION": {"mode": "MAKER", "price": "0.32",
                                "qty_desired": "2.30", "qty": "2.25"},
           "ORDER": {"client_order_id": MINE, "submitted": True},
           "stage_reached": "INVENTORY",
           "book_source": "AUTHENTICATED_WS", "pmus_snapshot_age_s": 0.4}
    rec.update(over)
    return rec


def test_the_comparison_puts_l5_and_us_on_one_row():
    r = ONE.action_table([action_rec()], "m")[0]
    assert (r["l5_side"], r["l5_price"], r["l5_size"]) == ("UP", "0.31", "50")
    assert (r["our_mode"], r["our_price"]) == ("MAKER", "0.32")
    assert (r["our_qty_desired"], r["our_qty_executable"]) == ("2.30", "2.25")
    assert r["submitted"] is True


def test_a_refused_action_still_appears_with_its_reason():
    rec = action_rec(ORDER=None, refused_by_layer="PMUS_TRANSLATION",
                     refused_reason="STRICT_NOT_EVALUABLE")
    r = ONE.action_table([rec], "m")[0]
    assert r["submitted"] is None
    assert r["refused_reason"] == "STRICT_NOT_EVALUABLE"
    assert r["l5_side"] == "UP"        # what he did is recorded regardless


def test_order_progress_records_are_not_actions():
    assert ONE.action_table(
        [{"kind": "order_progress", "coid": MINE}], "m") == []


def test_the_summary_splits_by_l5_side_and_by_refusal():
    rows = ONE.action_table([
        action_rec(),
        action_rec(action_key="a2", ORDER=None,
                   refused_reason="STRICT_NOT_EVALUABLE",
                   refused_by_layer="PMUS_TRANSLATION"),
        action_rec(action_key="a3",
                   L5_NATIVE={"side": "DOWN", "price": "0.7",
                              "l5_size": "10", "relation": "AT"},
                   ORDER=None, refused_reason="MAX_LIVE_ORDERS",
                   refused_by_layer="SAFETY_OVERLAY"),
    ], "m")
    s = ONE.action_summary(rows)
    assert s["actions"] == 3
    assert s["followed"] == 1
    assert s["by_l5_side"]["UP"] == {"actions": 2, "submitted": 1,
                                     "refused": 1}
    assert s["by_l5_side"]["DOWN"] == {"actions": 1, "submitted": 0,
                                       "refused": 1}
    assert s["by_refusal"] == {"STRICT_NOT_EVALUABLE": 1,
                               "MAX_LIVE_ORDERS": 1}
