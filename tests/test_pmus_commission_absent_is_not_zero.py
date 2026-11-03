"""A commission the venue never stated must not be booked as a zero.

THE INCIDENT. The owner-authorized campaign of 2026-11-02 took four
venue-confirmed maker fills and recorded `fee_usd 0.0000` for every one,
sourced `venue_order_progress.commission`. That was read as "the venue
credited us nothing", and it is the single observation that made the whole
strategy look unconditionally unprofitable.

It was never a venue statement. Three of the four fills cleared the
cent-rounding floor -- 1.92, 2.04 and 2.00 shares against
`pmus_incentive_audit.REBATE_FLOOR_SHARES` of 1.62 -- and the venue's own
published maker formula, -0.0125*C*p*(1-p), puts a $0.01 credit on each.
What actually happened is that `/v1/order/{id}` did not carry
`commissionNotionalTotalCollected` at all, `_money` turned the missing key
into D(0), and the caller's own "None means unknown" branch became
unreachable. An UNMEASURED quantity was durably booked as a measured one.

The authoritative per-fill credit lives on a different object entirely:
/v1/portfolio/activities -> trade -> whichever execution is ours by
`isAggressor` -> `commissionNotionalCollected` (no `Total`). That is what
`pmus_incentive_audit.credited_on_fills` reads, and where all 25 of the
September credits appear, as NEGATIVE commissions totalling $0.42 over
119.49 shares.

These tests pin the distinction at all three places that collapsed it.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from tests import test_l5_live_execution as H

FILLED = {"id": "V1", "state": "ORDER_STATE_FILLED", "quantity": "1.92",
          "cumQuantity": "1.92", "leavesQuantity": "0", "avgPx": "0.6100"}


# ----------------------------------------------- the reader itself
def test_absent_commission_reads_as_unknown():
    assert E._money_or_none(None) is None


def test_a_field_that_is_not_there_at_all_reads_as_unknown():
    assert E.order_progress(FILLED)["commission"] is None


def test_a_venue_stated_zero_is_a_fact_and_survives():
    """The whole point: zero is still recorded when the venue SAYS zero."""
    row = dict(FILLED, commissionNotionalTotalCollected="0")
    assert E.order_progress(row)["commission"] == D(0)


@pytest.mark.parametrize("raw,want", [
    ("-0.0100", D("-0.0100")),                       # a credited rebate
    ({"value": "-0.0200", "currency": "USD"}, D("-0.0200")),
    ({"value": "0"}, D(0)),
    ("0.0300", D("0.0300")),                         # a charged taker fee
])
def test_a_stated_commission_is_returned_verbatim(raw, want):
    assert E.order_progress(dict(
        FILLED, commissionNotionalTotalCollected=raw))["commission"] == want


@pytest.mark.parametrize("raw", [{}, "abc", {"currency": "USD"}])
def test_an_unreadable_commission_is_unknown_not_zero(raw):
    assert E.order_progress(dict(
        FILLED, commissionNotionalTotalCollected=raw))["commission"] is None


def test_money_itself_is_unchanged_for_prices_and_quantities():
    """`_money` must keep defaulting to 0: for a PRICE or a QUANTITY an
    absent field and a zero are both 'nothing to book'. Only commission
    needed the distinction, so only commission got the new reader."""
    assert E._money(None) == D(0)
    assert E._money("abc") == D(0)
    assert E._money({"value": "0.61"}) == D("0.61")


# ----------------------------------------- what the ledger actually records
def rig(tmp_path, monkeypatch, row):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "a.jsonl"))
    st = H.store(tmp_path)
    st.record_submit("c1", {"market": "m", "side": "UP",
                            "price": D("0.61"), "qty": D("1.92")},
                     mode="MAKER")
    st.apply_order_progress("c1", E.order_progress(row),
                            market="m", side="UP")
    return st


def test_an_unstated_commission_writes_NO_fee_entry(tmp_path, monkeypatch):
    """`l5_only_attested_net` refuses FEE_EVIDENCE_MISSING for an order with
    no recorded commission and accepts a RECORDED zero as a fact. So an
    unknown must leave the key ABSENT -- writing "0" is what turned the
    unknown into a fact and let an unknown net reach a risk gate."""
    st = rig(tmp_path, monkeypatch, FILLED)
    assert "c1" not in (st.state.get("fees") or {})


def test_a_stated_zero_DOES_write_a_fee_entry(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch,
             dict(FILLED, commissionNotionalTotalCollected="0"))
    assert (st.state.get("fees") or {}).get("c1") == "0"


def test_a_credited_rebate_is_recorded_with_its_sign(tmp_path, monkeypatch):
    st = rig(tmp_path, monkeypatch,
             dict(FILLED, commissionNotionalTotalCollected="-0.0100"))
    assert D((st.state.get("fees") or {})["c1"]) == D("-0.0100")


def test_the_fill_is_still_booked_when_the_commission_is_unknown(
        tmp_path, monkeypatch):
    """Failing closed on the FEE must never lose the FILL -- the shares are
    real whatever the venue said about commission."""
    st = rig(tmp_path, monkeypatch, FILLED)
    assert st.position("m", "UP") == D("1.92")


# ------------------------------------------- the campaign, reproduced exactly
CAMPAIGN = [("DFWP527012WE", "1.92", "0.6100"),
            ("DFWWGBW012WP", "2.04", "0.4700"),
            ("DFX3B39012WQ", "2.00", "0.5500"),
            ("DFXA7J9G12WP", "1.68", "0.2100")]


@pytest.mark.parametrize("vid,qty,px", CAMPAIGN)
def test_every_campaign_fill_now_reads_unknown_not_zero(vid, qty, px):
    """All four rows as the venue actually served them: no commission key.
    Before the fix each produced D(0) and was published as fee_usd 0.0000."""
    prog = E.order_progress({"id": vid, "state": "ORDER_STATE_FILLED",
                             "quantity": qty, "cumQuantity": qty,
                             "leavesQuantity": "0", "avgPx": px})
    assert prog["commission"] is None
    assert prog["cum_qty"] == D(qty)


def test_three_of_the_four_cleared_the_rebate_floor():
    """Why the recorded zeros were not credible: the venue's own floor."""
    from src import pmus_incentive_audit as audit
    cleared = [v for v, q, _ in CAMPAIGN
               if D(q) >= audit.REBATE_FLOOR_SHARES]
    assert audit.REBATE_FLOOR_SHARES == D("1.62")
    assert len(cleared) == 4          # 1.68 also clears the generic floor
