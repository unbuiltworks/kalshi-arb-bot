from decimal import Decimal

from src.kalshi_nested_probe_quality_watch import probe_quality_gate

D = Decimal


def _econ(**overrides):
    row = {
        "reason": "READY",
        "maker_bid": "0.93",
        "maker_ask": "1.00",
        "hedge_price": "0.01",
        "hedge_size": "20",
        "taker_fee": "0.01",
        "locked_profit": "0.03",
    }
    row.update(overrides)
    return row


def test_go_accepts_93_to_95_with_one_cent_seed():
    ok, reason, detail = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(),
        target_profit=D("0.03"),
    )
    assert ok
    assert reason == "GO_SHADOW"
    assert D(detail["maker_improvement"]) == D("0.02")


def test_go_accepts_94_to_95():
    ok, _, _ = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(maker_bid="0.94"),
        target_profit=D("0.03"),
    )
    assert ok


def test_rejects_56_to_95_jump():
    ok, reason, _ = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(maker_bid="0.56"),
        target_profit=D("0.03"),
    )
    assert not ok
    assert reason == "MAKER_IMPROVEMENT_TOO_LARGE"


def test_rejects_two_cent_seed():
    ok, reason, _ = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(maker_bid="0.94", hedge_price="0.02"),
        target_profit=D("0.03"),
    )
    assert not ok
    assert reason == "SEED_TOO_EXPENSIVE"


def test_rejects_empty_bid():
    ok, reason, _ = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(maker_bid="0"),
        target_profit=D("0.03"),
    )
    assert not ok
    assert reason == "EMPTY_MAKER_BID"


def test_rejects_profit_below_target():
    ok, reason, _ = probe_quality_gate(
        quote=D("0.95"),
        economics=_econ(maker_bid="0.94", locked_profit="0.02"),
        target_profit=D("0.03"),
    )
    assert not ok
    assert reason == "LOCKED_PROFIT_BELOW_TARGET"
