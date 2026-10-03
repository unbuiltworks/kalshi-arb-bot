"""Every bound on the R2 execution-validation cohort, proved.

Each requirement is paired with its NEGATIVE: the thing that must be refused
is asserted to raise, not merely the thing that must be allowed. A budget that
only passes its happy path is not a budget.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src.gemini_execution import cohort, r2_exec_budget as B

CID = "COHORT-TEST-R2-EXEC"


@pytest.fixture()
def root(tmp_path, monkeypatch):
    r = str(tmp_path / "cohorts")
    pred = tmp_path / "hist.jsonl"
    pred.write_text('{"kind":"PLACE"}\n')
    monkeypatch.setattr(cohort, "HISTORICAL_LEDGER", str(pred))
    B.create(CID, root=r, note="test")
    return r


def intent(price="0.80", qty="1"):
    return {"price": price, "quantity": qty, "side": "buy", "outcome": "yes"}


# ---------------------------------------------------------------- the budget

def test_budget_is_ten_and_quantity_is_one():
    assert B.BUDGET == 10
    assert B.QUANTITY == D("1")


def test_structural_maximum_is_derived_not_asserted():
    """10.20 must be a CONSEQUENCE of the bounds, not a number typed in."""
    assert B.MAX_LOSS_PER_PROBE == D("1.00") + D("0.02") == D("1.02")
    assert B.STRUCTURAL_MAX_LOSS == B.MAX_LOSS_PER_PROBE * B.BUDGET
    assert B.STRUCTURAL_MAX_LOSS == D("10.20")


def test_max_fee_claim_holds_at_every_cent():
    """ceil(0.07*1*P*(1-P)) must never exceed the 0.02 the ceiling assumes."""
    from decimal import ROUND_CEILING
    worst = max(
        (D("0.07") * D("1") * (D(p) / 100) * (1 - D(p) / 100)
         ).quantize(D("0.01"), rounding=ROUND_CEILING)
        for p in range(1, 100))
    assert worst == B.MAX_FEE_AT_ANY_PRICE == D("0.02")


def test_no_function_can_raise_the_budget():
    import inspect
    src = inspect.getsource(B)
    assert "BUDGET =" in src
    # exactly one assignment to BUDGET, at module scope
    assert sum(1 for ln in src.splitlines()
               if ln.strip().startswith("BUDGET =")) == 1
    for bad in ("BUDGET +=", "BUDGET = BUDGET", "def replenish", "def extend",
                "def increase", "def reset"):
        assert bad not in src, bad


def test_ten_slots_then_refused(root):
    for i in range(10):
        row = B.consume_slot(CID, "MKT%02d" % i, intent(), root=root)
        assert row["slot"] == i
    assert B.remaining(CID, root=root) == 0
    with pytest.raises(B.BudgetRefused) as e:
        B.consume_slot(CID, "MKT10", intent(), root=root)
    assert "BUDGET_EXHAUSTED" in str(e.value)


# ------------------------------------------------- one probe per market

def test_one_submission_per_market(root):
    B.consume_slot(CID, "MKT-A", intent(), root=root)
    with pytest.raises(B.BudgetRefused) as e:
        B.consume_slot(CID, "MKT-A", intent(), root=root)
    assert "MARKET_ALREADY_FINISHED" in str(e.value)
    assert B.consumed(CID, root=root) == 1, "the refusal must not spend a slot"


def test_a_missed_market_consumes_no_slot_but_is_never_revisited(root):
    B.finish_market(CID, "MKT-B", "MISSED_NOT_EXECUTABLE",
                    {"why": "offer vanished"}, root=root)
    assert B.consumed(CID, root=root) == 0, "MISSED spends no slot"
    g = B.may_probe(CID, "MKT-B", root=root)
    assert not g["ok"] and "MARKET_ALREADY_FINISHED" in g["why"]


# ------------------------------------------------------------ q and price

def test_quantity_must_be_exactly_one(root):
    for bad in ("2", "0.01", "0", "1.5"):
        with pytest.raises(B.BudgetRefused):
            B.consume_slot(CID, "MKT-Q" + bad, intent(qty=bad), root=root)
    assert B.consumed(CID, root=root) == 0


def test_price_must_be_inside_zero_to_one(root):
    for bad in ("0", "1.01", "-0.5"):
        with pytest.raises(B.BudgetRefused):
            B.consume_slot(CID, "MKT-P" + bad, intent(price=bad), root=root)
    assert B.consumed(CID, root=root) == 0


# -------------------------------------------- durability and restart recovery

def test_slot_is_on_disk_before_the_caller_can_submit(root):
    B.consume_slot(CID, "MKT-D", intent(), root=root)
    led = os.path.join(cohort.directory(CID, root=root), cohort.LEDGER)
    rows = [json.loads(l) for l in open(led) if l.strip()]
    assert rows and rows[0]["kind"] == "PLACE" and rows[0]["slot"] == 0


def test_restart_recovers_consumed_slots_rather_than_resetting(root):
    for i in range(4):
        B.consume_slot(CID, "MKT-R%d" % i, intent(), root=root)
    # a "restart" is simply another reader: nothing is cached in the process
    import importlib
    import src.gemini_execution.r2_exec_budget as fresh
    importlib.reload(fresh)
    assert fresh.consumed(CID, root=root) == 4
    assert fresh.remaining(CID, root=root) == 6


def test_a_torn_row_counts_as_consumed(root):
    """A crash mid-write may or may not have reached the venue. Counting it
    free could send an eleventh probe; counting it spent wastes one."""
    B.consume_slot(CID, "MKT-T", intent(), root=root)
    led = os.path.join(cohort.directory(CID, root=root), cohort.LEDGER)
    with open(led, "a") as fh:
        fh.write('{"kind": "PLACE", "slot": 1, "inten')      # torn
    assert B.consumed(CID, root=root) == 2


def test_ambiguous_send_leaves_the_slot_spent(root):
    """There is no release path, which is what makes 'never retried' true."""
    import inspect
    src = inspect.getsource(B)
    for bad in ("def release", "def refund", "def unconsume", "def rollback"):
        assert bad not in src, bad
    B.consume_slot(CID, "MKT-AMB", intent(), root=root)
    assert B.consumed(CID, root=root) == 1
    g = B.may_probe(CID, "MKT-AMB", root=root)
    assert not g["ok"]


# ------------------------------------------------------- the loss stop

def test_only_confirmed_cashflow_counts_toward_the_loss_stop(root):
    B.record_settlement(CID, "MKT-U", slot=0, realized_principal_net="-1.00",
                        cashflow_confirmed=False, evidence="resolution only",
                        root=root)
    assert B.realized_principal_loss(CID, root=root) == D("0")
    B.record_settlement(CID, "MKT-C", slot=1, realized_principal_net="-1.00",
                        cashflow_confirmed=True, evidence="positions FLAT",
                        root=root)
    assert B.realized_principal_loss(CID, root=root) == D("1.00")


def test_loss_stop_expires_the_cohort_at_eight_dollars(root):
    assert B.expired(CID, root=root) is None
    for i in range(8):
        B.record_settlement(CID, "MKT-L%d" % i, slot=i,
                            realized_principal_net="-1.00",
                            cashflow_confirmed=True, evidence="x", root=root)
    why = B.expired(CID, root=root)
    assert why and "REALIZED_LOSS_STOP" in why
    with pytest.raises(B.BudgetRefused):
        B.consume_slot(CID, "MKT-AFTER", intent(), root=root)


def test_wins_do_not_offset_the_loss_stop_into_more_room(root):
    """The stop is on LOSS, not on net. A winner must not buy another probe."""
    for i in range(8):
        B.record_settlement(CID, "MKT-X%d" % i, slot=i,
                            realized_principal_net="-1.00",
                            cashflow_confirmed=True, evidence="x", root=root)
    B.record_settlement(CID, "MKT-WIN", slot=9, realized_principal_net="+5.00",
                        cashflow_confirmed=True, evidence="x", root=root)
    assert B.realized_principal_loss(CID, root=root) == D("8.00")
    assert "REALIZED_LOSS_STOP" in B.expired(CID, root=root)


# --------------------------------------------------- the statistical gate

def test_statistical_gate_arrival_expires_the_cohort(root):
    assert B.expired(CID, root=root, stat_gate_windows=99) is None
    why = B.expired(CID, root=root, stat_gate_windows=100)
    assert why and "STATISTICAL_GATE_REACHED" in why
    with pytest.raises(B.BudgetRefused):
        B.consume_slot(CID, "MKT-GATE", intent(), root=root,
                       stat_gate_windows=100)


# ------------------------------------------------------- creation discipline

def test_a_cohort_is_created_once(root):
    with pytest.raises(cohort.CohortRefused):
        B.create(CID, root=root)


def test_state_opens_no_mutation_path(root):
    B.consume_slot(CID, "MKT-S", intent(), root=root)
    s = B.state(CID, root=root)
    assert s["budget"] == 10 and s["consumed"] == 1 and s["remaining"] == 9
    assert s["structural_max_loss"] == "10.20"
    assert s["realized_loss_stop"] == "8.00"
    assert s["markets_submitted"] == 1
