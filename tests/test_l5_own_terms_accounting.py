"""The venue quotes one price; we kept booking it as two different things.

LIVE 2026-09-26. `apply_order_progress` booked fills at the venue's avgPx --
a WIRE number, always the long (YES) side's price -- straight into
`leg_cost`, which every consumer downstream reads as OWN-outcome cash. On
1600z that recorded 10.46 DOWN shares as having cost 4.4399 when they cost
6.0201, reported a settlement of +5.0244 against a real +3.4442, and left
realized_today reading +4.7662 while the account had made +1.9092.

The same `dn_c` feeds risk_exposure_usd, so the $5 gate was under-binding on
exactly the cheap short legs this strategy accumulates.

Every number below is from the authoritative record, not constructed:
the L5_LIVE_SETTLED line for cpc-btc-updown-15m-2026-09-26-1600z and three
venue balance reads.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

# --- the authoritative 1600z settlement record, verbatim ------------------
UP_QTY, DN_QTY = D("9.44"), D("10.46")
UP_COST_WIRE, DN_COST_WIRE = D("0.995700"), D("4.439900")
SETTLEMENT = D("0.0000")                       # DOWN won
# Sum(1-p)q == Sum q - Sum pq, so the correction is exact, not averaged.
DN_COST_OWN = DN_QTY - DN_COST_WIRE            # 6.0201
COST_TOTAL_OWN = UP_COST_WIRE + DN_COST_OWN    # 7.0158
TRADING_PNL_OWN = DN_QTY - COST_TOTAL_OWN      # +3.4442

# --- venue balance anchors -------------------------------------------------
PRE_ARM = D("61.69765")
AFTER_1600Z = D("65.22685")
AT_DISARM = D("63.60685")
CASH_DELTA = AT_DISARM - PRE_ARM               # +1.90920


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


# ------------------------------------------------------- the normalisation

def test_a_wire_price_is_the_long_side_so_down_costs_one_minus_it():
    assert LX.own_outcome_price("UP", D("0.05"), basis=LX.WIRE) == D("0.05")
    assert LX.own_outcome_price("DOWN", D("0.05"), basis=LX.WIRE) == D("0.95")
    assert LX.own_outcome_price("DOWN", D("0.4245"),
                                basis=LX.WIRE) == D("0.5755")


def test_own_terms_pass_through_untouched():
    assert LX.own_outcome_price("DOWN", D("0.61"), basis=LX.OWN) == D("0.61")


def test_a_price_with_no_declared_basis_cannot_book_a_fill(store):
    """No default: the caller must say which convention it holds."""
    with pytest.raises(TypeError):
        store.apply_fill("m", "DOWN", D("1"), D("0.05"))      # no basis=


def test_an_unknown_basis_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        LX.own_outcome_price("DOWN", D("0.5"), basis="MIDPOINT")


# --------------------------------------------- the 1600z regression itself

def test_the_1600z_down_leg_costs_6_0201_not_4_4399(store):
    """Book the DOWN side at the venue's own wire average and check the
    recorded cost is the own-terms one."""
    avg_wire = DN_COST_WIRE / DN_QTY                     # 0.4245...
    store.apply_fill("1600z", "DOWN", DN_QTY, avg_wire,
                     basis=LX.WIRE)
    assert store.leg_cost("1600z")["DOWN"] == DN_COST_OWN == D("6.0201")


def test_the_1600z_settlement_is_plus_3_4442_not_plus_5_0244(store):
    store.state["positions"]["1600z|UP"] = str(UP_QTY)
    store.state["positions"]["1600z|DOWN"] = str(DN_QTY)
    store.state.setdefault("leg_cost", {})
    store.state["leg_cost"]["1600z|UP"] = str(UP_COST_WIRE)   # UP wire == own
    store.state["leg_cost"]["1600z|DOWN"] = str(DN_COST_OWN)
    store.state["committed"]["1600z"] = str(COST_TOTAL_OWN)
    out = store.retire_settled_market("1600z", yes_value=SETTLEMENT)
    assert D(out["cost_total"]) == COST_TOTAL_OWN == D("7.0158")
    assert D(out["redemption_total"]) == DN_QTY
    assert D(out["settlement_pnl"]) == TRADING_PNL_OWN == D("3.4442")


def test_the_corrected_settlement_is_within_a_dime_of_the_cash_it_moved():
    """The reported +5.0244 was $1.50 from the venue's cash. The corrected
    figure lands inside a dime of the whole observed move, which is what
    makes the diagnosis more than a plausible story."""
    observed = AFTER_1600Z - PRE_ARM                    # +3.52920
    assert abs(observed - TRADING_PNL_OWN) < D("0.10")
    assert abs(observed - D("5.0244")) > D("1.40")


def test_matched_and_residual_stay_separate_under_the_correction(store):
    store.state["positions"]["1600z|UP"] = str(UP_QTY)
    store.state["positions"]["1600z|DOWN"] = str(DN_QTY)
    store.state.setdefault("leg_cost", {})
    store.state["leg_cost"]["1600z|UP"] = str(UP_COST_WIRE)
    store.state["leg_cost"]["1600z|DOWN"] = str(DN_COST_OWN)
    out = store.retire_settled_market("1600z", yes_value=SETTLEMENT)
    assert D(out["matched_qty"]) == UP_QTY               # 9.44 pairs
    assert D(out["residual_qty"]) == DN_QTY - UP_QTY     # 1.02
    assert out["residual_side"] == "DOWN"
    assert (D(out["matched_pnl"]) + D(out["residual_pnl"])
            == D(out["settlement_pnl"]))
    assert "NOT summed" in out["labels"]


# ------------------------------------------------ the gate reads it too

def test_the_five_dollar_gate_uses_corrected_down_economics(store):
    """Understating dn_c makes a short leg look almost free and the gate
    under-binds. Same shares, two conventions, two different verdicts."""
    qty, wire = D("10"), D("0.05")           # DOWN at wire 0.05 == own 0.95
    store.apply_fill("m", "DOWN", qty, wire, basis=LX.WIRE)
    corrected = store.risk_exposure_usd("m")

    with tempfile.TemporaryDirectory() as d:
        old = LX.InventoryStore(os.path.join(d, "old.json"))
        old.apply_fill("m", "DOWN", qty, wire, basis=LX.OWN)   # the old bug
        buggy = old.risk_exposure_usd("m")

    assert corrected > buggy
    lim = LX.SafetyLimits(max_usd_per_market=D(5))
    kw = dict(price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
              residual_after=D(0), realized_today=D(0), open_markets=[],
              outstanding_by_market={}, outstanding_residual=D(0),
              live_order_count=0, orders_uncertain=None)
    assert lim.refuse(risk_by_market={"m": corrected}, **kw) == \
        "MAX_USD_PER_MARKET"
    assert lim.refuse(risk_by_market={"m": buggy}, **kw) != "MAX_USD_PER_MARKET"


# ------------------------------------------- restart-safe realized accounting

def test_rebase_decomposes_rather_than_setting_realized_to_the_balance(store):
    store.state["realized_today"] = "4.766200"           # contaminated
    store.save()
    out = store.rebase_session_accounting(
        attributed=TRADING_PNL_OWN, cash_delta=CASH_DELTA,
        anchor={"pre_arm": str(PRE_ARM), "at_disarm": str(AT_DISARM)},
        reason="wire-priced DOWN legs")
    assert D(out["realized_attributed"]) == D("3.4442")
    assert D(out["realized_unattributed"]) == CASH_DELTA - TRADING_PNL_OWN
    assert D(out["realized_total"]) == CASH_DELTA == D("1.90920")
    # the two lines are kept apart, and the contaminated scalar is preserved
    assert D(out["legacy_realized_today"]) == D("4.766200")
    assert out["realized_attributed"] != out["realized_total"]


def test_the_contaminated_scalar_stops_being_the_halt_authority(store):
    store.state["realized_today"] = "4.766200"
    store.save()
    assert store.realized_today() == D("4.766200")
    store.rebase_session_accounting(
        attributed=TRADING_PNL_OWN, cash_delta=CASH_DELTA,
        anchor={}, reason="x")
    assert store.realized_today() == CASH_DELTA


def test_the_halt_reads_the_corrected_settled_result(store):
    """A loss that the wire-priced book would have shown as a gain."""
    store.rebase_session_accounting(
        attributed=D("-2.00"), cash_delta=D("-5.00"), anchor={}, reason="x")
    lim = LX.SafetyLimits(daily_realized_loss_halt_usd=D(5))
    assert lim.refuse(
        price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
        residual_after=D(0), realized_today=store.realized_today(),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=0, risk_by_market={},
        orders_uncertain=None) == "DAILY_REALIZED_LOSS_HALT"


def test_a_settlement_credits_the_attributed_line_not_the_unattributed(store):
    store.rebase_session_accounting(attributed=D(0), cash_delta=D(0),
                                    anchor={}, reason="x")
    store.state["positions"]["m|DOWN"] = "1"
    store.state.setdefault("leg_cost", {})["m|DOWN"] = "0.40"
    store.retire_settled_market("m", yes_value=D(0))      # pays 1.00
    lines = store.realized_lines()
    assert D(lines["realized_attributed"]) == D("0.60")
    assert D(lines["realized_unattributed"]) == D(0)


def test_realized_lines_never_call_a_zero_commission_a_rebate(store):
    assert "NOT a rebate" in store.realized_lines()["labels"]


def test_rebasing_survives_a_restart():
    """The halt is cumulative across the container recycling under it."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "inv.json")
        first = LX.InventoryStore(path)
        first.state["realized_today"] = "4.766200"
        first.rebase_session_accounting(
            attributed=TRADING_PNL_OWN, cash_delta=CASH_DELTA,
            anchor={"pre_arm": str(PRE_ARM)}, reason="wire-priced DOWN legs")
        reloaded = LX.InventoryStore(path)
        assert reloaded.realized_today() == CASH_DELTA
        lines = reloaded.realized_lines()
        assert D(lines["realized_attributed"]) == TRADING_PNL_OWN
        assert lines["cash_anchor"]["pre_arm"] == str(PRE_ARM)
        assert D(lines["legacy_realized_today"]) == D("4.766200")


# ------------------------------------- the one-shot repair of the live store

def test_the_startup_rebase_decomposes_the_live_contaminated_total():
    from src import l5_pmus_live_runner as R
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_today"] = str(R.CONTAMINATED_REALIZED)
        s.save()
        out = R.rebase_contaminated_accounting(s)
        assert D(out["realized_attributed"]) == D("3.4442")
        assert D(out["realized_unattributed"]) == D("-1.53500")
        assert D(out["realized_total"]) == D("1.90920")
        assert out["cash_anchor"]["at_disarm_balance"] == "63.60685"


def test_the_rebase_is_idempotent_and_cannot_fire_twice():
    from src import l5_pmus_live_runner as R
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_today"] = str(R.CONTAMINATED_REALIZED)
        s.save()
        assert R.rebase_contaminated_accounting(s) is not None
        assert R.rebase_contaminated_accounting(s) is None
        assert R.rebase_contaminated_accounting(LX.InventoryStore(
            os.path.join(d, "i.json"))) is None


def test_the_rebase_leaves_an_uncontaminated_store_alone():
    from src import l5_pmus_live_runner as R
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "i.json"))
        s.state["realized_today"] = "-1.25"
        s.save()
        assert R.rebase_contaminated_accounting(s) is None
        assert s.realized_today() == D("-1.25")


def test_the_anchor_records_that_the_account_was_flat_at_each_read():
    from src import l5_pmus_live_runner as R
    assert "zero open orders" in R.REBASE_ANCHOR["flat_at_each_read"]
    assert (D(R.REBASE_ANCHOR["at_disarm_balance"])
            - D(R.REBASE_ANCHOR["pre_arm_balance"])
            == D(R.REBASE_ANCHOR["cash_delta"]))


# ----------------------------------- the same defect in the RESTING-order path

def test_an_adopted_venue_order_is_stored_in_own_terms(store):
    """live_orders["price"] is read by risk_exposure_usd as what the leg
    costs. venue_order_price returns the venue's long-side number, so an
    adopted short was being priced at a twentieth of its real cost."""
    store.reconcile_from_venue([], [{
        "client_order_id": "c1", "market": "m", "side": "DOWN",
        "price": {"value": "0.05", "currency": "USD"},
        "qty": "10", "remaining_qty": "10"}])
    assert store.live_orders()["c1"]["price"] == "0.95"
    assert store.risk_exposure_usd("m") == D("9.50")


def test_an_order_we_placed_ourselves_is_not_converted_twice(store):
    """record_submit already stores L5's own-side price."""
    store.record_submit("c2", {"market": "m", "side": "DOWN",
                               "price": "0.61", "qty": "1"})
    before = store.live_orders()["c2"]["price"]
    store.reconcile_from_venue([], [{"client_order_id": "c2", "market": "m",
                                     "side": "DOWN", "qty": "1",
                                     "remaining_qty": "1"}])
    # no venue price on the row, so ours is kept as-is
    assert store.live_orders()["c2"]["price"] == before == "0.61"


def test_the_resting_order_exposure_would_have_passed_the_gate_before(store):
    """The regression this prevents, stated as the gate verdict."""
    store.reconcile_from_venue([], [{
        "client_order_id": "c1", "market": "m", "side": "DOWN",
        "price": {"value": "0.05", "currency": "USD"},
        "qty": "10", "remaining_qty": "10"}])
    corrected = store.risk_exposure_usd("m")
    naive = D("0.05") * D("10")          # what the wire number would have said
    assert corrected == D("9.50") and naive == D("0.50")
    lim = LX.SafetyLimits(max_usd_per_market=D(5))
    kw = dict(price=D("0.5"), qty=D("1"), market="m", committed_by_market={},
              residual_after=D(0), realized_today=D(0), open_markets=[],
              outstanding_by_market={}, outstanding_residual=D(0),
              live_order_count=0, orders_uncertain=None)
    assert lim.refuse(risk_by_market={"m": corrected}, **kw) == \
        "MAX_USD_PER_MARKET"
    assert lim.refuse(risk_by_market={"m": naive}, **kw) != "MAX_USD_PER_MARKET"
