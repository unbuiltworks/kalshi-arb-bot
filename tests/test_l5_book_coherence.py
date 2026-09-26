"""Two live failures on 2026-09-26, reproduced.

1815z. reconcile_from_venue rewrote `positions` and never touched
`leg_cost`, so when the venue's NET replaced our GROSS the matched pair's
quantities vanished and both legs' costs stayed. The market settled on
up 1.81 / dn 0 / cost 4.3425 -- $2.40 a share for a contract paying at most
$1 -- and posted -4.3425 to realized, moving the loss halt with it.

The seven venue order rows say the real gross book was UP 0.49 at 0.0931 and
DOWN 5.73 at 4.2494. BOTH COSTS MATCH THE RECORD EXACTLY, so leg_cost was
right; only the quantities were lost. Settled at 0.0000 that book redeems
5.73 against 4.3425: a GAIN of 1.3875. The error is exactly 5.73, the
deleted DOWN shares, each paying $1.

1830z. Adopted orders carried no venue_order_id, so poll_orders skipped
them forever: no progress, 90s stall, re-adopt, stall again -- a loop every
92s with submissions refused throughout, for the whole window.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

# the seven authoritative 1815z venue rows: side, wire avgPx, cumQuantity
ROWS_1815Z = [("DOWN", "0.2000", "2.25"), ("DOWN", "0.2100", "1.07"),
              ("DOWN", "0.3400", "0.66"), ("DOWN", "0.3700", "1.53"),
              ("UP", "0.1900", "0.22"), ("UP", "0.1900", "0.27"),
              ("DOWN", "0.0700", "0.22")]


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def book_1815z(store, market="1815z"):
    for side, wire, qty in ROWS_1815Z:
        store.apply_fill(market, side, D(qty), D(wire), basis=LX.WIRE)
    return store


# ------------------------------------------------ the incoherent book itself

def test_the_exact_1815z_book_is_rejected(store):
    """up 1.81 / dn 0 / dn_cost 4.2494 -- cost with no shares."""
    store.state["positions"]["m|UP"] = "1.8100"
    store.state.setdefault("leg_cost", {})["m|UP"] = "0.093100"
    store.state["leg_cost"]["m|DOWN"] = "4.249400"
    kinds = {v["violation"] for v in store.book_violations("m")}
    assert "COST_WITHOUT_QUANTITY" in kinds


def test_risk_fails_closed_on_it(store):
    store.state["positions"]["m|UP"] = "1.8100"
    store.state.setdefault("leg_cost", {})["m|UP"] = "0.093100"
    store.state["leg_cost"]["m|DOWN"] = "4.249400"
    with pytest.raises(LX.IncoherentBook):
        store.risk_exposure_usd("m")
    assert store.orders_uncertain() is not None


def test_settlement_fails_closed_on_it(store):
    store.state["positions"]["m|UP"] = "1.8100"
    store.state.setdefault("leg_cost", {})["m|UP"] = "0.093100"
    store.state["leg_cost"]["m|DOWN"] = "4.249400"
    with pytest.raises(LX.IncoherentBook):
        store.retire_settled_market("m", yes_value=D(0))


def test_a_share_cannot_cost_more_than_a_dollar(store):
    store.state["positions"]["m|UP"] = "1"
    store.state.setdefault("leg_cost", {})["m|UP"] = "2.40"
    kinds = {v["violation"] for v in store.book_violations("m")}
    assert "COST_PER_SHARE_IMPOSSIBLE" in kinds


def test_a_normal_book_is_coherent(store):
    book_1815z(store)
    assert store.book_violations("1815z") == []
    store.risk_exposure_usd("1815z")            # does not raise


# -------------------------------------------- the authoritative 1815z result

def test_the_real_1815z_gross_and_costs(store):
    book_1815z(store)
    assert store.position("1815z", "UP") == D("0.49")
    assert store.position("1815z", "DOWN") == D("5.73")
    lc = store.leg_cost("1815z")
    assert lc["UP"] == D("0.093100")      # matches the live record exactly
    assert lc["DOWN"] == D("4.249400")    # matches the live record exactly


def test_1815z_was_a_gain_of_1_3875_not_a_loss_of_4_3425(store):
    book_1815z(store)
    out = store.retire_settled_market("1815z", yes_value=D(0))   # DOWN won
    assert D(out["redemption_total"]) == D("5.73")
    assert D(out["cost_total"]) == D("4.342500")
    assert D(out["settlement_pnl"]) == D("1.387500")
    assert D(out["settlement_pnl"]) - D("-4.34250000") == D("5.73")


def test_matched_and_residual_still_separate(store):
    book_1815z(store)
    out = store.retire_settled_market("1815z", yes_value=D(0))
    assert D(out["matched_qty"]) == D("0.49")
    assert out["residual_side"] == "DOWN"
    assert D(out["residual_qty"]) == D("5.24")
    assert (D(out["matched_pnl"]) + D(out["residual_pnl"])
            == D(out["settlement_pnl"]))


# ------------------------------------- reconcile must not invent gross:net

def test_a_disagreeing_net_never_overwrites_our_gross(store):
    """The venue reports ONE number. A gross book cannot be recovered from
    it, so a disagreement is NOT KNOWN rather than resolved in its favour."""
    book_1815z(store)
    before = (store.position("1815z", "UP"), store.position("1815z", "DOWN"))
    # a venue row proves its own side by bought-minus-sold, never a bare net
    store.reconcile_from_venue(
        [{"marketSlug": "1815z", "qtyBoughtDecimal": "0",
          "qtySoldDecimal": "1.81", "netPositionDecimal": "-1.81"}], [])
    assert (store.position("1815z", "UP"),
            store.position("1815z", "DOWN")) == before
    assert store.orders_uncertain() is not None     # fails closed


def test_an_agreeing_net_leaves_the_matched_pair_intact(store):
    book_1815z(store)
    net = store.position("1815z", "UP") - store.position("1815z", "DOWN")
    assert net == D("-5.24")
    store.reconcile_from_venue(
        [{"marketSlug": "1815z", "qtyBoughtDecimal": "0.49",
          "qtySoldDecimal": "5.73", "netPositionDecimal": str(net)}], [])
    assert store.position("1815z", "UP") == D("0.49")
    assert store.position("1815z", "DOWN") == D("5.73")
    assert store.orders_uncertain() is None


def test_reconcile_never_leaves_an_incoherent_book_unflagged(store):
    store.state.setdefault("leg_cost", {})["m|DOWN"] = "4.2494"
    store.state["positions"]["m|UP"] = "1.81"
    store.state.setdefault("leg_cost", {})["m|UP"] = "0.0931"
    store.reconcile_from_venue([], [])
    assert store.orders_uncertain() is not None


# ------------------------------------------ adopted-order lifecycle, end to end

def test_adopted_order_carries_everything_poll_orders_needs(store):
    store.reconcile_from_venue([], [{
        "id": "CQZ6WHYA6YC6", "market": "1830z", "side": "DOWN",
        "price": {"value": "0.50"}, "qty": "1.82", "remaining_qty": "1.82"}])
    o = store.live_orders()["CQZ6WHYA6YC6"]
    assert o["venue_order_id"] == "CQZ6WHYA6YC6"
    assert o["client_order_id"] == "CQZ6WHYA6YC6"
    assert o["adopted"] is True
    assert o["price"] == "0.50"                  # own terms for a DOWN leg


def test_the_full_adopted_lifecycle_absent_adopt_poll_fill_release(store):
    """venue order absent locally -> adopt -> progress -> fill -> release."""
    assert store.live_order_count() == 0
    store.reconcile_from_venue([], [{
        "id": "V1", "market": "m", "side": "DOWN",
        "price": {"value": "0.40"}, "qty": "2", "remaining_qty": "2"}])
    assert store.live_order_count() == 1
    o = store.live_orders()["V1"]
    assert o["venue_order_id"] == "V1"           # pollable

    # a progress read fills it in full
    prog = LX.order_progress({"id": "V1", "state": "ORDER_STATE_FILLED",
                              "cumQuantity": "2", "leavesQuantity": "0",
                              "avgPx": "0.40", "commission": "0.0000"})
    res = store.apply_order_progress("V1", prog, market="m", side="DOWN")
    assert D(res["delta"]) == D("2")
    assert store.live_order_count() == 0         # released
    assert store.position("m", "DOWN") == D("2")
    assert store.leg_cost("m")["DOWN"] == D("1.20")   # 2 * (1 - 0.40)
    assert store.book_violations("m") == []


def test_a_partial_fill_keeps_the_order_live_then_releases(store):
    store.reconcile_from_venue([], [{
        "id": "V2", "market": "m", "side": "UP",
        "price": {"value": "0.30"}, "qty": "4", "remaining_qty": "4"}])
    half = LX.order_progress({"id": "V2", "state": "ORDER_STATE_PARTIALLY_FILLED",
                              "cumQuantity": "1", "leavesQuantity": "3",
                              "avgPx": "0.30", "commission": "0.0000"})
    store.apply_order_progress("V2", half, market="m", side="UP")
    assert store.live_order_count() == 1
    full = LX.order_progress({"id": "V2", "state": "ORDER_STATE_FILLED",
                              "cumQuantity": "4", "leavesQuantity": "0",
                              "avgPx": "0.30", "commission": "0.0000"})
    res = store.apply_order_progress("V2", full, market="m", side="UP")
    assert D(res["delta"]) == D("3")             # the DELTA, not the total
    assert store.position("m", "UP") == D("4")
    assert store.live_order_count() == 0


def test_an_expired_adopted_order_is_released_with_no_fill(store):
    """All eight 1830z orders came back EXPIRED with cumQuantity 0."""
    store.reconcile_from_venue([], [{
        "id": "V3", "market": "m", "side": "DOWN",
        "price": {"value": "0.51"}, "qty": "0.08", "remaining_qty": "0.08"}])
    prog = LX.order_progress({"id": "V3", "state": "ORDER_STATE_EXPIRED",
                              "cumQuantity": "0", "leavesQuantity": "0",
                              "avgPx": "0.0000", "commission": "0.0000"})
    store.apply_order_progress("V3", prog, market="m", side="DOWN")
    assert store.live_order_count() == 0
    assert store.position("m", "DOWN") == D(0)
    assert store.leg_cost("m")["DOWN"] == D(0)


def test_the_92_second_loop_cannot_recur(store):
    """The loop was: adopt -> no venue_order_id -> poll skips -> stall ->
    re-adopt. With the id present, a poll makes progress and the order
    reaches a terminal state instead of going round again."""
    store.reconcile_from_venue([], [{
        "id": "V4", "market": "m", "side": "DOWN",
        "price": {"value": "0.50"}, "qty": "1", "remaining_qty": "1"}])
    pollable = [o for o in store.live_orders().values()
                if o.get("venue_order_id")]
    assert len(pollable) == 1
    prog = LX.order_progress({"id": "V4", "state": "ORDER_STATE_EXPIRED",
                              "cumQuantity": "0", "leavesQuantity": "0",
                              "avgPx": "0", "commission": "0"})
    store.apply_order_progress("V4", prog, market="m", side="DOWN")
    store.reconcile_from_venue([], [])
    assert store.live_order_count() == 0         # gone, not re-adopted


# -------------------------------------------- contaminated realized accounting

def test_the_1815z_contamination_is_rebased_to_the_reconstruction():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_attributed"] = "3.4442"
        s.state["realized_unattributed"] = "-5.87750"      # total -2.43330
        s.save()
        assert s.realized_today() == D("-2.43330")
        out = R.rebase_contaminated_accounting(s)
        assert D(out["realized_attributed"]) == D("4.8317")   # 3.4442+1.3875
        assert D(out["realized_unattributed"]) == D("-2.19400")
        assert D(out["realized_total"]) == D("2.63770")       # the cash delta


def test_each_rebase_applies_at_most_once():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_today"] = "4.766200"
        s.save()
        assert R.rebase_contaminated_accounting(s) is not None
        assert R.rebase_contaminated_accounting(s) is None
        ids = {r.get("id") for r in s.state["accounting_rebase_log"]}
        assert "wire-priced-down-legs" in ids


def test_an_untouched_store_is_never_rebased():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_today"] = "-0.55"
        s.save()
        assert R.rebase_contaminated_accounting(s) is None
        assert s.realized_today() == D("-0.55")


def test_the_unattributed_line_is_never_folded_into_trading():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_attributed"] = "3.4442"
        s.state["realized_unattributed"] = "-5.87750"
        s.save()
        R.rebase_contaminated_accounting(s)
        lines = s.realized_lines()
        assert lines["realized_attributed"] != lines["realized_total"]
        assert "NOT a trading result" in lines["labels"]
