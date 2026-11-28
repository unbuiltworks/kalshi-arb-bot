"""The audited verified-L5 NET attestation writer.

THE GAP THESE COVER. verified_l5_net_for_gates() always read a
`verified_l5_net_attestation` record and nothing in src/ or scripts/ ever
wrote one, so the independent strategy-loss gate could never activate:
opting in refused STRATEGY_NET_UNVERIFIED forever. These tests pin the
writer AND the properties that keep it from becoming a way to shrink an
account-wide loss.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_live_execution as X            # noqa: E402
from src import l5_live_validation_gates as VG    # noqa: E402


def _store(tmp_path):
    return X.InventoryStore(str(tmp_path / "s.json"))


def _verified(store, market, pnl, coids, fees):
    """A market that has passed the verified-L5 settlement test."""
    store.state["verified_l5_settlement_gross"] = str(
        D(str(store.state.get("verified_l5_settlement_gross") or 0)) + D(pnl))
    store.state.setdefault("verified_l5_settlements", []).append({
        "market": market, "pnl_before_fees": str(pnl),
        "fee_rebate_status": "NOT_VERIFIED",
        "source": "VENUE_SETTLEMENT_AND_MATCHED_L5_FILL_ORDERS"})
    store.state.setdefault("fill_provenance", {})[market] = {
        "legs": {}, "entries": [
            {"coid": c, "l5_owner_verified": True} for c in coids]}
    fm = store.state.setdefault("fees", {})
    for c, f in fees.items():
        fm[c] = str(f)


# ---------------------------------------------------------------- happy path
def test_gross_minus_recorded_fees_becomes_an_accepted_attestation(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "2.00", ["c1", "c2"], {"c1": "0.03", "c2": "0.04"})
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is True
    rec = out["attestation"]
    assert rec["source"] == s.VERIFIED_L5_NET_SOURCE
    assert rec["gross_usd"] == "2.00"
    assert rec["fees_usd"] == "0.07"
    assert rec["net_usd"] == "1.93"
    assert rec["order_ids"] == ["c1", "c2"]
    assert rec["fee_evidence"] == {"c1": "0.03", "c2": "0.04"}
    # and the READER accepts exactly this record
    assert s.verified_l5_net_for_gates() == D("1.93")


def test_a_loss_nets_more_negative_after_fees(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "-1.00", ["c1"], {"c1": "0.05"})
    s.reconcile_verified_l5_net()
    assert s.verified_l5_net_for_gates() == D("-1.05")


def test_a_recorded_zero_commission_is_a_fact_and_is_accepted(tmp_path):
    """A zero RECORDED fee is evidence; an ABSENT one is not."""
    s = _store(tmp_path)
    _verified(s, "m1", "0.50", ["c1"], {"c1": "0"})
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is True
    assert out["attestation"]["net_usd"] == "0.50"
    assert out["attestation"]["fee_evidence"]["c1"] == "0"


def test_the_rows_are_marked_fees_verified_rebates_excluded(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "0.01"})
    s.reconcile_verified_l5_net()
    rows = s.state["verified_l5_settlements"]
    assert rows[0]["fee_rebate_status"] == "FEES_VERIFIED_REBATES_EXCLUDED"
    # rebates are excluded, explicitly and visibly
    assert s.state["verified_l5_net_attestation"]["rebates_usd_excluded"] == "0"


def test_the_attestation_survives_a_reload(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "3.00", ["c1"], {"c1": "0.10"})
    s.reconcile_verified_l5_net()
    again = X.InventoryStore(str(tmp_path / "s.json"))
    assert again.verified_l5_net_for_gates() == D("2.90")


# ------------------------------------------------------------ fails closed
def test_no_verified_settlement_refuses_rather_than_attesting_zero(tmp_path):
    """The correct answer while every leg carries an unknown cost basis."""
    s = _store(tmp_path)
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"] == "NO_VERIFIED_L5_SETTLEMENTS"
    assert "verified_l5_net_attestation" not in s.state
    assert s.verified_l5_net_for_gates() is None


def test_a_missing_fee_is_never_counted_as_zero(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "2.00", ["c1", "c2"], {"c1": "0.03"})   # c2 absent
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"] == "FEE_EVIDENCE_MISSING"
    assert out["orders"] == ["c2"]
    assert s.verified_l5_net_for_gates() is None


def test_an_unparseable_fee_refuses(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "not-a-number"})
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"].startswith("FEE_UNPARSEABLE")
    assert s.verified_l5_net_for_gates() is None


def test_a_fill_without_a_coid_refuses(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "0.01"})
    s.state["fill_provenance"]["m1"]["entries"].append(
        {"coid": None, "l5_owner_verified": True})
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"].startswith("FILL_WITHOUT_COID")


def test_an_unverified_owner_inside_a_verified_row_refuses(tmp_path):
    """A corrupted book, not something to skip past."""
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "0.01"})
    s.state["fill_provenance"]["m1"]["entries"].append(
        {"coid": "manual1", "l5_owner_verified": False})
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"].startswith("UNVERIFIED_OWNER_IN_VERIFIED_ROW")
    assert s.verified_l5_net_for_gates() is None


def test_missing_provenance_refuses(tmp_path):
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "0.01"})
    s.state["fill_provenance"] = {}
    out = s.reconcile_verified_l5_net()
    assert out["ok"] is False
    assert out["reason"].startswith("PROVENANCE_MISSING")


# --------------------------------------- it is NOT an account-wide offset
def test_it_never_touches_account_wide_realized_history(tmp_path):
    """THE PROPERTY THAT MATTERS MOST. This must not become a way to make a
    recorded account-wide loss smaller."""
    s = _store(tmp_path)
    s.state["realized_attributed"] = "-51.14"
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = "-51.14"
    s.state["leg_cost_basis_unknown"] = ["mX|DOWN"]
    _verified(s, "m1", "5.00", ["c1"], {"c1": "0.02"})
    s.reconcile_verified_l5_net()
    assert s.state["realized_attributed"] == "-51.14"
    assert s.state["realized_today"] == "-51.14"
    lines = s.realized_lines()
    assert lines["realized_attributed"] == "-51.14"
    # the legacy total is still NEVER presented as verified
    assert lines["verified_l5_realized_net"] is None
    assert lines["verified_l5_status"] == "UNPROVEN_LEGACY_ATTRIBUTION"
    assert lines["unknown_basis_keys"] == ["mX|DOWN"]


def test_the_account_wide_cumulative_gate_still_binds_with_an_attestation(
        tmp_path):
    """The strategy budget is NEVER substituted for the account-wide cap."""
    s = _store(tmp_path)
    _verified(s, "m1", "1.00", ["c1"], {"c1": "0.01"})
    s.reconcile_verified_l5_net()
    net = s.verified_l5_net_for_gates()
    assert net == D("0.99")                     # a verified PROFIT
    g = VG.ValidationGates(max_cash_deployed_usd=D("7"),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D("20"),
                           max_strategy_loss_usd=D("10"),
                           cash_policy=VG.NO_RECYCLING)
    reason = g.refuse(
        order_qty=D("1"), order_price=D("0.44"), order_intent="BUY_LONG",
        filled_legs={}, outstanding=[], book_uncertain=False,
        unmatched_after=D(0),
        cumulative_realized=D("-51.14"),        # the real account-wide figure
        verified_strategy_net=net,
        cumulative_unrealized_loss=D(0),
        available_cash_usd=D("354"))
    assert reason is not None and reason.startswith("CUMULATIVE_LOSS_LIMIT")
