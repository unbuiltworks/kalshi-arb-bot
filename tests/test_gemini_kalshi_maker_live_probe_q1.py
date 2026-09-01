from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata
from src import gemini_kalshi_maker_live_probe_q1 as probe

D = Decimal


def book(venue, market, yes_bid, yes_bid_size, yes_ask, yes_ask_size, ts=1_800_000_000_000):
    return TopOfBook(
        venue=venue,
        market_id=market,
        ts_ms=ts,
        yes_bid=D(yes_bid),
        yes_bid_size=D(yes_bid_size),
        yes_ask=D(yes_ask),
        yes_ask_size=D(yes_ask_size),
    )


def fee_meta():
    return KalshiSeriesFeeMetadata(
        series_ticker="KXXRP15M",
        fee_type="quadratic",
        fee_multiplier="1",
        last_updated_ts="",
        scheduled_changes=(),
    )


def test_kalshi_yes_maker_is_post_only_gtc_bid():
    payload = probe.kalshi_maker_payload(
        ticker="KXXRP15M-TEST",
        direction="KALSHI_YES_GEMINI_NO",
        maker_price=D("0.61"),
        client_order_id="makerprobe-run-k",
    )
    assert payload["side"] == "bid"
    assert payload["price"] == "0.6100"
    assert payload["count"] == "1.00"
    assert payload["time_in_force"] == "good_till_canceled"
    assert payload["post_only"] is True
    assert payload["cancel_order_on_pause"] is True
    assert payload["reduce_only"] is False


def test_kalshi_no_maker_transforms_to_yes_ask():
    payload = probe.kalshi_maker_payload(
        ticker="KXXRP15M-TEST",
        direction="GEMINI_YES_KALSHI_NO",
        maker_price=D("0.37"),
        client_order_id="makerprobe-run-k",
    )
    assert payload["side"] == "ask"
    assert payload["price"] == "0.6300"
    assert payload["post_only"] is True


def test_gemini_hedge_is_exact_quantity_fok_with_bounded_cushion():
    payload = probe.gemini_hedge_payload(
        symbol="xrp-test",
        direction="KALSHI_YES_GEMINI_NO",
        ask_price=D("0.31"),
        quantity=D("0.5"),
        client_order_id="makerhedge-run-g",
    )
    assert payload["eventOutcome"] == "NO"
    assert payload["side"] == "BUY"
    assert payload["type"] == "LIMIT"
    assert payload["timeInForce"] == "FOK"
    assert payload["price"] == "0.3400"
    assert payload["quantity"] == "0.5"


def test_candidate_requires_three_cent_post_hedge_target():
    # Gemini NO ask = 0.30. With its taker fee and zero Kalshi maker fee,
    # a Kalshi YES quote inside this spread can preserve >=3c.
    g = book("gemini", "xrp-g", "0.70", "5", "0.72", "5")
    k = book("kalshi", "xrp-k", "0.55", "3", "0.66", "3")
    c = probe.candidate_from_books(gemini=g, kalshi=k, fee_meta=fee_meta(), now_ms=g.ts_ms)
    assert c is not None
    assert c.locked_profit >= probe.TARGET_PROFIT
    assert c.maker_price >= c.best_bid
    assert c.maker_price < c.best_ask


def test_arm_requires_all_software_gates_and_persistent_claim(monkeypatch, tmp_path):
    monkeypatch.setenv(probe.VOLUME_ENV, str(tmp_path))
    monkeypatch.setenv(probe.RUN_ID_ENV, "test-run")
    monkeypatch.setenv(probe.LIVE_ENV, "YES")
    monkeypatch.setenv(probe.ARM_ENV, "YES")
    monkeypatch.setenv(probe.DEADLINE_ENV, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    monkeypatch.setattr(probe, "live_probe_authorized", lambda requested: bool(requested))

    arm = probe._maybe_create_arm("test-run")
    assert arm is not None and arm.exists()
    result = probe._claim_before_order("test-run")
    assert result.name.endswith(".result.json")
    paths = probe._paths("test-run", required=True)
    assert paths is not None
    arm_path, claim_path, result_path = paths
    assert not arm_path.exists()
    assert claim_path.exists()
    assert result_path == result
    with pytest.raises(PermissionError):
        probe._claim_before_order("test-run")


def test_disarmed_without_probe_specific_gate(monkeypatch):
    monkeypatch.setenv(probe.LIVE_ENV, "NO")
    monkeypatch.setenv(probe.ARM_ENV, "YES")
    monkeypatch.setattr(probe, "live_probe_authorized", lambda requested: True)
    assert probe._live_software_authorized() is False
