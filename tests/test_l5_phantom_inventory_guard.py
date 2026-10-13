"""Regression tests for the 0.1300 DOWN phantom. Written from the venue's
own authoritative record, not from a reconstruction."""
from __future__ import annotations

from src import l5_phantom_inventory_guard as G

#: the venue's own order record for D0VC6VJDMZ7J, verbatim fields
CANCELLED_NO_FILL = {
    "id": "D0VC6VJDMZ7J",
    "marketSlug": "cpc-btc-updown-15m-2026-10-10-1345z",
    "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
    "intent": "ORDER_INTENT_BUY_LONG", "state": "ORDER_STATE_CANCELED",
    "price": "0.62", "quantity": 0.13, "cumQuantity": 0,
    "leavesQuantity": 0, "avgPx": "0.0000",
}
#: a venue position row whose halves imply a short -- the only shape that
#: `position_row` turns into DOWN
SHORT_ROW = {"marketSlug": "cpc-btc-updown-15m-2026-10-10-1345z",
             "qtyBought": "0", "qtySold": "0.13"}
FLAT_ANCHOR = {"as_of_utc": "2026-10-11T06:01:12Z", "open_orders": [],
               "positions": {}, "source": "pmus-readonly-recovery f877b931"}


# ---- invariant 2: the write is refused -----------------------------------
def test_the_exact_phantom_write_is_refused():
    assert G.refuse_cost_less_adoption(
        fill_count=0, our_orders=[CANCELLED_NO_FILL]) is not None


def test_the_refusal_names_the_contradiction():
    r = G.refuse_cost_less_adoption(fill_count=0,
                                    our_orders=[CANCELLED_NO_FILL])
    assert "contradicts_our_order_records" in r
    assert "cumQuantity=0" in r


def test_a_genuine_reconciliation_gap_is_still_adopted():
    """We DO have fills, so a quantity we cannot cost is real and must stay
    visible to risk. Refusing it would hide live exposure."""
    assert G.refuse_cost_less_adoption(fill_count=3,
                                       our_orders=[CANCELLED_NO_FILL]) is None


def test_a_partially_filled_order_still_permits_adoption():
    partial = dict(CANCELLED_NO_FILL, cumQuantity=0.05)
    assert G.refuse_cost_less_adoption(fill_count=0,
                                       our_orders=[partial]) is None


def test_no_order_records_at_all_refuses():
    """Nothing of ours filled and nothing of ours is known: still not
    inventory."""
    assert G.refuse_cost_less_adoption(fill_count=0, our_orders=[]) is not None


# ---- invariant 1: the cause survives -------------------------------------
def test_the_causing_row_is_recorded_verbatim():
    rec = G.adoption_provenance("mkt", "DOWN", "0.13", SHORT_ROW)
    assert rec["kind"] == G.COST_LESS
    assert rec["venue_row"] == SHORT_ROW
    assert rec["qty"] == "0.13"


def test_provenance_copies_rather_than_aliases_the_row():
    row = dict(SHORT_ROW)
    rec = G.adoption_provenance("mkt", "DOWN", "0.13", row)
    row["qtySold"] = "999"
    assert rec["venue_row"]["qtySold"] == "0.13"


# ---- invariant 3: it can finally leave, but only then --------------------
def test_the_phantom_is_droppable_once_the_venue_is_flat():
    assert G.may_drop_cost_less(qty="0.13", fill_count=0, leg_cost="0",
                                venue_flat=True) is True


def test_not_dropped_while_the_venue_still_reports_it():
    assert G.may_drop_cost_less(qty="0.13", fill_count=0, leg_cost="0",
                                venue_flat=False) is False


def test_a_position_with_a_cost_basis_is_never_dropped():
    assert G.may_drop_cost_less(qty="0.13", fill_count=0, leg_cost="0.0806",
                                venue_flat=True) is False


def test_a_position_with_a_booked_fill_is_never_dropped():
    assert G.may_drop_cost_less(qty="0.13", fill_count=1, leg_cost="0",
                                venue_flat=True) is False


def test_nothing_to_drop_is_not_a_drop():
    assert G.may_drop_cost_less(qty="0", fill_count=0, leg_cost="0",
                                venue_flat=True) is False


def test_the_drop_posts_no_pnl_and_keeps_its_anchor():
    rec = G.drop_record("mkt", "DOWN", "0.13", FLAT_ANCHOR)
    assert rec["settlement_pnl"] == "0"
    assert rec["anchor"] == FLAT_ANCHOR
    assert rec["closed_against"] == "AUTHORITATIVE_FLAT_WITH_ZERO_FILLS"


# ---- the module stays inert ---------------------------------------------
def test_module_has_no_network_order_or_arming_path():
    import ast
    import inspect
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(G))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    for bad in ("requests", "httpx", "urllib", "socket", "os", "open",
                "place_order", "create_order", "cancel", "arm", "environ"):
        assert bad not in names, bad
