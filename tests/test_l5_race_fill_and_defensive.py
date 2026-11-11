"""Campaign 6 taught two accounting lessons; these hold them.

1. A fill that races its own cancellation produces NO positive lifecycle
   delta, so the counters missed it: 11 fills / 21.67 contracts where the
   venue had settled 12 / 23.82. Money was right, counters were not.
2. A risk-reducing completion above $1.00 is a DEFENSIVE loss, not thin pair
   edge, and the venue's matched line folds the two together.

The numbers below are the real ones, from analysis/l5/campaign6/.
"""
from decimal import Decimal as D

import pytest

from src import l5_window_accounting as WA


def _sub(coid, side, own, qty):
    return {"coid": coid, "side": side, "own_px": own, "qty": qty,
            "mode": "MAKER"}


def _life(coid, cum, state, px="0", terminal=True):
    return {"coid": coid, "cum_qty": cum, "delta": cum, "state": state,
            "terminal": terminal, "avg_px": px, "commission": "0.0000"}


def _new(coid):
    """The only lifecycle row a raced fill leaves behind."""
    return {"coid": coid, "cum_qty": "0", "delta": "0",
            "state": "ORDER_STATE_NEW", "terminal": False,
            "avg_px": "0.0000", "commission": "0.0000"}


# ---------------------------------------------------------------- merge ----

def test_raced_fill_is_counted_from_the_fill_record():
    """The 1630z DOWN leg: cum 0 in lifecycle, 2.15 at the venue."""
    life = [_new("l5f-raced")]
    fills = [{"coid": "l5f-raced", "qty": "2.15", "price": "0.4900",
              "cumulative_qty": "2.15", "remaining": "0"}]
    merged = WA.merge_fill_sources(life, fills)
    ev = WA.fill_events(merged)
    assert len(ev) == 1
    assert ev[0]["delta"] == D("2.15")
    assert ev[0]["coid"] == "l5f-raced"


def test_merging_is_idempotent():
    """Running the merge twice must not invent a second fill."""
    life = [_new("l5f-raced")]
    fills = [{"coid": "l5f-raced", "cumulative_qty": "2.15",
              "price": "0.4900"}]
    once = WA.merge_fill_sources(life, fills)
    twice = WA.merge_fill_sources(once, fills)
    assert len(WA.fill_events(once)) == len(WA.fill_events(twice)) == 1


def test_a_fill_the_lifecycle_already_has_is_not_doubled():
    life = [_life("l5f-normal", "1.93", "ORDER_STATE_FILLED", "0.4500")]
    fills = [{"coid": "l5f-normal", "cumulative_qty": "1.93",
              "price": "0.4500"}]
    merged = WA.merge_fill_sources(life, fills)
    ev = WA.fill_events(merged)
    assert len(ev) == 1
    assert sum(e["delta"] for e in ev) == D("1.93")


def test_a_partial_lifecycle_fill_is_topped_up_not_restated():
    """Lifecycle saw 1.00 of 2.15; the fill record is authority for the rest."""
    life = [_life("l5f-part", "1.00", "ORDER_STATE_PARTIALLY_FILLED",
                  "0.4900", terminal=False)]
    fills = [{"coid": "l5f-part", "cumulative_qty": "2.15",
              "price": "0.4900"}]
    merged = WA.merge_fill_sources(life, fills)
    ev = WA.fill_events(merged)
    assert sum(e["delta"] for e in ev) == D("2.15")


def test_cancel_path_shape_is_read_too():
    """The deadman reports qty/avg_px with no cumulative field."""
    merged = WA.merge_fill_sources(
        [_new("l5f-raced")],
        [{"client_order_id": "l5f-raced", "qty": "2.15",
          "avg_px": "0.4900", "commission": "0.0000"}])
    assert sum(e["delta"] for e in WA.fill_events(merged)) == D("2.15")


def test_zero_and_junk_fill_rows_are_ignored():
    merged = WA.merge_fill_sources(
        [_new("l5f-a")],
        [{"coid": "l5f-a", "cumulative_qty": "0"}, {}, {"qty": "1"},
         "not a mapping"])
    assert WA.fill_events(merged) == []


def test_omitting_fills_reproduces_the_old_counts():
    """Backward compatibility: no fills argument, no behaviour change."""
    life = [_life("l5f-x", "2", "ORDER_STATE_FILLED", "0.4200")]
    assert WA.merge_fill_sources(life) == [dict(r) for r in life]


# ------------------------------------------------------------ defensive ----

def test_the_1600z_window_separates_edge_from_the_defensive_loss():
    """1600z really completed three pairs: 0.99, 0.87 and 1.05."""
    submits = [
        _sub("c1", "DOWN", "0.3600", "1.88"), _sub("c2", "UP", "0.6300",
                                                   "1.88"),
        _sub("c3", "DOWN", "0.4500", "2"), _sub("c4", "UP", "0.4200", "2"),
        _sub("c5", "DOWN", "0.4100", "1.94"), _sub("c6", "UP", "0.6400",
                                                   "1.94"),
    ]
    life = [_life("c1", "1.88", "ORDER_STATE_FILLED", "0.6400"),
            _life("c2", "1.88", "ORDER_STATE_FILLED", "0.6300"),
            _life("c3", "2", "ORDER_STATE_FILLED", "0.5500"),
            _life("c4", "2", "ORDER_STATE_FILLED", "0.4200"),
            _life("c5", "1.94", "ORDER_STATE_FILLED", "0.5900"),
            _life("c6", "1.94", "ORDER_STATE_FILLED", "0.6400")]
    pe = WA.pair_economics(WA.order_outcomes(submits, life))
    assert pe["pairs_completed"] == 3
    assert pe["defensive_pairs"] == 1
    assert pe["pair_edge_usd"] == D("0.2788")
    assert pe["defensive_loss_usd"] == D("-0.0970")
    # And the two still sum to what the venue reported for the window.
    assert pe["pair_edge_usd"] + pe["defensive_loss_usd"] == D("0.1818")
    assert pe["worst_pair_cost"] == D("1.05")
    assert pe["residual_qty"] == {"UP": D(0), "DOWN": D(0)}


def test_a_pair_at_exactly_one_dollar_is_not_defensive():
    """$1.00 is break-even, not a loss; the boundary must not drift."""
    submits = [_sub("a", "DOWN", "0.4000", "1"), _sub("b", "UP", "0.6000",
                                                      "1")]
    life = [_life("a", "1", "ORDER_STATE_FILLED", "0.6000"),
            _life("b", "1", "ORDER_STATE_FILLED", "0.6000")]
    pe = WA.pair_economics(WA.order_outcomes(submits, life))
    assert pe["defensive_pairs"] == 0
    assert pe["pair_edge_usd"] == D(0)


def test_an_unmatched_leg_is_residual_not_a_pair():
    submits = [_sub("a", "DOWN", "0.4600", "2.01")]
    life = [_life("a", "2.01", "ORDER_STATE_FILLED", "0.5400")]
    pe = WA.pair_economics(WA.order_outcomes(submits, life))
    assert pe["pairs_completed"] == 0
    assert pe["residual_qty"]["DOWN"] == D("2.01")


def test_a_leg_with_no_readable_own_price_is_counted_not_guessed():
    submits = [{"coid": "a", "side": "DOWN", "qty": "1"},
               _sub("b", "UP", "0.6000", "1")]
    life = [_life("a", "1", "ORDER_STATE_FILLED", "0.6000"),
            _life("b", "1", "ORDER_STATE_FILLED", "0.6000")]
    pe = WA.pair_economics(WA.order_outcomes(submits, life))
    assert pe["unpriceable_legs"] == 1
    assert pe["pairs_completed"] == 0


def test_defensive_loss_never_enters_realized_net():
    """realized_net_usd stays the venue's settlement line, untouched."""
    submits = [_sub("a", "DOWN", "0.4100", "1.94"),
               _sub("b", "UP", "0.6400", "1.94")]
    life = [_life("a", "1.94", "ORDER_STATE_FILLED", "0.5900"),
            _life("b", "1.94", "ORDER_STATE_FILLED", "0.6400")]
    rec = WA.window_record(market="m", submits=submits, lifecycle=life,
                           settlement={"settlement_pnl": "-0.0970",
                                       "matched_qty": "1.94",
                                       "matched_pnl": "-0.0970"})
    assert rec["pair_economics"]["defensive_loss_usd"] == D("-0.0970")
    assert rec["realized_net_usd"] == D("-0.0970")


# ------------------------------------------------- end-to-end, real data ----

def test_window_record_counts_the_raced_fill_in_contracts():
    """1630z: four fills, 8.16 contracts, two pairs, zero residual."""
    submits = [
        _sub("w1", "DOWN", "0.4000", "1.93"), _sub("w2", "UP", "0.4500",
                                                   "1.93"),
        _sub("w3", "UP", "0.3900", "2.15"), _sub("w4", "DOWN", "0.5100",
                                                 "2.15"),
    ]
    life = [_life("w1", "1.93", "ORDER_STATE_FILLED", "0.6000"),
            _life("w2", "1.93", "ORDER_STATE_FILLED", "0.4500"),
            _life("w3", "2.15", "ORDER_STATE_FILLED", "0.3900"),
            _new("w4")]                      # the raced leg
    fills = [{"coid": "w4", "qty": "2.15", "price": "0.4900",
              "cumulative_qty": "2.15"}]

    without = WA.window_record(market="m", submits=submits, lifecycle=life)
    assert without["orders"]["fill_events"] == 3
    assert without["contracts"]["filled_total"] == D("6.01")

    with_fills = WA.window_record(market="m", submits=submits,
                                  lifecycle=life, fills=fills)
    assert with_fills["orders"]["fill_events"] == 4
    assert with_fills["contracts"]["filled_total"] == D("8.16")
    pe = with_fills["pair_economics"]
    assert pe["pairs_completed"] == 2
    assert pe["pair_edge_usd"] == D("0.5045")
    assert pe["defensive_loss_usd"] == D(0)
    assert pe["residual_qty"] == {"UP": D(0), "DOWN": D(0)}


def test_campaign_rolling_carries_the_defensive_split():
    submits = [_sub("a", "DOWN", "0.4100", "1.94"),
               _sub("b", "UP", "0.6400", "1.94")]
    life = [_life("a", "1.94", "ORDER_STATE_FILLED", "0.5900"),
            _life("b", "1.94", "ORDER_STATE_FILLED", "0.6400")]
    rec = WA.window_record(market="m", submits=submits, lifecycle=life,
                           settlement={"settlement_pnl": "-0.0970",
                                       "matched_qty": "1.94",
                                       "matched_pnl": "-0.0970"})
    roll = WA.campaign_rolling([rec])
    assert roll["defensive_pairs"] == 1
    assert roll["defensive_loss_usd"] == D("-0.0970")
    assert roll["pair_edge_usd"] == D(0)
    assert roll["pairs_completed"] == 1
