from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.pk_crypto_ev_census import (
    BBO,
    SpotTape,
    _size_score,
    kalshi_proxy_fair,
    polymarket_proxy_fair,
    polymarket_taker_fee,
)


def _flat_tape(start: datetime, *, price: float = 100.0) -> SpotTape:
    tape = SpotTape()
    start_ms = int(start.timestamp() * 1000)
    # 15m of five-second observations: full TWAP-window coverage plus well over
    # the minimum 20 observations inside the five-minute volatility lookback.
    for i in range(181):
        tape.add(start_ms + i * 5_000, price)
    return tape


def _volatile_tape(start: datetime, *, seconds: int) -> SpotTape:
    tape = SpotTape()
    start_ms = int(start.timestamp() * 1000)
    steps = seconds // 5
    for i in range(steps + 1):
        # Small alternating moves produce non-zero realized short-horizon vol
        # without making the synthetic contract effectively certain.
        price = 100.0 + (0.20 if i % 2 else 0.0)
        tape.add(start_ms + i * 5_000, price)
    # Finish slightly above the threshold so an incorrect spot-indicator model
    # would say 1.0 even though meaningful time/volatility remains.
    tape.samples[-1] = type(tape.samples[-1])(tape.samples[-1].ts_ms, 100.05)
    return tape


def test_polymarket_taker_fee_uses_bankers_rounding_total_order():
    # 0.05 * 100 * .50 * .50 = $1.25 exactly.
    assert polymarket_taker_fee(Decimal("0.50"), Decimal("100")) == Decimal("1.25")


def test_spot_tape_integral_is_price_time():
    tape = SpotTape()
    tape.add(0, 100.0)
    tape.add(1000, 100.0)
    tape.add(2000, 100.0)
    assert tape.integral(0, 2000) == 200.0


def test_polymarket_proxy_fair_requires_full_window_spot_coverage():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(minutes=10)
    close = start + timedelta(minutes=15)
    tape = SpotTape()
    # Plenty of samples, but the collector did not observe from the window start.
    for i in range(30):
        tape.add(int((now - timedelta(seconds=29 - i)).timestamp() * 1000), 100.0 + i * 0.001)
    assert polymarket_proxy_fair(tape, start, close, int(now.timestamp() * 1000)) is None


def test_completed_flat_polymarket_proxy_resolves_yes_on_equality():
    start = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    close = start + timedelta(minutes=15)
    tape = _flat_tape(start)
    fair = polymarket_proxy_fair(tape, start, close, int(close.timestamp() * 1000))
    assert fair == 1.0


def test_midwindow_polymarket_proxy_is_probability_not_spot_indicator():
    start = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    now = start + timedelta(minutes=7, seconds=30)
    close = start + timedelta(minutes=15)
    tape = _volatile_tape(start, seconds=450)
    fair = polymarket_proxy_fair(tape, start, close, int(now.timestamp() * 1000))
    assert fair is not None
    assert 0.05 < fair < 0.95


def test_prefinalminute_kalshi_proxy_is_probability_not_spot_indicator():
    start = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    now = start + timedelta(minutes=5)
    close = now + timedelta(minutes=7, seconds=30)
    tape = _volatile_tape(start, seconds=300)
    fair = kalshi_proxy_fair(tape, close, 100.0, int(now.timestamp() * 1000))
    assert fair is not None
    assert 0.05 < fair < 0.95


def test_size_score_never_counts_more_than_displayed_touch_depth():
    bbo = BBO(
        yes_bid=Decimal("0.48"),
        yes_bid_depth=Decimal("5"),
        yes_ask=Decimal("0.50"),
        yes_ask_depth=Decimal("5"),
        received_ms=0,
    )

    def zero_fee(price: Decimal, quantity: Decimal) -> Decimal:
        return Decimal("0")

    rows = _size_score(
        venue="kalshi",
        ticker="KXBTC15M-TEST",
        fair=0.60,
        bbo=bbo,
        kalshi_fee_fn=zero_fee,
    )
    q1 = next(row for row in rows if row["q"] == 1)
    q10 = next(row for row in rows if row["q"] == 10)
    assert q1["best_total_ev"] is not None
    assert q10["yes_depth_ok"] is False
    assert q10["no_depth_ok"] is False
    assert q10["best_total_ev"] is None
