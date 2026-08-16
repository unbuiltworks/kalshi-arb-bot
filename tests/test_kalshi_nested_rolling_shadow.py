from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_rolling_shadow import (
    Candidate,
    QuoteState,
    desired_shadow_quote,
    nearest_future_event,
    parse_event_close,
    trade_fill_gate,
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


def _active_state(*, fill_eligible=10.025):
    state = QuoteState(candidate=_candidate())
    state.active_price = D("0.95")
    state.posted_monotonic = 10.0
    state.fill_eligible_monotonic = fill_eligible
    return state


def _trade(*, count="1", yes_price="0.95"):
    return {
        "taker_outcome_side": "no",
        "yes_price_dollars": yes_price,
        "count_fp": count,
    }


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


def test_nearest_future_event_horizon_rejects_multi_day_capital_lock():
    rows = [
        {"event_ticker": "KXETHD-26AUG1717"},
        {"event_ticker": "KXETHD-26AUG2117"},
    ]
    now = datetime(2026, 8, 16, 18, 5, tzinfo=NY)
    assert nearest_future_event(
        rows,
        now=now,
        max_time_to_close_seconds=6 * 3600,
    ) is None


def test_nearest_future_event_horizon_accepts_same_evening_event():
    rows = [
        {"event_ticker": "KXETHD-26AUG1619"},
        {"event_ticker": "KXETHD-26AUG1717"},
    ]
    now = datetime(2026, 8, 16, 18, 5, tzinfo=NY)
    assert nearest_future_event(
        rows,
        now=now,
        max_time_to_close_seconds=6 * 3600,
    ) == "KXETHD-26AUG1619"


def test_dynamic_quote_requires_executable_hedge_and_preserves_three_cents():
    maker = KalshiOrderBook("MAKER")
    hedge = KalshiOrderBook("HEDGE")
    maker.yes = {D("0.90"): D("10")}
    maker.no = {}
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


def test_fill_gate_rejects_trade_before_activation_latency():
    maker = KalshiOrderBook("MAKER")
    maker.yes = {D("0.90"): D("10")}
    ok, reason = trade_fill_gate(
        state=_active_state(fill_eligible=10.025),
        maker_book=maker,
        trade_msg=_trade(),
        now_mono=10.024,
    )
    assert not ok
    assert reason == "ACTIVATION_LATENCY"


def test_fill_gate_rejects_sub_contract_print():
    maker = KalshiOrderBook("MAKER")
    maker.yes = {D("0.90"): D("10")}
    ok, reason = trade_fill_gate(
        state=_active_state(),
        maker_book=maker,
        trade_msg=_trade(count="0.50"),
        now_mono=10.100,
    )
    assert not ok
    assert reason == "TRADE_SIZE_LT_1"


def test_fill_gate_rejects_queue_priority_ambiguity():
    maker = KalshiOrderBook("MAKER")
    maker.yes = {D("0.95"): D("2")}
    ok, reason = trade_fill_gate(
        state=_active_state(),
        maker_book=maker,
        trade_msg=_trade(),
        now_mono=10.100,
    )
    assert not ok
    assert reason == "QUEUE_PRIORITY_AMBIGUOUS"


def test_fill_gate_accepts_one_lot_trade_through_after_activation():
    maker = KalshiOrderBook("MAKER")
    maker.yes = {D("0.90"): D("10")}
    ok, reason = trade_fill_gate(
        state=_active_state(),
        maker_book=maker,
        trade_msg=_trade(count="1.00", yes_price="0.94"),
        now_mono=10.100,
    )
    assert ok
    assert reason == "PLAUSIBLE_FILL_TRADE_THROUGH"
