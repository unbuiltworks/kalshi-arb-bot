"""The 0.1300 DOWN phantom, replayed against the real InventoryStore."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MKT = "cpc-btc-updown-15m-2026-10-10-1345z"
#: the only venue row shape `position_row` turns into DOWN: sold > bought
SHORT_ROW = {"marketSlug": MKT, "qtyBought": "0", "qtySold": "0.13"}
LONG_ROW = {"marketSlug": MKT, "qtyBought": "0.13", "qtySold": "0"}


def store(tmp_path):
    return LX.InventoryStore(str(tmp_path / "inv.json"))


def test_the_cost_less_write_stays_visible_to_risk(tmp_path):
    """It must NOT be refused: a position the venue reports is live exposure
    even when we cannot cost it, and hiding it is worse than an incoherent
    book. Four existing tests enforce this and they are right."""
    s = store(tmp_path)
    s.reconcile_from_venue([SHORT_ROW], [])
    assert s.state["positions"].get("%s|DOWN" % MKT) == "0.13"


def test_the_causing_row_is_now_recorded_verbatim(tmp_path):
    """What was actually missing: only the resulting drift was logged, so the
    cause could not be reconstructed afterwards."""
    s = store(tmp_path)
    s.reconcile_from_venue([SHORT_ROW], [])
    rec = s.state["cost_less_adoptions"][-1]
    assert rec["venue_row"] == SHORT_ROW
    assert rec["qty"] == "0.13"
    assert rec["fill_count"] == 0


def test_a_costed_position_records_no_cost_less_adoption(tmp_path):
    s = store(tmp_path)
    s.apply_fill(MKT, "DOWN", D("0.13"), D("0.40"), coid="c1", basis=LX.WIRE)
    s.reconcile_from_venue([{"marketSlug": MKT, "qtyBought": "0",
                             "qtySold": "0.13"}], [])
    assert not s.state.get("cost_less_adoptions")


def test_an_empty_positions_response_does_NOT_delete_the_book(tmp_path):
    """The terminal transition is deliberately NOT inferred from an empty
    response: empty covers an authoritative flat account AND an unparseable
    one, and deleting live exposure on the second is the worse failure.
    Three existing tests enforce this; see the note in reconcile_from_venue."""
    s = store(tmp_path)
    s.state["positions"]["%s|DOWN" % MKT] = "0.13"
    s.reconcile_from_venue([], [])
    assert s.state["positions"].get("%s|DOWN" % MKT) == "0.13"
    assert not s.state.get("cost_less_drops")


def test_the_long_side_row_is_recorded_the_same_way(tmp_path):
    """The side is not what makes it a phantom -- the absent fill is."""
    s = store(tmp_path)
    s.reconcile_from_venue([LONG_ROW], [])
    assert s.state["cost_less_adoptions"][-1]["side"] == "UP"


def test_reconcile_still_returns_its_report(tmp_path):
    s = store(tmp_path)
    out = s.reconcile_from_venue([SHORT_ROW], [])
    assert isinstance(out, dict) and "drift" in out


# ---- invariant 3, behind an explicit attestation ------------------------
def test_an_attested_flat_read_drops_the_stuck_phantom(tmp_path):
    s = store(tmp_path)
    s.state["positions"]["%s|DOWN" % MKT] = "0.13"
    s.reconcile_from_venue([], [], venue_flat_attested=True)
    assert ("%s|DOWN" % MKT) not in s.state["positions"]
    drop = s.state["cost_less_drops"][-1]
    assert drop["settlement_pnl"] == "0"
    assert drop["anchor"]["venue_flat_attested"] is True


def test_without_the_attestation_nothing_is_dropped(tmp_path):
    s = store(tmp_path)
    s.state["positions"]["%s|DOWN" % MKT] = "0.13"
    s.reconcile_from_venue([], [])
    assert s.state["positions"].get("%s|DOWN" % MKT) == "0.13"


def test_an_attested_read_that_still_returns_rows_drops_nothing(tmp_path):
    """`venue_flat_attested` is not a licence: if the venue did report rows,
    the account is not flat and the branch must not run."""
    s = store(tmp_path)
    s.state["positions"]["%s|DOWN" % MKT] = "0.13"
    s.reconcile_from_venue([LONG_ROW], [], venue_flat_attested=True)
    assert s.state["positions"].get("%s|DOWN" % MKT) is not None


def test_an_attested_flat_read_keeps_a_costed_position(tmp_path):
    s = store(tmp_path)
    s.apply_fill(MKT, "DOWN", D("0.13"), D("0.40"), coid="c1", basis=LX.WIRE)
    s.reconcile_from_venue([], [], venue_flat_attested=True)
    assert s.state["positions"].get("%s|DOWN" % MKT) is not None
    assert not s.state.get("cost_less_drops")
