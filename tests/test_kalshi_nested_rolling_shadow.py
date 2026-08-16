from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_rolling_shadow import (
    Candidate,
    desired_shadow_quote,
    nearest_future_event,
    parse_event_close,
)

D = Decimal
NY = ZoneInfo("America/New_York")
CENT_RANGES = ({"start": "0.00", "end": "1.00", "step": "0.01"},)


def _candidate(*, maker_side="yes", hedge_side="no", target="0.03"):
    return Candidate(
        series="KXETHD",
        event_ticker="KXETHD-26AUG1717",
        event_close=datetime(2026, 8, 17, 17, tzinfo=NY),
        maker_ticker="MAKER",
        maker_side=maker_side,
        hedge_ticker="HEDGE",
        hedge_side=hedge_side,
        price_ranges=CENT_RANGES,
        fee_multiplier=D("1"),
        target_profit=D(target),
    )


def test_parse_event_close_uses_new_york_local_hour():
    assert parse_event_close("KXETHD-26AUG1717") == datetime(2026, 8, 17, 17, tzinfo=NY)
    assert parse_event_close("bad") is None


def test_nearest_future_event_skips_closed_and_longer_lockup():
    rows = [
        {"event_ticker": "KXETHD-26AUG1618"},
        {"event_ticker": "KXETHD-26AUG1717"},
        {"event_ticker": "KXETHD-26AUG2117"},
    ]
    now = datetime(2026, 8, 16, 18, 5, tzinfo=NY)
    assert nearest_future_event(rows, now=now) == "KXETHD-26AUG1717"


def test_dynamic_quote_requires_executable_hedge_and_preserves_three_cents():
    maker = KalshiOrderBook("MAKER")
    hedge = KalshiOrderBook("HEDGE")
    # YES maker: external YES bid .90; external NO bid absent -> maker ask ceiling $1.
    maker.yes = {D("0.90"): D("10")}
    maker.no = {}
    # Buy NO hedge at .01 by taking YES bid .99.
    hedge.yes = {D("0.99"): D("50")}
    hedge.no = {}

    quote, econ = desired_shadow_quote(candidate=_candidate(), maker_book=maker, hedge_book=hedge)
    assert quote == D("0.95")
    assert econ["reason"] == "READY"
    assert D(econ["locked_profit"]) >= D("0.03")


def test_dynamic_quote_cancels_when_hedge_worsens_beyond_target():
    maker = KalshiOrderBook("MAKER")
    hedge = KalshiOrderBook("HEDGE")
    maker.yes = {D("0.90"): D("10")}
    maker.no = {}
    # NO hedge ask .08; fee is .01, so max maker for 3c target is .88: no bid improvement.
    hedge.yes = {D("0.92"): D("20")}
    hedge.no = {}

    quote, econ = desired_shadow_quote(candidate=_candidate(), maker_book=maker, hedge_book=hedge)
    assert quote is None
    assert econ["reason"] == "NO_VALID_INSIDE_QUOTE_AT_TARGET"


def test_dynamic_quote_fails_closed_without_hedge_depth():
    maker = KalshiOrderBook("MAKER")
    hedge = KalshiOrderBook("HEDGE")
    maker.yes = {D("0.90"): D("10")}
    hedge.yes = {}
    quote, econ = desired_shadow_quote(candidate=_candidate(), maker_book=maker, hedge_book=hedge)
    assert quote is None
    assert econ["reason"] == "NO_HEDGE_ASK"
