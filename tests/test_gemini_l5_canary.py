import base64
import hashlib
import hmac
import json
from decimal import Decimal as D

import pytest

from src.gemini_l5_canary import (
    ARM_TOKEN, ORDER_PATH, CanaryBlocked, CanaryConfig, build_maker_order,
    require_terms_accepted, signed_headers, submit_one_probe, validate_probe,
)


def armed():
    return CanaryConfig(armed=True)


def good_kwargs():
    return dict(
        cfg=armed(), symbol="GEMI-BTC05M2609131200-UP", quantity=D("1"),
        side="buy", outcome="yes", price=D("0.41"), edge_after_fees=D("0.03"),
        active_market_count=0, residual_usd=D("0"), daily_realized_pnl_usd=D("0"),
    )


def test_canary_is_disarmed_by_default():
    kw = good_kwargs(); kw["cfg"] = CanaryConfig()
    with pytest.raises(CanaryBlocked, match="canary_disarmed"):
        validate_probe(**kw)


def test_canary_is_q1_only_and_one_market_only():
    kw = good_kwargs(); kw["quantity"] = D("2")
    with pytest.raises(CanaryBlocked, match="q1_only"):
        validate_probe(**kw)
    kw = good_kwargs(); kw["active_market_count"] = 1
    with pytest.raises(CanaryBlocked, match="one_market_at_a_time"):
        validate_probe(**kw)


def test_canary_requires_fee_adjusted_edge_and_loss_stop():
    kw = good_kwargs(); kw["edge_after_fees"] = D("0.009")
    with pytest.raises(CanaryBlocked, match="edge_below_floor"):
        validate_probe(**kw)
    kw = good_kwargs(); kw["daily_realized_pnl_usd"] = D("-2")
    with pytest.raises(CanaryBlocked, match="daily_realized_loss_stop"):
        validate_probe(**kw)


def test_canary_limits_first_lane_to_btc_5m_15m_and_buy_only():
    kw = good_kwargs(); kw["symbol"] = "GEMI-ETH05M2609131200-UP"
    with pytest.raises(CanaryBlocked, match="symbol_not_btc_5m_or_15m"):
        validate_probe(**kw)
    kw = good_kwargs(); kw["side"] = "sell"
    with pytest.raises(CanaryBlocked, match="buy_only"):
        validate_probe(**kw)


def test_order_is_always_maker_or_cancel():
    order = build_maker_order(symbol="GEMI-BTC15M2609131200-UP", outcome="no", price=D("0.44"))
    assert order["quantity"] == "1"
    assert order["side"] == "buy"
    assert order["makerOrCancel"] is True
    assert order["timeInForce"] == "good-til-cancel"


def test_signing_encodes_logical_payload_and_sends_empty_body():
    order = build_maker_order(symbol="GEMI-BTC05M2609131200-UP", outcome="yes", price=D("0.41"))
    headers, body = signed_headers(ORDER_PATH, order, api_key="account-test", api_secret="secret", nonce=123)
    assert body == b""
    decoded = json.loads(base64.b64decode(headers["X-GEMINI-PAYLOAD"]))
    assert decoded["request"] == ORDER_PATH
    assert decoded["nonce"] == 123
    assert decoded["makerOrCancel"] is True
    expected = hmac.new(b"secret", headers["X-GEMINI-PAYLOAD"].encode(), hashlib.sha384).hexdigest()
    assert headers["X-GEMINI-SIGNATURE"] == expected
    assert headers["Content-Length"] == "0"


def test_terms_must_be_current():
    with pytest.raises(CanaryBlocked, match="prediction_market_terms_not_current"):
        require_terms_accepted({"hasAcceptedLatest": False})
    require_terms_accepted({"hasAcceptedLatest": True})


def test_submit_is_injected_and_only_runs_after_all_gates():
    calls = []
    def post(url, headers, body):
        calls.append((url, headers, body))
        return {"orderId": 123, "status": "accepted"}
    out = submit_one_probe(
        cfg=armed(), symbol="GEMI-BTC05M2609131200-UP", outcome="yes", price=D("0.41"),
        edge_after_fees=D("0.03"), active_market_count=0, residual_usd=D("0"),
        daily_realized_pnl_usd=D("0"), terms_status={"hasAcceptedLatest": True},
        api_key="account-test", api_secret="secret", post=post,
    )
    assert out["orderId"] == 123
    assert len(calls) == 1
    assert calls[0][2] == b""
