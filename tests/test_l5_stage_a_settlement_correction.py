"""The Stage A settlement correction: auditable, idempotent, halt-tightening.

The 2230z row was retired against the expired book's price 0.0100 and booked
-0.561000 when the window had lost -0.590000. This restates that ONE row,
preserves the original verbatim, and can only ever move realized further from
zero.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

SPEC = R.SETTLEMENT_CORRECTIONS[0]
M = SPEC["market"]


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        # the live ledger as deployment c6ddfbcd holds it
        s.state["retired_markets"] = {M: {
            "t": "2026-10-14T22:45:31.560354+00:00",
            "yes_value": "0.0100", "settlement_pnl": "-0.561000",
            "up_qty": "2.90", "dn_qty": "0",
            "cost_total": "0.590000", "redemption_total": "0.029000",
        }}
        s.state["realized_attributed"] = "-0.661000"
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = "-0.661000"
        yield s


def apply(store):
    return store.correct_settled_market(
        M, correction_id=SPEC["id"], expect=SPEC["expect"],
        corrected_yes_value=SPEC["corrected_yes_value"],
        evidence=SPEC["evidence"])


def test_there_is_exactly_one_call_site_and_it_is_boot_only():
    """Owner-approved for ONE market, ONCE. Not a tick, not a migration."""
    import inspect
    src = inspect.getsource(R)
    calls = [ln.strip() for ln in src.splitlines()
             if "apply_settlement_corrections(" in ln
             and not ln.lstrip().startswith("def ")]
    assert calls == ["apply_settlement_corrections(self.store)"]
    # and it is in __init__, not in tick/poll/sweep
    init = inspect.getsource(R.Runner.__init__)
    assert "apply_settlement_corrections(self.store)" in init
    assert "apply_settlement_corrections" not in inspect.getsource(R.Runner.tick)


def test_the_list_holds_exactly_the_one_approved_market():
    assert len(R.SETTLEMENT_CORRECTIONS) == 1
    assert SPEC["market"] == "cpc-btc-updown-15m-2026-10-14-2230z"


def test_a_second_boot_over_the_same_ledger_changes_nothing(store):
    """The boot call is safe to repeat: this is what every restart does."""
    first = R.apply_settlement_corrections(store)
    assert first[0]["corrected"] is True
    reopened = LX.InventoryStore(store.path)
    again = R.apply_settlement_corrections(reopened)
    assert again[0]["corrected"] is False
    assert again[0]["reason"] == "CORRECTION_ALREADY_APPLIED"
    assert reopened.realized_today() == D("-0.690000")
    assert len(reopened.state["settlement_corrections"]) == 1


def test_before_and_after_are_exactly_the_published_figures(store):
    assert store.realized_today() == D("-0.661000")
    out = apply(store)
    assert out["corrected"] is True
    assert out["delta"] == "-0.029000"
    assert out["realized_before"]["realized_attributed"] == "-0.661000"
    assert out["realized_after"]["realized_attributed"] == "-0.690000"
    assert out["realized_after"]["realized_unattributed"] == "0"
    assert out["realized_after"]["realized_total"] == "-0.690000"
    row = store.state["retired_markets"][M]
    assert row["yes_value"] == "0"
    assert row["settlement_pnl"] == "-0.590000"
    assert D(row["redemption_total"]) == D(0)


def test_the_original_record_is_preserved_verbatim(store):
    apply(store)
    sup = store.state["retired_markets"][M]["superseded"]
    assert sup["yes_value"] == "0.0100"
    assert sup["settlement_pnl"] == "-0.561000"
    assert sup["redemption_total"] == "0.029000"
    assert sup["t"] == "2026-10-14T22:45:31.560354+00:00"


def test_the_audit_row_carries_the_evidence(store):
    apply(store)
    log = store.state["settlement_corrections"]
    assert len(log) == 1
    assert log[0]["id"] == SPEC["id"]
    assert log[0]["was_settlement_pnl"] == "-0.561000"
    assert log[0]["now_settlement_pnl"] == "-0.590000"
    assert log[0]["delta"] == "-0.029000"
    assert "242.97505" in log[0]["evidence"]["cash_anchor"]["pre_arm_balance"]


# ------------------------------------------------------------ idempotency

def test_a_second_application_changes_nothing(store):
    apply(store)
    again = apply(store)
    assert again["corrected"] is False
    assert again["reason"] == "CORRECTION_ALREADY_APPLIED"
    assert store.realized_today() == D("-0.690000")
    assert len(store.state["settlement_corrections"]) == 1


def test_idempotent_across_a_restart(store):
    apply(store)
    reopened = LX.InventoryStore(store.path)
    again = reopened.correct_settled_market(
        M, correction_id=SPEC["id"], expect=SPEC["expect"],
        corrected_yes_value=D(0), evidence={})
    assert again["corrected"] is False
    assert reopened.realized_today() == D("-0.690000")


def test_a_fresh_id_still_cannot_correct_a_corrected_row(store):
    apply(store)
    other = store.correct_settled_market(
        M, correction_id="some-other-id", expect=SPEC["expect"],
        corrected_yes_value=D(0), evidence={})
    assert other["corrected"] is False
    assert other["reason"] == "ALREADY_CORRECTED"
    assert store.realized_today() == D("-0.690000")


def test_a_ledger_that_does_not_match_the_evidence_is_refused(store):
    store.state["retired_markets"][M]["settlement_pnl"] = "-0.400000"
    out = apply(store)
    assert out["corrected"] is False
    assert out["reason"] == "LEDGER_DOES_NOT_MATCH_EVIDENCE"
    assert "settlement_pnl" in out["mismatch"]
    assert store.realized_today() == D("-0.661000")


def test_a_market_never_retired_is_refused(store):
    out = store.correct_settled_market(
        "cpc-btc-updown-15m-2026-10-14-2245z", correction_id="x",
        expect={}, corrected_yes_value=D(0), evidence={})
    assert out["corrected"] is False
    assert out["reason"] == "MARKET_NOT_RETIRED"


# ------------------------------------------------- the halt cannot be relaxed

def test_a_correction_that_would_improve_realized_is_refused(store):
    """The only direction available is further from zero."""
    out = store.correct_settled_market(
        M, correction_id="would-improve", expect=SPEC["expect"],
        corrected_yes_value=D(1), evidence={})
    assert out["corrected"] is False
    assert out["reason"] == "CORRECTION_WOULD_RELAX_LOSS_HALT"
    assert store.realized_today() == D("-0.661000")


def test_the_corrected_total_is_closer_to_the_halt_not_further(store):
    before = store.realized_today()
    apply(store)
    after = store.realized_today()
    assert after < before
    limits = LX.SafetyLimits(daily_realized_loss_halt_usd=D("5"))
    # the halt fires at <= -5; both figures are above it, and the corrected
    # one is nearer, so the correction can only bring the halt forward.
    assert limits.refuse(
        price=D("0.5"), qty=D("1"), market=M, committed_by_market={},
        residual_after=D(0), realized_today=after, open_markets=[],
        outstanding_by_market={}, live_order_count=0) != "DAILY_REALIZED_LOSS_HALT"
    assert limits.refuse(
        price=D("0.5"), qty=D("1"), market=M, committed_by_market={},
        residual_after=D(0), realized_today=D("-5.0") + (after - before),
        open_markets=[], outstanding_by_market={}, live_order_count=0
    ) == "DAILY_REALIZED_LOSS_HALT"


def test_unattributed_is_never_invented(store):
    """rebase_session_accounting would have posted +0.100000 here, because
    Stage A's cash anchor does not span the 2026-10-12 line. That credit
    would have made realized LESS negative."""
    apply(store)
    assert store.state["realized_unattributed"] == "0"
    assert store.realized_lines()["realized_total"] == "-0.690000"
