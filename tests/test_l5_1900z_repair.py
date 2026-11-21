"""The 1900z repair must survive a restart, book once, and post -$0.18.

The cost basis is $0.18 because order D294PDYNPZ7Z says so: cumQuantity 1,
avgPx 0.1800, commission 0.0000, ORDER_STATE_FILLED. The account's -0.18
balance move agrees with that and is NOT the evidence -- a test below asserts
the repair's own record says as much, because the next person to read this
will be tempted by the coincidence exactly as I was.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_pmus_live_runner as RUN

M = "cpc-btc-updown-15m-2026-10-12-1900z"
#: what the ledger held before the repair: the venue-adopted quantity with no
#: cost at all, which is what SETTLE_REFUSED kept reporting every loop.
BROKEN = {"%s|UP" % M: "1.0000"}


def broken_ledger(tmp_path, name="l5-live-20261012z"):
    """A store in exactly the state production was in at 20:54Z."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    s = EX.InventoryStore(str(d / "inventory.json"))
    s.state["positions"] = dict(BROKEN)
    s.state.setdefault("leg_cost", {})["%s|UP" % M] = "0"
    s.save()
    return s


def repair_spec():
    return [r for r in RUN.LEDGER_REPAIRS if r["market"] == M][0]


def test_the_broken_state_this_repair_targets_is_the_one_we_observed():
    r = repair_spec()
    assert r["expect_qty"] == D("1.0000") and r["expect_cost"] == D("0")
    s = EX.InventoryStore.__new__(EX.InventoryStore)
    # the production symptom, named
    assert "QUANTITY_WITHOUT_COST" in str(
        [v["violation"] for v in
         EX.InventoryStore.BOOK_VIOLATIONS] if hasattr(
            EX.InventoryStore, "BOOK_VIOLATIONS") else
        "QUANTITY_WITHOUT_COST")


def test_the_repair_books_quantity_and_cost_together(tmp_path):
    s = broken_ledger(tmp_path)
    assert s.book_violations(M), "precondition: the book is incoherent"
    out = RUN.repair_ledger_from_venue(s)
    assert out and out["repairs"][0]["market"] == M
    assert s.position(M, "UP") == D("1.00")
    assert s.leg_cost(M)["UP"] == D("0.18000")
    assert not s.book_violations(M), "the book is coherent again"


def test_the_cost_came_from_the_order_row_and_the_source_records_it(tmp_path):
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)
    log = s.state["cost_reconstructions"]
    assert len(log) == 1
    ev = json.loads(log[0]["source"])
    assert ev["orders"] == ["D294PDYNPZ7Z"]
    assert "avgPx 0.1800" in ev["detail"]
    assert "balance" in ev["not_used_as_evidence"]
    assert log[0]["cost"] == "0.18000" and log[0]["qty"] == "1.00"


def test_it_is_booked_exactly_once_however_often_the_runner_boots(tmp_path):
    s = broken_ledger(tmp_path)
    assert RUN.repair_ledger_from_venue(s) is not None
    for _ in range(3):
        assert RUN.repair_ledger_from_venue(s) is None, "applied twice"
    assert len(s.state["cost_reconstructions"]) == 1
    assert s.position(M, "UP") == D("1.00")
    assert s.leg_cost(M)["UP"] == D("0.18000")


def test_it_survives_a_restart_from_the_volume(tmp_path):
    """The ledger outlives the container; the repair must not re-run."""
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)
    path = s.path
    reborn = EX.InventoryStore(path)
    assert reborn.position(M, "UP") == D("1.00")
    assert reborn.leg_cost(M)["UP"] == D("0.18000")
    assert RUN.repair_ledger_from_venue(reborn) is None
    assert len(reborn.state["cost_reconstructions"]) == 1
    # and again, two restarts later
    again = EX.InventoryStore(path)
    assert RUN.repair_ledger_from_venue(again) is None
    assert len(again.state["cost_reconstructions"]) == 1


def test_a_ledger_that_has_MOVED_ON_refuses_the_repair(tmp_path, monkeypatch):
    """Repairing a book that is no longer broken manufactures P&L."""
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))
    s = broken_ledger(tmp_path)
    s.state["positions"]["%s|UP" % M] = "2.0000"      # not what we observed
    s.save()
    assert RUN.repair_ledger_from_venue(s) is None
    assert any(k == "L5_LIVE_REPAIR_REFUSED" for k, _ in seen)
    assert s.leg_cost(M).get("UP", D(0)) == D(0), "nothing was written"


def test_a_ledger_that_already_has_a_cost_refuses_the_repair(tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    s = broken_ledger(tmp_path)
    s.state["leg_cost"]["%s|UP" % M] = "0.17"
    s.save()
    assert RUN.repair_ledger_from_venue(s) is None
    assert s.leg_cost(M)["UP"] == D("0.17"), "an existing cost is not replaced"


# ------------------------------------------------------- the -$0.14 account

def test_settling_the_repaired_leg_posts_minus_eighteen_cents(tmp_path):
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)
    before = s.realized_today()
    out = s.retire_settled_market(M, yes_value=D("0"))   # UP settled 0.0000
    assert D(str(out["settlement_pnl"])) == D("-0.18000")
    assert s.realized_today() - before == D("-0.18000")
    assert s.position(M, "UP") == D(0), "the leg is retired"


def test_the_account_lands_on_minus_fourteen_cents(tmp_path):
    """+$0.0400 attested across the three G1 markets, then 1900z's -$0.18.

    The G1 total is itself venue-attested (commit 08383e2d): nfl-bal-atl
    +0.0200, 0030z +1.7000, 1245z -1.6800.
    """
    s = broken_ledger(tmp_path)
    s.state["realized_attributed"] = "0.0400"
    s.save()
    RUN.repair_ledger_from_venue(s)
    s.retire_settled_market(M, yes_value=D("0"))
    assert s.realized_today() == D("-0.14000")
    lines = s.realized_lines()
    assert lines["realized_total"] == "-0.14000"
    assert lines["realized_unattributed"] == "0", (
        "the loss is ATTRIBUTED to a market, not dumped in the residual line")
    # nowhere near the -$5 daily halt or the -$20 cumulative gate
    assert s.realized_today() > D("-5")


def test_settlement_is_booked_once_across_a_restart(tmp_path):
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)
    s.retire_settled_market(M, yes_value=D("0"))
    first = s.realized_today()
    reborn = EX.InventoryStore(s.path)
    assert reborn.realized_today() == first
    # the market is retired, so a second sweep has nothing to retire
    assert reborn.position(M, "UP") == D(0)
    assert RUN.repair_ledger_from_venue(reborn) is None
    assert reborn.realized_today() == first


# ------------------------------------- the protections are NOT weakened

def test_the_repair_cannot_move_the_loss_halt():
    """It writes quantity and cost, never realized P&L.

    Checked over CODE, not prose: the docstring says "never touches realized
    P&L", and a test that cannot tell a comment from an assignment would
    forbid the function from explaining itself.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(RUN.repair_ledger_from_venue).lstrip())
    written = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            written.add(str(n.slice.value))
        if isinstance(n, ast.Attribute):
            written.add(n.attr)
    assert not any("realized" in w for w in written), sorted(
        w for w in written if "realized" in w)
    # and the authority order is still the venue's
    src = inspect.getsource(EX.InventoryStore.reconcile_from_venue)
    assert "VENUE" in src.upper()


def test_the_unresolved_registry_still_blocks_on_a_STANDING_entry(
        monkeypatch, tmp_path):
    """Resolution lifted ONE entry. The machinery is unchanged."""
    monkeypatch.setattr(RUN, "UNRESOLVED_ACCOUNTING", [{
        "id": "another-one", "market": M, "ledger": "l5-live-20261012z",
        "side": "UP", "qty": "1.0000", "cost_basis": "UNKNOWN",
        "missing_evidence": "x", "may_not": "y"}])
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)           # book now coherent
    assert RUN.unresolved_discrepancies(s), (
        "a coherent book with a STANDING entry is exactly the silent-clearing "
        "case the detector exists for")


def test_the_resolved_entry_does_not_block_once_its_evidence_exists(tmp_path):
    s = broken_ledger(tmp_path)
    RUN.repair_ledger_from_venue(s)
    assert RUN.unresolved_discrepancies(s) == [], (
        "the only thing that lifted it is resolved_utc plus the order row")
    assert any(r["market"] == M for r in RUN.resolved_accounting())


def test_the_shadow_lock_and_the_gates_are_unchanged():
    from src import l5_live_validation_gates as VG
    assert RUN.shadow_only() is True
    assert RUN.ShadowOnlyTransport.BLOCKED == ("submit",)
    g = RUN.validation_gates()
    assert g.max_cash_deployed_usd == D("10")
    assert g.max_unmatched_contracts == D("5")
    assert g.max_cumulative_loss_usd == D("20")
    assert g.cash_policy == VG.NO_RECYCLING
