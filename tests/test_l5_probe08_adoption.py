"""The line that killed Probe 08's report, and the class of mistake it is.

Probes 04 through 07 all closed out with nothing resting, so `open_orders`
was empty every time and the venue open-order ADOPTION path never once ran.
Probe 08's treatment is the first thing to leave an order resting at the
close -- and the line that read its price had been written against a shape
nobody had ever seen:

    "price": str(o.get("price", mine.get("price", 0)))

`o.get(k, default)` returns the default only when the key is ABSENT, so a
present-but-null price became the STRING "None"; a wrapped price became a
dict repr. Either way `Decimal` refused it and the report died AFTER the
close-out had already run.

This is the third time in this programme: Probe 05's KeyError, Probe 06's
missing side field, and now this. The rule these tests enforce is the one
that was learned each time -- read the venue by NAME, and when the name is
not there, fail closed and CAPTURE, never guess and never crash.
"""
from __future__ import annotations

import pathlib
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX

MARKET = "cpc-btc-updown-15m-2026-09-26-1130z"


def store():
    return EX.InventoryStore(str(pathlib.Path(tempfile.mkdtemp()) / "i.json"))


# ----------------------------------------------- the exact crashing input

@pytest.mark.parametrize("bad", ["None", "", "null", "n/a", {}, [],
                                 {"nope": 1}, True, False])
def test_an_unreadable_price_is_none_not_an_exception(bad):
    assert EX.venue_decimal(bad) is None


@pytest.mark.parametrize("raw,want", [
    ("0.66", "0.66"), (0.5, "0.5"), (D("0.71"), "0.71"),
    ({"value": "0.66"}, "0.66"), ({"amount": "0.34"}, "0.34"),
    ({"value": {"value": "0.2"}}, "0.2"),
])
def test_a_readable_price_is_read(raw, want):
    assert EX.venue_decimal(raw) == D(want)


def test_the_present_but_null_price_that_crashed_probe_08():
    """`o.get("price", fallback)` never reached the fallback for this row."""
    row = {"client_order_id": "c1", "price": None}
    assert row.get("price", "0") is None, "the bug, restated"
    assert EX.venue_order_price(row) is None, "and now it is caught"


def test_a_wrapped_price_is_unwrapped_by_name():
    assert EX.venue_order_price({"price": {"value": "0.66"}}) == D("0.66")


def test_price_is_taken_from_the_first_name_that_yields_a_number():
    assert EX.venue_order_price({"price": None, "limitPrice": "0.7"}) == D("0.7")


# -------------------------------------- a risk metric must never raise

def test_outstanding_does_not_raise_on_the_crashing_record():
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "UP", "price": "None",
               "remaining": "2"}}
    assert st.outstanding_by_market()[MARKET] == D(2)


def test_the_five_dollar_gate_does_not_raise_on_it_either():
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "UP", "price": "None",
               "remaining": "2"}}
    assert st.risk_exposure_usd(MARKET) == D(2)


def test_outstanding_residual_does_not_raise_on_it():
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "DOWN", "price": None,
               "remaining": "3"}}
    assert st.outstanding_residual(MARKET) == D(-3)


# ------------------------------- and must never read unknown as FREE

def test_an_unreadable_price_is_priced_at_worst_case_not_at_zero():
    """`or 0` handed the gate a position that looked costless."""
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "UP", "price": None,
               "remaining": "4"}}
    assert st.risk_exposure_usd(MARKET) == D(4) == D(4) * EX.UNKNOWN_PRICE_WORST_CASE
    assert st.risk_exposure_usd(MARKET) > D(0)


def test_worst_case_is_a_dollar_because_a_share_cannot_settle_above_one():
    assert EX.UNKNOWN_PRICE_WORST_CASE == D(1)


def test_an_unreadable_short_is_also_priced_at_worst_case():
    """On BOTH legs the higher price is the worse case."""
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "DOWN", "price": None,
               "remaining": "2"}}
    known = store()
    known.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "DOWN", "price": "0.30",
               "remaining": "2"}}
    assert st.risk_exposure_usd(MARKET) >= known.risk_exposure_usd(MARKET)


def test_a_known_price_is_still_priced_at_that_price():
    st = store()
    st.state["live_orders"] = {
        "c1": {"market": MARKET, "side": "UP", "price": "0.60",
               "remaining": "2"}}
    assert st.risk_exposure_usd(MARKET) == D("1.20")


# ------------------------------------------- adoption records the shape

def venue_order(**kw):
    row = {"client_order_id": "v1", "market": MARKET, "side": "UP",
           "remaining_qty": "1.5"}
    row.update(kw)
    return row


def test_adopting_a_readable_order_keeps_its_price():
    st = store()
    st.reconcile_from_venue([], [venue_order(price="0.66")])
    assert st.state["live_orders"]["v1"]["price"] == "0.66"
    assert st.state["live_orders"]["v1"]["price_unreadable"] is False


def test_adopting_a_wrapped_price_unwraps_it():
    st = store()
    st.reconcile_from_venue([], [venue_order(price={"value": "0.66"})])
    assert st.state["live_orders"]["v1"]["price"] == "0.66"


def test_adopting_an_unreadable_price_marks_it_and_captures_the_keys():
    """Fail closed and CAPTURE. Probe 06's lesson, applied to orders."""
    st = store()
    st.reconcile_from_venue([], [venue_order(price=None, weirdField=1)])
    o = st.state["live_orders"]["v1"]
    assert o["price"] is None, "never the string 'None' again"
    assert o["price_unreadable"] is True
    assert "weirdField" in o["price_row_keys"], "the real shape is recorded"


def test_an_adopted_unreadable_order_still_consumes_the_envelope():
    st = store()
    st.reconcile_from_venue([], [venue_order(price=None)])
    assert st.risk_exposure_usd(MARKET) == D("1.5")


def test_adoption_falls_back_to_our_own_price_when_the_venue_omits_it():
    st = store()
    st.record_submit("v1", {"market": MARKET, "side": "UP",
                            "price": D("0.64"), "qty": D("1.5")})
    st.reconcile_from_venue([], [venue_order()])
    assert st.state["live_orders"]["v1"]["price"] == "0.64"
    assert st.state["live_orders"]["v1"]["price_unreadable"] is False


def test_our_own_stored_none_is_not_trusted_as_a_fallback():
    """record_submit stringifies, so a priceless intent stored "None"."""
    st = store()
    st.record_submit("v1", {"market": MARKET, "side": "UP",
                            "price": None, "qty": D("1.5")})
    assert st.state["live_orders"]["v1"]["price"] == "None", "the latent bug"
    st.reconcile_from_venue([], [venue_order()])
    assert st.state["live_orders"]["v1"]["price"] is None
    assert st.state["live_orders"]["v1"]["price_unreadable"] is True


def test_report_time_reconciliation_survives_a_resting_order():
    """The whole Probe 08 failure, end to end: adopt, then read."""
    st = store()
    st.reconcile_from_venue([], [venue_order(price=None)])
    st.outstanding_by_market()
    st.risk_exposure_usd(MARKET)
    st.outstanding_residual(MARKET)
    st.gross_inventory(MARKET)


# ------------------------------- reading the evidence without changing it

def test_recovery_reads_one_journal_and_writes_another(tmp_path, monkeypatch):
    """Probe 08's journal is the only record of a window whose report died.

    Recovery appends its own row to the journal it holds, so extracting the
    evidence by pointing L5_SESSION_JOURNAL at it would modify the evidence
    in the act of reading it.
    """
    import scripts.l5_pmus_live_oneshot as ONE

    evidence = tmp_path / "probe08b_journal.jsonl"
    evidence.write_text(
        '{"kind": "action", "market": "%s", "action_key": "a1",'
        ' "L5_NATIVE": {"side": "UP"}, "PMUS_TRANSLATION": {},'
        ' "INVENTORY_CLASS": {"inventory_class": "FLAT_OPEN",'
        ' "residual_before": "0"}}\n' % MARKET)
    before = evidence.read_bytes()

    out = ONE.recover(http=object(), state_path=str(tmp_path / "inv.json"),
                      journal_path=str(tmp_path / "recovery.jsonl"),
                      read_path=str(evidence), market=MARKET)

    assert out["journal"]["records"] == 1
    assert out["journal"]["read_only_source"] is True
    assert evidence.read_bytes() == before, "the evidence was appended to"
    assert (tmp_path / "recovery.jsonl").exists(), "the row went elsewhere"


def test_the_extracted_action_carries_its_inventory_class(tmp_path):
    import scripts.l5_pmus_live_oneshot as ONE

    evidence = tmp_path / "j.jsonl"
    evidence.write_text(
        '{"kind": "action", "market": "%s", "action_key": "a1",'
        ' "L5_NATIVE": {"side": "DOWN"}, "PMUS_TRANSLATION": {},'
        ' "INVENTORY_CLASS": {"inventory_class": "REDUCE_RESIDUAL",'
        ' "residual_before": "2.25"}}\n' % MARKET)
    out = ONE.recover(http=object(), state_path=str(tmp_path / "inv.json"),
                      journal_path=str(tmp_path / "r.jsonl"),
                      read_path=str(evidence), market=MARKET)
    row = out["action_table"][0]
    assert row["inventory_class"] == "REDUCE_RESIDUAL"
    assert row["residual_before"] == "2.25"


def test_without_the_override_it_reads_the_journal_it_writes(tmp_path,
                                                             monkeypatch):
    import scripts.l5_pmus_live_oneshot as ONE
    monkeypatch.delenv(ONE.RECOVER_JOURNAL_ENV, raising=False)
    j = tmp_path / "j.jsonl"
    j.write_text("")
    out = ONE.recover(http=object(), state_path=str(tmp_path / "inv.json"),
                      journal_path=str(j), market=MARKET)
    assert out["journal"]["read_only_source"] is False
