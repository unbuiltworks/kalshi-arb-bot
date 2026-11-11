"""THE ORDER-BY-ORDER RECONSTRUCTION, PINNED AGAINST THE VENUE (#208).

The evidence base for fill behaviour was a hand-transcribed tuple list in
`analysis/l5/issue206/own_order_lifecycles.py`. This pins the replacement,
which derives every field from the tapes -- and the pin that matters is the
one the venue can settle: our reconstructed per-side quantities and costs
must equal `L5_LIVE_SETTLED` exactly, in every window, on both sides.

That single assertion is what makes the rest of the reconstruction
trustworthy. It caught a real defect while this was being written: DOWN cost
was computed from the wire price instead of `1 - wire`, so `up_cost`
reconciled and `dn_cost` came out as the complement.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "analysis", "l5", "issue208"))
import order_lifecycle_reconstruction as R        # noqa: E402

TAPES = [os.path.join(os.path.dirname(__file__), "..", "analysis", "l5",
                      "residual_exit", "preserved", "*.ndjson")]


@pytest.fixture(scope="module")
def built():
    return R.reconstruct(TAPES)


def test_the_tapes_are_present_and_hold_our_own_orders(built):
    assert built["events_read"] > 900
    assert len(built["orders"]) == 15
    assert all(c.startswith("l5f-") for c in built["orders"])


def test_shadow_quotes_are_excluded_from_the_order_set(built):
    """`shadow|ARGMAX|` coids are modelled quotes, not orders."""
    assert built["excluded"]["shadow_coids"] >= 20
    assert not any("shadow" in c or "ARGMAX" in c for c in built["orders"])


def test_every_window_reconciles_penny_exact_with_the_venue(built):
    """THE LOAD-BEARING ASSERTION. Derived numbers against the venue's own
    settlement, both sides, every window."""
    rc = R.reconcile(built["orders"], built["settlements"])
    assert len(rc) == 7
    for r in rc:
        assert r["resolution_source"] == "VENUE_SETTLEMENT_PX", r
        assert r["reconciles"], r


def test_a_down_leg_costs_one_minus_the_wire_price(built):
    """The convention that broke the reconciliation when it was backwards."""
    rc = {r["window"]: r for r in R.reconcile(built["orders"],
                                              built["settlements"])}
    r = rc["2026-10-24 0100z"]
    assert D(r["our_dn_cost"]) == D(r["settled_dn_cost"]) == D("0.384800")
    # 1.04 at own 0.37; the wire price was 0.63, and 1.04 x 0.63 = 0.6552
    assert D(r["our_dn_cost"]) != D("0.655200")


def test_no_fill_was_ever_partial(built):
    """Nothing calibrates a partial-fill replacement path, because in every
    venue-confirmed fill we have, the whole quantity filled in one step."""
    fills = [o for o in built["orders"].values()
             if o.terminal_state == R.FILLED]
    assert len(fills) == 11
    assert all(not o.partial for o in fills)
    assert {o.fill_steps for o in fills} == {1}


def test_every_non_fill_was_our_own_cancel(built):
    cancels = [o for o in built["orders"].values()
               if o.terminal_state == R.CANCELED]
    assert len(cancels) == 4
    assert all(o.venue_order_id for o in cancels)      # all reached the venue


def test_the_log_lag_is_real_measured_and_kept_out_of_the_rest_time(built):
    """The two clocks, and why a duration that mixes them is inflated.

    The quote carries its own process clock; the venue's lifecycle events
    carry none, so their only timestamp is the collector's. The collector lag
    on the quote line alone runs from 0.03 s to over 9 s here, so `rest_s`
    is deliberately acknowledgement -> terminal, both collector, and never
    quote-body -> terminal.
    """
    lags = [o.log_lag_s for o in built["orders"].values()
            if o.log_lag_s is not None]
    assert len(lags) == 15
    assert min(lags) < 0.2 and max(lags) > 9.0          # it is not a constant
    for o in built["orders"].values():
        if o.rest_s is None or o.dur_from_quote_mixed_s is None:
            continue
        # THE IDENTITY. The mixed figure exceeds the same-clock rest by the
        # collector lag on the quote line PLUS the quote-line-to-ACK-line
        # gap -- both of which are measurement, not the order's life:
        #   mixed = terminal_line - quote_body
        #   rest  = terminal_line - ack_line
        #   so  mixed - rest = (ack_line - quote_line) + (quote_line - body)
        assert o.dur_from_quote_mixed_s >= o.rest_s - 0.002
        assert abs((o.dur_from_quote_mixed_s - o.rest_s)
                   - (o.quote_to_ack_s + o.log_lag_s)) < 0.01, o.coid


def test_four_of_seven_windows_paired_and_each_was_inside_the_ceiling(built):
    """Realized pair cost comes from settlement, in own-price terms."""
    prs = {p["window"]: p for p in R.pairs(built["orders"],
                                           built["settlements"])}
    paired = {w: p for w, p in prs.items() if p["realized_pair_cost"]}
    assert len(paired) == 4
    for w, p in paired.items():
        assert D(p["realized_pair_cost"]) <= D("0.96"), (w, p)
        assert p["completion_state"] == R.FILLED
        assert D(p["residual_qty"]) == 0
    assert sorted(D(p["realized_pair_cost"]) for p in paired.values()) == [
        D("0.8500"), D("0.9300"), D("0.9400"), D("0.9600")]


def test_every_unpaired_window_lost_its_completion_to_our_own_cancel(built):
    prs = R.pairs(built["orders"], built["settlements"])
    unpaired = [p for p in prs if not p["realized_pair_cost"]]
    assert len(unpaired) == 3
    for p in unpaired:
        assert p["completion_state"] == R.CANCELED, p
        assert D(p["residual_qty"]) > 0


def test_the_hold_before_the_completion_quote_dominates_the_rest_time(built):
    """THE FINDING. A completion that is allowed to rest fills in seconds;
    what costs the pair is the time before it is even quoted."""
    prs = R.pairs(built["orders"], built["settlements"])
    holds = [p["hold_before_completion_quote_s"] for p in prs
             if p["hold_before_completion_quote_s"] is not None]
    rests = [p["completion_rest_s"] for p in prs
             if p["completion_rest_s"] is not None]
    assert len(holds) == 7
    assert min(holds) >= 10.0 and max(holds) > 200.0
    assert max(rests) < 20.0
    assert sorted(holds)[len(holds) // 2] > max(rests)
