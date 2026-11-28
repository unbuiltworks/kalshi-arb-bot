"""Small-order submission, fill reconciliation and inventory accounting.

WHY SMALL SIZES GET THEIR OWN TESTS. The venue's `minimumTradeQty` is 0.01
shares, so a leg at a 0.08 rung is $0.0008 of notional and a matched pair of
one lot is worth $0.0008 of locked economics. Every rounding the ledger does
to the cent is larger than the whole position. The existing suite exercises
realistic clips; this one pushes on the floor, where a quantisation that is
invisible at $2.00 destroys the entire quantity.

Three properties, at the smallest sizes the venue will accept:

  SUBMISSION READINESS  a clip that cannot buy one lot is REFUSED, not
                        rounded up to one; a clip that can buy exactly one
                        lot submits exactly one lot.
  FILL RECONCILIATION   a partial fill is never promoted, a duplicate
                        execution report is never counted twice, and a
                        cancel that loses to a fill books the fill.
  INVENTORY ACCOUNTING  quantity and cost basis survive a restart byte for
                        byte, at four-decimal notionals.

NOTHING IS SUBMITTED LIVE. `Venue` is a double and `Venue.sent` is a list.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_venue_constraints as VC

from tests.test_l5_btc5m_execution import (          # noqa: E402
    ARMED, MARKET, Rig, armed, clean_env,            # noqa: F401
)

ONE_LOT = D("0.01")
RUNG = D("0.08")


@pytest.fixture
def tiny(monkeypatch, tmp_path):
    """The straddle, clipped so near the floor that one lot is the order."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.00")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "1.00")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "5")
    #: THE ENTRY-EV RATES ARE SET HERE because these tests are about
    #: ACCOUNTING at the size floor, not about the gate. Leaving them unset
    #: refuses every order with ENTRY_EV_P_PAIR_NATURAL_NOT_SET -- which is
    #: the gate working, and is what my first version of this fixture hit.
    #: They are SYNTHETIC and mechanism-only: nothing here claims these are
    #: measured rates, and the gate's own behaviour is covered in
    #: tests/test_l5_straddle_ev.py and the conditional-basis file.
    monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.95")
    monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.0001")
    monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.50")
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)
    return lambda clip: monkeypatch.setenv(X.CLIP_USD_ENV, clip)


@pytest.fixture
def rig6(tmp_path):
    return Rig(tmp_path, max_live_orders=6)


def step(rig, t=30.0):
    return rig.ex.step(market=MARKET,
                       book={"bid": "0.60", "ask": "0.62", "age_s": 0.1},
                       t_in_window_s=t, p_up="0.75", p_source="TEST")


def inv_of(rig):
    i = rig.ex.inventory(MARKET)
    return {k: (D(str(v["qty"])), D(str(v["cost"]))) for k, v in i.items()}


# ================================================ SUBMISSION READINESS

def test_a_configured_clip_below_a_venue_lot_refuses(rig6, armed, tiny):
    """THE DEFECT THIS CLOSES. `clip_usd` fell back to its $0.10 DEFAULT for
    any configured value outside [0.02, max], justified as "a typo must never
    widen the size this strategy trades in" -- but a value BELOW the floor is
    a request to trade SMALLER, and defaulting WIDENED it.

    MEASURED against this module before the fix: L5_BTC5M_CLIP_USD=0.0008
    sent 1.25 shares a side at 0.08 -- $0.10 a leg against $0.0008 asked,
    125 TIMES the configured size, with no refusal and nothing in the report
    saying the value had been ignored. For a brief whose whole point is $5 of
    capital, that is the difference between a $0.0016 pair and a $0.20 one.
    """
    tiny("0.00001")                     # below one lot at ANY price
    rep = step(rig6)
    assert rig6.venue.sent == [], rep
    assert rep["placed"] == []
    assert any(r["reason"].startswith("CLIP_USD_BELOW_VENUE_LOT")
               for r in rep["refused"]), rep["refused"]


def test_the_old_floor_no_longer_silently_widens_the_size(rig6, armed, tiny):
    """A clip between the old $0.02 floor and one venue lot is now HONOURED
    at its own size rather than replaced by $0.10."""
    tiny("0.0008")
    step(rig6)
    assert rig6.venue.sent
    for s in rig6.venue.sent:
        assert D(s["qty"]) == ONE_LOT, "silently widened to %s" % s["qty"]
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig6.venue.sent)
    assert spent == D("0.0016"), spent        # two legs, one lot each


def test_an_absent_clip_still_takes_the_default(rig6, armed, tiny,
                                                monkeypatch):
    """Not configuring is a choice; it keeps today's behaviour exactly."""
    monkeypatch.delenv(X.CLIP_USD_ENV, raising=False)
    step(rig6)
    assert rig6.venue.sent
    for s in rig6.venue.sent:
        assert D(s["qty"]) == D("1.25")      # $0.10 / 0.08


@pytest.mark.parametrize("raw,frag", [
    ("abc", "CLIP_USD_UNREADABLE"),
    ("0", "CLIP_USD_NOT_POSITIVE"),
    ("-1", "CLIP_USD_NOT_POSITIVE"),
    ("0.00001", "CLIP_USD_BELOW_VENUE_LOT"),
    ("99", "CLIP_USD_ABOVE_MODE_CEILING"),
])
def test_every_unusable_configured_clip_names_itself(raw, frag, monkeypatch):
    monkeypatch.setenv(X.CLIP_USD_ENV, raw)
    got = X.clip_usd_refusal(X.STRADDLE_CLIP_MAX_USD)
    assert got and got.startswith(frag), got


def test_an_absent_or_blank_clip_is_not_a_refusal(monkeypatch):
    monkeypatch.delenv(X.CLIP_USD_ENV, raising=False)
    assert X.clip_usd_refusal() is None
    monkeypatch.setenv(X.CLIP_USD_ENV, "   ")
    assert X.clip_usd_refusal() is None


def test_a_clip_that_buys_exactly_one_lot_sends_exactly_one_lot(
        rig6, armed, tiny):
    tiny("0.0008")                      # one lot at 0.08
    step(rig6)
    assert rig6.venue.sent, "one lot of demand must be submittable"
    for s in rig6.venue.sent:
        assert D(s["qty"]) == ONE_LOT, s
        assert D(s["price"]) == RUNG


def test_the_smallest_order_is_on_both_venue_grids(rig6, armed, tiny):
    tiny("0.0008")
    step(rig6)
    spec = VC.from_market({"minimumTradeQty": 0.01,
                           "orderPriceMinTickSize": 0.01})
    for s in rig6.venue.sent:
        assert VC.check_order(s["price"], s["qty"], spec) is None, s


# ================================================ FILL RECONCILIATION

def test_a_full_fill_of_one_lot_is_booked_at_four_decimals(rig6, armed, tiny):
    """$0.0008 must not quantise to $0.00. The notional is smaller than the
    cent the venue reports in, so any rounding here is total loss."""
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    side = rig6.venue.by_oid[oid]["side"]
    rig6.venue.fill(oid)
    rig6.tick(2)
    step(rig6)
    qty, cost = inv_of(rig6)[side]
    assert qty == ONE_LOT, qty
    assert cost == ONE_LOT * RUNG == D("0.0008"), cost


def test_a_partial_of_one_lot_cannot_exist_and_full_is_exact(rig6, armed,
                                                             tiny):
    """One lot is indivisible on this venue, so the only partial of a
    one-lot order is zero. Nothing may invent a sub-lot position."""
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    row = rig6.venue.order(oid)
    assert D(row["quantity"]) == ONE_LOT
    rig6.venue.fill(oid)
    assert D(rig6.venue.order(oid)["cumQuantity"]) == ONE_LOT
    assert D(rig6.venue.order(oid)["leavesQuantity"]) == D("0")


def test_a_partial_fill_of_a_multi_lot_order_is_not_promoted(rig6, armed,
                                                             tiny):
    tiny("0.0040")                      # five lots at 0.08
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    want = D(str(rig6.venue.by_oid[oid]["qty"]))
    assert want >= D("0.05")
    rig6.venue.fill(oid, qty=D("0.02"))
    row = rig6.venue.order(oid)
    assert row["state"] == "ORDER_STATE_PARTIALLY_FILLED"
    assert D(row["cumQuantity"]) == D("0.02")
    assert D(row["leavesQuantity"]) == want - D("0.02")
    rig6.tick(2)
    step(rig6)
    side = rig6.venue.by_oid[oid]["side"]
    qty, cost = inv_of(rig6)[side]
    assert qty == D("0.02"), "only the filled part is inventory"
    assert cost == D("0.02") * RUNG


def test_a_repeated_execution_report_is_not_counted_twice(rig6, armed, tiny):
    """Reading the same venue row again must not add inventory. The adapter
    reconciles from venue state, so re-reading is the normal case."""
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    side = rig6.venue.by_oid[oid]["side"]
    rig6.venue.fill(oid)
    for _ in range(4):
        rig6.tick(2)
        step(rig6)
    qty, cost = inv_of(rig6)[side]
    assert qty == ONE_LOT, "a re-read added inventory: %s" % qty
    assert cost == D("0.0008"), cost


def test_a_cancel_that_loses_to_a_fill_books_the_fill(rig6, armed, tiny):
    """The race, at one lot. A cancel is not evidence the order did not
    fill, and the filled lot must appear as inventory."""
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    side = rig6.venue.by_oid[oid]["side"]
    rig6.venue.fill_on_cancel.add(oid)
    rig6.venue.cancel(oid, MARKET)
    rig6.tick(2)
    step(rig6)
    qty, _cost = inv_of(rig6)[side]
    assert qty == ONE_LOT, "the fill that beat the cancel was lost"


# ================================================ INVENTORY ACCOUNTING

def test_inventory_and_cost_basis_survive_a_restart_exactly(rig6, armed,
                                                            tiny):
    """A restart re-reads the ledger from disk. At $0.0008 a cost basis that
    round-trips through a cent-quantising writer comes back as zero."""
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    rig6.venue.fill(oid)
    rig6.tick(2)
    step(rig6)
    before = inv_of(rig6)
    rig6.restart()
    after = inv_of(rig6)
    assert before == after, (before, after)
    assert any(q > 0 for q, _c in before.values()), "nothing was held"
    for _side, (q, c) in before.items():
        if q > 0:
            assert c > 0, "a held lot with a zero cost basis"


def test_the_ledger_on_disk_keeps_four_decimals(rig6, armed, tiny):
    tiny("0.0008")
    step(rig6)
    oid = next(iter(rig6.venue.by_oid))
    rig6.venue.fill(oid)
    rig6.tick(2)
    step(rig6)
    blob = json.dumps(rig6.disk())
    assert "0.01" in blob, "the filled quantity is not on disk"
    #: and the cost is not quantised away
    held = [c for q, c in inv_of(rig6).values() if q > 0]
    assert held and all(c == D("0.0008") for c in held), held


def test_accumulating_lots_sums_the_basis_without_drift(rig6, armed, tiny):
    """Ten one-lot fills must cost exactly ten lots, not 0.0080000001."""
    tiny("0.0008")
    total = D("0")
    for _ in range(10):
        step(rig6)
        for oid in list(rig6.venue.by_oid):
            if oid not in rig6.venue.full and oid not in rig6.venue.retired:
                rig6.venue.fill(oid)
                total += ONE_LOT
        rig6.tick(2)
    step(rig6)
    held_q = sum(q for q, _c in inv_of(rig6).values())
    held_c = sum(c for _q, c in inv_of(rig6).values())
    assert held_q == held_q.quantize(D("0.01")), held_q
    assert held_c == held_q * RUNG, (held_c, held_q)


def test_the_six_order_limit_holds_at_one_lot_each(rig6, armed, tiny):
    """The order COUNT is the binding limit at this size, not the budget:
    six one-lot orders at 0.08 is $0.0048 of notional."""
    tiny("0.0008")
    for _ in range(10):
        step(rig6)
        rig6.tick(2)
        assert len(rig6.ex.live_orders()) <= 6
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig6.venue.sent)
    assert spent < D("0.10"), spent


def test_a_tighter_cash_cap_still_wins_at_this_size(rig6, armed, tiny,
                                                    monkeypatch):
    tiny("0.0008")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.0016")
    for _ in range(10):
        step(rig6)
        rig6.tick(2)
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig6.venue.sent)
    assert spent <= D("0.0016"), spent
