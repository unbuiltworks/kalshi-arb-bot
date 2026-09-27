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


# =====================================================================
# the corruption already on the volume
# =====================================================================

def orphaned(store):
    """The volume as the incident left it: 0.7 UP / $0.097 under ""."""
    store.state["positions"] = {"|UP": "0.7"}
    store.state["leg_cost"] = {"|UP": "0.09700"}
    store.state["fill_count"] = {"|UP": 2}
    store.state["committed"] = {"": "0.09700"}
    store.state["realized_attributed"] = "0.71760000"
    store.state["realized_unattributed"] = "-2.62630"
    store.save()


def test_enumeration_names_a_corrupt_key_instead_of_raising(store):
    """A guard that refuses to BOOK against a bad key is right. One that
    refuses to LIST it leaves the process unable to say what is wrong --
    which is what wedged every reconcile on 2026-09-27."""
    orphaned(store)
    assert store.corrupt_market_keys() == ["", "|UP"]
    # enumeration skips it rather than dying on it
    assert store.markets_with_exposure() == []


def test_the_orphan_is_repaired_once_against_venue_evidence(store):
    from src import l5_pmus_live_runner as R
    orphaned(store)
    before = store.realized_today()
    assert before == D("-1.90870000")

    out = R.repair_orphaned_keys(store)
    assert out is not None
    # the -0.097 is ATTRIBUTED: two venue fill rows at venue prices
    assert D(store.realized_lines()["realized_attributed"]) == D("0.62060000")
    assert D(store.realized_lines()["realized_unattributed"]) == D("-2.62630")
    assert store.realized_today() == D("-2.00570000")   # the authoritative cash
    assert store.corrupt_market_keys() == []
    assert store.markets_with_exposure() == []

    rec = store.state["orphan_repairs"][-1]
    assert rec["bad_key"] == ""                       # preserved, not rewritten
    assert rec["authoritative_market"] == (
        "cpc-btc-updown-15m-2026-09-27-0115z")
    assert rec["evidence"]["orders"] == ["CR52P8GZ4YAQ", "CR52QM4S6YBY"]

    # once only, and a fresh process repeats nothing
    assert R.repair_orphaned_keys(store) is None
    again = LX.InventoryStore(store.path)
    assert R.repair_orphaned_keys(again) is None
    assert again.realized_today() == D("-2.00570000")


def test_a_clean_store_is_never_touched(store):
    from src import l5_pmus_live_runner as R
    store.apply_fill(M, "UP", D("1"), D("0.5"), basis=LX.WIRE)
    before = dict(store.state["leg_cost"])
    assert R.repair_orphaned_keys(store) is None
    assert store.state["leg_cost"] == before


def test_the_repair_refuses_when_the_ledger_disagrees_with_the_evidence(store):
    """If the volume does not hold what the venue evidence describes, the
    evidence is not about this ledger. Repairing it blind would be
    manufacturing P&L."""
    from src import l5_pmus_live_runner as R
    orphaned(store)
    store.state["leg_cost"] = {"|UP": "0.5000"}       # not 0.09700
    store.save()
    assert R.repair_orphaned_keys(store) is None
    assert store.corrupt_market_keys() != []          # left alone, still bad
    assert store.realized_today() == D("-1.90870000")


def test_repair_requires_stated_evidence(store):
    orphaned(store)
    with pytest.raises(ValueError):
        store.repair_orphaned_market_key(
            key="", to_market=M, qty=D("0.7"), cost=D("0.097"), side="UP",
            settlement_pnl=D("-0.097"), evidence={})


def test_the_orphan_repair_is_idempotent_across_restarts(store):
    """EXPLICIT RESTART IDEMPOTENCY.

    The repair runs at boot, and the executor has restarted five times in
    this incident alone. Every one of these must hold on the second boot and
    the tenth: the -0.09700 is attributed ONCE, realized cannot move again,
    the corruption stays gone, and the audit record survives -- because a
    repair that re-applies is just a slower way of manufacturing P&L.
    """
    from src import l5_pmus_live_runner as R
    orphaned(store)
    path = store.path

    first = R.repair_orphaned_keys(store)
    assert first is not None
    attributed_after = store.realized_lines()["realized_attributed"]
    total_after = store.realized_today()
    audit_after = list(store.state["orphan_repairs"])
    assert total_after == D("-2.00570000")
    assert len(audit_after) == 1

    # ten cold boots off the same file, exactly as the runner does it
    for boot in range(10):
        s2 = LX.InventoryStore(path)
        assert R.repair_orphaned_keys(s2) is None, "re-applied on boot %d" % boot

        # 1. attributed exactly once
        assert s2.realized_lines()["realized_attributed"] == attributed_after
        # 2. realized cannot move again
        assert s2.realized_today() == total_after == D("-2.00570000")
        # 3. the corruption stays gone
        assert s2.corrupt_market_keys() == []
        assert s2.markets_with_exposure() == []
        # 4. the audit record remains, unchanged
        assert s2.state["orphan_repairs"] == audit_after
        assert s2.state["orphan_repairs"][0]["bad_key"] == ""
        assert s2.state["orphan_repairs"][0]["id"] == "blank-market-key-0115z"

    # and the unattributed line never absorbed any of it
    assert D(s2.realized_lines()["realized_unattributed"]) == D("-2.62630")


def test_a_second_orphan_of_the_same_shape_is_not_swept_up_by_the_done_flag(store):
    """The id guard must not become a blanket amnesty. If a NEW corrupt key
    appears after the repair has run, it is still corruption and must still
    be named -- not silently tolerated because a repair with that id is
    already logged."""
    from src import l5_pmus_live_runner as R
    orphaned(store)
    R.repair_orphaned_keys(store)
    assert store.corrupt_market_keys() == []

    store.state["leg_cost"]["  |DOWN"] = "0.25"       # a fresh bad key
    store.save()
    s2 = LX.InventoryStore(store.path)
    assert s2.corrupt_market_keys() == ["  |DOWN"]    # named, not ignored
    assert R.repair_orphaned_keys(s2) is None         # and NOT auto-repaired
    assert s2.realized_today() == D("-2.00570000")    # accounting untouched
