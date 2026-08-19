from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.sports_kgp_micro_guard import MicroExecutionPlan, MicroLeg
from src.sports_kgp_micro_transport import (
    consume_arm_token,
    gemini_payload,
    kalshi_payload,
    live_authorized,
    polymarket_payload,
    submit_micro_pair,
    validate_plan_transport,
    VenueResult,
)


def leg(venue: str, normalized: str, native: str, price: str) -> MicroLeg:
    return MicroLeg(
        venue=venue,
        market_id={"gemini": "GEMI-X", "kalshi": "KX-X", "polymarket": "poly-x"}[venue],
        normalized_outcome=normalized,
        native_outcome=native,
        raw_outcome_price=D(price),
        source_side="YES",
        received_ms=1_800_000_000_000,
    )


def plan(a="kalshi", b="gemini") -> MicroExecutionPlan:
    yes = leg(a, "YES", "YES", "0.40")
    no = leg(b, "NO", "NO", "0.55")
    return MicroExecutionPlan(
        settlement_key="key",
        quantity=D("1"),
        unit_cost=D("0.95"),
        locked_profit=D("0.05"),
        quote_skew_ms=10,
        created_ms=1_800_000_000_000,
        yes_leg=yes,
        no_leg=no,
        rule_fingerprint_sha256="a" * 64,
    )


def test_gemini_payload_is_q1_fok_buy():
    p = gemini_payload(leg("gemini", "NO", "NO", "0.55"))
    assert p["side"] == "BUY"
    assert p["timeInForce"] == "FOK"
    assert p["quantity"] == "1"
    assert p["eventOutcome"] == "NO"


def test_kalshi_no_payload_uses_yes_space_ask():
    p = kalshi_payload(leg("kalshi", "NO", "NO", "0.55"))
    assert p["side"] == "ask"
    assert D(p["price"]) == D("0.45")
    assert p["time_in_force"] == "fill_or_kill"
    assert D(p["count"]) == D("1")


def test_polymarket_payload_is_automatic_q1_fok():
    p = polymarket_payload(leg("polymarket", "NO", "NO", "0.55"))
    assert p["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert p["tif"] == "TIME_IN_FORCE_FILL_OR_KILL"
    assert p["quantity"] == 1
    assert p["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATIC"


def test_all_three_pair_shapes_have_immediate_payloads():
    for a, b in (("kalshi", "gemini"), ("kalshi", "polymarket"), ("gemini", "polymarket")):
        payloads = validate_plan_transport(plan(a, b))
        assert set(payloads) == {a, b}


def test_non_q1_transport_fails_closed():
    p = plan()
    bad = MicroExecutionPlan(**{**p.__dict__, "quantity": D("2")})
    with pytest.raises(ValueError, match="exactly one"):
        validate_plan_transport(bad)


def test_live_gate_requires_cli_env_and_token(monkeypatch, tmp_path: Path):
    token = tmp_path / "arm"
    monkeypatch.setenv("ENABLE_SPORTS_KGP_MICRO_LIVE", "YES")
    assert not live_authorized(live_requested=False, token_path=token)
    assert not live_authorized(live_requested=True, token_path=token)
    token.write_text("one use", encoding="utf-8")
    assert live_authorized(live_requested=True, token_path=token)
    assert consume_arm_token(token)
    assert not token.exists()
    assert not live_authorized(live_requested=True, token_path=token)


class FakeGemini:
    def __init__(self, result): self.result = result; self.flattened = False
    async def place_fok(self, payload): return self.result
    async def emergency_flatten_market(self, payload):
        self.flattened = True
        return SimpleNamespace(venue="gemini", client_order_id="fg", status="FILLED", filled_quantity="1", average_fill_price="0.5", reason=None, reconciled=True)


class FakeKalshi:
    def __init__(self, result): self.result = result; self.flattened = False
    async def place(self, payload): return self.result
    async def emergency_flatten(self, payload):
        self.flattened = True
        return SimpleNamespace(venue="kalshi", client_order_id="fk", status="FILLED", filled_quantity="1", average_fill_price="0.5", reason=None, reconciled=True)


def existing(venue, status, qty):
    return SimpleNamespace(
        venue=venue, client_order_id=f"{venue}-1", status=status,
        filled_quantity=str(qty), average_fill_price="0.4" if D(str(qty)) else None,
        reason=None, reconciled=False,
    )


@pytest.mark.asyncio
async def test_both_filled_is_clean_success():
    report = await submit_micro_pair(
        plan("kalshi", "gemini"),
        gemini=FakeGemini(existing("gemini", "FILLED", 1)),
        kalshi=FakeKalshi(existing("kalshi", "FILLED", 1)),
    )
    assert report["outcome"] == "BOTH_FILLED"
    assert report["hard_halt_required"] is False
    assert report["flatten"] is None


@pytest.mark.asyncio
async def test_one_leg_fill_flattens_and_hard_halts():
    gemini = FakeGemini(existing("gemini", "CANCELED", 0))
    kalshi = FakeKalshi(existing("kalshi", "FILLED", 1))
    report = await submit_micro_pair(plan("kalshi", "gemini"), gemini=gemini, kalshi=kalshi)
    assert report["outcome"] == "ONE_LEG_FILLED"
    assert report["hard_halt_required"] is True
    assert kalshi.flattened is True
    assert report["flatten"]["status"] == "FILLED"


@pytest.mark.asyncio
async def test_unresolved_ambiguity_hard_halts_without_retry():
    report = await submit_micro_pair(
        plan("kalshi", "gemini"),
        gemini=FakeGemini(existing("gemini", "UNKNOWN", 0)),
        kalshi=FakeKalshi(existing("kalshi", "CANCELED", 0)),
    )
    assert report["outcome"] == "AMBIGUOUS_SUBMISSION"
    assert report["hard_halt_required"] is True
