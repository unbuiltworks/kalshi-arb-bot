"""The production order lifecycle: desired surface -> RETAIN / ADD / CANCEL.

THE DEFECT THESE PIN. LIVE-2 cancelled three of five NO rungs at 9s, 19s and
11s because the desired price had moved. The frozen lane's own
minimum_rest_time is 30.0s, and the code carrying that discipline
(continuous_probe.stale_orders) was called nowhere in production. Every
cancel surrendered queue position for a reason that is not a safety reason.

THE GEOMETRY THESE PIN (EP006, measured, not preferred):
  BTC15m  3,434 maker transactions consumed >=2 distinct L5 prices at
          ADJACENT ticks in ONE taker action -> >=2 concurrent resting orders.
  BTC5m   ZERO in 6,229, where ~356 were expected at BTC15m's rate. BTC5m
          therefore keeps ONE level; it does not inherit the ladder.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import ws_continuous as WC

T0 = 1_000_000.0
BTC5M = "GEMI-BTC05M"
BTC15M = "GEMI-BTC15M"


def _working(*orders, now=T0):
    w = WC.WorkingOrders()
    for i, (leg, price, qty, age) in enumerate(orders):
        w.add("c%d" % i, leg=leg, price=price, qty=qty,
              now=now - age, venue_order_id="v%d" % i)
    return w


def _rung(leg, price, qty="1"):
    return {"leg": leg, "price": price, "qty": qty, "action": CP.OPEN}


# =================================================== 1. the rest floor holds

@pytest.mark.parametrize("age", [0.0, 9.0, 19.0, 29.9])
def test_a_moved_price_does_not_cancel_a_young_valid_order(age):
    """THE LIVE-2 DEFECT, at the exact ages it happened: 9s and 19s."""
    w = _working(("NO", "0.58", "1", age))
    out = WC.reconcile_surface([_rung("NO", "0.68")], w, now=T0,
                               cohort_prefix=BTC5M)
    assert out["cancel"] == [], out["cancel"]
    assert len(out["retain"]) == 1
    assert out["retain"][0]["order"]["price"] == "0.58"
    # and the moved quote does NOT also go on: the slot is occupied
    assert out["place"] == []
    assert out["mutations"] == 0, "a retained order must cost no slot"


def test_at_the_floor_the_normal_path_may_replace_it():
    """>= 30s: ordinary reconciliation resumes."""
    w = _working(("NO", "0.58", "1", 30.0))
    out = WC.reconcile_surface([_rung("NO", "0.68")], w, now=T0,
                               cohort_prefix=BTC5M)
    assert len(out["cancel"]) == 1
    assert out["cancel"][0]["order"]["price"] == "0.58"
    assert len(out["place"]) == 1
    assert out["place"][0]["rung"]["price"] == "0.68"
    assert out["mutations"] == 2


def test_an_exactly_desired_order_is_KEEP_not_RETAIN_at_any_age():
    """KEEP and RETAIN are different states and must not be conflated."""
    for age in (1.0, 99.0):
        w = _working(("NO", "0.58", "1", age))
        out = WC.reconcile_surface([_rung("NO", "0.58")], w, now=T0,
                                   cohort_prefix=BTC5M)
        assert len(out["keep"]) == 1 and out["retain"] == []
        assert out["mutations"] == 0


# ============================================ 2. safety bypasses the floor

@pytest.mark.parametrize("reason", WC.SAFETY_CANCEL_REASONS)
def test_every_enumerated_safety_reason_cancels_a_young_order(reason):
    w = _working(("NO", "0.58", "1", 0.5))
    out = WC.reconcile_surface([], w, now=T0, cohort_prefix=BTC5M,
                               safety=reason)
    assert len(out["cancel"]) == 1, reason
    assert out["cancel"][0]["safety"] == reason
    assert out["retain"] == []


def test_an_unenumerated_safety_reason_is_refused_not_honoured():
    """A free-text reason would be a way to bypass the floor by label."""
    w = _working(("NO", "0.58", "1", 0.5))
    for bogus in ("price moved", "", "URGENT", "terminal_withdrawal"):
        with pytest.raises(WC.SafetyCancelUnknown):
            WC.reconcile_surface([], w, now=T0, cohort_prefix=BTC5M,
                                 safety=bogus)


def test_the_enumeration_is_exactly_the_six_agreed_states():
    assert set(WC.SAFETY_CANCEL_REASONS) == {
        "TERMINAL_WITHDRAWAL", "ENVELOPE_BREACH", "MARKET_INVALIDATED",
        "POSITION_CONFLICT", "RECOVERY_SAFETY", "FAIL_CLOSED"}
    assert "PRICE_MOVED" not in WC.SAFETY_CANCEL_REASONS


# =================================== 3. BTC15m two adjacent levels, BTC5m one

def test_btc15m_exposes_two_adjacent_same_side_levels_at_once():
    """EP006's minimum supported geometry, and no more."""
    w = _working()
    out = WC.reconcile_surface([_rung("YES", "0.69"), _rung("YES", "0.70")],
                               w, now=T0, cohort_prefix=BTC15M)
    assert out["leg_slots"] == 2
    assert len(out["place"]) == 2
    assert sorted(p["rung"]["price"] for p in out["place"]) == ["0.69", "0.70"]


def test_btc15m_stops_at_two_and_does_not_become_a_grid():
    w = _working()
    out = WC.reconcile_surface(
        [_rung("YES", "0.68"), _rung("YES", "0.69"), _rung("YES", "0.70")],
        w, now=T0, cohort_prefix=BTC15M)
    assert len(out["place"]) == 2, "three levels is not authorized"


def test_btc5m_keeps_one_level_and_does_not_inherit_the_ladder():
    """ZERO multi-price transactions in 6,229. Absence of evidence is not a
    licence to copy BTC15m's geometry."""
    w = _working()
    out = WC.reconcile_surface([_rung("YES", "0.69"), _rung("YES", "0.70")],
                               w, now=T0, cohort_prefix=BTC5M)
    assert out["leg_slots"] == 1
    assert len(out["place"]) == 1


def test_an_unknown_or_absent_family_gets_the_conservative_single_level():
    for prefix in (None, "", "GEMI-ETH05M"):
        out = WC.reconcile_surface(
            [_rung("YES", "0.69"), _rung("YES", "0.70")],
            _working(), now=T0, cohort_prefix=prefix)
        assert out["leg_slots"] == 1, prefix


# ============================ 4. a fill does not knock out the other rung

def test_one_fill_leaves_the_other_valid_rung_resting():
    """The sibling rung is still valid and still young; nothing about the
    fill makes cancelling it a safety matter."""
    w = _working(("YES", "0.69", "1", 5.0), ("YES", "0.70", "1", 5.0))
    # the 0.70 rung filled and left the working set
    w.drop("c1")
    out = WC.reconcile_surface([_rung("YES", "0.68")], w, now=T0,
                               cohort_prefix=BTC15M)
    assert out["cancel"] == []
    assert len(out["retain"]) == 1
    assert out["retain"][0]["order"]["price"] == "0.69"
    # A FREE SLOT IS NO LONGER PERMISSION. This asserted `place == 1` on the
    # reasoning that the filled rung freed a slot -- which is precisely the
    # rule that turned capacity into authority: the desired surface asks for
    # ONE YES rung, 0.69 is still holding it, so 0.68 is HELD until 0.69
    # passes the floor and cancels. The part of this test that matters is
    # unchanged: the sibling rung is not cancelled because something else
    # filled.
    assert out["place"] == []
    assert [h["rung"]["price"] for h in out["hold"]] == ["0.68"]
    assert out["mutations"] == 0


# ================================= 5. the bounds see every live rung

def test_the_two_level_envelope_is_derived_not_chosen():
    e = LE.LIVE_Q1_BTC15M_2LVL
    assert e.action_size == D("1")
    assert e.max_working_orders == 4        # 2 levels x 2 sides
    assert e.max_committed_usd == D("4.00")  # 4 rungs x $1.00 ceiling
    assert e.max_residual_usd == D("2.00")   # UNCHANGED from LIVE_Q1
    assert e.max_same_side_run == 2
    assert e.cohort_prefix == "GEMI-BTC15M"


def test_live_q1_for_btc5m_was_not_widened():
    """BTC5m's authorized limits must not pay for BTC15m's geometry."""
    e = LE.LIVE_Q1
    assert e.max_working_orders == 2
    assert e.max_committed_usd == D("2.50")
    assert e.max_residual_usd == D("2.00")


def test_every_simultaneously_live_rung_counts_against_committed_cash():
    """Four live rungs at $0.99 is $3.96 and fits; a fifth does not."""
    inv = CP.Inventory()
    resting = tuple({"leg": "YES", "price": "0.99", "qty": "1"}
                    for _ in range(3))
    assert LE.would_breach("NO", "0.99", inv, qty=D("1"), resting=resting,
                           env=LE.LIVE_Q1_BTC15M_2LVL) is None
    resting4 = resting + ({"leg": "NO", "price": "0.99", "qty": "1"},)
    why = LE.would_breach("NO", "0.99", inv, qty=D("1"), resting=resting4,
                          env=LE.LIVE_Q1_BTC15M_2LVL)
    assert why is not None, "a fifth working order must be refused"


def test_the_two_level_envelope_still_fails_closed_on_residual():
    inv = CP.Inventory()
    inv.apply_fill("YES", "2", "0.10", source="t")     # 2 contracts residual
    assert LE.would_breach("YES", "0.10", inv, qty=D("1"),
                           env=LE.LIVE_Q1_BTC15M_2LVL) is not None


# ================================ 6. restart / outstanding-order recovery

def test_recovery_restores_multiple_resting_orders_from_the_venue():
    w = WC.WorkingOrders()
    note = w.reconcile([
        {"clientOrderId": "a", "outcome": "yes", "price": "0.69",
         "quantity": "1", "orderId": "1"},
        {"clientOrderId": "b", "outcome": "yes", "price": "0.70",
         "quantity": "1", "orderId": "2"},
        {"clientOrderId": "c", "outcome": "no", "price": "0.29",
         "quantity": "1", "orderId": "3"},
    ], now=T0)
    assert len(w.rungs()) == 3, note
    assert {r["price"] for r in w.rungs()} == {"0.69", "0.70", "0.29"}


def test_the_outstanding_ledger_tracks_several_concurrent_rungs(tmp_path):
    from src.gemini_execution import outstanding_ledger as OL
    led = OL.OutstandingLedger(str(tmp_path / "o.jsonl"),
                               auth_id="A", symbol="GEMI-BTC15M-X")
    for i, (leg, px) in enumerate((("YES", "0.69"), ("YES", "0.70"),
                                   ("NO", "0.29"), ("NO", "0.28"))):
        led.intent("c%d" % i, leg=leg, price=px, qty="1")
        led.accepted("c%d" % i, venue_order_id="v%d" % i)
    c = led.committed_usd()
    assert c["readable"], c
    assert D(str(c["usd"])) == D("0.69") + D("0.70") + D("0.29") + D("0.28")
    assert len(led.blocks_restart()) == 4


def test_recovered_orders_have_an_age_so_the_floor_is_evaluable():
    """A recovered order with no resting_since would be age 0 and retained
    forever; it must default to NOW, which makes it immediately eligible for
    ordinary reconciliation once the floor elapses."""
    w = WC.WorkingOrders()
    w.reconcile([{"clientOrderId": "a", "outcome": "yes", "price": "0.69",
                  "quantity": "1", "orderId": "1"}], now=T0)
    assert w.rungs()[0]["resting_since"] == T0
    out = WC.reconcile_surface([_rung("YES", "0.60")], w, now=T0 + 31,
                               cohort_prefix=BTC15M)
    assert len(out["cancel"]) == 1


# ================================ 7. the driver actually uses the lifecycle

def test_the_driver_passes_a_clock_and_the_family():
    import inspect
    src = inspect.getsource(WC.Driver.decide)
    assert "reconcile_surface(" in src
    assert "now=now" in src
    assert "cohort_prefix=self._cohort_prefix()" in src
    assert "diff_surface(" not in src


def test_withdrawal_is_an_enumerated_safety_cancel():
    import inspect
    src = inspect.getsource(WC.Driver.decide)
    assert 'safety="TERMINAL_WITHDRAWAL"' in src


def test_diff_surface_is_a_facade_over_one_implementation():
    """Two reconciliation implementations is how they drift apart."""
    import inspect
    assert "reconcile_surface(desired, working, now=None)" in \
        inspect.getsource(WC.diff_surface)
    # and without a clock nothing is retained -- the old behaviour exactly
    w = _working(("NO", "0.58", "1", 0.5))
    out = WC.diff_surface([_rung("NO", "0.68")], w)
    assert len(out["cancel"]) == 1 and out["retain"] == []


# ============ RETAIN COSTS ZERO MUTATIONS AND FULL RISK ============

def test_a_retained_order_still_counts_as_a_working_order():
    """RETAIN removes nothing from the working set, so the venue still holds
    it and every bound must still see it."""
    w = _working(("YES", "0.69", "1", 1.0), ("NO", "0.29", "1", 1.0))
    out = WC.reconcile_surface([_rung("YES", "0.60")], w, now=T0,
                               cohort_prefix=BTC15M)
    # NOTE: on BTC15m a retained order leaves the SECOND slot free, so an ADD
    # is correct here. My first version asserted mutations == 0 and was
    # wrong about the code, not the other way round. What this test is
    # actually about is RISK, below.
    assert len(out["retain"]) == 2
    assert len(w.rungs()) == 2, "RETAIN must not drop the order"
    why = LE.would_breach("YES", "0.10", CP.Inventory(), qty=D("1"),
                          resting=w.rungs(), env=LE.LIVE_Q1_BTC15M)
    assert why is not None and "resting" in why


def test_a_retained_orders_notional_still_counts_as_committed_cash():
    """Zero mutations is NOT zero capital. Two retained rungs at $0.99 plus a
    third would exceed $2.50, and must be refused on CASH, not on count."""
    w = _working(("YES", "0.99", "1", 1.0))
    out = WC.reconcile_surface([_rung("YES", "0.10")], w, now=T0,
                               cohort_prefix=BTC5M)      # one slot: no ADD
    assert len(out["retain"]) == 1 and out["mutations"] == 0
    inv = CP.Inventory()
    inv.apply_fill("NO", "1", "0.99", source="t")      # $0.99 already spent
    why = LE.would_breach("NO", "0.99", inv, qty=D("1"), resting=w.rungs(),
                          env=LE.LIVE_Q1_BTC15M)
    # 0.99 spent + 0.99 retained + 0.99 new = 2.97 > 2.50
    # the breach names the arithmetic, which is the point: filled 0.99 +
    # resting 0.99 + this rung 0.99 = 2.97, past 2.50. The RETAINED rung's
    # $0.99 is in that sum -- zero mutations, full capital.
    assert why is not None
    assert "resting 0.99" in why and "2.97" in why and "2.50" in why


def test_the_btc15m_probe_envelope_is_live_q1s_numbers():
    """The frozen policy emits ONE rung per outcome, so nothing is widened."""
    a, b = LE.LIVE_Q1_BTC15M, LE.LIVE_Q1
    for f in ("action_size", "max_same_side_run", "max_yes", "max_no",
              "max_total_spend_usd", "max_committed_usd", "max_residual_usd",
              "max_working_orders", "max_concurrent_markets", "max_actions"):
        assert getattr(a, f) == getattr(b, f), f
    assert a.cohort_prefix == "GEMI-BTC15M"


def test_every_frozen_lane_emits_one_rung_at_the_bid():
    """The fact the four-rung envelope was sized for: no lane can produce a
    second level, so a four-order surface is UNREACHABLE under any of them."""
    from src.l5_surface import params as PR
    for name, lane in PR.load_frozen().items():
        assert str(lane.params["rung_count"].value) == "1", name
        assert list(lane.params["tick_offsets"].value) == [0], name


def test_the_two_level_envelope_is_capability_not_the_proposal():
    """Kept and tested, but it pays for a surface nothing can emit."""
    assert LE.LIVE_Q1_BTC15M_2LVL.max_working_orders == 4
    assert LE.LIVE_Q1_BTC15M.max_working_orders == 2
