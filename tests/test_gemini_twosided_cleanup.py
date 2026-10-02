"""Regression test for the taker-cleanup double-count.

The bug: simulate() closed residual AT the opposite touch -- which already
pays the crossing -- and THEN subtracted another (ask-bid)/2 * qty from net.
On the 2026-08-30/09-01 historical sample that overstated the loss 3.3x
(-$0.3157 vs -$0.0964 per matched share).

This pins the invariant: net_before_compensation must equal the cash actually
transacted, and taker cleanup must be reported as a diagnostic only.
"""
from decimal import Decimal as D

from scripts import gemini_twosided_sim as sim


def _tape():
    """One market. Book 0.40/0.50. A seller crosses and fills our resting bid
    for 5, nothing else trades, and the window expires so the long is closed
    at the bid.

    cash = -0.40*5 (we bought)  +0.40*5 (we closed at the bid) = 0.00
    The pre-fix code would have returned 0.00 - (0.10/2)*5 = -0.25.
    """
    t0 = "2026-09-01T00:00:00+00:00"
    frames = [
        (t0, {"type": "l2_updates", "symbol": "M",
              "changes": [["buy", "0.40", "100"], ["sell", "0.50", "100"]]}),
        ("2026-09-01T00:00:05+00:00",
         {"type": "l2_updates", "symbol": "M", "changes": [],
          "trades": [{"price": "0.40", "quantity": "105", "side": "sell"}]}),
    ]
    meta = {"M": {"event_expiry": "2026-09-01T00:05:00+00:00"}}
    return frames, meta


def test_cleanup_is_not_charged_twice():
    frames, meta = _tape()
    r = sim.simulate(frames, meta, "M", 5, 90)
    assert r["fills"] == 1, r
    # queue ahead was 100, the trade was 105, so exactly 5 reach us
    assert D(r["buy_qty"]) == D(5)
    # bought 5 @ 0.40 and closed 5 @ 0.40 -> zero cash, not -0.25
    assert D(r["net_before_compensation"]) == D(0), r["net_before_compensation"]
    # the crossing cost is still VISIBLE, just never netted
    assert D(r["taker_cleanup_cost_DIAGNOSTIC_NOT_NETTED"]) > 0
    assert D(r["residual_after_cleanup"]) == 0


def test_queue_ahead_is_only_consumed_by_trades():
    """A trade smaller than the queue ahead must fill nothing."""
    frames, meta = _tape()
    frames[1][1]["trades"][0]["quantity"] = "50"      # < 100 resting ahead
    r = sim.simulate(frames, meta, "M", 5, 90)
    assert r["fills"] == 0, r
    assert D(r["buy_qty"]) == D(0)


def test_unknown_side_trade_fills_nothing():
    frames, meta = _tape()
    frames[1][1]["trades"][0]["side"] = ""
    r = sim.simulate(frames, meta, "M", 5, 90)
    assert r["fills"] == 0
    assert r["unknown_side_trades"] == 1
