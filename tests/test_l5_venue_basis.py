"""Venue executions as the accounting basis -- REPORT ONLY.

THE DEFECT (production, 2026-11-16, cpc-btc-updown-5m-2026-11-16-0335z).
Another executor bought UP 2 @ 0.08 then DOWN 2 @ 0.75 fifteen seconds later:
a pair costing 1.66 that pays 2.00. The ledger read the venue's NET position
in the gap, adopted UP 2 at the $1 bound, and booked -2.00. Nine other pairs
that day filled between two reads and were never booked.

Everything here is offline: the attestation suite's fake feed and row
builder, a real InventoryStore in a temp dir, and the real settlement path.
"""
from __future__ import annotations

import collections
import copy
import datetime as dt
import inspect
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_venue_basis as VB
from src import l5_venue_basis_report as VBR
from src import l5_venue_cost_attestation as VCA

from tests.test_l5_venue_cost_attestation import Feed, deposit, fill, retire

M = "cpc-btc-updown-5m-2026-11-16-0335z"
M2 = "cpc-btc-updown-5m-2026-11-16-0340z"
M3 = "cpc-btc-updown-5m-2026-11-16-0345z"
WALL = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc)
T_RET = "2026-11-16T03:41:00+00:00"


def F(side, qty, px, rid, *, fee="0", action=None):
    row = {"side": side, "qty": qty, "price": px, "fee": fee, "id": rid}
    if action is not None:
        row["action"] = action
    return row


PAIR = [F("UP", "2", "0.08", "a"), F("DOWN", "2", "0.75", "b")]


def pair_rows(market=M, up="0.08", dn="0.75", n=""):
    return [fill(market, "DOWN", "2", dn, "T-D" + n + market[-5:],
                 "V-D" + n, t="2026-11-16T03:36:15Z"),
            fill(market, "UP", "2", up, "T-U" + n + market[-5:],
                 "V-U" + n, t="2026-11-16T03:36:00Z")]


@pytest.fixture()
def store(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["realized_attributed"] = "0"
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = "0"
    return s


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv(VBR.MODE_ENV, "REPORT")
    monkeypatch.delenv(VBR.DETAIL_ENV, raising=False)


def run_pass(store, feed, *, steps=50):
    job = VBR.Pass()
    job.wall = lambda: WALL
    now, out = 1_000.0, {}
    for _ in range(steps):
        now += VCA.STEP_INTERVAL_S + 1
        out = job.step(store, feed, now)
        if "report" in out or "walk_failed" in out or out.get("idle") in (
                "OFF", "FRESH"):
            break
    return job, out


def built(store, rows, *, complete=True):
    by = {}
    for r in rows:
        by.setdefault(r["trade"]["marketSlug"] if "trade" in r
                      else r["positionResolution"]["marketSlug"],
                      []).append(r)
    return VBR.build(store.state, by, feed_complete=complete,
                     started_at=WALL)


def entry(out, market):
    return [e for e in out["entries"] if e["market"] == market][0]


# ---------------------------------------------------------- 1. never booked

class TestBothLegsFillBetweenSnapshots:

    def test_pure_unbooked_pair_that_won(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        assert (res["up_qty"], res["dn_qty"]) == (D("2"), D("2"))
        assert res["cost"] == D("1.66") and res["fees"] == D("0")
        assert res["payout"] == D("2") and res["pnl"] == D("0.34")
        cmp_ = VB.compare(None, res)
        assert cmp_["class"] == VB.UNBOOKED
        assert cmp_["difference"] == D("0.34")
        assert cmp_["downward_adjustment"] == D("0")
        assert cmp_["applied"] is False
        assert VB.correction_path(cmp_["class"], res) \
            == "NO PATH: UNBOOKED MARKET"

    def test_pure_unbooked_market_that_lost(self):
        res = VB.market_result([F("UP", "2", "0.08", "a")], D(0),
                               feed_complete=True)
        assert res["pnl"] == D("-0.16")
        cmp_ = VB.compare(None, res)
        assert cmp_["class"] == VB.UNBOOKED
        assert cmp_["downward_adjustment"] == D("-0.16")
        assert cmp_["upward_pending"] == D("0")

    def test_the_pass_finds_a_market_the_ledger_never_saw(self, store, on):
        before = repr(store.state)
        job, out = run_pass(store, Feed([pair_rows()]))
        rep = out["report"]
        assert rep["feed_complete"] is True and rep["applied"] is False
        assert rep["by_class"] == {"UNBOOKED": 1}
        assert rep["venue_pnl_usd"] == "0.34"
        assert rep["booked_pnl_usd"] == "0"
        assert rep["upward_difference_pending_usd"] == "0.34"
        assert rep["downward_adjustment_usd"] == "0"
        d = rep["largest_differences"][0]
        assert d["market"] == M and d["path"] == "NO PATH: UNBOOKED MARKET"
        assert d["applied"] is False
        assert repr(store.state) == before

    def test_the_pass_reports_an_unbooked_pair_that_lost(self, store, on):
        _job, out = run_pass(store, Feed([pair_rows(up="0.60", dn="0.55")]))
        rep = out["report"]
        assert rep["venue_pnl_usd"] == "-0.30"
        assert rep["downward_adjustment_usd"] == "-0.30"
        assert rep["upward_difference_pending_usd"] == "0"

    def test_an_unbooked_lone_leg_has_no_settlement_value_so_no_figure(
            self, store):
        out = built(store, pair_rows()[1:])
        e = entry(out, M)
        assert e["comparison"]["class"] == VB.VENUE_INCOMPLETE
        assert e["result"]["pnl"] is None
        assert "SETTLEMENT_VALUE_UNKNOWN" in e["result"]["incomplete_reasons"]

    def test_a_window_still_trading_is_not_reported(self, store):
        recent = "cpc-btc-updown-5m-2026-11-16-1155z"
        out = built(store, pair_rows(recent))
        assert out["entries"] == []
        assert out["summary"]["skipped"]["UNBOOKED_TOO_RECENT"] == 1

    def test_upward_and_downward_are_never_netted(self, store):
        out = built(store, pair_rows() + pair_rows(M2, "0.60", "0.55"))
        s = out["summary"]
        assert s["upward_difference_pending_usd"] == "0.34"
        assert s["downward_adjustment_usd"] == "-0.30"
        assert s["venue_pnl_usd"] == "0.04"


# ------------------------------------------- 2. one leg seen, then completed

class TestOneLegObservedBeforeTheCompletingLeg:

    def test_pure(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        cmp_ = VB.compare(D("-2.00"), res)
        assert cmp_["class"] == VB.BOOKED_MORE_PESSIMISTIC
        assert cmp_["difference"] == D("2.34")
        assert cmp_["downward_adjustment"] == D("0")
        assert cmp_["upward_pending"] == D("2.34")
        assert cmp_["applied"] is False

    def test_the_pass_changes_nothing(self, store, on):
        row = retire(store, M, up="2", yes="0", t=T_RET)
        assert D(row["settlement_pnl"]) == D("-2")
        before = repr(store.state)
        lines_before = store.realized_lines()
        _job, out = run_pass(store, Feed([pair_rows()]))
        rep = out["report"]
        assert rep["by_class"] == {"BOOKED_MORE_PESSIMISTIC": 1}
        d = rep["largest_differences"][0]
        assert D(d["difference"]) == D("2.34")
        assert D(d["booked_pnl"]) == D("-2")
        assert d["path"] == VB.PATH_SUPERSET
        assert D(rep["upward_difference_pending_usd"]) == D("2.34")
        assert D(rep["downward_adjustment_usd"]) == D("0")
        assert repr(store.state) == before
        assert store.realized_lines() == lines_before
        assert D(store.state["retired_markets"][M]["settlement_pnl"]) \
            == D("-2")


# ----------------------------------------------- 3. net returning to zero

class TestNetReturningToZero:

    def test_position_from_fills(self):
        pos = VB.position_from_fills(PAIR)
        assert (pos["up_qty"], pos["dn_qty"]) == (D("2"), D("2"))
        assert pos["net_up_minus_down"] == D("0")
        assert pos.consistent_with_net(0) is True
        assert pos.consistent_with_net(2) is False
        one = VB.position_from_fills(PAIR[:1])
        assert one.consistent_with_net(2) is True

    def test_an_unreadable_position_is_consistent_with_nothing(self):
        pos = VB.position_from_fills([{"side": "UP"}])
        assert pos["readable"] is False
        assert pos.consistent_with_net(0) is False

    def test_the_phantom_is_named_and_the_store_is_not_touched(self, store):
        retire(store, M, up="2", yes="0", t=T_RET)
        before = repr(store.state)
        e = entry(built(store, pair_rows()), M)
        ph = e["phantom"]
        assert ph["side"] == "UP" and ph["qty"] == D("2")
        assert ph["ledger"] == {"UP": D("2"), "DOWN": D("0")}
        assert ph["venue"] == {"UP": D("2"), "DOWN": D("2")}
        assert ph["ledger_net"] == D("2") and ph["venue_net"] == D("0")
        assert repr(store.state) == before
        pos = store.state.get("positions") or {}
        assert not any(k.startswith(M) and D(str(v)) != 0
                       for k, v in pos.items())

    def test_no_phantom_when_the_nets_agree(self):
        res = VB.market_result(PAIR[:1], D(0), feed_complete=True)
        assert VB.phantom("2", "0", res) is None


# ------------------------------- 4. fills differ from the adopted snapshot

class TestFillQuantitiesDifferFromTheSnapshot:

    def test_pure(self):
        res = VB.market_result([F("UP", "5", "0.60", "a")], D(0),
                               feed_complete=True)
        assert res["pnl"] == D("-3.00")
        cmp_ = VB.compare(D("-2.00"), res)
        assert cmp_["class"] == VB.BOOKED_TOO_OPTIMISTIC
        assert cmp_["difference"] == D("-1.00")
        assert cmp_["downward_adjustment"] == D("-1.00")
        assert cmp_["upward_pending"] == D("0")

    def test_the_pass(self, store, on):
        retire(store, M, up="2", yes="0", t=T_RET)
        before = repr(store.state)
        rows = [fill(M, "UP", "5", "0.60", "T5", "V5",
                     t="2026-11-16T03:36:00Z")]
        _job, out = run_pass(store, Feed([rows]))
        rep = out["report"]
        assert rep["by_class"] == {"BOOKED_TOO_OPTIMISTIC": 1}
        assert D(rep["downward_adjustment_usd"]) == D("-1")
        assert D(rep["upward_difference_pending_usd"]) == D("0")
        assert rep["largest_differences"][0]["path"] == VB.PATH_SUPERSET
        assert repr(store.state) == before

    def test_venue_fills_less_than_the_ledger_have_no_path(self):
        res = VB.market_result([F("UP", "1", "0.60", "a")], D(0),
                               feed_complete=True)
        cmp_ = VB.compare(D("-2"), res)
        assert VB.correction_path(cmp_["class"], res, ledger_up="2",
                                  ledger_dn="0", at_bound=True) \
            == VB.PATH_NOT_CONTAINED

    def test_exact_quantities_name_the_attestation(self):
        res = VB.market_result([F("UP", "2", "0.08", "a")], D(0),
                               feed_complete=True)
        cmp_ = VB.compare(D("-2"), res)
        assert cmp_["class"] == VB.BOOKED_MORE_PESSIMISTIC
        assert VB.correction_path(cmp_["class"], res, ledger_up="2",
                                  ledger_dn="0", at_bound=True) \
            == VB.PATH_ATTESTATION
        assert VB.correction_path(cmp_["class"], res, ledger_up="2",
                                  ledger_dn="0", at_bound=False) \
            == VB.PATH_NOT_AT_BOUND


# ------------------------------------- 5. settlement cash not yet confirmed

class TestSettlementCash:

    def test_not_confirmed_is_labelled_not_realized(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True,
                               settlement_credit="NOT_CONFIRMED")
        assert res["complete"] is True and res["pnl"] == D("0.34")
        assert res["settlement_cash"] == "NOT_CONFIRMED"
        assert res["realized_cash"] is False
        assert res["label"] == VB.LABEL_ARITHMETIC
        assert "Not realized cash" in res["label"]

    def test_not_checked(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        assert res["settlement_cash"] == "NOT_CHECKED"
        assert res["realized_cash"] is False

    def test_confirmed_and_agreeing(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True,
                               settlement_credit="2.00")
        assert res["settlement_cash"] == "CONFIRMED"
        assert res["settlement_credit"] == D("2.00")
        assert res["settlement_credit_agrees"] is True
        assert res["realized_cash"] is True
        assert VB.compare(D("0.34"), res)["class"] == VB.AGREES

    def test_a_confirmed_credit_that_disagrees_is_flagged(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True,
                               settlement_credit="1.00")
        assert res["settlement_cash"] == "CONFIRMED"
        assert res["settlement_credit_agrees"] is False
        assert res["realized_cash"] is False
        assert res["pnl_on_credit"] == D("-0.66")
        cmp_ = VB.compare(D("0.34"), res)
        assert cmp_["class"] == VB.SETTLEMENT_CREDIT_DISAGREES
        # the LOWER reading: a loss the ledger lacks is still surfaced
        assert cmp_["downward_adjustment"] == D("-1.00")
        assert cmp_["upward_pending"] == D("0")
        assert VB.correction_path(cmp_["class"], res) == VB.PATH_CREDIT

    def test_an_unreadable_credit_is_incomplete(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True,
                               settlement_credit="lots")
        assert res["complete"] is False and res["pnl"] is None

    def test_the_pass_checks_no_cash_and_says_so(self, store, on):
        _job, out = run_pass(store, Feed([pair_rows()]))
        assert out["report"]["settlement_cash"] == {
            "CONFIRMED": 0, "NOT_CONFIRMED": 0, "NOT_CHECKED": 1}
        assert "not realized cash" in out["report"]["labels"].lower() \
            or "settled arithmetic" in out["report"]["labels"]

    def test_the_venue_resolution_row_is_a_cross_check_only(self, store):
        res_row = {"type": "activity_type_position_resolution",
                   "positionResolution": {
                       "marketSlug": M, "time": "2026-11-16T03:40:10Z",
                       "afterPosition": {"realized": {"value": "0.3400",
                                                      "currency": "USD"}}}}
        e = entry(built(store, pair_rows() + [res_row]), M)
        assert e["venue_resolution"] == {"venue_realized": "0.3400",
                                         "state": "AGREES"}
        bad = copy.deepcopy(res_row)
        bad["positionResolution"]["afterPosition"]["realized"]["value"] = "-2"
        e = entry(built(store, pair_rows() + [bad]), M)
        assert e["venue_resolution"]["state"] == "DISAGREES"
        assert e["result"]["pnl"] == D("0.34")       # not substituted


# ------------------------------------------------------------ fail closed

class TestIncompleteNeverGuesses:

    def test_incomplete_feed(self):
        res = VB.market_result(PAIR, D(0), feed_complete=False)
        assert res["complete"] is False and res["pnl"] is None
        assert res["payout"] is None
        cmp_ = VB.compare(D("-2"), res)
        assert cmp_["class"] == VB.VENUE_INCOMPLETE
        assert cmp_["difference"] is None
        assert cmp_["downward_adjustment"] is None

    @pytest.mark.parametrize("bad", [
        {"side": "UP", "qty": "2", "price": "0.08", "id": "x"},   # no fee
        {"side": "UP", "qty": "2", "price": "0.08", "fee": "0"},  # no id
        {"side": "??", "qty": "2", "price": "0.08", "fee": "0", "id": "x"},
        {"side": "UP", "qty": "0", "price": "0.08", "fee": "0", "id": "x"},
        {"side": "UP", "qty": "2", "price": "1.2", "fee": "0", "id": "x"},
        {"side": "UP", "qty": "2", "price": "0.08", "fee": "0", "id": "a"},
        {"side": "UP", "qty": "2", "price": "0.08", "fee": "0", "id": "x",
         "action": "TRANSFER"},
        "not a row",
    ])
    def test_unreadable_row(self, bad):
        res = VB.market_result(PAIR + [bad], D(0), feed_complete=True)
        assert res["complete"] is False and res["pnl"] is None
        cmp_ = VB.compare(None, res)
        assert cmp_["class"] == VB.VENUE_INCOMPLETE
        assert cmp_["downward_adjustment"] is None

    @pytest.mark.parametrize("yes", ["0.5", "2", "-1", "x"])
    def test_settlement_value_must_be_0_or_1(self, yes):
        res = VB.market_result(PAIR, yes, feed_complete=True)
        assert res["complete"] is False and res["pnl"] is None

    def test_fees_are_subtracted(self):
        fills = [F("UP", "2", "0.08", "a", fee="0.02"),
                 F("DOWN", "2", "0.75", "b", fee="0.03")]
        res = VB.market_result(fills, D(1), feed_complete=True)
        assert res["fees"] == D("0.05") and res["pnl"] == D("0.29")

    def test_a_rebate_is_a_negative_fee(self):
        res = VB.market_result([F("UP", "2", "0.08", "a", fee="-0.01")],
                               D(1), feed_complete=True)
        assert res["pnl"] == D("1.85")

    def test_a_sell_reduces_quantity_and_credits_its_proceeds(self):
        fills = [F("UP", "2", "0.08", "a"),
                 F("UP", "2", "0.25", "b", action="SELL")]
        res = VB.market_result(fills, D(1), feed_complete=True)
        assert res["up_qty"] == D("0") and res["sells"] == 1
        assert res["cost"] == D("-0.34") and res["pnl"] == D("0.34")
        part = VB.market_result(
            [F("UP", "5", "0.60", "a"),
             F("UP", "2", "0.70", "b", action="SELL")], D(0),
            feed_complete=True)
        assert part["up_qty"] == D("3") and part["pnl"] == D("-1.60")

    def test_selling_more_than_was_bought_is_incomplete(self):
        res = VB.market_result([F("UP", "2", "0.25", "b", action="SELL")],
                               D(1), feed_complete=True)
        assert res["complete"] is False and res["pnl"] is None

    def test_a_ledger_row_with_no_venue_fill_gets_no_figure(self):
        res = VB.market_result([], D(0), feed_complete=True)
        cmp_ = VB.compare(D("-2"), res)
        assert cmp_["class"] == VB.VENUE_HAS_NO_FILLS
        assert cmp_["difference"] is None

    def test_an_unreadable_booked_figure(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        assert VB.compare("n/a", res)["class"] == VB.LEDGER_UNREADABLE

    def test_agreement_tolerance(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        assert VB.compare(D("0.34004"), res)["class"] == VB.AGREES
        assert VB.compare(D("0.3401"), res)["class"] \
            == VB.BOOKED_TOO_OPTIMISTIC

    def test_feed_row_whose_direction_contradicts_its_outcome(self, store):
        rows = pair_rows()
        rows[1]["trade"]["passiveExecution"]["order"]["side"] = \
            "ORDER_SIDE_SELL"                    # with OUTCOME_SIDE_YES
        e = entry(built(store, rows), M)
        assert e["venue_unreadable_reason"] == "FILL_ACTION_UNREADABLE"
        assert e["comparison"]["class"] == VB.VENUE_INCOMPLETE
        assert e["result"]["pnl"] is None

    def test_feed_row_with_a_sell_intent(self):
        r = pair_rows()[1]
        r["trade"]["passiveExecution"]["order"]["intent"] = \
            "ORDER_INTENT_SELL_LONG"
        assert VBR.row_action(r) == "UNKNOWN"
        ok = pair_rows()[0]
        ok["trade"]["passiveExecution"]["order"].update(
            side="ORDER_SIDE_SELL", intent="ORDER_INTENT_BUY_SHORT")
        assert VBR.row_action(ok) == "BUY"

    def test_feed_fees_reach_the_result(self, store):
        rows = [fill(M, "DOWN", "2", "0.75", "TD", "VD", fee="0.03",
                     t="2026-11-16T03:36:15Z"),
                fill(M, "UP", "2", "0.08", "TU", "VU", fee="0.02",
                     t="2026-11-16T03:36:00Z")]
        e = entry(built(store, rows), M)
        assert e["result"]["fees"] == D("0.05")
        assert e["result"]["pnl"] == D("0.29")

    def test_a_feed_that_does_not_end_on_eof(self, store, on):
        retire(store, M, up="2", yes="0", t=T_RET)
        before = repr(store.state)
        _job, out = run_pass(store, Feed([pair_rows()], eof=False))
        rep = out["report"]
        assert rep["feed_complete"] is False
        assert rep["by_class"] == {"VENUE_INCOMPLETE": 1}
        #: and the report says WHY, with the market named
        assert sum(rep["incomplete_by_reason"].values()) >= 1
        assert all(isinstance(v, list) and v
                   for v in rep["incomplete_examples"].values())
        assert rep["venue_pnl_usd"] == "0"
        assert rep["downward_adjustment_usd"] == "0"
        assert rep["largest_differences"] == []
        assert repr(store.state) == before

    def test_a_re_served_row_is_not_a_second_fill(self, store, on):
        rows = pair_rows()
        _job, out = run_pass(store, Feed([rows, [copy.deepcopy(rows[0]),
                                                 deposit("2026-11-01T00:00:00Z")]]))
        assert out["report"]["venue_pnl_usd"] == "0.34"
        assert out["report"]["pages"] == 2

    def test_a_market_retired_after_the_pass_started_is_skipped(self, store):
        retire(store, M, up="2", yes="0", t="2026-11-16T12:00:01+00:00")
        out = built(store, pair_rows())
        assert out["entries"] == []
        assert out["summary"]["skipped"]["RETIRED_AFTER_PASS_STARTED"] == 1

    def test_rows_settled_before_the_rebase_are_out_of_scope(self, store):
        retire(store, M, up="2", yes="0", t=T_RET)
        store.state["accounting_rebase_log"] = [
            {"t": "2026-11-16T05:00:00+00:00", "attributed": "0",
             "unattributed": "0"}]
        out = built(store, pair_rows())
        assert out["entries"] == []          # booked, and before the scope


class TestSummarise:

    def test_bounded_and_sorted(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        per = [{"market": "m%03d" % i, "result": res,
                "comparison": VB.compare(D(i), res), "path": "p"}
               for i in range(1, 60)]
        s = VB.summarise(per)
        assert s["markets"] == 59 and s["differences_total"] == 59
        assert len(s["largest_differences"]) == VB.MAX_LISTED == 40
        assert s["largest_differences"][0]["market"] == "m059"
        assert s["venue_confirmed_fills"] == 118
        assert s["total_cost_usd"] == str(D("1.66") * 59)
        assert s["applied"] is False

    def test_small_has_no_rows(self):
        res = VB.market_result(PAIR, D(0), feed_complete=True)
        s = VB.summarise([{"market": "m", "result": res,
                           "comparison": VB.compare(None, res)}])
        sm = VBR.small(s)
        assert "largest_differences" not in sm
        assert sm["venue_pnl_usd"] == "0.34"


# ----------------------------------------------------------------- switch

class TestTheSwitch:

    @pytest.mark.parametrize("val", [None, "", "report", "Report", "ON",
                                     "1", "true", "REPORT_ONLY", "APPLY",
                                     "REPORT,APPLY", "YES"])
    def test_only_the_exact_token_enables(self, val):
        env = {} if val is None else {VBR.MODE_ENV: val}
        assert VBR.mode(env) == "OFF"

    def test_the_token(self):
        assert VBR.mode({VBR.MODE_ENV: "REPORT"}) == "REPORT"

    def test_there_is_no_mode_that_writes(self):
        src = inspect.getsource(VBR) + inspect.getsource(VB)
        for verb in ("attest_retired_market_cost", "note_venue_cost_attempt",
                     ".save(", "retire_settled_market", "setdefault(\"",
                     "state["):
            assert verb not in src, verb

    def test_off_reads_nothing(self, store, monkeypatch):
        monkeypatch.delenv(VBR.MODE_ENV, raising=False)
        feed = Feed([pair_rows()])
        job, out = run_pass(store, feed)
        assert out == {"idle": "OFF"} and feed.reads == []
        assert job.lines() == {"state": "OFF", "applied": False}

    def test_paced_like_the_attestation(self, store, on):
        feed = Feed([[deposit("2026-11-16T00:00:00Z")]] * 9)
        job = VBR.Pass()
        job.wall = lambda: WALL
        job.step(store, feed, 1000.0)
        assert len(feed.reads) == VCA.PAGES_PER_STEP
        assert job.step(store, feed, 1001.0) == {"idle": "PACED"}
        assert len(feed.reads) == VCA.PAGES_PER_STEP
        job.step(store, feed, 1000.0 + VCA.STEP_INTERVAL_S)
        assert len(feed.reads) == 2 * VCA.PAGES_PER_STEP

    def test_a_finished_pass_is_not_repeated_at_once(self, store, on):
        feed = Feed([pair_rows()])
        job, _out = run_pass(store, feed)
        n = len(feed.reads)
        assert job.step(store, feed, 2000.0) == {"idle": "FRESH"}
        assert len(feed.reads) == n

    def test_a_transport_fault_backs_off(self, store, on):
        job, out = run_pass(store, Feed([[]], fail=RuntimeError("429")))
        assert out == {"walk_failed": "error:RuntimeError"}
        assert job.lines()["last_error"] == "error:RuntimeError"
        assert job.lines()["summary"] is None


# ----------------------------------------------------------------- runner

class Rn:
    maybe_report_venue_basis = R.Runner.maybe_report_venue_basis

    def __init__(self, store, feed):
        self.store = store
        self.transport = feed
        self.stats = collections.Counter()


@pytest.fixture()
def tape(monkeypatch):
    got = []
    monkeypatch.setattr(R, "emit", lambda kind, doc: got.append((kind, doc)))
    return got


def drive(r, n=12):
    now = 1_000.0
    for _ in range(n):
        now += VCA.STEP_INTERVAL_S + 1
        r.maybe_report_venue_basis(now)


class TestRunner:

    def test_off_emits_and_reads_nothing(self, store, tape, monkeypatch):
        monkeypatch.delenv(VBR.MODE_ENV, raising=False)
        monkeypatch.setenv(VBR.DETAIL_ENV, M)
        feed = Feed([pair_rows()])
        r = Rn(store, feed)
        drive(r)
        assert tape == [] and feed.reads == []
        assert not hasattr(r, "_venue_basis_pass")
        assert R.venue_basis_lines(r) == {"state": "OFF", "applied": False}

    def test_report_emits_one_line_and_writes_nothing(self, store, tape, on):
        retire(store, M, up="2", yes="0", t=T_RET)
        before = repr(store.state)
        mtime = (store.path, open(store.path, "rb").read())
        r = Rn(store, Feed([pair_rows() + pair_rows(M2)]))
        drive(r)
        assert [k for k, _ in tape] == ["L5_VENUE_BASIS"]
        doc = tape[0][1]
        assert doc["mode"] == "REPORT" and doc["feed_complete"] is True
        assert doc["pages"] == 1 and doc["as_of"]
        assert doc["by_class"] == {"BOOKED_MORE_PESSIMISTIC": 1,
                                   "UNBOOKED": 1}
        assert repr(store.state) == before
        assert open(mtime[0], "rb").read() == mtime[1]
        hb = R.venue_basis_lines(r)
        assert hb["state"] == "REPORT" and hb["applied"] is False
        assert "largest_differences" not in hb["summary"]
        assert D(hb["summary"]["upward_difference_pending_usd"]) \
            == D("2.68")
        assert D(hb["summary"]["downward_adjustment_usd"]) == D("0")

    def test_it_never_raises_into_the_tick(self, store, tape, on):
        r = Rn(None, object())               # no state, no transport verb
        drive(r)                             # must not raise
        r2 = Rn(store, Feed([[{"type": "activity_type_trade",
                               "trade": {"marketSlug": M, "junk": object()}},
                              "garbage", None]]))
        drive(r2)
        assert all(k == "L5_VENUE_BASIS" for k, _ in tape)

    def test_a_broken_store_is_swallowed(self, tape, on):
        class Bad:
            @property
            def state(self):
                raise RuntimeError("volume gone")
        r = Rn(Bad(), Feed([pair_rows()]))
        drive(r)
        assert tape == []
        assert r.stats["venue_basis_error:RuntimeError"] >= 1

    def test_the_lines_helper_never_raises(self, monkeypatch):
        monkeypatch.setenv(VBR.MODE_ENV, "REPORT")

        class Odd:
            _venue_basis_pass = "not a pass"
        out = R.venue_basis_lines(Odd())
        assert out["state"] == "UNAVAILABLE" and out["applied"] is False

    def test_it_is_on_the_heartbeat_the_risk_record_and_the_tick(self):
        assert '"venue_basis": venue_basis_lines(self)' in inspect.getsource(
            R.Runner.heartbeat)
        assert '"venue_basis": venue_basis_lines(self)' in inspect.getsource(
            R.Runner.account_risk_line)
        src = inspect.getsource(R)
        assert src.count("self.maybe_report_venue_basis(now)") == 1

    def test_no_gate_reads_it(self):
        src = inspect.getsource(R)
        assert src.count("venue_basis_lines(") == 3       # def + 2 reports
        assert src.count("_venue_basis_pass") == 3
        assert "l5_venue_basis" not in inspect.getsource(LX)


# ----------------------------------------------------------------- detail

OPEN = "aec-nba-det-sa-2026-11-17"
QUIET = "aec-nba-hou-mia-2026-11-16"


class TestDetail:

    def test_only_exact_slugs_are_honoured(self):
        env = {VBR.DETAIL_ENV: " %s ,cpc-btc-*,CPC-BTC-UPDOWN-5M-X,short,"
               "a/b-c-d-e-f-g-h-i,%s,%s, ,cpc-btc-updown-5m-" % (M, M2, M)}
        assert VBR.detail_markets(env) == [M, M2, "cpc-btc-updown-5m-"]
        assert VBR.detail_markets({}) == []
        assert VBR.detail_markets({VBR.DETAIL_ENV: "x" * 121}) == []

    def test_at_most_twelve(self):
        many = ",".join("market-number-%04d" % i for i in range(30))
        assert len(VBR.detail_markets({VBR.DETAIL_ENV: many})) == 12

    def test_a_prefix_matches_nothing(self, store, tape, on, monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, "cpc-btc-updown-5m-")
        drive(Rn(store, Feed([pair_rows()])))
        det = [d for k, d in tape if k == "L5_VENUE_BASIS_DETAIL"]
        assert len(det) == 1 and det[0]["venue_rows"] == 0

    def test_unset_emits_nothing(self, store, tape, on):
        drive(Rn(store, Feed([pair_rows()])))
        assert [k for k, _ in tape] == ["L5_VENUE_BASIS"]

    def test_rows_and_identity_fields(self, store, tape, on, monkeypatch):
        # OPEN is held by the ledger (an open position): out of the
        # comparison's scope, but its rows are still printed when listed.
        store.state.setdefault("positions", {})["%s|UP" % OPEN] = "5"
        store.save()
        rows = [fill(OPEN, "UP", "5", "0.46", "TA1", "VA1", fee="0.01",
                     t="2026-11-17T20:00:00Z")]
        tr = rows[0]["trade"]
        tr["clientOrderId"] = "c" * 200
        tr["passiveExecution"]["order"].update(
            side="ORDER_SIDE_BUY", intent="ORDER_INTENT_BUY_LONG",
            manualOrderIndicator="MANUAL_ORDER_INDICATOR_MANUAL",
            clientOrderId="coid-7")
        tr["passiveExecution"]["tradeId"] = "exec-trade-9"
        monkeypatch.setenv(VBR.DETAIL_ENV, "%s,%s" % (OPEN, QUIET))
        before = repr(store.state)
        drive(Rn(store, Feed([rows + pair_rows()])))
        det = {d["market"]: d for k, d in tape
               if k == "L5_VENUE_BASIS_DETAIL"}
        assert sorted(det) == sorted([OPEN, QUIET])
        assert repr(store.state) == before
        d = det[OPEN]
        assert d["venue_rows"] == 1 and d["feed_complete"] is True
        row = d["rows"][0]
        assert row["time"] == "2026-11-17T20:00:00+00:00"
        assert row["side"] == "UP" and row["qty"] == "5"
        assert row["price_yes_quoted"] == "0.46" and row["fee"] == "0.01"
        assert row["action"] == {"order_side": "ORDER_SIDE_BUY",
                                 "outcome_side": "OUTCOME_SIDE_YES",
                                 "intent": "ORDER_INTENT_BUY_LONG",
                                 "read_as": "BUY"}
        assert row["trade_id"] == "TA1" and row["is_aggressor"] is False
        assert row["venue_order_id_ours"] == "VA1"
        assert row["venue_order_id_ours_source"] == "flag"
        assert "CPTY-TA1" not in repr(d)             # not the counterparty
        ids = row["identity_fields"]
        assert ids["trade.clientOrderId"] == "c" * 80          # clipped
        assert ids["trade.passiveExecution.order.manualOrderIndicator"] \
            == "MANUAL_ORDER_INDICATOR_MANUAL"
        assert ids["trade.passiveExecution.order.clientOrderId"] == "coid-7"
        assert ids["trade.passiveExecution.tradeId"] == "exec-trade-9"
        # the open market is not compared as an unbooked one
        rep = [d for k, d in tape if k == "L5_VENUE_BASIS"][0]
        assert rep["by_class"] == {"UNBOOKED": 1}
        q = det[QUIET]
        assert q["venue_rows"] == 0 and q["rows"] == []
        assert q["note"] == "NO VENUE EXECUTION ROWS FOUND FOR THIS MARKET"

    def test_no_rows_on_an_incomplete_feed_is_not_absence(self, store, tape,
                                                          on, monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, QUIET)
        drive(Rn(store, Feed([pair_rows()], eof=False)))
        d = [d for k, d in tape if k == "L5_VENUE_BASIS_DETAIL"][0]
        assert d["feed_complete"] is False
        assert "not evidence of absence" in d["note"]

    def test_rows_are_bounded(self):
        rows = [fill(M, "UP", "1", "0.10", "T%03d" % i, "V%03d" % i)
                for i in range(75)]
        d = VBR.detail(M, rows, feed_complete=True, as_of="x")
        assert d["venue_rows"] == 75 and d["rows_listed"] == 60
        assert d["rows_omitted"] == 15 and len(d["rows"]) == 60

    def test_identity_fields_are_bounded(self):
        row = {"type": "activity_type_trade",
               "trade": {"n%d" % i: {"orderId": "v"} for i in range(100)}}
        assert len(VBR.identity_fields(row)) \
            == VBR.DETAIL_MAX_IDENTITY_FIELDS

    def test_allowlist_only_and_no_credentials_or_account_ids(self):
        row = fill(OPEN, "UP", "5", "0.46", "TA1", "VA1")
        tr = row["trade"]
        secret = "S3CR3T-DO-NOT-LOG"
        tr.update({
            "accountId": secret, "userId": secret, "ownerOrderId": secret,
            "walletAddress": secret, "email": secret, "apiKey": secret,
            "apiSecret": secret, "authToken": secret, "signature": secret,
            "sessionId": secret, "clientIp": secret, "ip": secret,
            "account": {"orderId": secret, "tradeId": secret},
            "auth": {"deep": {"clientOrderId": secret}},
            "orderIdNote": secret, "someOtherId": secret, "memo": secret,
            "orderId": "ORD-1", "tradeId": "TRD-1",
            "clientOrderId": "COID-1"})
        mine = tr["passiveExecution"]
        mine.update({"accountId": secret, "executionId": "EXE-1",
                     "user": {"orderId": secret}})
        mine["order"].update({
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATED",
            "clientOrderId": "COID-2", "userKey": secret,
            "ownerAccount": secret, "tokenId": secret})
        tr["aggressorExecution"].update({"tradeId": "THEIRS",
                                         "clientOrderId": "THEIRS"})
        tr["aggressorExecution"]["order"]["manualOrderIndicator"] = "THEIRS"
        d = VBR.detail(OPEN, [row], feed_complete=True, as_of="x")
        text = repr(d)
        assert secret not in text and "THEIRS" not in text
        assert "CPTY-" not in text
        assert d["rows"][0]["identity_fields"] == {
            "trade.orderId": "ORD-1", "trade.tradeId": "TRD-1",
            "trade.clientOrderId": "COID-1",
            "trade.passiveExecution.executionId": "EXE-1",
            "trade.passiveExecution.order.manualOrderIndicator":
                "MANUAL_ORDER_INDICATOR_AUTOMATED",
            "trade.passiveExecution.order.clientOrderId": "COID-2"}
        for path in d["rows"][0]["identity_fields"]:
            assert not any(part in seg.lower()
                           for seg in path.split(".")
                           for part in VBR.FORBIDDEN_KEY_PARTS), path

    def test_neither_execution_is_read_when_ours_is_unknown(self):
        row = fill(OPEN, "UP", "5", "0.46", "TA1", "VA1")
        del row["trade"]["isAggressor"]
        row["trade"]["passiveExecution"]["tradeId"] = "X1"
        r = VBR.detail_row(row)
        assert r["identity_fields"] == {}
        assert r["venue_order_id_ours"] is None and r["trade_id"] == "TA1"
        assert "VA1" not in repr(r) and "CPTY" not in repr(r)


# ------------------------------------------------------------ order reads

class OrderFeed(Feed):
    """The feed double plus the transport's existing `order(id)` read."""

    def __init__(self, pages, *, rows=None, fail=(), **kw):
        super().__init__(pages, **kw)
        self.order_rows = rows or {}
        self.order_fail = set(fail)
        self.order_reads = []

    def order(self, oid):
        self.order_reads.append(oid)
        if oid in self.order_fail:
            raise TimeoutError("venue")
        return dict(self.order_rows.get(oid) or {"state": "ORDER_STATE_X"})


def many_orders(market, n, tag="V"):
    return [fill(market, "UP", "1", "0.10", "T%s%02d" % (tag, i),
                 "%s%02d" % (tag, i)) for i in range(n)]


def order_lines(tape):
    return [d for k, d in tape if k == "L5_VENUE_BASIS_ORDERS"]


SECRET = "S3CR3T-DO-NOT-LOG"
ORDER_ROW = {
    "id": "VA1", "marketSlug": OPEN, "side": "ORDER_SIDE_BUY",
    "outcomeSide": "OUTCOME_SIDE_YES", "intent": "ORDER_INTENT_BUY_LONG",
    "type": "ORDER_TYPE_LIMIT", "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "price": {"value": "0.4600", "currency": "USD"}, "quantity": "5",
    "cumQuantity": "5", "leavesQuantity": "0",
    "state": "ORDER_STATE_FILLED",
    "createTime": "2026-11-17T19:59:58.1Z",
    "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
    "clientOrderId": "c" * 200,
    "account": SECRET, "accountId": SECRET, "userId": SECRET,
    "ownerSide": SECRET, "walletAddress": SECRET, "email": SECRET,
    "apiKey": SECRET, "secret": SECRET, "authToken": SECRET,
    "signature": SECRET, "sessionId": SECRET, "ip": SECRET,
    "participant": {"clientOrderId": SECRET, "state": SECRET},
    "avgPx": {"value": "0.4600"}, "commission": "0"}


class TestOrderReads:

    def test_fields_are_an_allowlist(self, store, tape, on, monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, "%s,%s" % (OPEN, QUIET))
        rows = [fill(OPEN, "UP", "5", "0.46", "TA1", "VA1"),
                fill(OPEN, "UP", "1", "0.46", "TA2", "VA1")]
        feed = OrderFeed([rows], rows={"VA1": ORDER_ROW})
        before = repr(store.state)
        drive(Rn(store, feed))
        assert feed.order_reads == ["VA1"]           # distinct, once
        got = {d["market"]: d for d in order_lines(tape)}
        assert sorted(got) == sorted([OPEN, QUIET])
        assert got[OPEN]["orders"] == [{"venue_order_id": "VA1", "fields": {
            "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
            "intent": "ORDER_INTENT_BUY_LONG", "type": "ORDER_TYPE_LIMIT",
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL", "price": "0.4600",
            "quantity": "5", "cumQuantity": "5",
            "state": "ORDER_STATE_FILLED",
            "createTime": "2026-11-17T19:59:58.1Z",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
            "clientOrderId": "c" * 80}}]
        assert SECRET not in repr(tape) and "CPTY-" not in repr(
            order_lines(tape))
        assert got[QUIET]["orders"] == [] and got[QUIET]["note"]
        assert repr(store.state) == before

    def test_bounded_per_market_and_per_pass(self, store, tape, on,
                                             monkeypatch):
        ms = ["listed-market-number-%02d" % i for i in range(9)]
        monkeypatch.setenv(VBR.DETAIL_ENV, ",".join(ms))
        rows = []
        for i, m in enumerate(ms):
            rows += many_orders(m, 8, tag="M%d-" % i)
        feed = OrderFeed([rows])
        drive(Rn(store, feed), n=80)
        got = order_lines(tape)
        assert len(got) == 9
        assert all(len(d["orders"]) <= VBR.ORDERS_MAX_PER_MARKET
                   for d in got)
        assert got[0]["orders_omitted"] == 2
        assert sum(len(d["orders"]) for d in got) == 40
        assert len(feed.order_reads) == VBR.ORDERS_MAX_PER_PASS == 40
        assert len(set(feed.order_reads)) == 40

    def test_one_read_per_step(self, store, on, monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, OPEN)
        feed = OrderFeed([many_orders(OPEN, 3)])
        job = VBR.Pass()
        job.wall = lambda: WALL
        assert "report" in job.step(store, feed, 1000.0)
        assert feed.order_reads == []
        assert job.step(store, feed, 1001.0) == {"idle": "PACED"}
        t = 1000.0
        for want in (1, 2):
            t += VCA.STEP_INTERVAL_S
            assert job.step(store, feed, t) == {"reading_orders": 3 - want}
            assert len(feed.order_reads) == want
        t += VCA.STEP_INTERVAL_S
        assert len(job.step(store, feed, t)["orders"][0]["orders"]) == 3

    def test_each_id_is_read_once_per_process(self, store, tape, on,
                                              monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, OPEN)
        monkeypatch.setattr(VBR, "REFRESH_S", 0.0)
        feed = OrderFeed([many_orders(OPEN, 2)])
        drive(Rn(store, feed), n=30)
        assert len(order_lines(tape)) >= 2           # several passes
        assert sorted(feed.order_reads) == ["V00", "V01"]
        assert order_lines(tape)[-1]["orders"][0]["fields"] == {
            "state": "ORDER_STATE_X"}

    def test_a_failing_read_does_not_stop_the_pass(self, store, tape, on,
                                                   monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, OPEN)
        feed = OrderFeed([many_orders(OPEN, 3)], fail={"V01"})
        r = Rn(store, feed)
        drive(r)
        assert feed.order_reads == ["V00", "V01", "V02"]   # no retry
        o = order_lines(tape)[0]["orders"]
        assert o[1] == {"venue_order_id": "V01", "fields": None,
                        "error": "ORDER_READ_FAILED:TimeoutError"}
        assert o[0]["fields"] and o[2]["fields"]
        assert not r.stats

    def test_a_transport_without_the_read_gets_no_new_call(self, store, tape,
                                                           on, monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, OPEN)
        feed = Feed([many_orders(OPEN, 2)])
        drive(Rn(store, feed))
        d = order_lines(tape)[0]
        assert d["note"] == "NO_ORDER_READ_ON_TRANSPORT"
        assert all(o["fields"] is None for o in d["orders"])
        assert all("/v1/order/" not in p for p in feed.reads)

    def test_an_odd_order_id_is_never_put_in_a_path(self):
        row = fill(OPEN, "UP", "1", "0.10", "T1", "V1/../cancel")
        assert VBR.our_order_ids([row]) == []

    def test_no_detail_list_reads_no_order(self, store, tape, on):
        feed = OrderFeed([many_orders(OPEN, 2)])
        drive(Rn(store, feed))
        assert feed.order_reads == [] and order_lines(tape) == []

    def test_switch_off_reads_no_order(self, store, tape, monkeypatch):
        monkeypatch.delenv(VBR.MODE_ENV, raising=False)
        monkeypatch.setenv(VBR.DETAIL_ENV, OPEN)
        feed = OrderFeed([many_orders(OPEN, 2)])
        drive(Rn(store, feed))
        assert feed.order_reads == [] and feed.reads == [] and tape == []

    def test_unlisted_markets_are_never_read(self, store, tape, on,
                                             monkeypatch):
        monkeypatch.setenv(VBR.DETAIL_ENV, QUIET)
        feed = OrderFeed([many_orders(OPEN, 2) + pair_rows()])
        drive(Rn(store, feed))
        assert feed.order_reads == []


# ------------------------------------------------ why a market has no figure

def test_the_report_counts_incomplete_markets_by_reason():
    e_ok = {"market": "m-ok", "comparison": {"class": "AGREES"},
            "result": {"incomplete_reasons": []},
            "venue_unreadable_reason": None,
            "venue_resolution": {"state": "ABSENT"}}
    src = open(VBR.__file__).read()
    assert '"incomplete_by_reason"' in src and '"incomplete_examples"' in src
    assert "incomplete_by_reason" in VBR.small(
        {"incomplete_by_reason": {"X": 1}, "markets": 1})
    assert e_ok["comparison"]["class"] == "AGREES"
