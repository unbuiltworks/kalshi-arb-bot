"""The 0415z ledger correction: prepared, auditable, and the owner's to apply.

Window 0415z of 2026-11-11 settled at -$0.4400 in the ledger against
-$0.1408 charged by the venue (see tests/test_l5_fill_after_position_update
for how). The correction is an entry in
`COST_BASIS_RESTATEMENTS_AWAITING_OWNER`: deploying the code applies
nothing; it is applied once, through `InventoryStore.correct_cost_basis`
and all of its guards, only when `L5_COST_RESTATEMENT_APPROVED` names its
id. The original row is preserved, not erased.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

ENTRY = R.COST_BASIS_RESTATEMENTS_AWAITING_OWNER[0]
MARKET = "cpc-btc-updown-15m-2026-11-11-0415z"
KEY = "%s|UP" % MARKET
BEFORE_WINDOW = D("-15.38160000")      # all-time, before 0415z settled
BOOKED = D("-15.82160000")             # what production holds now
RESTATED = D("-15.52240000")           # what the venue's cash says
RESOLUTION = {"source": "VENUE_SETTLEMENT_PX", "market": MARKET,
              "yes_value": "0.0000"}


@pytest.fixture()
def settled():
    """The ledger as 04:30:32 left it, written by the real settlement."""
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["positions"] = {KEY: "0.4400"}
        s.state["leg_cost"] = {KEY: "0.4400"}
        s.state["leg_cost_basis_unknown"] = [KEY]
        s.state["committed"] = {MARKET: "0.140800"}
        s.state["risk_floor_usd"] = {MARKET: "0.4400"}
        s.state["realized_attributed"] = str(BEFORE_WINDOW)
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = str(BEFORE_WINDOW)
        out = s.retire_settled_market(MARKET, yes_value=D("0.0000"),
                                      resolution=RESOLUTION)
        assert out["retired"] is True, out
        yield s


def captured(monkeypatch):
    lines = []
    monkeypatch.setattr(R, "emit", lambda label, rec: lines.append(
        (label, rec)))
    return lines


class TestTheReplayReproducesProduction:

    def test_the_row_is_the_one_production_logged(self, settled):
        row = settled.state["retired_markets"][MARKET]
        mismatch = {k: (v, row.get(k)) for k, v in ENTRY["expect"].items()
                    if str(row.get(k)) != str(v)}
        assert mismatch == {}
        assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"

    def test_the_all_time_realized_matches_production(self, settled):
        assert settled.realized_today() == BOOKED

    def test_the_fill_reconciles_to_the_measured_cost(self):
        total = sum(D(f["qty"]) * D(f["price"])
                    for f in ENTRY["evidence"]["per_fill"])
        assert total == ENTRY["measured_cost"] == D("0.1408")


class TestNothingHappensWithoutTheOwner:

    @pytest.mark.parametrize("raw", [None, "", "YES", "1", "0415z",
                                     ENTRY["id"].upper(), ENTRY["id"] + "x"])
    def test_deploying_the_code_changes_no_number(self, settled,
                                                  monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(R.COST_RESTATEMENT_APPROVAL_ENV,
                               raising=False)
        else:
            monkeypatch.setenv(R.COST_RESTATEMENT_APPROVAL_ENV, raw)
        lines = captured(monkeypatch)
        R.apply_cost_basis_restatements(settled)
        row = settled.state["retired_markets"][MARKET]
        assert row["settlement_pnl"] == "-0.44000000"
        assert settled.realized_today() == BOOKED
        assert not row.get("cost_superseded")
        waiting = [r for lab, r in lines
                   if lab == "L5_LIVE_COST_RESTATE_AWAITING_OWNER"]
        assert [w["id"] for w in waiting] == [ENTRY["id"]]

    def test_the_older_list_is_untouched(self):
        assert len(R.COST_BASIS_RESTATEMENTS) == 1
        assert ENTRY not in R.COST_BASIS_RESTATEMENTS


class TestWithTheOwnersApproval:

    @pytest.fixture()
    def applied(self, settled, monkeypatch):
        monkeypatch.setenv(R.COST_RESTATEMENT_APPROVAL_ENV, ENTRY["id"])
        lines = captured(monkeypatch)
        out = R.apply_cost_basis_restatements(settled)
        return settled, out, lines

    def test_it_is_applied_once(self, applied):
        s, out, lines = applied
        assert [a["id"] for a in out["applied"]] == [ENTRY["id"]]
        assert [lab for lab, _r in lines].count("L5_LIVE_COST_RESTATED") == 1

    def test_the_row_now_carries_the_measured_cost(self, applied):
        s, _out, _lines = applied
        row = s.state["retired_markets"][MARKET]
        assert D(row["cost_total"]) == D("0.1408")
        assert D(row["settlement_pnl"]) == D("-0.1408")

    def test_realized_moves_by_exactly_the_overstatement(self, applied):
        s, _out, _lines = applied
        assert s.realized_today() == RESTATED
        assert RESTATED - BOOKED == D("0.2992")

    def test_the_original_row_is_preserved_not_erased(self, applied):
        s, _out, _lines = applied
        row = s.state["retired_markets"][MARKET]
        was = row["cost_superseded"]
        assert "0.4400" in str(was) and "-0.44000000" in str(was)
        recs = [r for r in s.state["cost_basis_corrections"]
                if r.get("id") == ENTRY["id"]]
        assert len(recs) == 1
        assert "DN6KVVR0144P" in str(recs[0])

    def test_a_second_boot_changes_nothing(self, applied, monkeypatch):
        s, _out, _lines = applied
        lines = captured(monkeypatch)
        assert R.apply_cost_basis_restatements(s) is None
        assert s.realized_today() == RESTATED
        refused = [r for lab, r in lines
                   if lab == "L5_LIVE_COST_RESTATE_REFUSED"
                   and r.get("id") == ENTRY["id"]]
        assert refused and refused[0]["reason"] in (
            "COST_CORRECTION_ALREADY_APPLIED", "COST_ALREADY_CORRECTED")

    def test_it_refuses_a_row_that_is_not_the_one_it_names(
            self, settled, monkeypatch):
        settled.state["retired_markets"][MARKET]["up_qty"] = "0.5000"
        monkeypatch.setenv(R.COST_RESTATEMENT_APPROVAL_ENV, ENTRY["id"])
        captured(monkeypatch)
        assert R.apply_cost_basis_restatements(settled) is None
        assert settled.realized_today() == BOOKED
