from decimal import Decimal as D
import inspect

import pytest

from src import pmus_micro_executor as m
from src import pmus_q1_executor_v4 as q1


class MetaFake:
    def __init__(self, minq="0.01", tick="0.005"):
        self.minq = minq
        self.tick = tick

    def request(self, method, path, body=None, base=q1.API):
        assert method == "GET"
        assert path.startswith("/v1/market/slug/")
        assert base == q1.GATEWAY
        return {"market": {"minimumTradeQty": self.minq,
                           "orderPriceMinTickSize": self.tick}}


def test_micro_quantity_targets_penny_and_caps_expensive_complement():
    assert m.choose_micro_quantity(D("0.575"), D("0.01")) == D("0.01")
    # A naive 1c entry target at 29c would choose q=.03, but the 71c
    # complement would cost 2.13c. MICRO caps q=.02 so either leg <=2c.
    q = m.choose_micro_quantity(D("0.29"), D("0.01"))
    assert q == D("0.02")
    assert q * D("0.29") <= D("0.02")
    assert q * D("0.71") <= D("0.02")


def test_very_cheap_entry_cannot_imply_large_complement():
    q = m.choose_micro_quantity(D("0.01"), D("0.01"))
    assert q == D("0.02")
    assert q * D("0.99") <= D("0.02")


def test_micro_requires_fractional_venue_support():
    with pytest.raises(m.Halt, match="MICRO_FRACTIONAL_QTY_UNSUPPORTED"):
        m.choose_micro_quantity(D("0.50"), D("1"))


def test_market_spec_uses_live_min_qty_and_tick(monkeypatch):
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    spec = m.market_micro_spec(MetaFake(), "m", D("0.575"))
    assert spec["minimum_trade_qty"] == D("0.01")
    assert spec["tick"] == D("0.005")
    assert spec["qty"] == D("0.01")
    assert spec["entry_notional"] == D("0.00575")


def test_market_spec_fails_closed_on_unaudited_tick(monkeypatch):
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    with pytest.raises(m.Halt, match="MICRO_TICK_UNSUPPORTED"):
        m.market_micro_spec(MetaFake(tick="0.01"), "m", D("0.50"))


def test_body_emits_decimal_quantity_without_touching_q1():
    before = q1.QTY
    body = m._body("m", "OUTCOME_SIDE_YES", D("0.575"), D("0.01"), True)
    assert body["quantity"] == 0.01
    assert body["participateDontInitiate"] is True
    assert q1.QTY == before == 1
    # Dedicated module must never mutate the shared q1 quantity global.
    assert "q1.QTY =" not in inspect.getsource(m)


def test_authoritative_fill_threshold_is_requested_decimal_qty():
    partial = {"state": "ORDER_STATE_OPEN", "cumQuantity": "0.0100",
               "avgPx": {"value": "0.5750"}}
    full = {"state": "ORDER_STATE_FILLED", "cumQuantity": "0.0200",
            "avgPx": {"value": "0.5750"}}
    assert m.authoritative_fill(partial, D("0.02")) is None
    p = m.authoritative_fill(partial, D("0.02"), accept_partial=True)
    assert p["qty"] == D("0.0100")
    assert p["partial"] is True
    f = m.authoritative_fill(full, D("0.02"))
    assert f["qty"] == D("0.0200")
    assert f["partial"] is False


def test_residual_report_preserves_point_zero_one_not_zero_or_one(monkeypatch):
    monkeypatch.setattr(q1, "_position_for_slug", lambda *a, **k: D("0.0100"))
    report = m._residual_report(
        {"campaign": "x"}, "COMPLEMENT_UNFILLED_RESIDUAL_HOLD",
        D("0.42"), D("0.01"), "deadline", object(), "m")
    assert report["residual"] == "0.0100"
    assert report["residual_cap_qty"] == "0.01"
    assert report["residual_cap_breached"] is False
    assert report["status"] == "COMPLEMENT_UNFILLED_RESIDUAL_HOLD"


def test_residual_above_actual_first_fill_fails_loudly(monkeypatch):
    monkeypatch.setattr(q1, "_position_for_slug", lambda *a, **k: D("0.0200"))
    report = m._residual_report(
        {}, "HALT_WITH_RESIDUAL_HOLD", D("0.42"), D("0.01"),
        "unexpected", object(), "m")
    assert report["residual"] == "0.0200"
    assert report["residual_cap_breached"] is True
    assert report["status"] == "MICRO_RESIDUAL_CAP_BREACH"


def test_micro_has_third_arming_key(monkeypatch):
    monkeypatch.setattr(q1, "armed", lambda: True)
    monkeypatch.delenv(m.MICRO_LIVE_ENV, raising=False)
    assert m.micro_armed() is False
    monkeypatch.setenv(m.MICRO_LIVE_ENV, "YES")
    assert m.micro_armed() is True
