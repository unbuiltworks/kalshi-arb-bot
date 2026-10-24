"""Per-window fill, inventory and incentive accounting.

VALIDATED AGAINST A SETTLED CAMPAIGN, not against itself. The eight-window
20261024a campaign is venue-reconciled at +$0.6581, and the five windows whose
tapes are preserved sum to -$0.2856. The aggregator must reproduce those
figures and must reconcile its own first-leg/complement contract counts
against the venue's independent matched/residual lines.

THE THREE COUNTING TRAPS, each with its own test: a partial fill is one order
and may be several events; repeated reads of the same cumulative are one
event; an estimated rebate is never income.

Nothing here touches a venue, a credential or a limit.
"""
from __future__ import annotations

import glob
import json
from decimal import Decimal as D

import pytest

from src import l5_window_accounting as WA

TAPES = sorted(glob.glob(
    "analysis/l5/residual_exit/preserved/btc15m_20261024_*.ndjson"))

#: venue-confirmed settlement_pnl per preserved window, from L5_LIVE_SETTLED
EXPECTED = {"0100z": D("0.0416"), "0115z": D("-0.2304"),
            "0130z": D("0.1905"), "0145z": D("-0.3605"),
            "0200z": D("0.0732")}


def load(path):
    subs, life, settle = [], [], None
    for line in open(path):
        r = json.loads(line)
        try:
            b = json.loads(r["body"])
        except Exception:                                      # noqa: BLE001
            continue
        if (r["label"] == "L5_FILL_CALIB_PRESUBMIT"
                and str(b.get("coid", "")).startswith("l5f-")):
            subs.append(b)
        elif r["label"] == "L5_LIVE_LIFECYCLE":
            life.append(b)
        elif r["label"] == "L5_LIVE_SETTLED":
            settle = b
    return subs, life, settle


def records():
    out = []
    for p in TAPES:
        slug = p.split("_")[-1].replace(".ndjson", "")
        subs, life, settle = load(p)
        out.append((slug, WA.window_record(
            market=slug, submits=subs, lifecycle=life, settlement=settle)))
    return out


@pytest.mark.skipif(not TAPES, reason="preserved tapes absent")
class TestItReproducesTheSettledCampaign:

    def test_every_window_matches_its_venue_settlement(self):
        for slug, rec in records():
            assert rec["realized_net_usd"] == EXPECTED[slug], slug

    def test_the_rolling_total_matches_the_reconciled_sum(self):
        roll = WA.campaign_rolling([r for _s, r in records()])
        assert roll["realized_net_usd"] == sum(EXPECTED.values())
        assert roll["realized_net_usd"] == D("-0.2856")

    def test_first_leg_and_complement_reconcile_to_the_venue_lines(self):
        """min(first, complement) must equal the venue's matched quantity and
        the difference must equal its residual. This is the cross-check that
        catches a role misassignment."""
        for slug, rec in records():
            c, st = rec["contracts"], rec["settled"]
            first, comp = c["first_leg_filled"], c["complement_filled"]
            assert min(first, comp) == st["matched_qty"], slug
            assert abs(first - comp) == st["residual_qty"], slug

    def test_a_replaced_first_leg_is_still_a_first_leg(self):
        """0100z cancelled its DOWN first leg and re-placed it, then completed
        with UP. Index-based roles called the replacement a complement and
        reported 0 first-leg contracts against 2.08 complement contracts."""
        rec = dict(records())["0100z"]
        assert rec["contracts"]["first_leg_filled"] == D("1.04")
        assert rec["contracts"]["complement_filled"] == D("1.04")
        assert rec["orders"]["submitted"] == 3
        assert rec["orders"]["cancelled"] == 1

    def test_the_counts_are_what_was_counted_by_hand(self):
        roll = WA.campaign_rolling([r for _s, r in records()])
        assert roll["orders_submitted"] == 11
        assert roll["filled_orders"] == 8
        assert roll["fill_events"] == 8
        assert roll["partial_fills"] == 0
        assert roll["windows_with_a_completed_pair"] == 3
        assert roll["actual_fees_usd"] == D(0)


class TestTrapOnePartialFillIsOneOrder:

    def life(self, coid, steps):
        out = []
        prev = D(0)
        for cum in steps:
            out.append({"coid": coid, "already": str(prev), "cum": str(cum),
                        "cum_qty": str(cum), "delta": str(D(cum) - prev),
                        "state": "ORDER_STATE_PARTIALLY_FILLED",
                        "terminal": False, "commission": "0"})
            prev = D(cum)
        out[-1]["terminal"] = True
        out[-1]["state"] = "ORDER_STATE_FILLED"
        return out

    def test_three_increments_are_one_order_and_three_events(self):
        subs = [{"coid": "a", "side": "UP", "own_px": "0.30", "qty": "3"}]
        rec = WA.window_record(market="m", submits=subs,
                               lifecycle=self.life("a", ["1", "2", "3"]))
        assert rec["orders"]["submitted"] == 1
        assert rec["orders"]["filled_orders"] == 1
        assert rec["orders"]["fill_events"] == 3
        assert rec["contracts"]["filled_total"] == D("3")

    def test_contracts_are_the_final_cumulative_not_the_sum_of_reads(self):
        """Summing cum_qty across reads would report 6 for a 3-lot order."""
        subs = [{"coid": "a", "side": "UP", "own_px": "0.30", "qty": "3"}]
        rec = WA.window_record(market="m", submits=subs,
                               lifecycle=self.life("a", ["1", "2", "3"]))
        assert rec["contracts"]["filled_total"] == D("3")

    def test_an_incomplete_fill_is_flagged_partial(self):
        subs = [{"coid": "a", "side": "UP", "own_px": "0.30", "qty": "3"}]
        life = self.life("a", ["1", "2"])
        life[-1]["state"] = "ORDER_STATE_CANCELED"
        rec = WA.window_record(market="m", submits=subs, lifecycle=life)
        assert rec["orders"]["partial_fills"] == 1
        assert rec["orders"]["filled_orders"] == 1
        assert rec["contracts"]["filled_total"] == D("2")


class TestTrapTwoRepeatedReadsAreOneEvent:

    def test_the_same_cumulative_read_twice_counts_once(self):
        rows = [{"coid": "a", "cum_qty": "1", "delta": "1",
                 "state": "ORDER_STATE_FILLED", "terminal": True,
                 "commission": "0"}] * 4
        assert len(WA.fill_events(rows)) == 1

    def test_a_zero_delta_poll_is_not_a_fill(self):
        rows = [{"coid": "a", "cum_qty": "0", "delta": "0",
                 "state": "ORDER_STATE_NEW", "terminal": False}] * 9
        assert WA.fill_events(rows) == []

    def test_a_row_without_a_coid_is_ignored_rather_than_guessed(self):
        rows = [{"cum_qty": "1", "delta": "1"}]
        assert WA.fill_events(rows) == []


class TestTrapThreeAnEstimateIsNeverIncome:

    def sub(self, est):
        return [{"coid": "a", "side": "UP", "own_px": "0.30", "qty": "1",
                 "fees": {"schedule_estimate": {"maker_rebate_usd": est}}}]

    def life(self):
        return [{"coid": "a", "cum_qty": "1", "delta": "1", "terminal": True,
                 "state": "ORDER_STATE_FILLED", "commission": "0"}]

    def test_realized_excludes_the_estimate(self):
        rec = WA.window_record(
            market="m", submits=self.sub("0.0031"), lifecycle=self.life(),
            settlement={"settlement_pnl": "0.0400", "matched_qty": "1",
                        "matched_pnl": "0.0400", "residual_qty": "0",
                        "residual_pnl": "0"})
        assert rec["realized_net_usd"] == D("0.0400")
        assert rec["incentives"]["rebate_estimate_usd"] == D("0.0031")
        assert rec["incentives"]["rebate_credited_usd"] == D(0)
        assert rec["incentives"]["rebate_pending_usd"] == D("0.0031")

    def test_realized_excludes_a_CREDITED_rebate_too(self):
        """Credited rebates are reported, never implicitly added. Promoting
        them into realized has to be a deliberate act elsewhere."""
        rec = WA.window_record(
            market="m", submits=self.sub("0.0031"), lifecycle=self.life(),
            settlement={"settlement_pnl": "0.0400"},
            rebate_credits=[{"credit_id": "C1", "usd": "0.0030"}])
        assert rec["realized_net_usd"] == D("0.0400")
        assert rec["incentives"]["rebate_credited_usd"] == D("0.0030")

    def test_a_credit_replayed_twice_is_counted_once(self):
        out = WA.credited_rebates([{"credit_id": "C1", "usd": "0.0030"},
                                   {"credit_id": "C1", "usd": "0.0030"}])
        assert out["credited_usd"] == D("0.0030")
        assert len(out["credits"]) == 1

    def test_a_credit_without_a_durable_id_is_refused_not_counted(self):
        out = WA.credited_rebates([{"usd": "0.0030"},
                                   {"credit_id": "", "usd": "0.0030"},
                                   {"credit_id": "C2", "usd": None}])
        assert out["credited_usd"] == D(0)
        assert out["unidentifiable_skipped"] == 3

    def test_eligibility_is_carried_and_defaults_to_unverified(self):
        rec = WA.window_record(market="m")
        assert rec["incentives"]["eligibility"] == "UNVERIFIED"


class TestProvisionalThenConfirmedIsOneWindowTwice:

    def test_provisional_has_no_realized_figure_at_all(self):
        rec = WA.window_record(
            market="m",
            submits=[{"coid": "a", "side": "DOWN", "own_px": "0.30",
                      "qty": "1"}],
            lifecycle=[{"coid": "a", "cum_qty": "1", "delta": "1",
                        "terminal": True, "state": "ORDER_STATE_FILLED",
                        "commission": "0"}])
        assert rec["phase"] == "PROVISIONAL"
        assert rec["realized_net_usd"] is None
        assert rec["provisional_inventory"]["unmatched_estimate"] == D("1")

    def test_the_venue_record_overwrites_our_estimate(self):
        subs = [{"coid": "a", "side": "DOWN", "own_px": "0.30", "qty": "1"}]
        life = [{"coid": "a", "cum_qty": "1", "delta": "1", "terminal": True,
                 "state": "ORDER_STATE_FILLED", "commission": "0"}]
        conf = WA.window_record(market="m", submits=subs, lifecycle=life,
                                settlement={"settlement_pnl": "-0.30",
                                            "matched_qty": "0",
                                            "residual_qty": "1",
                                            "residual_side": "DOWN",
                                            "residual_pnl": "-0.30"})
        assert conf["phase"] == "CONFIRMED"
        assert conf["realized_net_usd"] == D("-0.30")
        assert "provisional_inventory" not in conf

    def test_rolling_never_nets_a_provisional_window_into_realized(self):
        prov = WA.window_record(market="p")
        conf = WA.window_record(market="c",
                                settlement={"settlement_pnl": "0.05"})
        roll = WA.campaign_rolling([prov, conf])
        assert roll["windows_provisional"] == 1
        assert roll["windows_confirmed"] == 1
        assert roll["realized_net_usd"] == D("0.05")


class TestItCannotAffectTrading:

    def test_the_module_imports_nothing_that_can_trade(self):
        """Checked on the IMPORTS and the module namespace, not on the prose:
        an earlier version of this test grepped the docstring and failed on
        the word "refuse" appearing in a sentence explaining that this module
        cannot refuse anything."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(WA))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                names.add(n.module or "")
                names.update(a.name for a in n.names)
        assert names <= {"__future__", "annotations", "decimal", "Decimal",
                         "D", "InvalidOperation", "typing", "Any", "Dict",
                         "Iterable", "List", "Mapping", "Optional",
                         "Sequence"}, names
        for forbidden in ("l5_live_execution", "l5_pmus_transport",
                          "requests", "urllib", "http", "socket", "os"):
            assert forbidden not in names, forbidden

    def test_it_exposes_no_callable_that_could_reach_a_venue(self):
        for attr in ("submit", "cancel", "arm", "transport", "refuse",
                     "post", "get"):
            assert not hasattr(WA, attr), attr

    def test_an_unreadable_quantity_is_none_rather_than_zero(self):
        assert WA._d("not-a-number") is None
        assert WA._d(None) is None
        assert WA._d("nan") is None
        assert WA._d("0") == D(0)


class TestTheRunnerWiring:
    """Reporting must be emitted twice for one window and must never be able
    to stop trading."""

    def src(self, fn):
        import inspect
        from src import l5_pmus_live_runner as R
        return inspect.getsource(getattr(R, fn) if hasattr(R, fn)
                                 else getattr(R.Runner, fn))

    def test_the_provisional_report_fires_on_rollover_for_the_OUTGOING_window(
            self):
        src = self.src("roll")
        assert "last_rolled_slug" in src
        assert "emit_window_accounting(str(prev), settlement=None)" in src

    def test_it_is_skipped_once_that_window_is_already_confirmed(self):
        assert '!= "CONFIRMED"' in self.src("roll")

    def test_the_confirmed_report_fires_beside_the_settlement_emit(self):
        src = self.src("retire_settled") if False else None
        import inspect
        from src import l5_pmus_live_runner as R
        whole = inspect.getsource(R)
        i = whole.index('emit("L5_LIVE_SETTLED"')
        j = whole.index("emit_window_accounting(market, settlement=settled)")
        assert j > i and (j - i) < 400

    def test_a_failure_in_reporting_cannot_propagate(self):
        src = self.src("emit_window_accounting")
        assert "except BaseException" in src
        assert "window_accounting_failed" in src
        assert "raise" in src          # only for KeyboardInterrupt/SystemExit

    def test_no_estimated_rebate_is_passed_as_a_credit(self):
        src = self.src("emit_window_accounting")
        assert "rebate_credits=()" in src
        assert "UNVERIFIED_NO_VENUE_CREDIT_RECORD" in src

    def test_an_unidentified_market_is_not_filed_under_the_transport_slug(
            self):
        src = self.src("lifecycle")
        assert "if m:" in src
        assert "transport_slug" not in src.split("if m:")[1]

    def test_the_lifecycle_accumulator_is_bounded(self):
        assert "len(rows) < 4096" in self.src("lifecycle")

    def test_submits_come_from_the_existing_calibration_records(self):
        src = self.src("_window_submits")
        assert '"orders"' in src and "pre_submit" in src
        assert 'startswith("l5f-")' in src     # shadow ids excluded
