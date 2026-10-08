"""THE 20-ORDER RUNAWAY, AND WHY IT CANNOT HAPPEN AGAIN.

On 2026-10-08 the 1230z BTC15m window accumulated twenty resting E1 orders
while the frozen spec declared `max_outstanding_orders: 1` and
`runner.eligible()` carried the rule that enforces it. The rule read
`pmus_provider`'s hard-coded `our_outstanding_orders: 0`, so it could not
fire. These tests fail on the old code and pass on the repair.

Every scenario the owner named is here: the runaway itself, restart
recovery, partial fills, uncertain acknowledgements, and the cancellation
race. Plus the thing I got wrong last time -- a test that proves the GATE
exists but never proves anything FEEDS it. So the first test below asserts
on the SOURCE that the literals are gone.
"""
from __future__ import annotations

import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from analysis.l5.e1_cohort import order_state as OS

HERE = Path(__file__).resolve().parent
PROVIDER_SRC = (HERE / "pmus_provider.py").read_text()
RUNNER_SRC = (HERE / "runner.py").read_text()

SLUG = "cpc-btc-updown-15m-2026-10-08-1230z"
UP = "OUTCOME_SIDE_YES"
DOWN = "OUTCOME_SIDE_NO"


def order(oid, *, state="ORDER_STATE_NEW", qty="0.13", cum="0",
          leaves=None, wire="0.55", side=DOWN, slug=SLUG):
    leaves = (str(D(qty) - D(cum)) if leaves is None else leaves)
    return {"id": oid, "marketSlug": slug, "outcomeSide": side,
            "state": state, "quantity": qty, "cumQuantity": cum,
            "leavesQuantity": leaves,
            "price": {"value": wire, "currency": "USD"}}


def reader_for(open_rows, hist_rows=None):
    hist_rows = open_rows if hist_rows is None else hist_rows

    def _read(path):
        if path == OS.OPEN_PATH:
            return {"orders": list(open_rows)}
        if path == OS.ORDERS_PATH:
            return {"orders": list(hist_rows)}
        raise AssertionError("unexpected path %r" % path)
    return _read


def view(open_rows, hist_rows=None, **kw):
    return OS.read_order_state(reader_for(open_rows, hist_rows),
                               slugs=[SLUG], outcome_up=UP, now=1000.0, **kw)


# --------------------------------------------------------------- the source
def test_the_two_literals_that_caused_the_runaway_are_gone():
    """THE TEST I SHOULD HAVE WRITTEN FIRST, LAST TIME.

    I verified that the spec declared the bound and that `eligible()` had an
    E3 clause, and never checked what fed it. A gate wired to a constant is
    the same defect class as the dead test in `conftest.py` that pytest
    never collected -- except this one moves money. So this asserts on the
    provider's SOURCE, where the literals lived.
    """
    assert '"our_outstanding_orders": 0,' not in PROVIDER_SRC
    assert '"our_open_residual": None,' not in PROVIDER_SRC
    assert "OS.outstanding_for_snapshot(ostate)" in PROVIDER_SRC
    assert "OS.residual_for_snapshot(ostate)" in PROVIDER_SRC


def test_the_runner_refuses_on_unestablished_order_state():
    assert "E3_ORDER_STATE_NOT_ESTABLISHED" in RUNNER_SRC
    assert "E3_AGGREGATE_VENUE_EXPOSURE" in RUNNER_SRC


def test_the_sentinel_is_truthy_so_it_refuses():
    """The reason a sentinel, not a number. `if x:` must refuse."""
    assert bool(OS.UNKNOWN_OUTSTANDING) is True
    assert OS.UNKNOWN_OUTSTANDING != 0


# ------------------------------------------------------- the runaway itself
def test_twenty_resting_orders_are_twenty_not_zero():
    rows = [order("CZH%02d" % i) for i in range(20)]
    v = view(rows)
    assert v["ok"], v
    assert v["n_outstanding"] == 20
    assert OS.outstanding_for_snapshot(v) == 20
    # and the one that matters: the E3 clause's input is TRUTHY.
    assert bool(OS.outstanding_for_snapshot(v))


def test_one_resting_order_already_blocks_the_second_first_leg():
    v = view([order("A1")])
    assert OS.outstanding_for_snapshot(v) == 1
    assert OS.reconcile_at_startup(v)["may_submit"] is False


def test_a_clean_slate_permits_exactly_one():
    v = view([])
    assert v["ok"]
    assert OS.outstanding_for_snapshot(v) == 0
    assert OS.residual_for_snapshot(v) is None
    assert OS.reconcile_at_startup(v)["may_submit"] is True


# ----------------------------------------------------------- partial fills
def test_a_partially_filled_order_is_outstanding():
    """It has leaves at the venue. It can still fill. It is not finished."""
    v = view([order("P1", state="ORDER_STATE_PARTIALLY_FILLED",
                    qty="0.13", cum="0.05", leaves="0.08")])
    assert v["n_outstanding"] == 1
    assert v["partially_filled_ids"] == ["P1"]
    # exposure counts the REMAINING quantity as open and the filled part as
    # inventory, on the OWN side (0.55 wire on NO -> 0.45 own).
    assert D(v["open_notional_usd"]) == D("0.08") * D("0.45")
    assert D(v["filled_notional_usd"]) == D("0.05") * D("0.45")
    assert D(v["aggregate_exposure_usd"]) == (D("0.08") + D("0.05")) * D("0.45")


def test_a_partial_fill_creates_a_residual_once_it_is_the_only_side():
    v = view([], [order("P1", state="ORDER_STATE_FILLED", qty="0.13",
                        cum="0.13", leaves="0")])
    assert v["n_outstanding"] == 0
    assert v["residual"]["outcome_side"] == DOWN
    assert D(v["residual"]["qty"]) == D("0.13")
    assert OS.residual_for_snapshot(v)["is_unmatched_directional_exposure"]


def test_a_matched_pair_leaves_no_residual():
    v = view([], [order("F1", state="ORDER_STATE_FILLED", cum="0.13",
                        leaves="0", side=DOWN),
                  order("F2", state="ORDER_STATE_FILLED", cum="0.13",
                        leaves="0", side=UP, wire="0.41")])
    assert v["residual"] is None
    assert D(v["matched_qty"]) == D("0.13")


# ------------------------------------------------ the long-side-only wire
def test_the_own_side_price_follows_the_venues_long_side_only_wire():
    """A6 proved it: own-side 0.6700 goes out as wire 0.3300."""
    assert OS.own_side_price("0.3300", DOWN, outcome_up=UP) == D("0.67")
    assert OS.own_side_price("0.6700", UP, outcome_up=UP) == D("0.67")


def test_summing_wire_values_would_have_understated_a_no_side_exposure():
    """The reason the conversion is not cosmetic."""
    v = view([order("N1", side=DOWN, wire="0.10", qty="0.13", cum="0")])
    #  wire 0.10 on NO means we are paying 0.90 a share, not 0.10.
    assert D(v["open_notional_usd"]) == D("0.13") * D("0.90")


# ------------------------------------------------------------- fail closed
def test_an_unreadable_venue_is_a_refusal_not_a_zero():
    def _boom(path):
        raise OSError("connection reset")
    v = OS.read_order_state(_boom, slugs=[SLUG], outcome_up=UP, now=1.0)
    assert v["ok"] is False
    assert v["reason"].startswith("ORDER_STATE_UNREADABLE")
    assert OS.outstanding_for_snapshot(v) == OS.UNKNOWN_OUTSTANDING
    assert bool(OS.outstanding_for_snapshot(v))
    assert OS.residual_for_snapshot(v)["FAILS_CLOSED"] is True
    assert OS.reconcile_at_startup(v)["may_submit"] is False


def test_an_unparsable_body_is_not_an_empty_book_of_orders():
    def _read(path):
        return {"somethingElse": "surprise"}
    v = OS.read_order_state(_read, slugs=[SLUG], outcome_up=UP, now=1.0)
    assert v["ok"] is False
    assert v["reason"] == "ORDER_STATE_UNPARSABLE"


def test_an_empty_orders_list_IS_recognised_as_zero():
    """The converse, so fail-closed does not become fail-always."""
    v = view([])
    assert v["ok"] is True and v["n_outstanding"] == 0


@pytest.mark.parametrize("state", ["ORDER_STATE_SOMETHING_NEW",
                                   "WEIRD", ""])
def test_an_unrecognised_state_is_outstanding(state):
    v = view([], [order("U1", state=state)])
    assert v["n_outstanding"] == 1, state
    c = [o for o in v["orders"] if o["id"] == "U1"][0]
    assert c["state_recognised"] is False


def test_a_missing_state_is_outstanding():
    row = order("M1")
    row.pop("state")
    v = view([], [row])
    assert v["n_outstanding"] == 1


def test_a_slow_read_is_stale_evidence():
    clock = {"t": 1000.0}

    def _read(path):
        clock["t"] += 4.0
        return {"orders": []}
    v = OS.read_order_state(_read, slugs=[SLUG], outcome_up=UP,
                            now=clock["t"], now_fn=lambda: clock["t"],
                            max_age_s=5.0)
    assert v["ok"] is False
    assert v["reason"].startswith("ORDER_STATE_STALE")
    assert bool(OS.outstanding_for_snapshot(v))


def test_an_outstanding_order_we_cannot_price_is_not_a_zero_exposure():
    row = order("X1")
    row["price"] = {"value": "not-a-price"}
    v = view([row])
    assert v["ok"] is False
    assert v["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"


# --------------------------------------------------- uncertain ack / races
def test_an_unconfirmed_submission_counts_as_outstanding():
    """`SubmissionAmbiguous` is never retried, and until the venue answers
    we must assume the order exists."""
    v = view([], pending_ids=["LOCAL_INTENT:w0:%s:%s:0.45" % (SLUG, DOWN)])
    assert v["n_outstanding"] == 1
    assert len(v["unconfirmed_pending_ids"]) == 1
    assert OS.reconcile_at_startup(v)["may_submit"] is False


def test_a_pending_id_the_venue_has_resolved_stops_counting():
    v = view([], [order("A1", state="ORDER_STATE_CANCELED", leaves="0")],
             pending_ids=["A1"])
    assert v["n_outstanding"] == 0
    assert v["unconfirmed_pending_ids"] == []


def test_a_pending_id_the_venue_shows_resting_counts_once_not_twice():
    v = view([order("A1")], pending_ids=["A1"])
    assert v["n_outstanding"] == 1
    assert v["outstanding_ids"] == ["A1"]


def test_the_cancellation_race_is_reported_not_resolved_conveniently():
    """One endpoint says canceled, the other says resting. We refuse."""
    v = view([order("R1")],
             [order("R1", state="ORDER_STATE_CANCELED", leaves="0")])
    assert v["ok"] is False
    assert v["reason"].startswith("ORDER_STATE_INCONSISTENT:R1_OPEN_AND")
    assert bool(OS.outstanding_for_snapshot(v))


def test_an_open_order_with_no_id_cannot_be_reconciled_so_it_refuses():
    row = order("Z1")
    row.pop("id")
    v = view([row], [])
    assert v["ok"] is False
    assert v["reason"] == "ORDER_STATE_INCONSISTENT:OPEN_ROW_WITHOUT_ID"


def test_the_open_endpoint_wins_on_liveness_over_a_stale_history_page():
    """History says NEW-and-paged-away; open says resting. Open wins, and
    that is not a conflict -- both mean live."""
    v = view([order("L1")], [])
    assert v["n_outstanding"] == 1
    assert v["open_ids"] == ["L1"]


# -------------------------------------------------------- restart recovery
def test_a_restart_into_pre_existing_resting_orders_refuses_to_submit():
    v = view([order("OLD1"), order("OLD2")])
    rec = OS.reconcile_at_startup(v)
    assert rec["may_submit"] is False
    assert rec["reconciled"] is True
    assert any("E3_OUR_ORDER_IS_OUTSTANDING:2" in b for b in rec["because"])
    assert rec["NOT_A_CANCEL"] is True


def test_a_restart_into_a_filled_first_leg_routes_to_complement_management():
    """Requirement 5: a filled first leg is INVENTORY. It must not license
    another uncontrolled first leg, and it must be visible as the thing
    complement management owns."""
    v = view([], [order("FILLED1", state="ORDER_STATE_FILLED", cum="0.13",
                        leaves="0")])
    rec = OS.reconcile_at_startup(v)
    assert rec["may_submit"] is False
    assert rec["filled_first_leg_goes_to_complement_management"] is True
    assert any("E3_OUR_RESIDUAL_IS_OPEN" in b for b in rec["because"])


def test_a_restart_with_unreadable_state_refuses_rather_than_assuming_clean():
    def _boom(path):
        raise RuntimeError("502")
    v = OS.read_order_state(_boom, slugs=[SLUG], outcome_up=UP, now=1.0)
    rec = OS.reconcile_at_startup(v)
    assert rec["may_submit"] is False
    assert rec["reconciled"] is False
    assert rec["FAILS_CLOSED"] is True


def test_reconcile_at_startup_on_a_missing_view_refuses():
    for bad in (None, {}, {"ok": False}):
        assert OS.reconcile_at_startup(bad)["may_submit"] is False


# ------------------------------------------------------------------- scope
def test_another_markets_order_is_not_e1s_outstanding_order():
    v = view([], [order("OTHER", slug="cpc-btc-updown-1h-2026-10-08-1300z")])
    assert v["n_outstanding"] == 0


def test_an_order_with_no_slug_is_counted_because_it_cannot_be_excluded():
    row = order("NOSLUG")
    row.pop("marketSlug")
    v = view([], [row])
    assert v["n_outstanding"] == 1


# ------------------------------------------------- aggregate, not per-order
def test_aggregate_exposure_sees_what_a_per_order_check_cannot():
    """Twenty 0.13-share orders pass every per-order size rule ever
    written. The sum is the only thing that can object."""
    rows = [order("CZH%02d" % i, wire="0.55") for i in range(20)]
    v = view(rows)
    assert D(v["aggregate_exposure_usd"]) == 20 * D("0.13") * D("0.45")
    assert v["AGGREGATE_NOT_PER_ORDER"] is True


def test_filled_inventory_is_included_in_aggregate_exposure():
    v = view([order("OPEN1")],
             [order("OPEN1"),
              order("DONE1", state="ORDER_STATE_FILLED", cum="0.13",
                    leaves="0")])
    agg = D(v["aggregate_exposure_usd"])
    assert agg == D(v["open_notional_usd"]) + D(v["filled_notional_usd"])
    assert D(v["filled_notional_usd"]) > 0


# ===================================================================
# THE RUNAWAY, THROUGH THE REAL LOOP
# ===================================================================
#
# Everything above tests `order_state` in isolation. This section drives the
# ACTUAL `run_cohort` loop against a fake venue that behaves the way the real
# one did on 2026-10-08: each accepted POST leaves a resting order. Under the
# old hard-coded `our_outstanding_orders: 0` the loop placed twenty. Under the
# repair it places one and then waits.

from analysis.l5.e1_cohort import latch as L            # noqa: E402
from analysis.l5.e1_cohort import runner as R           # noqa: E402
from analysis.l5.e1_cohort import stopping as SP        # noqa: E402
from analysis.l5.e1_cohort.test_e1_live_path import (   # noqa: E402,F401
    FakeTransport, _isolate, arm, fake_pin, _snapshot_from, BASE)


class _Clock:
    def __init__(self, t0):
        self.t = float(t0)

    def __call__(self):
        return self.t

    def sleep(self, s):
        assert s > 0, "a paced wait must never be zero"
        self.t += float(s)


def _snapshot_with_venue(*, clock, resting, bid="0.50"):
    """A snapshot built the way the provider builds one -- its E3 fields
    DERIVED from `resting`, which is the fake venue's order list."""
    s = _snapshot_from(fake_pin(BASE), window=0)
    s["first_leg"] = dict(s["first_leg"], best_bid=bid, best_ask="0.52")
    s["first_leg_price"] = bid
    s["ws_state"] = dict(s["ws_state"], book_unix=float(clock.t),
                         first_leg={"best_bid": bid, "best_ask": "0.52"})
    v = OS.read_order_state(reader_for(list(resting)),
                            slugs=[s["market"]],
                            outcome_up=(s.get("pin") or {}).get("outcome_up"),
                            now=clock.t, now_fn=clock)
    s["order_state"] = v
    s["our_outstanding_orders"] = OS.outstanding_for_snapshot(v)
    s["our_open_residual"] = OS.residual_for_snapshot(v)
    return s


def _drive(monkeypatch, tmp_path, *, venue_accepts_and_rests,
           max_iterations=40):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    clock = _Clock(BASE + 10)
    resting = []

    def provider():
        s = _snapshot_with_venue(clock=clock, resting=resting)
        # the venue's state is read FRESH on every observation, exactly as
        # the real provider re-reads it per snapshot. Each accepted POST
        # leaves ONE resting order, under THIS window's own slug -- the
        # scope rule only counts orders in the market we are trading.
        if venue_accepts_and_rests:
            while len(resting) < len(FakeTransport.posts):
                i = len(resting)
                resting.append(order("REST%02d" % i, slug=s["market"]))
            if len(resting) != len(FakeTransport.posts):
                return s
            s = _snapshot_with_venue(clock=clock, resting=resting)
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=max_iterations)
    return out, resting, clock


def test_the_loop_places_ONE_order_and_then_waits(monkeypatch, tmp_path):
    """THE REGRESSION. Forty iterations, a book that is always eligible, a
    venue that rests every accepted order. ONE placement.

    This is the exact shape of the 1230z window: nothing about the book or
    the bounds refused anything, and under the old constant the loop kept
    going. The only thing that stops at one is E3 reading real state.
    """
    out, resting, _ = _drive(monkeypatch, tmp_path,
                             venue_accepts_and_rests=True,
                             max_iterations=40)
    assert out["placement_count"] == 1, out["placement_count"]
    assert len(FakeTransport.posts) == 1, FakeTransport.posts
    # and every subsequent iteration refused for the RIGHT reason, patiently
    blocked = [r for r in out["refusals"]
               if any(str(g).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                      for g in r["refusals"])]
    assert blocked, out["refusals"]
    assert all(r["pacing"] == "PACE" for r in blocked)


def test_twenty_would_have_happened_without_the_repair(monkeypatch,
                                                       tmp_path):
    """THE COUNTERFACTUAL, so the test above is known to be load-bearing.

    Same loop, same bounds, same book -- but the snapshot publishes the OLD
    hard-coded `our_outstanding_orders: 0` while the venue fills up with
    resting orders. It runs away to the per-window attempt cap, which is
    exactly what happened at 1230z.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    clock = _Clock(BASE + 10)

    def provider():
        s = _snapshot_from(fake_pin(BASE), window=0)
        s["first_leg"] = dict(s["first_leg"], best_bid="0.50",
                              best_ask="0.52")
        s["first_leg_price"] = "0.50"
        s["ws_state"] = dict(s["ws_state"], book_unix=float(clock.t),
                             first_leg={"best_bid": "0.50",
                                        "best_ask": "0.52"})
        s["our_outstanding_orders"] = 0        # <- THE DEFECT, verbatim
        s["our_open_residual"] = None          # <- THE DEFECT, verbatim
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=40)
    assert out["placement_count"] > 1, (
        "the counterfactual did not run away, so the regression above "
        "proves nothing")
    assert len(FakeTransport.posts) > 1


def test_a_venue_that_rejects_nothing_still_cannot_exceed_one_outstanding():
    """Stated as arithmetic rather than as a loop: N resting orders means
    N outstanding, for every N, and E3 refuses on anything above zero."""
    for n in (1, 2, 5, 19, 20):
        v = view([order("R%02d" % i) for i in range(n)])
        assert OS.outstanding_for_snapshot(v) == n
        assert bool(OS.outstanding_for_snapshot(v))


def test_a_confirmed_id_absent_from_a_successful_read_is_resolved():
    """`/v1/orders/open` is the venue's statement of what rests. A
    confirmed id it does not list is not resting -- otherwise an order that
    filled and paged out of the history window would block E1 forever."""
    v = view([], [], pending_ids=["CZH22KCSPZ8H"])
    assert v["ok"] and v["n_outstanding"] == 0
    assert v["pending_ids_resolved_by_this_read"] == ["CZH22KCSPZ8H"]


def test_an_unidentified_submission_blocks_INDEFINITELY():
    """The other half of the same rule, and the one that matters for
    safety. A POST we could not identify is never resolved by absence."""
    for _ in range(3):
        v = view([], [], pending_ids=[OS.LOCAL_INTENT + "w0:slug:UP:0.45"])
        assert v["n_outstanding"] == 1
        assert v["pending_ids_resolved_by_this_read"] == []
        assert OS.reconcile_at_startup(v)["may_submit"] is False


def test_a_failed_read_resolves_NOTHING():
    """Absence is only evidence when the read succeeded."""
    def _boom(path):
        raise OSError("timeout")
    v = OS.read_order_state(_boom, slugs=[SLUG], outcome_up=UP, now=1.0,
                            pending_ids=["CZH22KCSPZ8H"])
    assert v["ok"] is False
    assert "pending_ids_resolved_by_this_read" not in v
    assert bool(OS.outstanding_for_snapshot(v))
