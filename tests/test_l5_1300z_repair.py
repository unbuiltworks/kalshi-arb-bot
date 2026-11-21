"""The 1300z repair: book what the venue says was bought, once, and only
onto the book it was written for.

The automatic path booked a zero fill from an incoherent row, leaving
positions 1300z|UP = 0.9000 with leg_cost 0 and fill_count 0 -- quantity with
no cost, which QUANTITY_WITHOUT_COST refused to settle 91+ times. The venue's
own order rows say 1.80 UP was bought for $0.54000 and settled worthless.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_pmus_live_runner as R

MKT = "cpc-btc-updown-15m-2026-09-27-1300z"
ENTRY = next(r for r in R.LEDGER_REPAIRS
             if r["id"] == "1300z-incoherent-resolver-zero-fill")


def broken(tmp_path):
    """The book exactly as the defect left it."""
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["positions"]["%s|UP" % MKT] = "0.9000"
    st.state.setdefault("leg_cost", {})["%s|UP" % MKT] = "0"
    st.save()
    return st


def test_the_broken_book_is_what_refused_to_settle(tmp_path):
    st = broken(tmp_path)
    v = st.book_violations(MKT)
    assert any(x["violation"] == "QUANTITY_WITHOUT_COST" for x in v)


def test_the_repair_books_the_venue_truth(tmp_path):
    st = broken(tmp_path)
    out = R.repair_ledger_from_venue(st)
    assert out and len(out["repairs"]) == 1
    assert st.residual(MKT) == D("1.80")
    assert st.leg_cost(MKT)["UP"] == D("0.54000")
    assert st.book_violations(MKT) == []


def test_the_repair_is_idempotent_across_restart(tmp_path):
    st = broken(tmp_path)
    R.repair_ledger_from_venue(st)
    first = (st.residual(MKT), st.leg_cost(MKT)["UP"], st.realized_today())
    again = R.repair_ledger_from_venue(EX.InventoryStore(str(tmp_path / "inv.json")))
    assert again is None, "the repair ran twice"
    st2 = EX.InventoryStore(str(tmp_path / "inv.json"))
    assert (st2.residual(MKT), st2.leg_cost(MKT)["UP"], st2.realized_today()) == first


def test_it_refuses_a_book_it_was_not_written_for(tmp_path):
    """A repair applied to a book that has moved on is a second corruption."""
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["positions"]["%s|UP" % MKT] = "0.5000"      # not what we saw
    st.save()
    assert R.repair_ledger_from_venue(st) is None
    assert st.residual(MKT) == D("0.5000")
    assert "1300z-incoherent-resolver-zero-fill" not in st.state.get(
        "ledger_repairs_applied", [])


def test_it_refuses_a_clean_book(tmp_path):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    assert R.repair_ledger_from_venue(st) is None
    assert st.residual(MKT) == D(0)


def test_the_repair_does_not_touch_realized(tmp_path):
    """The loss is posted by the ordinary settlement sweep, not by the repair."""
    st = broken(tmp_path)
    before = st.realized_today()
    R.repair_ledger_from_venue(st)
    assert st.realized_today() == before


def test_settling_the_repaired_book_posts_exactly_minus_54c(tmp_path):
    """settlementPx 0.0000 -> UP redeemed nothing."""
    st = broken(tmp_path)
    R.repair_ledger_from_venue(st)
    before = st.realized_today()
    res = st.retire_settled_market(MKT, yes_value=D("0"))
    assert D(str(res["settlement_pnl"])) == D("-0.54000")
    assert D(str(res["residual_qty"])) == D("1.80")
    assert res["residual_side"] == "UP"
    assert st.realized_today() == before + D("-0.54000")


def test_the_audit_trail_survives(tmp_path):
    st = broken(tmp_path)
    R.repair_ledger_from_venue(st)
    log = st.state.get("cost_reconstructions") or []
    assert len(log) == 1
    assert "CREX7ZDP2YC6" in log[0]["source"] and "CREWYQ75RYCA" in log[0]["source"]
    assert "original_bad_record" in ENTRY["evidence"]
    assert "0.9000" in ENTRY["evidence"]["original_bad_record"]


def test_the_evidence_names_its_source_and_the_cash_anchor():
    e = ENTRY["evidence"]
    assert e["source"].startswith("/v1/order")
    assert "60.59505" in e["cash_anchor"] and "60.05505" in e["cash_anchor"]
    assert e["api_key_fingerprint"] == "d51b44a3fa83"


def test_the_booked_numbers_are_the_venue_rows():
    assert ENTRY["qty"] == D("1.80")
    assert ENTRY["cost"] == D("0.9") * D("0.31") + D("0.9") * D("0.29")
