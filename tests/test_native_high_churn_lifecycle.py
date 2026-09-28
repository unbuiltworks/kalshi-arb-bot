"""DETERMINISTIC high-churn: >16 replacements, and the ten proofs.

$81 / (10 contracts * ~$0.50) is roughly sixteen quotes. If a cancelled,
zero-fill quote never released its reservation, quote seventeen onward would be
refused and the window would starve. So the churn here is deliberately past
that line, on a fixed price walk with no randomness.

The mandate is the REAL `pmus_live_mandate` guard over a real state dir.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_window_actions as WA
from src import pmus_l5_native_engine as E
from src import pmus_live_mandate as MANDATE
from src import pmus_native_money_path as MP

MARKET = "cpc-btc-updown-15m-2026-09-28-1830z"
CAP = D("81.00")
MANDATE_RAW = {
    "authorization_id": "OWNER-CHURN", "authorized_at": "2026-09-28T00:00:00Z",
    "service": "l5-live-probe-oneshot",
    "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
    "strategy_version": "churn",
    "max_markets": 1, "max_total_markets": 1,
    "max_spend_per_market": str(CAP), "max_total_spend": str(CAP),
    "max_residual": "20.00", "max_daily_realized_loss": "20.00",
    "expiry": "2099-01-01T00:00:00Z",
}


class Venue:
    """Deterministic. `fill_plan` maps the Nth accepted order -> filled qty."""

    def __init__(self, fill_plan=None, *, cancel_sticks=True,
                 unreadable_fill_on=(), unreadable_open_on=()):
        self.orders = {}
        self.n = 0
        self.fill_plan = dict(fill_plan or {})
        self.cancel_sticks = cancel_sticks
        self.unreadable_fill_on = set(unreadable_fill_on)
        self.unreadable_open_on = set(unreadable_open_on)
        self.resting_ids = {"up": None, "down": None}
        self._reported = set()
        self.open_calls = 0
        self.max_live_seen = 0

    def accept(self, body):
        self.n += 1
        oid = "o%d" % self.n
        side = "up" if body["outcomeSide"] == "OUTCOME_SIDE_YES" else "down"
        wire = D(str(body["price"]["value"]))
        own = wire if side == "up" else (D(1) - wire)
        filled = D(str(self.fill_plan.get(self.n, 0)))
        self.orders[oid] = {"side": side, "px": own,
                            "qty": D(str(body["quantity"])),
                            "filled": filled, "live": filled == 0}
        live = len([1 for o in self.orders.values() if o["live"]])
        self.max_live_seen = max(self.max_live_seen, live)
        return {"orderId": oid, "state": "ORDER_STATE_OPEN"}

    def cancel_order(self, oid):
        o = self.orders.get(str(oid))
        if o and self.cancel_sticks:
            o["live"] = False

    def cancel_resting(self, side):
        oid = self.resting_ids.get(side)
        if oid:
            self.cancel_order(oid)

    def open_orders(self):
        self.open_calls += 1
        if self.open_calls in self.unreadable_open_on:
            raise RuntimeError("open_orders unreadable")
        return [{"orderId": k, "marketSlug": MARKET}
                for k, o in self.orders.items() if o["live"]]

    def unbooked_fills(self):
        out = []
        for k, o in self.orders.items():
            if o["filled"] > 0 and k not in self._reported:
                self._reported.add(k)
                out.append({"side": o["side"], "qty": o["filled"],
                            "cost": o["filled"] * o["px"], "maker": True,
                            "fee": None})
        return out

    def filled_qty(self, oid):
        if oid in self.unreadable_fill_on:
            return None
        o = self.orders.get(str(oid))
        return None if o is None else o["filled"]

    def completion_fill(self, intent):
        return D(str(intent["qty"])), D(str(intent["cost"])), D(str(intent["fee"]))


def churn_rows(n=40):
    """A price that moves EVERY tick, so every tick is a replacement."""
    rows = []
    for i in range(n):
        bid = D("0.40") + (D(i % 10) * D("0.01"))
        rows.append(WA.book_row(t=1790612400.0 + i * 5, bid=str(bid),
                                ask=str(bid + D("0.02")),
                                bid_qty="50", ask_qty="50"))
    return rows


def run(tmp_path, venue, *, clip=D(10), rows=None):
    m, err = MANDATE.parse_mandate(json.dumps(MANDATE_RAW))
    assert m is not None, err

    class T:
        def request(self, method, path, body=None, **kw):
            return venue.accept(body)

    g = MANDATE.guard(T(), m, tmp_path, log=lambda k, **kw: None)
    mp = MP.NativeMoneyPath(
        market=MARKET,
        engine=E.NativeEngine(clip=clip, lock_gate=lambda *a, **k: False),
        transport=g, feed=None, venue=venue)
    refused, results = 0, []
    for row in (rows or churn_rows()):
        try:
            results.append(mp.on_book_state(
                row, {"t": row["t"], "bids": [["0.80", "50"]],
                      "offers": [["0.20", "50"]]}))
        except MANDATE.MandateRefusal:
            refused += 1
    return mp, mp.sweep_all(), refused, results


def spent(tmp_path, aid="OWNER-CHURN"):
    u = MANDATE.consumption(tmp_path, aid)
    return D(str(u["per_market"].get(MARKET, "0")))


# ---- the churn actually happens -------------------------------------------
def test_the_churn_exceeds_sixteen_replacements(tmp_path):
    mp, sweep, refused, _ = run(tmp_path, Venue())
    assert len(mp.submitted) > 16, len(mp.submitted)
    assert mp.registry.cumulative_submitted > CAP, (
        "premise: cumulative submissions must exceed the cap, %s"
        % mp.registry.cumulative_submitted)


# 1 ------------------------------------------------- no stacked live quotes
def test_no_side_ever_has_two_live_quotes(tmp_path):
    v = Venue()
    mp, sweep, refused, _ = run(tmp_path, v)
    assert v.max_live_seen <= 2, (
        "%d quotes live at once across two sides" % v.max_live_seen)
    for side in ("up", "down"):
        assert len(mp.registry.live_for(side)) <= 1


# 2 ------------------------------- previous order terminal before replacement
def test_a_replacement_waits_for_the_previous_order_to_go_terminal(tmp_path):
    v = Venue(cancel_sticks=False)          # cancels never take
    mp, sweep, refused, results = run(tmp_path, v)
    blocked = [r for r in results if r.get("replacement_blocked")]
    assert blocked, "a replacement went out over a nonterminal predecessor"
    assert len(mp.submitted) <= 2, (
        "%d orders went out while none could be cancelled" % len(mp.submitted))


# 3 ------------------------------ zero-fill cancellations release reservation
def test_zero_fill_cancellations_release_their_reservation(tmp_path):
    mp, sweep, refused, _ = run(tmp_path, Venue())
    assert mp.registry.filled_commitment == 0
    assert mp.registry.open_reserved == 0, mp.registry.as_dict()
    assert spent(tmp_path) == 0, (
        "cancelled zero-fill quotes still consume %s" % spent(tmp_path))


# 4 --------------------- partial fills retain ONLY filled + open reservation
def test_a_partial_fill_retains_only_its_filled_commitment(tmp_path):
    # order 3 half-fills: 5 of 10
    v = Venue(fill_plan={3: 5})
    mp, sweep, refused, _ = run(tmp_path, v)
    reg = mp.registry
    row = reg.orders["o3"]
    assert row["filled"] == 5
    assert reg.filled_commitment == row["price"] * 5, reg.as_dict()
    assert reg.open_reserved == 0, reg.as_dict()
    assert reg.exposure == reg.filled_commitment


# 5 --------------------------------- exposure never exceeds the mandate
@pytest.mark.parametrize("plan", [{}, {3: 5}, {2: 10, 9: 10},
                                  {i: 10 for i in range(1, 30, 3)}])
def test_live_economic_exposure_never_exceeds_the_mandate(tmp_path, plan):
    d = tmp_path / ("p%d" % abs(hash(str(plan))))
    d.mkdir()
    mp, sweep, refused, _ = run(d, Venue(fill_plan=plan))
    assert mp.registry.exposure <= CAP, mp.registry.as_dict()
    assert spent(d) <= CAP, str(spent(d))


# 6 ------------------------- cumulative submissions cannot bypass the mandate
def test_cumulative_submission_is_not_exposure_and_cannot_bypass_the_cap(
        tmp_path):
    # fills reduce the quote count (variant-B hold, completions), so this plan
    # needs a longer walk to push cumulative submission past the cap. The POINT
    # is that cumulative > cap while exposure <= cap.
    # a fill plan deliberately too small to reach the $5 floor: once LOCK5
    # latches, acquisition stops for the window (correct policy) and the churn
    # ends, so a lock would cap the very count this test is about.
    mp, sweep, refused, _ = run(tmp_path, Venue(fill_plan={5: 1, 17: 1}),
                                rows=churn_rows(90))
    assert mp.eng.locked_at is None, "the lock latched; churn premise void"
    reg = mp.registry
    assert reg.cumulative_submitted > CAP, reg.as_dict()
    assert reg.exposure <= CAP, reg.as_dict()
    assert spent(tmp_path) <= CAP
    assert refused == 0, "the window starved despite released reservations"


def test_a_cancellation_never_creates_additional_authority(tmp_path):
    """A fill's commitment is permanent, so churn cannot mint headroom."""
    mp, sweep, refused, _ = run(tmp_path, Venue(fill_plan={2: 10, 4: 10}))
    before = mp.registry.filled_commitment
    mp.sweep_all()
    assert mp.registry.filled_commitment == before, "a fill was released"
    assert spent(tmp_path) >= before


# 7 ------------------------------------- LOCK cancels/reconciles all orders
def test_lock_cancels_and_reconciles_every_session_order(tmp_path):
    from src import pmus_native_live_overlay as OV
    v = Venue()
    m, _ = MANDATE.parse_mandate(json.dumps(MANDATE_RAW))

    class T:
        def request(self, method, path, body=None, **kw):
            return v.accept(body)

    g = MANDATE.guard(T(), m, tmp_path, log=lambda k, **kw: None)
    mp = MP.NativeMoneyPath(market=MARKET,
                            engine=E.NativeEngine(clip=D(10),
                                                  lock_gate=lambda *a, **k: False),
                            transport=g, feed=None, venue=v)
    for row in churn_rows(6):
        mp.on_book_state(row, None)
    # inventory whose floor is already >= $5 by holding
    mp.eng.on_fill("up", D(100), D("10.00"), maker=True)
    mp.eng.on_fill("down", D(100), D("10.00"), maker=True)
    res = mp.on_book_state(churn_rows(8)[7], None)
    assert res["lock"] == OV.LATCHED, res
    assert v.open_orders() == [], v.open_orders()
    assert mp.registry.nonterminal() == [], mp.registry.as_dict()


# 8 ------------------------------------- close-out ends with zero open orders
def test_close_out_finishes_with_zero_open_orders(tmp_path):
    for plan in ({}, {3: 5}, {2: 10}):
        d = tmp_path / ("c%d" % abs(hash(str(plan))))
        d.mkdir()
        v = Venue(fill_plan=plan)
        mp, sweep, refused, _ = run(d, v)
        assert sweep["open_orders_after"] == 0, sweep
        assert sweep["nonterminal_after"] == 0, sweep
        assert sweep["clean"] is True, sweep
        assert v.open_orders() == []


# 9 --------------------------------------------- unknown state fails closed
def test_an_unreadable_filled_quantity_keeps_the_full_reservation(tmp_path):
    v = Venue(unreadable_fill_on={"o1"})
    mp, sweep, refused, results = run(tmp_path, v)
    row = mp.registry.orders["o1"]
    assert row["state"] == MP.UNKNOWN, row
    assert row["filled"] == 0
    assert mp.registry.open_reserved >= row["reserved"], (
        "an unknown order released budget")
    assert sweep["clean"] is False, "an unknown order was reported clean"


def test_an_unreadable_open_orders_read_blocks_replacement(tmp_path):
    v = Venue(unreadable_open_on={2, 3, 4, 5, 6})
    mp, sweep, refused, results = run(tmp_path, v)
    assert any(r.get("replacement_blocked") for r in results), (
        "a replacement proceeded on an unreadable venue read")


def test_a_cancel_that_raises_marks_the_order_unknown(tmp_path):
    class Boom(Venue):
        def cancel_order(self, oid):
            raise RuntimeError("cancel exploded")

    v = Boom()
    mp, sweep, refused, _ = run(tmp_path, v)
    assert any(r["state"] == MP.UNKNOWN for r in mp.registry.orders.values())
    assert sweep["clean"] is False


# 10 ------------------------------------ the frozen policy has not moved
def test_the_frozen_parameters_and_parity_are_untouched():
    assert (E.CLIP, E.THETA, E.VARIANT, E.HOLD_S) == (D(50), D("0.96"), "B", 60.0)
    assert (E.RESIDUAL_CAP_K, E.PROFIT_LOCK_USD) == (D(2), D(5))
    assert E.FLOOR_GUARD is False and E.COMPLEMENT_BIAS_C is None


@pytest.mark.slow
def test_q50_parity_is_still_25_of_25():
    from scripts.audit_economic_complete import load, USABLE
    from src import pmus_native_parity as P
    r = P.compare(load(), USABLE)
    assert r["exact_net_matches"] == 25 == r["n"]
    assert r["live_mean_net"] == r["frozen_mean_net"]
    assert round(float(r["live_mean_net"]), 4) == 6.1167
