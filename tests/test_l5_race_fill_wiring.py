"""The race-fill fix has to reach the live report, not just be importable.

Campaign 6's counters were wrong because the runner fed `window_record` the
lifecycle stream alone. These assert the runner now also hands it the
recorder's fill slots, and that the published report counts the raced fill.
"""
from decimal import Decimal as D

from src import l5_pmus_live_runner as RUN


class FakeRecorder:
    """Stands in for FillCalibration, which folds one slot per coid."""

    def __init__(self, orders):
        self.orders = orders


class FakeRunner:
    def __init__(self, orders):
        self.fill_calibration = FakeRecorder(orders)
        self.win_lifecycle = {}
        self.window_reports = {}
        self.stats = {}


MARKET = "cpc-btc-updown-15m-2026-10-28-1630z"


def _orders():
    """Four live orders; the last one is the leg that raced its cancel."""
    return {
        "l5f-w1": {"pre_submit": {"slug": MARKET, "side": "DOWN", "t": 1.0,
                                  "own_px": "0.4000", "qty": "1.93"},
                   "fills": [{"kind": "fill", "t": 2.0, "qty": "1.93",
                              "price": "0.6000", "cumulative_qty": "1.93"}]},
        "l5f-w2": {"pre_submit": {"slug": MARKET, "side": "UP", "t": 3.0,
                                  "own_px": "0.4500", "qty": "1.93"},
                   "fills": [{"kind": "fill", "t": 4.0, "qty": "1.93",
                              "price": "0.4500", "cumulative_qty": "1.93"}]},
        "l5f-w3": {"pre_submit": {"slug": MARKET, "side": "UP", "t": 5.0,
                                  "own_px": "0.3900", "qty": "2.15"},
                   "fills": [{"kind": "fill", "t": 6.0, "qty": "2.15",
                              "price": "0.3900", "cumulative_qty": "2.15"}]},
        "l5f-w4": {"pre_submit": {"slug": MARKET, "side": "DOWN", "t": 7.0,
                                  "own_px": "0.5100", "qty": "2.15"},
                   "fills": [{"kind": "fill", "t": 8.0, "qty": "2.15",
                              "price": "0.4900", "cumulative_qty": "2.15"}]},
        # A shadow id must never enter the live accounting.
        "shadow|ARGMAX|%s|1" % MARKET: {
            "pre_submit": {"slug": MARKET, "side": "UP", "t": 9.0,
                           "own_px": "0.2000", "qty": "1"},
            "fills": [{"kind": "fill", "t": 10.0, "qty": "1",
                       "price": "0.2000", "cumulative_qty": "1"}]},
    }


def test_window_fills_reads_the_recorder_slots_in_order():
    got = RUN._window_fills(FakeRunner(_orders()), MARKET)
    assert [r["coid"] for r in got] == ["l5f-w1", "l5f-w2", "l5f-w3",
                                        "l5f-w4"]
    assert got[-1]["price"] == "0.4900"


def test_window_fills_excludes_shadow_ids():
    got = RUN._window_fills(FakeRunner(_orders()), MARKET)
    assert all(str(r["coid"]).startswith("l5f-") for r in got)


def test_window_fills_excludes_other_markets():
    orders = _orders()
    orders["l5f-other"] = {
        "pre_submit": {"slug": "cpc-btc-updown-15m-2026-10-28-1645z",
                       "side": "UP", "t": 1.0, "own_px": "0.5", "qty": "1"},
        "fills": [{"kind": "fill", "qty": "1", "price": "0.5",
                   "cumulative_qty": "1"}]}
    got = RUN._window_fills(FakeRunner(orders), MARKET)
    assert "l5f-other" not in [r["coid"] for r in got]


def test_window_fills_never_raises_on_a_broken_recorder():
    class Broken:
        @property
        def orders(self):
            raise RuntimeError("recorder exploded")

    r = FakeRunner({})
    r.fill_calibration = Broken()
    assert RUN._window_fills(r, MARKET) == []
    r.fill_calibration = None
    assert RUN._window_fills(r, MARKET) == []


def test_window_fills_tolerates_junk_fill_rows():
    orders = {"l5f-a": {"pre_submit": {"slug": MARKET, "side": "UP",
                                       "own_px": "0.5", "qty": "1"},
                        "fills": ["junk", None, {"qty": "1",
                                                 "cumulative_qty": "1",
                                                 "price": "0.5"}]}}
    got = RUN._window_fills(FakeRunner(orders), MARKET)
    assert len(got) == 1


def test_the_published_window_report_counts_the_raced_fill():
    """End to end: emit_window_accounting over the real 1630z shape."""
    from src import l5_window_accounting as WA

    runner = FakeRunner(_orders())
    # The lifecycle saw only three of the four fills; w4 raced its cancel and
    # left behind a single zero-delta progress row.
    runner.win_lifecycle[MARKET] = [
        {"coid": "l5f-w1", "cum_qty": "1.93", "delta": "1.93",
         "state": "ORDER_STATE_FILLED", "terminal": True,
         "avg_px": "0.6000", "commission": "0.0000"},
        {"coid": "l5f-w2", "cum_qty": "1.93", "delta": "1.93",
         "state": "ORDER_STATE_FILLED", "terminal": True,
         "avg_px": "0.4500", "commission": "0.0000"},
        {"coid": "l5f-w3", "cum_qty": "2.15", "delta": "2.15",
         "state": "ORDER_STATE_FILLED", "terminal": True,
         "avg_px": "0.3900", "commission": "0.0000"},
        {"coid": "l5f-w4", "cum_qty": "0", "delta": "0",
         "state": "ORDER_STATE_NEW", "terminal": False,
         "avg_px": "0.0000", "commission": "0.0000"},
    ]

    rec = WA.window_record(
        market=MARKET,
        submits=RUN._window_submits(runner, MARKET),
        lifecycle=runner.win_lifecycle[MARKET],
        fills=RUN._window_fills(runner, MARKET))

    assert rec["orders"]["fill_events"] == 4
    assert rec["contracts"]["filled_total"] == D("8.16")
    pe = rec["pair_economics"]
    assert pe["pairs_completed"] == 2
    assert pe["pair_edge_usd"] == D("0.5045")
    assert pe["defensive_pairs"] == 0
    assert pe["residual_qty"] == {"UP": D(0), "DOWN": D(0)}
