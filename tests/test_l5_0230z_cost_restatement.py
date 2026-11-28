"""The 0230z cost-basis restatement, verified before it is ever applied.

It replays the production ledger slice as it actually stood -- the adopted
$1/share basis, the real settlement, the real realized line -- then applies
`COST_BASIS_RESTATEMENTS` through the protected verb and checks every effect
the owner asked about: daily P&L, all-time P&L, risk-halt accounting, and
replay idempotency.

PRODUCTION FACTS (service `l5-pmus-live`, deployment 8db131c5):

  realized_today before settlement   -9.59120000   (02:30:23 reconcile)
  realized_today after settlement   -11.50120000   (02:46:23 settlement)
  realized_unattributed                       0
  utc_day 2026-10-30 realized             -1.91
  $5 halt room                             3.09

The restatement must leave the day on 2026-10-30 -- `correct_cost_basis`
rewrites `settlement_pnl` but never `t`, so a loss stays on the day it was
incurred -- and must not touch `realized_unattributed`, any limit, or any
other unknown-basis market.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

MARKET = "cpc-btc-updown-15m-2026-10-30-0230z"
OTHER = "cpc-btc-updown-15m-2026-10-16-0115z"     # another unknown-basis leg
KEY = "%s|DOWN" % MARKET
OTHER_KEY = "%s|UP" % OTHER

QTY = D("1.9100")
PLACEHOLDER = D("1.9100")          # 1.91 x $1.00
MEASURED = D("0.7258")             # 1.91 x 0.38
OVERSTATEMENT = D("1.1842")

REALIZED_BEFORE_SETTLEMENT = D("-9.59120000")
REALIZED_AFTER_SETTLEMENT = D("-11.50120000")
REALIZED_RESTATED = D("-10.31700000")

RESOLUTION = {"source": "VENUE_SETTLEMENT_PX", "market": MARKET,
              "yes_value": "1.0000"}

ENTRY = R.COST_BASIS_RESTATEMENTS[0]


@pytest.fixture()
def settled():
    """The ledger exactly as 02:46:23 left it, built by the real code.

    The adopted state is set directly -- 1.91 DOWN at the $1/share bound,
    the key declared in `leg_cost_basis_unknown` -- and `retire_settled_market`
    then writes the row, so the row under test is the one settlement
    produces rather than one this test asserts into place.
    """
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["positions"] = {KEY: "1.9100", OTHER_KEY: "1.0000"}
        s.state["leg_cost"] = {KEY: "1.9100", OTHER_KEY: "1.0000"}
        s.state["leg_cost_basis_unknown"] = [OTHER_KEY, KEY]
        s.state["committed"] = {MARKET: "1.451600"}
        s.state["risk_floor_usd"] = {MARKET: "1.9100"}
        s.state["realized_attributed"] = str(REALIZED_BEFORE_SETTLEMENT)
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = str(REALIZED_BEFORE_SETTLEMENT)
        out = s.retire_settled_market(MARKET, yes_value=D("1.0000"),
                                      resolution=RESOLUTION)
        assert out["retired"] is True, out
        yield s


class TestTheReplayReproducesProduction:
    """If this drifts, the restatement's precondition is wrong."""

    def test_the_settlement_pnl_is_the_figure_that_was_booked(self, settled):
        row = settled.state["retired_markets"][MARKET]
        assert row["settlement_pnl"] == "-1.91000000"

    def test_the_all_time_realized_matches_production(self, settled):
        assert settled.realized_today() == REALIZED_AFTER_SETTLEMENT

    def test_the_day_realized_matches_production(self, settled):
        assert settled.realized_current_utc_day() == D("-1.91000000")

    def test_the_row_is_labelled_an_upper_bound(self, settled):
        row = settled.state["retired_markets"][MARKET]
        assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
        assert row["unverified_cost_basis_keys"] == [KEY]


class TestThePreconditionMatchesTheStoredRowFieldForField:
    """Guard 4 refuses on any mismatch, so this is what makes the deploy
    either work or refuse loudly -- never guess."""

    def test_every_expected_field_matches(self, settled):
        row = settled.state["retired_markets"][MARKET]
        mismatch = {k: {"expected": v, "found": row.get(k)}
                    for k, v in ENTRY["expect"].items()
                    if str(row.get(k)) != str(v)}
        assert mismatch == {}

    def test_the_entry_names_this_market_and_side(self):
        assert ENTRY["market"] == MARKET
        assert ENTRY["side"] == "DOWN"

    def test_the_list_holds_exactly_one_entry(self):
        assert len(R.COST_BASIS_RESTATEMENTS) == 1


class TestTheMeasuredCostIsRederivedNotAsserted:
    """Guard 6 recomputes from the named fills; `measured_cost` is checked
    against that, never trusted."""

    def test_the_fills_reconcile_to_the_measured_cost(self):
        total = D(0)
        for f in ENTRY["evidence"]["per_fill"]:
            # PMUS quotes the YES price, so a DOWN share costs 1 - price
            total += D(f["qty"]) * (D(1) - D(f["price"]))
        assert total == ENTRY["measured_cost"] == MEASURED

    def test_the_fill_quantity_equals_the_ledger_quantity(self, settled):
        row = settled.state["retired_markets"][MARKET]
        qty = sum(D(f["qty"]) for f in ENTRY["evidence"]["per_fill"])
        assert qty == D(row["dn_qty"]) == QTY

    def test_a_wrong_measured_cost_is_refused(self, settled):
        out = settled.correct_cost_basis(
            MARKET, "DOWN", correction_id="wrong-cost",
            expect=ENTRY["expect"], measured_cost=D("0.5000"),
            measured_fees=D("0.0000"), evidence=ENTRY["evidence"])
        assert out["corrected"] is False
        assert out["reason"] == "COST_NOT_RECONCILED"
        assert settled.realized_today() == REALIZED_AFTER_SETTLEMENT


class TestTheEffectsTheOwnerAskedToVerify:

    @pytest.fixture()
    def done(self, settled):
        res = R.apply_cost_basis_restatements(settled)
        assert res and len(res["applied"]) == 1, res
        return settled, res["applied"][0]

    # ---- daily P&L
    def test_the_day_realized_becomes_the_venue_truth(self, done):
        s, _r = done
        assert s.realized_current_utc_day() == D("-0.72580000")

    def test_the_loss_stays_on_the_day_it_was_incurred(self, done):
        """`t` is not rewritten; `cost_corrected_at` records the later read."""
        s, _r = done
        row = s.state["retired_markets"][MARKET]
        assert row["t"].startswith("20")
        assert row["cost_corrected_at"] != row["t"]
        assert row["t"] == row["cost_superseded"]["t"]

    # ---- all-time P&L
    def test_the_all_time_realized_moves_by_the_overstatement(self, done):
        s, _r = done
        assert s.realized_today() == REALIZED_RESTATED
        assert REALIZED_AFTER_SETTLEMENT + OVERSTATEMENT == REALIZED_RESTATED

    def test_the_delta_is_the_overstatement(self, done):
        _s, r = done
        assert D(r["delta"]) == OVERSTATEMENT

    def test_the_unattributed_line_is_untouched(self, done):
        s, _r = done
        assert s.state["realized_unattributed"] == "0"

    # ---- risk-halt accounting
    def test_the_halt_room_is_restated_and_still_does_not_halt(self, done):
        s, _r = done
        day = s.realized_current_utc_day()
        halt = D("5")
        assert bool(day <= -halt) is False
        assert day + halt == D("4.27420000")

    def test_no_limit_was_moved(self, done):
        _s, r = done
        assert r["halt_unchanged"] is True
        assert r["limits_untouched"] is True

    # ---- the row itself
    def test_the_cost_and_pnl_are_restated(self, done):
        s, _r = done
        row = s.state["retired_markets"][MARKET]
        assert D(row["cost_total"]) == MEASURED
        assert D(row["settlement_pnl"]) == D("-0.72580000")
        assert D(row["redemption_total"]) == 0

    def test_the_provenance_becomes_measured(self, done):
        s, _r = done
        row = s.state["retired_markets"][MARKET]
        assert row["cost_provenance"] == "MEASURED_FROM_VENUE_FILLS"

    def test_the_original_row_is_preserved_verbatim(self, done):
        s, _r = done
        was = s.state["retired_markets"][MARKET]["cost_superseded"]
        assert was["cost_total"] == "1.9100"
        assert was["settlement_pnl"] == "-1.91000000"
        assert was["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"

    def test_the_audit_trail_records_the_trade_ids(self, done):
        s, _r = done
        rec = s.state["cost_basis_corrections"][-1]
        assert rec["id"] == ENTRY["id"]
        assert rec["trade_ids"] == ["DDDXP54G12TT"]
        assert rec["key"] == KEY
        assert rec["cost_provenance"] == "MEASURED_FROM_VENUE_FILLS"
        assert rec["ownership_unchanged"] is True

    def test_ownership_attestation_is_not_invented(self, done):
        """A corrected cost is not evidence of who traded."""
        s, _r = done
        row = s.state["retired_markets"][MARKET]
        assert row["l5_owner_attribution_verified"] is False

    # ---- scope
    def test_no_other_unknown_basis_market_is_touched(self, done):
        s, _r = done
        assert s.state["positions"][OTHER_KEY] == "1.0000"
        assert s.state["leg_cost"][OTHER_KEY] == "1.0000"
        assert OTHER not in (s.state.get("retired_markets") or {})
        assert len(s.state["cost_basis_corrections"]) == 1

    def test_the_unknown_list_is_deliberately_not_cleared(self, done):
        """The established convention -- `leg_cost_basis_unknown` is not
        cleared by a correction; `cost_superseded` is the never-twice flag.
        The key stays, so the audit trail stays."""
        s, _r = done
        assert KEY in s.state["leg_cost_basis_unknown"]
        assert OTHER_KEY in s.state["leg_cost_basis_unknown"]


class TestReplayIdempotency:

    def test_a_second_pass_changes_nothing(self, settled):
        R.apply_cost_basis_restatements(settled)
        after_one = settled.realized_today()
        row_one = dict(settled.state["retired_markets"][MARKET])
        for _ in range(3):
            assert R.apply_cost_basis_restatements(settled) is None
        assert settled.realized_today() == after_one == REALIZED_RESTATED
        assert settled.state["retired_markets"][MARKET] == row_one
        assert len(settled.state["cost_basis_corrections"]) == 1

    def test_the_second_pass_refuses_by_correction_id(self, settled):
        R.apply_cost_basis_restatements(settled)
        out = settled.correct_cost_basis(
            MARKET, "DOWN", correction_id=ENTRY["id"],
            expect=ENTRY["expect"], measured_cost=MEASURED,
            measured_fees=D("0.0000"), evidence=ENTRY["evidence"])
        assert out["reason"] == "COST_CORRECTION_ALREADY_APPLIED"

    def test_it_survives_a_reload_from_disk(self, settled):
        R.apply_cost_basis_restatements(settled)
        reloaded = LX.InventoryStore(settled.path)
        assert reloaded.realized_today() == REALIZED_RESTATED
        assert R.apply_cost_basis_restatements(reloaded) is None


class TestItRefusesRatherThanGuesses:

    def test_a_ledger_that_does_not_match_is_refused(self, settled):
        settled.state["retired_markets"][MARKET]["cost_total"] = "1.5000"
        before = settled.realized_today()
        assert R.apply_cost_basis_restatements(settled) is None
        assert settled.realized_today() == before
        assert not settled.state.get("cost_basis_corrections")

    def test_an_unretired_market_is_refused(self, settled):
        settled.state["retired_markets"].pop(MARKET)
        before = settled.realized_today()
        assert R.apply_cost_basis_restatements(settled) is None
        assert settled.realized_today() == before

    def test_an_incomplete_walk_is_refused(self, settled):
        ev = dict(ENTRY["evidence"],
                  activity_walk={"complete": False, "pages": 0})
        out = settled.correct_cost_basis(
            MARKET, "DOWN", correction_id="incomplete-walk",
            expect=ENTRY["expect"], measured_cost=MEASURED,
            measured_fees=D("0.0000"), evidence=ev)
        assert out["reason"] == "ACTIVITY_WALK_INCOMPLETE"

    def test_it_cannot_raise_a_cost(self, settled):
        """A FIRST correction may only ever come down."""
        out = settled.correct_cost_basis(
            MARKET, "DOWN", correction_id="would-raise",
            expect=ENTRY["expect"], measured_cost=D("2.5000"),
            measured_fees=D("0.0000"),
            evidence={"per_fill": [{"trade_id": "X", "qty": "1.9100",
                                    "price": "-0.3089005235602094240837696"
                                             "33510",
                                    "fee": "0.0000"}],
                      "activity_walk": {"complete": True}})
        assert out["corrected"] is False
        assert out["reason"] in ("COST_CORRECTION_MUST_REDUCE",
                                "COST_NOT_RECONCILED")
