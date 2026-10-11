"""The SELL path is DISARMED, position-backed, and shadow-safe."""
import asyncio

import pytest

from src.gemini_execution import sell_path as S
from src.gemini_execution import ci15m_probe as P, payload, spec


def test_disarmed_by_default():
    assert S.SELL_AUTH_ID is None
    assert S.armed()["armed"] is False


def test_submit_refused_while_disarmed():
    with pytest.raises(S.SellRefused) as e:
        asyncio.run(S.submit_sell("C", "SYM", "TK", outcome="yes",
                                  price="0.55", quantity="1",
                                  position_rows=[]))
    assert "no authorization grants a sell" in str(e.value)


def test_build_sell_matches_documented_schema():
    body = S.build_sell(symbol="GEMI-X", outcome="no", price="0.35",
                        quantity="50")
    assert body["side"] == spec.SIDE_SELL
    assert set(body) == set(payload.ALLOWED_ORDER_FIELDS)
    assert set(payload.ALLOWED_ORDER_FIELDS) <= set(spec.ORDER_REQUEST_FIELDS)
    assert body["makerOrCancel"] is True
    assert payload.validate_structure(body)["unknown"] == []


def test_build_sell_rejects_clientorderid_shape():
    body = S.build_sell(symbol="GEMI-X", outcome="yes", price="0.5",
                        quantity="1")
    body["clientOrderId"] = "x"
    with pytest.raises(payload.PayloadViolation):
        payload.validate_structure(body)


def test_unreadable_positions_is_not_zero():
    got = S.free_quantity(None, "SYM", "yes")
    assert got["readable"] is False and got["free"] is None
    with pytest.raises(S.SellRefused) as e:
        S.assert_position_backed(None, "SYM", "yes", "1")
    assert "unreadable" in str(e.value)


def test_empty_read_is_a_fact_and_refuses_naked_sell():
    got = S.free_quantity([], "SYM", "yes")
    assert got["readable"] is True and got["free"] == "0"
    with pytest.raises(S.SellRefused) as e:
        S.assert_position_backed([], "SYM", "yes", "1")
    assert "SHORT" in str(e.value)


def test_quantity_on_hold_is_not_sellable():
    rows = [{"symbol": "SYM", "outcome": "yes", "totalQuantity": "3",
             "quantityOnHold": "3"}]
    assert S.free_quantity(rows, "SYM", "yes")["free"] == "0"
    with pytest.raises(S.SellRefused):
        S.assert_position_backed(rows, "SYM", "yes", "1")


def test_position_backed_sell_is_admitted():
    rows = [{"symbol": "SYM", "outcome": "yes", "totalQuantity": "3",
             "quantityOnHold": "1"}]
    got = S.assert_position_backed(rows, "SYM", "yes", "2")
    assert got["position_backed"] is True and got["free"] == "2"
    with pytest.raises(S.SellRefused):
        S.assert_position_backed(rows, "SYM", "yes", "3")


def test_other_symbol_and_other_outcome_do_not_back_the_sell():
    rows = [{"symbol": "OTHER", "outcome": "yes", "totalQuantity": "9",
             "quantityOnHold": "0"},
            {"symbol": "SYM", "outcome": "no", "totalQuantity": "9",
             "quantityOnHold": "0"}]
    with pytest.raises(S.SellRefused):
        S.assert_position_backed(rows, "SYM", "yes", "1")


def test_zero_or_negative_quantity_refused():
    rows = [{"symbol": "SYM", "outcome": "yes", "totalQuantity": "3",
             "quantityOnHold": "0"}]
    for q in ("0", "-1"):
        with pytest.raises(S.SellRefused):
            S.assert_position_backed(rows, "SYM", "yes", q)


def test_shadow_write_boundary_is_the_only_write(monkeypatch):
    """Armed + position-backed, but in SHADOW nothing reaches the venue."""
    seen = {}

    async def sink(**kw):
        seen.update(kw)
        return {"ok": True, "status": 201, "shadow": True,
                "body": {"orderId": 1, "status": "open"}}

    async def no_outstanding(symbol):
        return {"ok": True}

    monkeypatch.setattr(S, "SELL_AUTH_ID", S.arming.EXPECTED_AUTH_ID,
                        raising=False)
    monkeypatch.setattr(P.BUD, "halted", lambda *a, **k: None)
    monkeypatch.setattr(P, "AUTHORIZED_POLICY_FINGERPRINT", P.fingerprint())
    monkeypatch.setattr(P, "one_outstanding_order", no_outstanding)
    P.set_write_sink(sink, mode=P.WRITE_SHADOW)
    try:
        rows = [{"symbol": "SYM", "outcome": "yes", "totalQuantity": "2",
                 "quantityOnHold": "0"}]
        got = asyncio.run(S.submit_sell("C", "SYM", "TK", outcome="yes",
                                        price="0.55", quantity="1",
                                        position_rows=rows))
    finally:
        P.set_write_sink(None)
    assert got["status"] == "SUBMITTED"
    assert got["write_mode"] == P.WRITE_SHADOW
    assert seen["side"] == spec.SIDE_SELL
    assert seen["allow_taker"] is False


def test_armed_sell_still_refuses_unbacked_quantity(monkeypatch):
    monkeypatch.setattr(S, "SELL_AUTH_ID", S.arming.EXPECTED_AUTH_ID,
                        raising=False)
    monkeypatch.setattr(P.BUD, "halted", lambda *a, **k: None)
    monkeypatch.setattr(P, "AUTHORIZED_POLICY_FINGERPRINT", P.fingerprint())
    called = {"n": 0}

    async def sink(**kw):
        called["n"] += 1
        return {}

    P.set_write_sink(sink, mode=P.WRITE_SHADOW)
    try:
        with pytest.raises(S.SellRefused):
            asyncio.run(S.submit_sell("C", "SYM", "TK", outcome="yes",
                                      price="0.55", quantity="5",
                                      position_rows=[
                                          {"symbol": "SYM", "outcome": "yes",
                                           "totalQuantity": "2",
                                           "quantityOnHold": "0"}]))
    finally:
        P.set_write_sink(None)
    assert called["n"] == 0


def test_sell_path_does_not_touch_the_frozen_policy():
    import hashlib
    import pathlib
    p = pathlib.Path("analysis/gemini/info_edge/btc15m_shadow_policy.py")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == (
        "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")
