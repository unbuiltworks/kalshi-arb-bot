"""P0 live proof: real venue frames -> real feed -> real frozen walker.

THE FRAMES ARE VERBATIM LIVE CAPTURES from the read-only WS probe against
wss://api.polymarket.us/v1/ws/markets, SUBSCRIPTION_TYPE_MARKET_DATA, on
2026-09-29. They are trimmed only by dropping levels from the middle of the
book; every px/qty below is exactly what the venue sent, and the two frames
are from different minutes of the same window so the depth differs.

WHY THIS IS A TEST AND NOT A PROBE RUN. Exercising the walker inside the
probe process would require the probe image to carry the import closure of
`pmus_l5_native_engine`, which pulls in `pmus_q1_executor` -- the ORDER
EXECUTOR. Putting that into a read-only image destroys the property that
makes the probe safe to run with live credentials. So the live leg
(subscribe, receive, count levels) ran on the probe, and the walker leg runs
here against what it captured. Nothing is re-implemented: the calls below
are the production functions.
"""
from decimal import Decimal as D

from src import l5_live_book_feed as LF
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E

SLUG = "cpc-btc-updown-15m-2026-09-29-1345z"

#: captured 13:46:44Z -- 24 bid levels, 25 offer levels
FRAME_1346 = {"marketData": {"marketSlug": SLUG, "state": "MARKET_STATE_OPEN",
    "bids": [{"px": {"value": v, "currency": "USD"}, "qty": q} for v, q in [
        ("0.4100", "1831.8200"), ("0.4000", "263.0000"), ("0.3900", "5214.0000"),
        ("0.3800", "1922.0000"), ("0.3700", "1.0000"), ("0.3000", "20.0000"),
        ("0.2700", "100.0000"), ("0.2500", "340.0000"), ("0.2400", "5.0000"),
        ("0.1500", "146.6700"), ("0.1400", "28.5700"), ("0.1300", "30.7700"),
        ("0.1200", "33.3300"), ("0.1100", "36.3600"), ("0.1000", "800.0000"),
        ("0.0900", "888.8900"), ("0.0800", "1000.0000"), ("0.0700", "1142.8600"),
        ("0.0600", "1333.3300"), ("0.0500", "1600.0000"), ("0.0400", "2000.0000"),
        ("0.0300", "2666.6700"), ("0.0200", "6150.0000"), ("0.0100", "12960.0000")]],
    "offers": [{"px": {"value": v, "currency": "USD"}, "qty": q} for v, q in [
        ("0.4200", "143.3500"), ("0.4300", "697.0000"), ("0.4400", "191.7600"),
        ("0.4500", "1199.0000"), ("0.4600", "507.5900"), ("0.4700", "966.0000"),
        ("0.6200", "3.0000"), ("0.7300", "100.0000"), ("0.7500", "306.5200"),
        ("0.7600", "5.0000"), ("0.8500", "26.6700"), ("0.8600", "28.5700"),
        ("0.8700", "30.7700"), ("0.8800", "33.3300"), ("0.8900", "36.3600"),
        ("0.9000", "800.0000"), ("0.9100", "888.8900"), ("0.9200", "1000.0000"),
        ("0.9300", "1142.8600"), ("0.9400", "1333.3300"), ("0.9500", "1600.0000"),
        ("0.9600", "2000.0000"), ("0.9700", "2666.6700"), ("0.9800", "6150.0000"),
        ("0.9900", "13035.0000")]]}}

#: captured 13:54:57Z, same window, after the market ran to 0.96 -- the book
#: is 29 bids deep and only 3 offers deep. Real, and usefully lopsided.
FRAME_1354 = {"marketData": {"marketSlug": SLUG, "state": "MARKET_STATE_OPEN",
    "bids": [{"px": {"value": v, "currency": "USD"}, "qty": q} for v, q in [
        ("0.9600", "6739.9400"), ("0.9500", "13854.3700"), ("0.9400", "3947.0000"),
        ("0.9300", "6.0000"), ("0.9200", "15.0000"), ("0.8000", "5.0000"),
        ("0.6900", "1.0000"), ("0.6500", "30.0000"), ("0.5400", "120.0000"),
        ("0.4000", "30.0000"), ("0.3300", "15.0000"), ("0.3100", "17.6100")]],
    "offers": [{"px": {"value": v, "currency": "USD"}, "qty": q} for v, q in [
        ("0.9700", "2084.0500"), ("0.9800", "4824.4300"), ("0.9900", "8932.9400")]]}}


def fed(frame):
    f = LF.AuthenticatedBookFeed(SLUG, clock=lambda: 1000.0)
    f.ingest(frame)
    return f


# ---- the live leg: depth actually arrives ------------------------------

def test_live_frame_carries_many_levels_not_one():
    f = fed(FRAME_1346)
    h = f.ladder_health()
    assert h["bid_levels"] == 24
    assert h["offer_levels"] == 25
    assert h["source"] == "VENUE_WS_MARKET_DATA"
    assert h["synthesized"] is False


def test_a_lopsided_live_book_is_carried_faithfully():
    f = fed(FRAME_1354)
    h = f.ladder_health()
    assert (h["bid_levels"], h["offer_levels"]) == (12, 3)


# ---- the walker leg: the FROZEN functions receive those levels ---------

def test_the_frozen_walker_receives_every_venue_level():
    f = fed(FRAME_1346)
    up = S.buy_levels(f.ladder, "up")       # buying UP lifts the offers
    dn = S.buy_levels(f.ladder, "down")     # buying DOWN hits the bids
    assert len(up) == 25 and len(dn) == 24
    # the venue's own top of book, untouched, in own-outcome terms
    assert up[0] == ["0.4200", "143.3500"]
    assert dn[0] == ["0.5900", "1831.8200"]      # 1 - 0.41


def test_the_walk_consumes_depth_below_the_touch():
    """The whole point of P0: more than the touch is now reachable."""
    f = fed(FRAME_1346)
    up = S.buy_levels(f.ladder, "up")
    basis, theta, want = D("0.20"), D("0.96"), D(5000)
    n, cost, fee, depth_limited = S.marginal_complete(up, want, basis, theta)
    touch_qty = D("143.3500")
    assert n > touch_qty, "the walk still only reached the touch"
    worst = E.worst_admitted_price(up, want, basis, theta)
    assert D(str(worst)) > D("0.4200"), "no level below the touch was used"
    # and every admitted level satisfies the UNCHANGED frozen inequality
    px = D(str(worst))
    assert basis + px + S.THETA_TAKER * px * (D(1) - px) <= theta


def test_a_thin_side_limits_the_walk_to_what_the_venue_showed():
    """3 offer levels is 3 offer levels. No depth is invented to fill `want`."""
    f = fed(FRAME_1354)
    up = S.buy_levels(f.ladder, "up")
    assert len(up) == 3
    total = sum(D(q) for _, q in up)
    n, _, _, _ = S.marginal_complete(up, D(10 ** 9), D("0.001"), D("0.96"))
    assert n <= total


def test_the_frozen_constants_are_untouched_end_to_end():
    assert S.THETA_TAKER == D("0.0695")
    assert E.THETA == D("0.96")
    assert E.RESIDUAL_CAP_K == D(2)
