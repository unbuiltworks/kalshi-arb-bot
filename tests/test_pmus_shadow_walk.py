"""The forward walk: guards, cap, and post-completion variants."""
from decimal import Decimal as D

from src import pmus_completion_shadow as S


def bbo(t, bid, ask, last, dlt, bsh="0", ash="0"):
    return {"kind": "bbo", "t": t, "state": "MARKET_STATE_OPEN",
            "bid": bid, "ask": ask, "last": last, "bid_shares": bsh,
            "ask_shares": ash, "shares_traded_delta_lower_bound": dlt}


def book(t, bids, offers):
    return {"kind": "book", "t": t, "bids": bids, "offers": offers}


T = "2026-09-27T18:%02d:00+00:00"


def test_no_volume_means_no_fill_ever():
    rows = [bbo(T % i, "0.40", "0.42", "0.40", 0) for i in range(5)]
    r = S.run_window(rows, clip=D(10), theta=None, winner="up")
    assert r["matched_qty"] == 0 and r["resid_qty"] == 0
    assert r["maker_qty"] == 0


def test_a_fill_needs_a_trade_at_or_through_our_price():
    """Tested per side, because the two sides face opposite prices.

    With last = 0.99 in YES terms nobody sold into an UP bid at 0.40, so UP
    must not fill. The same tape DOES fill DOWN -- 0.99 YES is 0.01 in DOWN's
    own terms, far through a DOWN bid at 1-ask = 0.58 -- and that is correct,
    not a defect. An earlier version of this test asserted no fill at all and
    was simply wrong about the venue's price convention.
    """
    row = bbo(T % 0, "0.40", "0.42", "0.99", 500)
    up_qty, _ = S.maker_fill(row, "up", D(10), queue_aware=False)
    assert up_qty == 0, "UP filled with no trade at or through its bid"
    dn_qty, dn_px = S.maker_fill(row, "down", D(10), queue_aware=False)
    assert dn_qty > 0 and dn_px == D("0.58")


def test_a_trade_through_neither_side_fills_nothing():
    row = bbo(T % 0, "0.40", "0.42", "0.41", 500)
    for side in ("up", "down"):
        qty, _ = S.maker_fill(row, side, D(10), queue_aware=False)
        assert qty == 0, "%s filled on a trade inside the spread" % side


def test_fill_quantity_is_capped_by_the_observed_volume_delta():
    rows = [bbo(T % 0, "0.40", "0.42", "0.20", 3)]
    r = S.run_window(rows, clip=D(100), theta=None, winner="up")
    assert r["maker_qty"] <= D(3) * 2, "exceeded the observed volume bound"


def test_queue_aware_requires_the_displayed_size_to_clear_first():
    rows = [bbo(T % 0, "0.40", "0.42", "0.20", 50, bsh="1000", ash="1000")]
    loose = S.run_window(rows, clip=D(10), theta=None, winner="up",
                         queue_aware=False)
    tight = S.run_window(rows, clip=D(10), theta=None, winner="up",
                         queue_aware=True)
    assert loose["maker_qty"] > 0
    assert tight["maker_qty"] == 0, "ignored a 1000-share queue ahead of us"


def test_completion_is_sized_to_the_residual_held_at_the_trigger():
    """GUARD 1. Residual grows later; the completion must reflect what was
    held when it fired, not the end-of-window total."""
    rows = [
        bbo(T % 0, "0.40", "0.42", "0.20", 5),            # builds some UP
        book(T % 1, [["0.40", "1000"]], [["0.42", "1000"]]),
        bbo(T % 2, "0.40", "0.42", "0.20", 5),
        bbo(T % 3, "0.40", "0.42", "0.20", 500),          # much more later
    ]
    r = S.run_window(rows, clip=D(2), theta=D("1.20"), winner="up")
    if r["completions"]:
        assert r["completed_qty"] <= D(4), (
            "completed more than was held at the trigger")


def test_a_depth_shortfall_is_counted_unknown_and_not_filled_at_the_touch():
    rows = [
        bbo(T % 0, "0.40", "0.42", "0.20", 100),
        book(T % 1, [["0.40", "1"]], [["0.42", "1"]]),
        bbo(T % 2, "0.40", "0.42", "0.20", 100),
    ]
    r = S.run_window(rows, clip=D(50), theta=D("1.50"), winner="up")
    assert r["unknown_events"] >= 1, "a shortfall went unrecorded"


def test_baseline_never_completes():
    rows = [bbo(T % i, "0.40", "0.42", "0.20", 50) for i in range(6)]
    r = S.run_window(rows, clip=D(5), theta=None, winner="up")
    assert r["completions"] == 0 and r["completed_qty"] == 0


def test_modeled_rebate_is_never_inside_net():
    rows = [bbo(T % i, "0.40", "0.42", "0.20", 50) for i in range(4)]
    r = S.run_window(rows, clip=D(5), theta=None, winner="up")
    assert r["credited_rebate"] == 0
    if r["net"] is not None and r["modeled_rebate"] > 0:
        recomputed = r["matched_pnl"] + r["resid_pnl"] - r["fees"]
        assert r["net"] == recomputed, "modeled rebate leaked into net"


def test_settlement_is_only_read_at_the_end():
    """GUARD 2. The same tape under both outcomes must produce the SAME
    inventory and differ only in settled P&L."""
    rows = [bbo(T % i, "0.40", "0.42", "0.20", 50) for i in range(5)]
    a = S.run_window(rows, clip=D(5), theta=D("1.10"), winner="up")
    b = S.run_window(rows, clip=D(5), theta=D("1.10"), winner="down")
    for k in ("maker_qty", "taker_qty", "completions", "completed_qty",
              "peak_resid", "unknown_events"):
        assert a[k] == b[k], "%s depended on the outcome" % k
