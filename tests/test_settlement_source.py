import pytest

from src.l5_settlement_source import SettlementUnavailable, winner_from_bbo


def _bbo(state="MARKET_STATE_EXPIRED", px="0.0000"):
    md = {"state": state}
    if px is not None:
        md["settlementPx"] = {"value": px, "currency": "USD"}
    return {"marketData": md}


def test_the_real_1330z_payload_settles_down():
    """settlementPx 0 means the UP leg paid nothing."""
    assert winner_from_bbo(_bbo(px="0.0000")) == "down"


def test_settlement_one_is_up():
    assert winner_from_bbo(_bbo(px="1.0000")) == "up"


def test_an_open_market_is_refused_however_tempting_its_last_price():
    with pytest.raises(SettlementUnavailable, match="not settlement evidence"):
        winner_from_bbo({"marketData": {"state": "MARKET_STATE_OPEN",
                                        "lastTradePx": {"value": "0.99"},
                                        "settlementPx": None}})


def test_a_missing_settlement_price_is_refused():
    with pytest.raises(SettlementUnavailable, match="absent"):
        winner_from_bbo(_bbo(px=None))


def test_a_fractional_settlement_is_refused_not_rounded():
    with pytest.raises(SettlementUnavailable, match="neither 0 nor 1"):
        winner_from_bbo(_bbo(px="0.5000"))
    with pytest.raises(SettlementUnavailable, match="neither 0 nor 1"):
        winner_from_bbo(_bbo(px="0.9900"))
