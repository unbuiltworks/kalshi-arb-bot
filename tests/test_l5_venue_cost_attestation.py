"""Venue-attested cost basis for settled positions this ledger did not open.

THE DEFECT (2026-11-13). Foreign legs -- BTC5m's, in cpc-btc-updown-5m-* --
adopted from the venue were settled at a $1.00/share upper bound. BTC5m rests
at $0.08, so a lone losing leg booked 12.5x its cost and a completed pair
booked 0 instead of its gain. The figures below are TEST FIXTURES taken from
that day's heartbeat; production derives every number from the venue.

Everything here is offline: a fake activity feed, a real InventoryStore in a
temp dir, and the real settlement path to write the rows under test.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_live_execution as LX
from src import l5_venue_cost_attestation as VCA

ROOT = Path(__file__).resolve().parents[1]

M0035 = "cpc-btc-updown-5m-2026-11-13-0035z"
M1605 = "cpc-btc-updown-5m-2026-11-13-1605z"
M1610 = "cpc-btc-updown-5m-2026-11-13-1610z"
M1615 = "cpc-btc-updown-5m-2026-11-13-1615z"
M_OLD = "cpc-btc-updown-5m-2026-11-12-2300z"     # before the approved floor

DAY = dt.datetime(2026, 11, 13, 23, 0, tzinfo=dt.timezone.utc).timestamp()
PREV_DAY = dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _no_retry_spacing(monkeypatch):
    """Attempts are spaced RETRY_S apart in production; most tests here
    exercise the attempt bound itself, so the spacing is lifted. It has its
    own test below."""
    monkeypatch.setattr(VCA, "RETRY_S", 0.0)


# ------------------------------------------------------------------ builders

def retire(store, market, *, up="0", dn="0", cost=None, yes="0",
           unknown=None, t="2026-11-13T12:00:00+00:00"):
    """Hold a market at the $1 bound (or as given) and settle it through
    the REAL retire_settled_market, then stamp the production time."""
    pos = store.state.setdefault("positions", {})
    lc = store.state.setdefault("leg_cost", {})
    if D(up) > 0:
        pos["%s|UP" % market] = up
        lc["%s|UP" % market] = str(D(up)) if cost is None else cost["UP"]
    if D(dn) > 0:
        pos["%s|DOWN" % market] = dn
        lc["%s|DOWN" % market] = str(D(dn)) if cost is None else cost["DOWN"]
    keys = unknown if unknown is not None else (
        ["%s|UP" % market] if D(up) > 0 or D(dn) == 0 else []) + (
        ["%s|DOWN" % market] if D(dn) > 0 else [])
    unk = store.state.setdefault("leg_cost_basis_unknown", [])
    for k in keys:
        if k not in unk:
            unk.append(k)
    out = store.retire_settled_market(
        market, yes_value=D(yes),
        resolution={"source": "VENUE_SETTLEMENT_PX", "market": market,
                    "yes_value": yes})
    assert out["retired"] is True, out
    store.state["retired_markets"][market]["t"] = t
    store.save()
    return store.state["retired_markets"][market]


def fill(market, side, qty, own_px, tid, vid, *, t="2026-11-13T12:00:00Z",
         fee="0"):
    """A trade row shaped as the production feed sends it: `type` plus a
    nested `trade` whose side, order id, shares, price and commission live
    in OUR execution object, selected by isAggressor."""
    own = D(own_px)
    wire = own if side == "UP" else D(1) - own
    q = D(qty)
    mine = {"order": {"id": vid,
                      "outcomeSide": ("OUTCOME_SIDE_YES" if side == "UP"
                                      else "OUTCOME_SIDE_NO")},
            "lastShares": str(q), "lastPx": {"value": str(wire),
                                             "currency": "USD"},
            "commissionNotionalCollected": {"value": fee, "currency": "USD"}}
    other = {"order": {"id": "CPTY-" + tid,
                       "outcomeSide": ("OUTCOME_SIDE_NO" if side == "UP"
                                       else "OUTCOME_SIDE_YES")},
             "lastShares": str(q), "lastPx": {"value": str(wire),
                                              "currency": "USD"},
             "commissionNotionalCollected": {"value": "0.01",
                                             "currency": "USD"}}
    return {"type": "activity_type_trade", "trade": {
        "id": tid, "marketSlug": market, "qty": str(q),
        "price": {"value": str(wire), "currency": "USD"},
        "cost": {"value": str(q * own + D(fee)), "currency": "USD"},
        "isAggressor": False, "passiveExecution": mine,
        "aggressorExecution": other, "time": t}}


def deposit(t):
    return {"type": "activity_type_account_deposit",
            "accountBalanceChange": {"time": t, "amount": "1"}}


class Feed:
    """A paged, newest-first activity feed behind the transport's `_get`."""

    def __init__(self, pages, *, eof=True, fail=None):
        self.pages = pages
        self.eof = eof
        self.fail = fail
        self.reads = []

    def _get(self, path):
        self.reads.append(path)
        if self.fail is not None:
            raise self.fail
        assert path.startswith("/v1/portfolio/activities"), path
        i = 0
        if "cursor=" in path:
            i = int(path.split("cursor=")[1])
        last = i == len(self.pages) - 1
        body = {"activities": self.pages[i]}
        if last and self.eof:
            body["eof"] = True
        elif not last:
            body["nextCursor"] = str(i + 1)
        return body


def run_until_idle(store, feed, *, steps=200):
    att = VCA.Attestor()
    now = 1_000.0
    results = []
    for _ in range(steps):
        now += VCA.STEP_INTERVAL_S + 1
        out = att.step(store, feed, now)
        results.append(out)
        if out.get("idle") == "NO_CANDIDATES":
            break
    return att, results


def day_fixture_fills():
    return [
        fill(M1615, "UP", "5", "0.08", "T-1615", "DPT427WG13Z6",
             t="2026-11-13T16:16:00Z"),
        fill(M1610, "UP", "5", "0.08", "T-1610", "DPT1RZAG14F1",
             t="2026-11-13T16:11:00Z"),
        fill(M1605, "DOWN", "5", "0.08", "T-1605-D", "DPSZ4WG014EX",
             t="2026-11-13T16:06:30Z"),
        fill(M1605, "UP", "5", "0.08", "T-1605-U", "DPSZ4S3013ZK",
             t="2026-11-13T16:06:00Z"),
        deposit("2026-11-13T05:00:00Z"),
        fill(M0035, "UP", "28.12", "0.08", "T-0035", "DPCNDNYG14F0",
             t="2026-11-13T00:36:00Z"),
    ]


@pytest.fixture()
def day(tmp_path):
    """The 2026-11-13 ledger at the bound, as the heartbeat reported it."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["realized_attributed"] = "0"
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = "0"
    # a row from the day before the approved floor: never touched
    retire(s, M_OLD, up="5", yes="0", t="2026-11-12T23:06:00+00:00")
    retire(s, M0035, up="28.12", yes="0", t="2026-11-13T00:41:00+00:00")
    # 1605z: the venue NETS a DOWN buy (a YES sale) against the UP leg, so the
    # adopted position went flat and settled at 0 with its key still listed
    retire(s, M1605, yes="1", unknown=["%s|UP" % M1605],
           t="2026-11-13T16:11:00+00:00")
    retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
    retire(s, M1615, up="5", yes="0", t="2026-11-13T16:21:00+00:00")
    return s


# ------------------------------------------------------- the fixture's own day

class TestTheFixtureReproducesTheHeartbeat:

    def test_the_bound_figures(self, day):
        r = day.state["retired_markets"]
        assert D(r[M0035]["settlement_pnl"]) == D("-28.12")
        assert D(r[M1605]["settlement_pnl"]) == D("0")
        assert D(r[M1610]["settlement_pnl"]) == D("-5")
        assert D(r[M1615]["settlement_pnl"]) == D("-5")
        for m in (M0035, M1605, M1610, M1615):
            assert r[m]["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"

    def test_the_day_figure(self, day):
        assert day.realized_current_utc_day(now=DAY) == D("-38.12")


class TestVenueAttestation:

    def test_the_four_markets_book_their_real_results(self, day):
        run_until_idle(day, Feed([day_fixture_fills()]))
        r = day.state["retired_markets"]
        assert D(r[M0035]["settlement_pnl"]) == D("-2.2496")
        assert D(r[M1605]["settlement_pnl"]) == D("4.20")
        assert D(r[M1610]["settlement_pnl"]) == D("-0.40")
        assert D(r[M1615]["settlement_pnl"]) == D("-0.40")
        for m in (M0035, M1605, M1610, M1615):
            assert r[m]["pnl_provenance"] == "VENUE_ATTESTED_COST"
            # a known cost is not ownership: never reclassified as L5
            assert r[m]["l5_owner_attribution_verified"] is False
            assert r[m]["cost_superseded"]["pnl_provenance"] == \
                "UNKNOWN_COST_UPPER_BOUND"
        assert r[M1605]["venue_qty_form"] == "VENUE_NET"
        assert r[M0035]["venue_qty_form"] == "GROSS"
        # and nothing becomes verified L5
        assert not day.state.get("verified_l5_settlements")

    def test_the_correction_record_carries_old_new_and_evidence(self, day):
        run_until_idle(day, Feed([day_fixture_fills()]))
        recs = {r["market"]: r for r in day.state["cost_basis_corrections"]}
        rec = recs[M1605]
        assert rec["kind"] == "VENUE_ATTESTED_MARKET_COST"
        assert D(rec["old_pnl"]) == 0 and D(rec["new_pnl"]) == D("4.20")
        assert D(rec["delta"]) == D("4.20")
        assert sorted(rec["venue_order_ids"]) == ["DPSZ4S3013ZK",
                                                  "DPSZ4WG014EX"]
        assert sorted(rec["trade_ids"]) == ["T-1605-D", "T-1605-U"]
        assert rec["settlement_utc_day"] == "2026-11-13"
        assert rec["cost_provenance"] == "VENUE_ATTESTED_COST"
        assert rec["activity_walk"]["sufficient"] is True
        assert recs[M0035]["venue_order_ids"] == ["DPCNDNYG14F0"]
        assert D(recs[M0035]["delta"]) == D("25.8704")

    def test_the_daily_figure_moves_by_exactly_the_delta_on_its_own_day(
            self, day):
        before_day = day.realized_current_utc_day(now=DAY)
        before_prev = day.realized_current_utc_day(now=PREV_DAY)
        before_life = day.realized_today()
        run_until_idle(day, Feed([day_fixture_fills()]))
        delta = D("-2.2496") + D("4.20") - D("0.40") - D("0.40") - D("-38.12")
        assert delta == D("39.2704")
        assert day.realized_current_utc_day(now=DAY) == before_day + delta
        assert day.realized_current_utc_day(now=DAY) == D("1.1504")
        # the day before is untouched
        assert day.realized_current_utc_day(now=PREV_DAY) == before_prev
        # and the lifetime figure moves by the same delta
        assert day.realized_today() == before_life + delta

    def test_a_row_before_the_approved_floor_is_never_touched(self, day):
        old = json.dumps(day.state["retired_markets"][M_OLD], sort_keys=True)
        feed = Feed([day_fixture_fills()
                     + [fill(M_OLD, "UP", "5", "0.08", "T-OLD", "V-OLD",
                             t="2026-11-12T23:01:00Z")]])
        run_until_idle(day, feed)
        assert json.dumps(day.state["retired_markets"][M_OLD],
                          sort_keys=True) == old

    def test_a_gross_held_pair_books_its_gain(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        row = retire(s, M1605, up="5", dn="5", yes="1",
                     t="2026-11-13T16:11:00+00:00")
        assert D(row["settlement_pnl"]) == D("-5")
        run_until_idle(s, Feed([day_fixture_fills()]))
        row = s.state["retired_markets"][M1605]
        assert D(row["settlement_pnl"]) == D("4.20")
        assert row["venue_qty_form"] == "GROSS"
        assert D(row["cost_total"]) == D("0.80")


class TestFailClosed:

    def _single(self, tmp_path, up="5"):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up=up, yes="0", t="2026-11-13T16:16:00+00:00")
        return s

    def _unchanged(self, s, market=M1610, pnl="-5"):
        row = s.state["retired_markets"][market]
        assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
        assert D(row["settlement_pnl"]) == D(pnl)
        assert "cost_superseded" not in row
        assert not [r for r in s.state.get("cost_basis_corrections") or []
                    if r.get("market") == market]

    def test_a_quantity_mismatch_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)
        run_until_idle(s, Feed([[fill(M1610, "UP", "4", "0.08", "T1", "V1")]]))
        self._unchanged(s)
        rec = s.state["venue_cost_attestation"]["markets"][M1610]
        assert rec["status"] == "UPPER_BOUND_KEPT"
        assert "VENUE_QTY_NE_LEDGER_QTY" in rec["last_reason"]
        assert rec["attempts"] == VCA.MAX_ATTEMPTS

    def test_an_extra_fill_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)
        run_until_idle(s, Feed([[
            fill(M1610, "UP", "5", "0.08", "T1", "V1"),
            fill(M1610, "UP", "1", "0.08", "T2", "V2")]]))
        self._unchanged(s)

    def test_an_unreadable_venue_keeps_the_bound_and_spends_no_attempt(
            self, tmp_path):
        s = self._single(tmp_path)
        feed = Feed([[]], fail=RuntimeError("HTTP 429"))
        att = VCA.Attestor()
        out = att.step(s, feed, 10_000.0)
        assert out.get("walk_failed", "").startswith("error:")
        self._unchanged(s)
        assert not (s.state.get("venue_cost_attestation") or {}).get(
            "markets", {}).get(M1610)
        # backed off, not hammering
        assert att.step(s, feed, 10_010.0) == {"idle": "PACED"}

    def test_a_malformed_body_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)

        class Bad:
            def _get(self, path):
                return ["not", "a", "page"]
        att = VCA.Attestor()
        att.step(s, Bad(), 10_000.0)
        self._unchanged(s)

    def test_a_walk_that_stops_short_keeps_the_bound(self, tmp_path,
                                                      monkeypatch):
        s = self._single(tmp_path)
        monkeypatch.setattr(VCA, "MAX_WALK_PAGES", 2)
        # no eof, no row older than the floor: never sufficient
        pages = [[fill(M1610, "UP", "5", "0.08", "T%d" % i, "V%d" % i,
                       t="2026-11-13T16:11:00Z")] for i in range(5)]
        run_until_idle(s, Feed(pages, eof=False))
        self._unchanged(s)
        rec = s.state["venue_cost_attestation"]["markets"][M1610]
        assert rec["last_reason"].startswith("WALK_INSUFFICIENT")

    def test_a_fill_without_a_readable_side_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)
        f = fill(M1610, "UP", "5", "0.08", "T1", "V1")
        del f["trade"]["isAggressor"]          # which execution is ours: unknown
        run_until_idle(s, Feed([[f]]))
        self._unchanged(s)

    def test_a_fill_without_a_fee_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)
        f = fill(M1610, "UP", "5", "0.08", "T1", "V1")
        del f["trade"]["passiveExecution"]["commissionNotionalCollected"]
        run_until_idle(s, Feed([[f]]))
        self._unchanged(s)

    def test_a_venue_cost_that_disagrees_keeps_the_bound(self, tmp_path):
        s = self._single(tmp_path)
        f = fill(M1610, "UP", "5", "0.08", "T1", "V1")
        f["trade"]["cost"] = {"value": "2.00", "currency": "USD"}
        run_until_idle(s, Feed([[f]]))
        self._unchanged(s)

    def test_a_netted_market_with_one_venue_leg_keeps_the_bound(self,
                                                                 tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1605, yes="1", unknown=["%s|UP" % M1605],
               t="2026-11-13T16:11:00+00:00")
        run_until_idle(s, Feed([[fill(M1605, "UP", "5", "0.08", "T1", "V1")]]))
        self._unchanged(s, market=M1605, pnl="0")

    def test_an_exception_in_the_verb_keeps_the_bound(self, tmp_path,
                                                       monkeypatch):
        s = self._single(tmp_path)

        def boom(*a, **k):
            raise ZeroDivisionError("x")
        monkeypatch.setattr(s, "attest_retired_market_cost", boom)
        run_until_idle(s, Feed([[fill(M1610, "UP", "5", "0.08", "T1",
                                      "V1")]]))
        self._unchanged(s)
        rec = s.state["venue_cost_attestation"]["markets"][M1610]
        assert rec["last_reason"] == "EXCEPTION:ZeroDivisionError"

    def test_the_verb_refuses_without_a_sufficient_walk(self, tmp_path):
        s = self._single(tmp_path)
        legs = VCA.legs_from_rows([fill(M1610, "UP", "5", "0.08", "T1",
                                        "V1")], M1610)["legs"]
        out = s.attest_retired_market_cost(
            M1610, legs=legs, walk={"sufficient": False}, correction_id="x")
        assert out["reason"] == "VENUE_WALK_NOT_SUFFICIENT"
        self._unchanged(s)

    def test_a_market_whose_basis_is_known_is_never_touched(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", cost={"UP": "0.40"}, yes="0", unknown=[],
               t="2026-11-13T16:16:00+00:00")
        row = s.state["retired_markets"][M1610]
        assert row["pnl_provenance"] != "UNKNOWN_COST_UPPER_BOUND"
        before = json.dumps(row, sort_keys=True)
        feed = Feed([[fill(M1610, "UP", "5", "0.01", "T1", "V1")]])
        run_until_idle(s, feed)
        assert json.dumps(s.state["retired_markets"][M1610],
                          sort_keys=True) == before
        assert feed.reads == []          # not even read


class TestNoDoubleCounting:

    def test_store_owned_fills_are_replaced_not_added(self, tmp_path):
        """UP 5 held: 2 of OUR fills at 0.30 (booked, absorbed) plus 3
        adopted at the bound. The venue shows all five; the side's cost is
        REPLACED by their sum, so our 0.60 is counted once."""
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        row = retire(s, M1610, up="5", cost={"UP": "3.60"}, yes="0",
                     t="2026-11-13T16:16:00+00:00")
        assert D(row["settlement_pnl"]) == D("-3.60")
        run_until_idle(s, Feed([[
            fill(M1610, "UP", "2", "0.30", "T-OWN", "V-OWN"),
            fill(M1610, "UP", "3", "0.08", "T-5M", "V-5M")]]))
        row = s.state["retired_markets"][M1610]
        assert D(row["cost_total"]) == D("0.84")
        assert D(row["settlement_pnl"]) == D("-0.84")     # not -1.44

    def test_trade_ids_cannot_be_spent_twice(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        retire(s, M1615, up="5", yes="0", t="2026-11-13T16:21:00+00:00")
        legs = VCA.legs_from_rows([fill(M1610, "UP", "5", "0.08", "T1",
                                        "V1")], M1610)["legs"]
        ok = s.attest_retired_market_cost(
            M1610, legs=legs, walk={"sufficient": True}, correction_id="a")
        assert ok["attested"] is True
        again = s.attest_retired_market_cost(
            M1615, legs=legs, walk={"sufficient": True}, correction_id="b")
        assert again["reason"] == "TRADE_ID_ALREADY_CONSUMED"


class TestIdempotentAndDurable:

    def test_a_second_pass_changes_nothing(self, day):
        run_until_idle(day, Feed([day_fixture_fills()]))
        snap = json.dumps(day.state, sort_keys=True, default=str)
        feed = Feed([day_fixture_fills()])
        run_until_idle(day, feed)
        assert json.dumps(day.state, sort_keys=True, default=str) == snap
        assert feed.reads == []

    def test_the_verb_refuses_a_repeat(self, day):
        run_until_idle(day, Feed([day_fixture_fills()]))
        legs = VCA.legs_from_rows(day_fixture_fills(), M1610)["legs"]
        out = day.attest_retired_market_cost(
            M1610, legs=legs, walk={"sufficient": True},
            correction_id="venue-attest|%s" % M1610)
        assert out["attested"] is False
        assert out["reason"] in ("CORRECTION_ALREADY_APPLIED",
                                 "NOT_AT_THE_UPPER_BOUND")

    def test_it_survives_a_restart(self, day):
        run_until_idle(day, Feed([day_fixture_fills()]))
        again = LX.InventoryStore(day.path)
        assert D(again.state["retired_markets"][M1605]["settlement_pnl"]) == \
            D("4.20")
        assert VCA.candidates(again) == []
        assert again.realized_current_utc_day(now=DAY) == D("1.1504")

    def test_corrections_are_bounded_per_step(self, day, monkeypatch):
        monkeypatch.setattr(VCA, "MAX_CORRECTIONS_PER_STEP", 1)
        att = VCA.Attestor()
        feed = Feed([day_fixture_fills()])
        out = att.step(day, feed, 1_000.0)
        assert len(out.get("evaluated") or []) == 1


class TestPacing:

    def test_a_refused_market_is_not_re_read_inside_retry_s(self, tmp_path,
                                                            monkeypatch):
        monkeypatch.setattr(VCA, "RETRY_S", 600.0)
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        feed = Feed([[fill(M1610, "UP", "4", "0.08", "T1", "V1")]])
        run_until_idle(s, feed)
        rec = s.state["venue_cost_attestation"]["markets"][M1610]
        assert rec["attempts"] == 1 and rec["status"] == "PENDING"

    def test_a_just_settled_market_waits_for_the_feed(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0",
               t=dt.datetime.now(dt.timezone.utc).isoformat())
        assert VCA.candidates(s) == []
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=VCA.SETTLE_AGE_S + 1)
        assert [m for _t, m in VCA.candidates(s, now=later)] == [M1610]


class TestTheWalk:

    def test_the_time_floor_stops_the_walk_early(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        pages = [[fill(M1610, "UP", "5", "0.08", "T1", "V1",
                       t="2026-11-13T16:11:00Z")],
                 [deposit("2026-11-01T00:00:00Z")],
                 [deposit("2026-10-01T00:00:00Z")]]
        feed = Feed(pages, eof=False)
        run_until_idle(s, feed)
        assert D(s.state["retired_markets"][M1610]["settlement_pnl"]) == \
            D("-0.40")
        assert len(feed.reads) == 2                  # page 3 never read
        rec = s.state["cost_basis_corrections"][-1]
        assert rec["activity_walk"]["by"] == "TIME_FLOOR"

    def test_out_of_order_times_disable_the_time_floor(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(VCA, "MAX_WALK_PAGES", 3)
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        pages = [[deposit("2026-10-01T00:00:00Z")],
                 [fill(M1610, "UP", "5", "0.08", "T1", "V1",
                       t="2026-11-13T16:11:00Z")],
                 [deposit("2026-11-01T00:00:00Z")],
                 [deposit("2026-09-01T00:00:00Z")]]
        run_until_idle(s, Feed(pages, eof=False))
        assert s.state["retired_markets"][M1610]["pnl_provenance"] == \
            "UNKNOWN_COST_UPPER_BOUND"

    def test_only_gets_against_the_activity_feed(self, day):
        feed = Feed([day_fixture_fills()])
        run_until_idle(day, feed)
        assert feed.reads and all(
            p.startswith("/v1/portfolio/activities") for p in feed.reads)


class TestHeartbeat:

    def test_the_block_reports_counts_reasons_and_delta(self, day):
        att, _ = run_until_idle(day, Feed([day_fixture_fills()]))
        hb = att.heartbeat(day)
        assert hb["attested"] == 4
        assert hb["corrections_applied"] == 4
        assert D(hb["corrections_delta_usd"]) == D("39.2704")
        assert hb["still_upper_bound"]["count"] == 0
        assert hb["upper_bound_before_floor_not_in_scope"] == 1
        assert hb["retro_floor_utc_day"] == "2026-11-13"

    def test_the_block_names_why_a_bound_was_kept(self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        retire(s, M1610, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        att, _ = run_until_idle(
            s, Feed([[fill(M1610, "UP", "4", "0.08", "T1", "V1")]]))
        hb = att.heartbeat(s)
        assert hb["still_upper_bound"]["kept_after_max_attempts"] == 1
        assert any("VENUE_QTY_NE_LEDGER_QTY" in k
                   for k in hb["still_upper_bound"]["reasons"])

    def test_the_runner_publishes_the_block(self):
        src = inspect.getsource(__import__(
            "src.l5_pmus_live_runner", fromlist=["Runner"]).Runner.heartbeat)
        assert '"venue_cost_attestation": self.venue_cost_attestation_lines()' \
            in src

    def test_the_runner_block_never_raises(self, day):
        from src import l5_pmus_live_runner as LR

        class Stub:
            store = day
        out = LR.Runner.venue_cost_attestation_lines(Stub())
        assert "still_upper_bound" in out

        class Broken:
            store = None
        out = LR.Runner.venue_cost_attestation_lines(Broken())
        assert "error" in out or "still_upper_bound" in out


class TestRunnerWiring:

    def test_the_tick_hook_runs_after_the_settlement_sweep(self):
        """In the ordinary tick only. The cancel-mechanism tick keeps its
        exact preserved call list; a settled row there is attested on the
        next ordinary tick."""
        from src import l5_pmus_live_runner as LR
        src = inspect.getsource(LR.Runner.tick)
        assert ("        self.sweep_settlements()\n"
                "        self.maybe_attest_venue_costs(now)\n") in src
        assert "maybe_attest_venue_costs" not in inspect.getsource(
            LR.Runner.mechanism_tick)

    def test_the_hook_swallows_everything(self, day):
        from src import l5_pmus_live_runner as LR
        import collections

        class Stub:
            store = day
            transport = Feed([[]], fail=RuntimeError("boom"))
            stats = collections.Counter()
        LR.Runner.maybe_attest_venue_costs(Stub(), 1_000.0)

        class Worse:
            store = day
            transport = Feed([[]])
            stats = collections.Counter()

            def __getattr__(self, name):
                raise RuntimeError(name)
        st = Worse()
        st._venue_cost_attestor = object()     # .step missing -> raises
        LR.Runner.maybe_attest_venue_costs(st, 1_000.0)
        assert any(k.startswith("venue_cost_attest_error")
                   for k in Worse.stats)


class TestTheOrderPathIsUntouched:
    """Source scan: the new path reads, and writes only a settled row."""

    def test_the_module_never_submits_or_cancels(self):
        src = (ROOT / "src/l5_venue_cost_attestation.py").read_text()
        for word in ("submit(", "cancel(", '"POST"', "'POST'", "DELETE",
                     "arm(", "/v1/order"):
            assert word not in src, word

    def test_the_verb_writes_no_open_position_or_risk_field(self):
        src = inspect.getsource(LX.InventoryStore.attest_retired_market_cost)
        for key in ("positions", "leg_cost\"", "committed", "risk_floor_usd",
                    "live_orders", "adopted_unattributed", "halt",
                    "realized_unattributed\"] ="):
            assert not re.search(r'state\[\s*"%s' % re.escape(key), src), key
        assert "l5_owner_attribution_verified" not in src.split(
            "now_iso = ")[1]

    def test_no_gate_reads_the_new_module(self):
        for f in ("src/l5_live_execution.py", "src/l5_live_validation_gates.py",
                  "src/l5_pmus_transport.py"):
            assert "l5_venue_cost_attestation" not in (ROOT / f).read_text(), f

    def test_the_upper_bound_constant_is_unchanged(self):
        assert LX.UNKNOWN_PRICE_WORST_CASE == D(1)


class TestImage:

    def test_the_dockerfile_copies_the_module(self):
        text = (ROOT / "deploy/l5_pmus_live/Dockerfile").read_text()
        assert ("COPY src/l5_venue_cost_attestation.py "
                "src/l5_venue_cost_attestation.py") in text
        assert ("COPY src/l5_cost_basis_audit.py "
                "src/l5_cost_basis_audit.py") in text
