"""Identity at the venue boundary, and what happens when it is missing.

LIVE 2026-09-27. Two orders adopted from the venue -- CR52P8GZ4YAQ (0.10 UP
@ 0.1300) and CR52QM4S6YBY (0.60 UP @ 0.1400), $0.09700 of real cost, zero
commission -- were booked under the market key "".

Adoption read the venue row's market as `row["market"]`. This venue sends
`marketSlug`. For an order we SUBMITTED there is a local record to fall back
on and the bug stays hidden; for an order we merely ADOPTED there is not, so
the market arrived None. Then resolve_pending_terminal did
`str(row.get("market") or "")`, and `None or ""` is a perfectly good dict
key.

The damage was not the cents. markets_with_exposure() returned [""],
open_markets() returned [""], and MAX_CONCURRENT_MARKETS refused every
window after it -- the executor stopped trading and spent 125 settlement
reads asking the venue about a slug it has never heard of. The $0.09700
belonged to 0115z and was excluded from its settlement, which booked
+0.599500 against an authoritative +0.502500.

These tests assert FINANCIAL STATE, not log text: what is in positions,
leg_cost, committed, realized and the quarantine after each path runs.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

M = "cpc-btc-updown-15m-2026-09-27-0115z"

#: the two orders, exactly as /v1/order returned them
INCIDENT = [
    {"id": "CR52P8GZ4YAQ", "marketSlug": M, "side": "ORDER_SIDE_BUY",
     "outcomeSide": "OUTCOME_SIDE_YES", "intent": "ORDER_INTENT_BUY_LONG",
     "state": "ORDER_STATE_FILLED", "price": {"value": "0.13"},
     "quantity": 0.1, "cumQuantity": 0.1, "leavesQuantity": 0,
     "avgPx": {"value": "0.1300"},
     "commissionNotionalTotalCollected": {"value": "0.0000"}},
    {"id": "CR52QM4S6YBY", "marketSlug": M, "side": "ORDER_SIDE_BUY",
     "outcomeSide": "OUTCOME_SIDE_YES", "intent": "ORDER_INTENT_BUY_LONG",
     "state": "ORDER_STATE_FILLED", "price": {"value": "0.14"},
     "quantity": 0.6, "cumQuantity": 0.6, "leavesQuantity": 0,
     "avgPx": {"value": "0.1400"},
     "commissionNotionalTotalCollected": {"value": "0.0000"}},
]


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def financial_keys(store):
    """Every key any money-bearing store is using."""
    out = set()
    for name in ("positions", "leg_cost", "fill_count", "committed"):
        out |= set((store.state.get(name) or {}))
    return out


# =====================================================================
# the boundary itself
# =====================================================================

def test_marketslug_is_read_by_name(store):
    """The venue sends marketSlug. Reading `market` alone is the defect."""
    assert LX.venue_row_market(INCIDENT[0], doing="t") == M
    assert LX.venue_row_market({"market": M}, doing="t") == M
    assert LX.venue_row_market({"slug": M}, doing="t") == M


def test_no_identity_anywhere_refuses(store):
    """Neither venue nor local record has it: there is nothing to use."""
    with pytest.raises(LX.UnknownMarket):
        LX.venue_row_market({"id": "X", "quantity": 1}, doing="t")
    with pytest.raises(LX.UnknownMarket):
        LX.venue_row_market({"id": "X"}, doing="t", fallback=None)
    # a local record supplies it when the venue row does not
    assert LX.venue_row_market({"id": "X"}, doing="t", fallback=M) == M


@pytest.mark.parametrize("bad", [None, "", "   ", "\t\n", "    "])
def test_blank_identity_can_never_become_a_key(bad):
    with pytest.raises(LX.UnknownMarket):
        LX.canonical_market(bad, doing="t")


def test_apply_fill_refuses_before_it_mutates_anything(store):
    """THE INVARIANT. Not 'it logs an error' -- that after the refusal the
    books are byte-identical to before it."""
    before = {k: dict(store.state.get(k) or {})
              for k in ("positions", "leg_cost", "committed", "fill_count")}
    for bad in (None, "", "   "):
        with pytest.raises(LX.UnknownMarket):
            store.apply_fill(bad, "UP", D("0.7"), D("0.14"), basis=LX.WIRE)
    after = {k: dict(store.state.get(k) or {})
             for k in ("positions", "leg_cost", "committed", "fill_count")}
    assert before == after
    assert financial_keys(store) == set()


def test_no_money_store_can_hold_a_blank_market(store):
    """Belt and braces across every money-bearing path at once."""
    store.apply_fill(M, "ORDER_SIDE_BUY", D("0.7"), D("0.14"), basis=LX.WIRE)
    for key in financial_keys(store):
        head = str(key).rsplit("|", 1)[0] if "|" in str(key) else str(key)
        assert head.strip() != ""
        assert LX.canonical_market(head, doing="t") == head
    with pytest.raises(LX.UnknownMarket):
        store.risk_exposure_usd("")
    with pytest.raises(LX.UnknownMarket):
        store.cash_deployed_usd("  ")
    with pytest.raises(LX.UnknownMarket):
        store.retire_settled_market("", yes_value=D(0))


# =====================================================================
# the incident, end to end
# =====================================================================

def test_the_two_adopted_orders_book_to_0115z_not_to_blank(store):
    """THE INCIDENT. Adopt both from the venue exactly as they arrived,
    fill them, and assert the money lands in 0115z."""
    store.reconcile_from_venue([], INCIDENT)
    for row in INCIDENT:
        o = store.live_orders()[row["id"]]
        assert o["market"] == M                    # NOT "", NOT None
        assert o["side"] == LX.UP
        assert o["venue_side_raw"] == "ORDER_SIDE_BUY"

    store.reconcile_from_venue([], [])             # both vanish -> parked
    for row in INCIDENT:
        res = store.resolve_pending_terminal(row["id"], {
            "cum_qty": D(str(row["cumQuantity"])),
            "avg_px": D(row["avgPx"]["value"]), "terminal": True,
            "state": "ORDER_STATE_FILLED", "commission": D(0)})
        assert res["resolved"] is True
        assert res["market"] == M

    assert store.position(M, LX.UP) == D("0.7")
    assert store.leg_cost(M)[LX.UP] == D("0.09700")
    assert D(str(store.committed()[M])) == D("0.09700")
    assert "" not in financial_keys(store)
    assert store.quarantined() == {}


def test_an_adopted_order_with_no_market_is_quarantined_not_booked(store):
    """The counterfactual the fix turns on: if the venue row had carried no
    market at all, the order must produce NO accounting whatsoever."""
    nameless = dict(INCIDENT[0])
    nameless.pop("marketSlug")
    store.reconcile_from_venue([], [nameless])

    assert store.live_orders() == {}               # never adopted
    assert "CR52P8GZ4YAQ" in store.quarantined()
    q = store.quarantined()["CR52P8GZ4YAQ"]
    assert q["reason"] == "UnknownMarket"
    assert q["row"]["id"] == "CR52P8GZ4YAQ"        # raw row preserved
    # and the money stores are untouched
    assert financial_keys(store) == set()
    assert store.realized_today() == D(0)
    # a book holding an unidentified order refuses every submission
    assert store.orders_uncertain() is not None


def test_a_parked_order_that_lost_its_market_is_quarantined(store):
    """resolve_pending_terminal was the line that wrote "". Even if a
    pending row somehow has no market, no fill may be booked."""
    store.state["pending_terminal"] = {"X": {
        "coid": "X", "venue_order_id": "X", "market": None, "side": "UP"}}
    store.save()
    out = store.resolve_pending_terminal("X", {
        "cum_qty": D("0.7"), "avg_px": D("0.14"), "terminal": True,
        "state": "ORDER_STATE_FILLED", "commission": D(0)})
    assert out["resolved"] is False
    assert out["reason"] == "QUARANTINED_UNIDENTIFIED"
    assert financial_keys(store) == set()
    assert "X" in store.quarantined()
    assert store.pending_terminal() == {}          # not left spinning


def test_quarantine_blocks_the_book_until_a_human_resolves_it(store):
    """An order we cannot identify may hold inventory we cannot see, so
    uncertainty must not clear on its own."""
    nameless = dict(INCIDENT[1]); nameless.pop("marketSlug")
    store.reconcile_from_venue([], [nameless])
    assert store.orders_uncertain() is not None
    out = store.reconcile_from_venue([], [])       # a clean venue read
    assert out["uncertainty_cleared"] is False     # still not clear
    assert out["quarantined"] == ["CR52QM4S6YBY"]
    assert store.orders_uncertain() is not None


def test_the_blank_market_can_no_longer_occupy_a_concurrency_slot(store):
    """What actually stopped the executor: markets_with_exposure() returned
    [""] and MAX_CONCURRENT_MARKETS refused every later window."""
    store.reconcile_from_venue([], INCIDENT)
    store.reconcile_from_venue([], [])
    for row in INCIDENT:
        store.resolve_pending_terminal(row["id"], {
            "cum_qty": D(str(row["cumQuantity"])),
            "avg_px": D(row["avgPx"]["value"]), "terminal": True,
            "state": "ORDER_STATE_FILLED", "commission": D(0)})
    assert store.markets_with_exposure() == [M]
    assert "" not in store.markets_with_exposure()


def test_0115z_settles_on_the_full_gross_including_the_adopted_fills(store):
    """AUTHORITATIVE. 0115z's real book was UP 4.47, not 3.77: the 0.70
    adopted UP shares were excluded, which is why it booked +0.599500
    against a true +0.502500. DOWN won, so those shares are worthless.
    """
    # the market as it was actually traded
    store.apply_fill(M, "UP", D("3.77"), D("1.459200") / D("3.77"),
                     basis=LX.OWN)
    store.apply_fill(M, "DOWN", D("5.45"), D("3.391300") / D("5.45"),
                     basis=LX.OWN)
    # plus the two adopted fills that belonged to it
    for row in INCIDENT:
        store.apply_fill(M, row["side"], D(str(row["cumQuantity"])),
                         D(row["avgPx"]["value"]), basis=LX.WIRE)

    assert store.position(M, LX.UP) == D("4.47")
    assert store.leg_cost(M)[LX.UP] == D("1.556200")   # 1.4592 + 0.0970

    out = store.retire_settled_market(M, yes_value=D("0.0000"))
    assert D(out["settlement_pnl"]) == D("0.502500")   # not 0.599500
    assert D(out["up_qty"]) == D("4.47")
