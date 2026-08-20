import pytest

from src.weather_live_probe import (
    ARM_VALUE,
    ProbeConfig,
    q1_executable_price,
    settlement_identity,
    validate_config,
    v2_order_body,
)


def cfg(**overrides):
    data = dict(
        ticker="KXHIGHCHI-26AUG21-B80.5",
        outcome_side="NO",
        max_price_cents=60,
        expected_settlement_key="CLIMDW",
        arm=ARM_VALUE,
        dry_run=False,
    )
    data.update(overrides)
    return ProbeConfig(**data)


def test_probe_is_one_contract_fill_or_kill_no_retry_shape():
    body = v2_order_body(cfg(), 57)
    assert body["count"] == "1.00"
    assert body["time_in_force"] == "fill_or_kill"
    assert body["post_only"] is False
    assert body["side"] == "ask"
    assert body["price"] == "0.4300"  # NO 57c == YES ask 43c


def test_yes_probe_uses_yes_bid_price():
    body = v2_order_body(cfg(outcome_side="YES"), 11)
    assert body["side"] == "bid"
    assert body["price"] == "0.1100"


def test_config_rejects_non_weather_and_bad_price():
    with pytest.raises(ValueError):
        validate_config(cfg(ticker="NOTWEATHER"))
    with pytest.raises(ValueError):
        validate_config(cfg(max_price_cents=100))


def test_settlement_identity_requires_twc_and_exact_cli_key():
    market = {
        "rules_primary": (
            "If the maximum temperature recorded at Chicago (CLIMDW) for Aug 21, 2026, "
            "is between 80-81° fahrenheit according to The Weather Company, then the market resolves to Yes."
        )
    }
    key, is_twc = settlement_identity(market)
    assert key == "CLIMDW"
    assert is_twc is True


def test_q1_yes_and_no_executable_prices_derive_from_opposite_bids():
    book = {"orderbook": {"yes": [[43, 3], [42, 10]], "no": [[89, 5], [88, 10]]}}
    assert q1_executable_price(book, "YES") == 11
    assert q1_executable_price(book, "NO") == 57
