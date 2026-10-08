"""THE 40-ORDER STARTUP REFUSAL, AND WHY IT CANNOT HAPPEN AGAIN.

On 2026-10-08 at 20:30:43Z the armed probe booted, reached `run_cohort`,
and refused itself:

    startup_reconciliation.because =
      ORDER_STATE_NOT_ESTABLISHED_AT_STARTUP:ORDER_STATE_INCONSISTENT:
      UNPRICEABLE_EXPOSURE
    stopped_because = STARTUP_RECONCILIATION_REFUSED
    windows_opened 0   transport_constructed false   placement_count 0

THE CAUSE. `run_cohort` reconciles BEFORE it opens a window, so
`current_pin` is None and the view is built with `outcome_up=None`.
`own_side_price` cannot turn the venue's long-side-only wire into our
own-side cost without knowing which side is long, so it returns None for
every order. The tape's forty ids include the twenty FILLED 1215z orders,
and each of those hit `cum > 0 and own is None`, which set `priced = False`.
The engine could not price its own settled history, so it would not trade --
deterministically, on every armed boot, for as long as the tape carried a
fill.

WHY NO READ-ONLY CHECK CAUGHT IT. Look at the validator's own output:
`READER {"n_known": 0, ...}`. The read-only verifications passed NO known
ids, so there was nothing to price. The armed path is the only one that
feeds the registry's ids into a reconciliation with no pin.

THE REPAIR IS NOT A RELAXED PRICING RULE. It is asking the right question:
a fill is CURRENT exposure only while it is still inventory. Resolution is
established from durable, authoritative data and needs no window pin --
the order row's own `marketSlug` says which window it belongs to (and
therefore when that window ended), and `/v1/portfolio/positions` is the
venue's own statement of what is still held. All three conditions are
required and each is conservative: terminal, its own window ended, and no
venue position in that market. Anything else is live inventory that still
has to be priceable, and if it cannot be priced the refusal stands.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort.test_e1_outstanding_orders import (UP, DOWN,
                                                              order,
                                                              reader_for)

#: the two settled windows the live cohort actually traded.
S1215 = "cpc-btc-updown-15m-2026-10-08-1215z"
S1230 = "cpc-btc-updown-15m-2026-10-08-1230z"
#: a window that has NOT ended at `NOW` below.
S_LIVE = "cpc-btc-updown-15m-2026-10-08-2030z"

#: 2026-10-08T20:30:45Z -- the instant of the real refusal. 1215z and 1230z
#: have long ended; 2030z has not.
NOW = 1791491445.0

#: the real cohort's forty ids, as the tape holds them.
FILLED_IDS = ["CZGW4QWKMZ73", "CZGW0TNF8Z7T", "CZGW3Q9SCZ8K", "CZGW0DRSAZ7J",
              "CZGW5PZY0Z8E", "CZGW3QA24Z8K", "CZGW596D2Z7Z", "CZGW4QWS6Z73",
              "CZGW4QWW2Z73", "CZGW0DS1WZ7J", "CZGW4QWY6Z73", "CZGW6T12WZ84",
              "CZGVXST1EZ89", "CZGW596NAZ7Z", "CZGVWJ5HTZ7D", "CZGW5Q0DJZ8E",
              "CZGW59710Z7Z", "CZGW4QX9YZ73", "CZGVXST7JZ89", "CZGW6T1G8Z84"]
CANCELLED_IDS = ["CZH22KCSPZ8H", "CZH2FP4R8Z8K", "CZH2F2FBEZ7T",
                 "CZH2HF6RAZ8E", "CZH26B7HCZ73", "CZH28KHW0Z7Z",
                 "CZH2E3NAMZ7J", "CZH22KD92Z8H", "CZH25AYD0Z84",
                 "CZH28KJ1PZ7Z", "CZH28KJ6AZ7Z", "CZH22KDH8Z8H",
                 "CZH22KDJEZ8H", "CZH2A4FWPZ7D", "CZH28KJECZ7Z",
                 "CZH2E3NVWZ7J", "CZH2FP5MCZ8K", "CZH2A4G66Z7D",
                 "CZH2F2G70Z7T", "CZH2FP5QMZ8K"]
ALL_IDS = FILLED_IDS + CANCELLED_IDS


def history_rows():
    """The real shape: twenty FILLED 0.13 YES @0.49 in 1215z, twenty
    CANCELED 0.13 NO in 1230z."""
    rows = [order(i, state="ORDER_STATE_FILLED", qty="0.13", cum="0.13",
                  leaves="0", wire="0.49", side=UP, slug=S1215)
            for i in FILLED_IDS]
    rows += [order(i, state="ORDER_STATE_CANCELED", qty="0.13", cum="0",
                   leaves="0", wire="0.55", side=DOWN, slug=S1230)
             for i in CANCELLED_IDS]
    return rows


def startup_view(rows, *, outcome_up=None, positions=None, now=NOW,
                 known=None, open_rows=()):
    """`read_order_state` EXACTLY as the startup reconciliation calls it:
    every known id, and NO window pin -- so `outcome_up` is None."""
    return OS.read_order_state(
        reader_for(list(open_rows), hist_rows=rows, positions=positions),
        slugs=[], outcome_up=outcome_up, now=now,
        known_ids=list(known if known is not None
                       else [r["id"] for r in rows]))


# ===================== THE SLUG CARRIES ITS OWN WINDOW ==================

def test_the_slug_alone_says_when_its_window_ended():
    import calendar
    # 1215z is the fifteen-minute window starting 12:15Z, so it ENDED 12:30Z
    assert OS.window_end_unix(S1215) == float(
        calendar.timegm((2026, 10, 8, 12, 30, 0, 0, 0, 0)))
    assert OS.window_end_unix(S1230) == float(
        calendar.timegm((2026, 10, 8, 12, 45, 0, 0, 0, 0)))
    assert OS.window_end_unix(S1230) - OS.window_end_unix(S1215) == 900.0
    # and the window we would be trading has NOT ended at the refusal instant
    assert OS.window_end_unix(S_LIVE) > NOW


@pytest.mark.parametrize("bad", [None, "", "not-a-slug", "cpc-btc-updown",
                                 "cpc-btc-updown-15m-2026-13-45-9999z",
                                 "cpc-btc-updown-15m-2026-10-08-1215",
                                 12345])
def test_an_unparsable_slug_has_no_window_end(bad):
    assert OS.window_end_unix(bad) is None


# ============ 6. THE EXACT 40-ORDER STARTUP FAILURE, REPRODUCED =========

def test_the_forty_order_startup_refusal_is_gone(monkeypatch):
    """The regression. Forty historical ids, empty pin, zero venue
    positions -- the exact 20:30:43Z conditions."""
    view = startup_view(history_rows())
    assert view["ok"] is True, view.get("reason")
    assert view["reason"] is None
    # all forty are PRESERVED, not discarded
    assert view["n_known"] == 40
    assert len(view["orders"]) == 40
    assert {str(o["id"]) for o in view["orders"]} == set(ALL_IDS)
    # the twenty fills are reported AS HISTORY, with their quantity intact
    assert view["n_resolved_history"] == 20
    assert sorted(view["resolved_history_ids"]) == sorted(FILLED_IDS)
    assert D(view["historical_resolved_filled_qty"]) == D("2.60")
    assert view["resolution_established_without_a_window_pin"] is True
    # and they are NOT current exposure
    assert view["n_outstanding"] == 0
    assert view["residual"] is None
    assert D(view["open_notional_usd"]) == 0
    assert D(view["filled_notional_usd"]) == 0
    assert D(view["aggregate_exposure_usd"]) == 0


def test_startup_reconciliation_now_PERMITS_submission(monkeypatch):
    rec = OS.reconcile_at_startup(startup_view(history_rows()))
    assert rec["may_submit"] is True, rec
    assert rec["because"] is None
    assert rec["reconciled"] is True
    assert rec["n_outstanding"] == 0
    assert rec["residual"] is None
    assert rec["NOT_A_CANCEL"] is True


def test_the_old_failure_mode_is_named_and_cannot_recur_silently():
    """The forty fills used to make exposure unpriceable. Prove the view
    now reports WHY they are excluded rather than silently dropping
    them."""
    view = startup_view(history_rows())
    assert view["resolution_authority"] == (
        "GET /v1/portfolio/positions + the order's own marketSlug")
    assert view[
        "a_fill_in_a_held_market_is_live_inventory_and_must_be_priceable"
    ] is True
    assert view[
        "an_unpriceable_live_exposure_is_still_ORDER_STATE_INCONSISTENT"
    ] is True


# ====== 7. NEGATIVE: GENUINELY UNRESOLVED EXPOSURE STILL BLOCKS =========

def test_an_OPEN_order_with_no_pin_still_refuses():
    """An outstanding order we cannot price is not a $0 exposure. This is
    the rule that must NOT have been weakened."""
    live = order("CZLIVE0001", state="ORDER_STATE_NEW", qty="0.13", cum="0",
                 leaves="0.13", wire="0.55", side=DOWN, slug=S_LIVE)
    view = startup_view(history_rows() + [live], open_rows=[live])
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"
    rec = OS.reconcile_at_startup(view)
    assert rec["may_submit"] is False
    assert rec["FAILS_CLOSED"] is True
    assert "UNPRICEABLE_EXPOSURE" in rec["because"]


def test_a_FILL_IN_A_HELD_MARKET_still_refuses_without_a_pin():
    """The venue still reports a position, so that fill is live inventory.
    Unpriceable without a pin -- and it must keep refusing."""
    held = {"positions": {S1215: {"netPositionDecimal": "2.60"}},
            "eof": True}
    view = startup_view(history_rows(), positions=held)
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_a_FILL_IN_A_WINDOW_THAT_HAS_NOT_ENDED_still_refuses():
    """A filled first leg in the window we are trading right now is
    inventory that wants hedging, never history."""
    fresh = order("CZFRESH001", state="ORDER_STATE_FILLED", qty="0.13",
                  cum="0.13", leaves="0", wire="0.49", side=UP, slug=S_LIVE)
    view = startup_view(history_rows() + [fresh])
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_a_FILL_WITH_AN_UNPARSABLE_SLUG_still_refuses():
    """An identity we cannot establish is never read as 'settled'."""
    odd = order("CZODD00001", state="ORDER_STATE_FILLED", qty="0.13",
                cum="0.13", leaves="0", wire="0.49", side=UP,
                slug="some-market-we-cannot-parse")
    view = startup_view(history_rows() + [odd])
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"


def test_a_PARTIAL_FILL_is_outstanding_and_refuses_without_a_pin():
    part = order("CZPART0001", state="ORDER_STATE_PARTIALLY_FILLED",
                 qty="0.13", cum="0.05", leaves="0.08", wire="0.49",
                 side=UP, slug=S_LIVE)
    view = startup_view(history_rows() + [part], open_rows=[part])
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"


def test_live_inventory_WITH_a_pin_prices_but_the_RESIDUAL_blocks_startup():
    """With a pin the fill prices fine -- and then it is an open residual,
    which is its own, correct refusal. Reconciliation is not weakened; the
    refusal just becomes the honest one."""
    fresh = order("CZFRESH002", state="ORDER_STATE_FILLED", qty="0.13",
                  cum="0.13", leaves="0", wire="0.49", side=UP, slug=S_LIVE)
    view = startup_view(history_rows() + [fresh], outcome_up=UP)
    assert view["ok"] is True, view.get("reason")
    assert view["residual"] is not None
    assert D(view["residual"]["qty"]) == D("0.13")
    assert D(view["filled_notional_usd"]) == D("0.13") * D("0.49")
    rec = OS.reconcile_at_startup(view)
    assert rec["may_submit"] is False
    assert any("E3_OUR_RESIDUAL_IS_OPEN" in r for r in rec["because"])


def test_an_unreadable_positions_body_still_refuses_everything():
    """Positions are now read BEFORE pricing, so prove the refusal did not
    move: no positions, no resolution, no trading."""
    def broken(path):
        if path == OS.POSITIONS_PATH:
            raise OSError("positions down")
        return reader_for([], hist_rows=history_rows())(path)

    view = OS.read_order_state(broken, slugs=[], outcome_up=None, now=NOW,
                               known_ids=ALL_IDS)
    assert view["ok"] is False
    assert "ORDER_STATE_UNREADABLE" in view["reason"]
    assert OS.reconcile_at_startup(view)["may_submit"] is False


def test_an_unparsable_positions_body_still_refuses_everything():
    view = startup_view(history_rows(), positions=["not", "a", "mapping"])
    assert view["ok"] is False
    assert view["reason"] == "ORDER_STATE_UNPARSABLE:%s" % OS.POSITIONS_PATH


# ============== OWNERSHIP AND THE SENTINEL ARE UNTOUCHED ================

def test_a_local_intent_still_blocks_after_the_fix():
    """An ambiguous acknowledgement is still charged at the ceiling and
    still counts as outstanding."""
    view = startup_view(history_rows(),
                        known=ALL_IDS)
    assert view["ok"] is True
    v2 = OS.read_order_state(
        reader_for([], hist_rows=history_rows()), slugs=[], outcome_up=None,
        now=NOW, known_ids=ALL_IDS,
        pending_ids=[OS.LOCAL_INTENT + "w3:slug:UP:0.49"])
    assert v2["ok"] is True
    assert v2["n_outstanding"] == 1
    assert D(v2["open_notional_usd"]) > 0
    rec = OS.reconcile_at_startup(v2)
    assert rec["may_submit"] is False
    assert any("E3_OUR_ORDER_IS_OUTSTANDING" in r for r in rec["because"])


def test_the_sentinel_is_still_truthy_on_a_failed_read():
    view = startup_view(history_rows() + [
        order("CZLIVE0002", state="ORDER_STATE_NEW", leaves="0.13",
              slug=S_LIVE)],
        open_rows=[order("CZLIVE0002", state="ORDER_STATE_NEW",
                         leaves="0.13", slug=S_LIVE)])
    assert view["ok"] is False
    assert OS.outstanding_for_snapshot(view) == OS.UNKNOWN_OUTSTANDING
    assert bool(OS.outstanding_for_snapshot(view)) is True
    assert OS.residual_for_snapshot(view)["FAILS_CLOSED"] is True


def test_the_venue_exceeding_our_derived_inventory_still_refuses():
    """The 1215z lesson in the other direction, unchanged: never let
    inventory exist that our accounting does not know about."""
    ghost = {"positions": {S_LIVE: {"netPositionDecimal": "9.00"}},
             "eof": True}
    view = startup_view(history_rows(), positions=ghost, outcome_up=UP)
    assert view["ok"] is False
    assert "VENUE_POSITION" in view["reason"]
    assert "EXCEEDS_OUR_DERIVED" in view["reason"]


if __name__ == "__main__":
    import os
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
