from decimal import Decimal

from src.gemini_kalshi_xrp_maker_live_canary import MakerFill, kalshi_no_fok_payload

D = Decimal


def test_fractional_kalshi_no_hedge_payload_uses_yes_space_fok():
    p = kalshi_no_fok_payload(
        ticker="KXXRP15M-TEST",
        no_limit=D("0.074"),
        quantity=D("0.25"),
        client_id="test-hedge",
    )
    assert p["side"] == "ask"
    assert p["count"] == "0.25"
    assert p["price"] == "0.9260"
    assert p["time_in_force"] == "fill_or_kill"
    assert p["post_only"] is False


def test_maker_fill_uses_execution_delta_and_fee_field():
    f = MakerFill()
    f.add({"X":"PARTIALLY_FILLED","Z":"0.25","L":"0.81","n":"0.01"})
    assert f.quantity == D("0.25")
    assert f.notional == D("0.2025")
    assert f.fees == D("0.01")
    assert f.executions == 1
    f.add({"X":"CANCELED","Z":"0.25"})
    assert f.quantity == D("0.25")


def test_missing_fee_on_execution_assumes_one_cent_per_execution():
    f = MakerFill()
    f.add({"X":"PARTIALLY_FILLED","Z":"0.10","L":"0.70"})
    f.add({"X":"FILLED","Z":"0.90","L":"0.70"})
    assert f.quantity == D("1.00")
    assert f.fees == D("0.02")
    assert f.missing_fee_events == 2
