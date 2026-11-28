"""Proof that the $5 / residual envelope actually BINDS once fills exist.

Why this test had to be written. In the DISARMED session the SAFETY_OVERLAY
refused nothing across 613 L5 actions, and the shadow inventory reached
+52.87 shares. That is not the envelope being generous: `committed` and
`positions` only move in `apply_fill`, and DISARMED nothing fills, so the
overlay was evaluated against a permanently empty book of inventory. The
shadow numbers therefore say NOTHING about whether the live cap holds.

These tests exercise the same overlay with inventory actually present.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM

MK = "cpc-btc-updown-15m-2026-09-24-0100z"
WS = dt.datetime(2026, 9, 24, 1, 0, tzinfo=dt.timezone.utc).timestamp()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def book(off):
    return {"t": WS + off, "bid": "0.40", "ask": "0.60",
            "bid_qty": "100000", "ask_qty": "100000", "status": "OK"}


def action(off, side="UP", price="0.99", size="22.13"):
    """price 0.99 crosses the 0.60 ask, so it is a taker candidate."""
    return {"action_key": "k%s" % off, "receipt_utc": iso(WS + off),
            "l5": {"pmus_side": side, "l5_price": price, "l5_size": size},
            "pmus": {}}


class Disarmed:
    name = "PMUS"

    def submit(self, intent):
        raise EX.NotArmed("DISARMED")


def run(store, rows, now_off=None):
    # the book must sit within the FROZEN 2.0s staleness bound of the action,
    # otherwise the frozen rule refuses first and the overlay is never reached
    books = [{**book(0.0), "t": WS + _off(rows) - 0.5}]
    return W.run_window(rows, books, market=MK, window="0100z",
                        store=store, limits=PM.probe_limits(),
                        transport=Disarmed(), lam=PM.LAMBDA,
                        now_fn=lambda: dt.datetime.fromtimestamp(
                            WS + (now_off if now_off is not None
                                  else _off(rows) + 1.0),
                            dt.timezone.utc))


def _off(rows):
    """The earliest action offset in this batch."""
    import datetime as _dt
    return min((_dt.datetime.fromisoformat(r["receipt_utc"]).timestamp() - WS)
               for r in rows)


def test_with_no_fills_the_overlay_never_binds(tmp_path):
    """The shadow condition: this is why 613 actions produced 0 refusals."""
    s = EX.InventoryStore(str(tmp_path / "a.json"))
    out = run(s, [action(1.0)])
    assert out["summary"]["refused_by_layer"].get("SAFETY_OVERLAY") is None
    assert s.committed() == {} or s.committed().get(MK, D(0)) == D(0)


def test_the_5_dollar_cap_binds_once_inventory_is_real(tmp_path):
    s = EX.InventoryStore(str(tmp_path / "b.json"))
    s.apply_fill(MK, "UP", D("8"), D("0.60"), basis=EX.OWN)        # $4.80 committed
    assert s.committed()[MK] == D("4.80")
    out = run(s, [action(2.0)])
    r = out["records"][0]
    assert r["refused_by_layer"] == "SAFETY_OVERLAY"
    assert out["summary"]["would_submit"] == 0


def test_the_residual_cap_binds(tmp_path):
    s = EX.InventoryStore(str(tmp_path / "c.json"))
    s.apply_fill(MK, "UP", D("10"), D("0.01"), basis=EX.OWN)       # residual +10, cheap
    assert s.residual(MK) == D("10")
    out = run(s, [action(3.0, side="UP")])
    assert out["records"][0]["refused_by_layer"] == "SAFETY_OVERLAY"


def test_the_maximum_live_exposure_is_the_cap_not_the_shadow_number(tmp_path):
    """However large the shadow inventory grows, the live path cannot commit
    more than the envelope, because every fill is charged against it."""
    s = EX.InventoryStore(str(tmp_path / "d.json"))
    lim = PM.probe_limits()
    committed = D(0)
    for i in range(40):
        out = run(s, [action(float(i) + 1.0)])
        rec = out["records"][0]
        o = rec.get("ORDER") or {}
        if o.get("reason") == "NOT_ARMED" and o.get("intent"):
            # simulate the fill the live path would have taken
            it = o["intent"]
            # the intent carries L5's own-side price, so OWN terms
            s.apply_fill(MK, it["side"], D(str(it["qty"])),
                         D(str(it["price"])), basis=EX.OWN)
            committed = s.committed()[MK]
        assert committed <= lim.max_usd_per_market, committed
        assert abs(s.residual(MK)) <= lim.max_abs_residual
    assert committed <= D("5")


def test_the_daily_loss_halt_is_loaded_and_bounds_the_day():
    lim = PM.probe_limits()
    assert lim.daily_realized_loss_halt_usd == D("5")
    assert lim.max_concurrent_markets == 1
    assert PM.worst_case()["worst_case_total_usd"] == "5.2375"
