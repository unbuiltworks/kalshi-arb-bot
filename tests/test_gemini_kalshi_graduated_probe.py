import asyncio
from decimal import Decimal

import pytest

from src.gemini_kalshi_graduated_probe_guard import (
    build_leg_payloads,
    recompute_payload_locked_profit,
    validate_exact_graduated_payloads,
    validate_graduated_candidate,
)
from src import gemini_kalshi_probe_transport_quantity_safe as transport
from src.sports_dynamic_quantity import QuantitySafetyGates

D = Decimal


def gates(**overrides):
    values = dict(
        counterpart_certified=True,
        rule_fingerprint_certified=True,
        participation_certified=True,
        quote_fresh=True,
        quote_skew_ok=True,
        persistence_ok=True,
        final_revalidation_ok=True,
        capital_snapshot_fresh=True,
        no_unresolved_exposure=True,
    )
    values.update(overrides)
    return QuantitySafetyGates(**values)


def payloads(q=D("5"), *, safety=None, authorized=5):
    safety = safety or gates()
    return build_leg_payloads(
        direction="GEMINI_YES_KALSHI_NO",
        quantity=q,
        authorized_max_quantity=authorized,
        gates=safety,
        gemini_symbol="GEMI-TEST",
        kalshi_ticker="KXTEST",
        gemini_yes_bid=D("0.39"),
        gemini_yes_ask=D("0.40"),
        kalshi_yes_bid=D("0.50"),
        kalshi_yes_ask=D("0.51"),
    )


def economics(gp, kp):
    return recompute_payload_locked_profit(gp, kp, kalshi_fee_multiplier=D("1"))[1]


def test_q5_payload_requires_explicit_q5_authorization():
    with pytest.raises(ValueError, match="authorization"):
        payloads(authorized=1)


def test_q5_payload_requires_complete_rule_fingerprint():
    with pytest.raises(ValueError, match="rule_fingerprint_certified"):
        payloads(safety=gates(rule_fingerprint_certified=False))


def test_q5_profit_floor_scales_to_fifteen_cents():
    with pytest.raises(ValueError, match="3 cents per contract"):
        validate_graduated_candidate(
            quantity=D("5"),
            locked_profit=D("0.149999"),
            quote_skew_ms=1,
            authorized_max_quantity=5,
            gates=gates(),
        )


def test_q5_exact_payload_recomputation_passes_when_model_matches():
    gp, kp = payloads()
    modeled = economics(gp, kp)
    assert modeled >= D("0.15")
    recomputed = validate_exact_graduated_payloads(
        gp,
        kp,
        modeled_locked_profit=modeled,
        kalshi_fee_multiplier=D("1"),
        quote_skew_ms=10,
        authorized_max_quantity=5,
        gates=gates(),
    )
    assert recomputed == modeled


class Result:
    def __init__(self, quantity, status="FILLED"):
        self.filled_quantity = D(str(quantity))
        self.status = status

    def as_dict(self):
        return {"status": self.status, "filled_quantity": str(self.filled_quantity)}


class Gemini:
    def __init__(self, initial):
        self.initial = D(str(initial))
        self.flattened = []

    async def place_fok(self, payload):
        if str(payload.get("side")) == "SELL":
            q = D(str(payload["quantity"]))
            self.flattened.append(q)
            return Result(q)
        return Result(self.initial)


class Kalshi:
    def __init__(self, initial):
        self.initial = D(str(initial))
        self.flattened = []

    async def place(self, payload):
        if bool(payload.get("reduce_only")):
            q = D(str(payload["count"]))
            self.flattened.append(q)
            return Result(q)
        return Result(self.initial)


def submit(monkeypatch, g_fill, k_fill):
    monkeypatch.setattr(transport, "live_probe_authorized", lambda _cli_live: True)
    gp, kp = payloads()
    modeled = economics(gp, kp)
    g = Gemini(g_fill)
    k = Kalshi(k_fill)
    result = asyncio.run(
        transport.submit_dual_probe_quantity_safe(
            g,
            k,
            gp,
            kp,
            modeled_locked_profit=modeled,
            kalshi_fee_multiplier=D("1"),
            quote_skew_ms=10,
            authorized_max_quantity=5,
            gates=gates(),
            cli_live=True,
        )
    )
    return result, g, k


def test_q5_five_five_is_only_full_success(monkeypatch):
    result, g, k = submit(monkeypatch, "5", "5")
    assert result["outcome"] == "BOTH_FILLED"
    assert result["hard_halt_required"] is False
    assert not g.flattened and not k.flattened


def test_q5_five_zero_flattens_exact_gemini_five(monkeypatch):
    result, g, k = submit(monkeypatch, "5", "0")
    assert result["hard_halt_required"] is True
    assert g.flattened == [D("5")]
    assert k.flattened == []


def test_q5_zero_five_flattens_exact_kalshi_five(monkeypatch):
    result, g, k = submit(monkeypatch, "0", "5")
    assert result["hard_halt_required"] is True
    assert g.flattened == []
    assert k.flattened == [D("5")]


def test_q5_partial_vs_full_flattens_both_exact_quantities(monkeypatch):
    result, g, k = submit(monkeypatch, "2.5", "5")
    assert result["hard_halt_required"] is True
    assert g.flattened == [D("2.5")]
    assert k.flattened == [D("5")]


def test_q5_matching_partial_is_still_flattened_and_halted(monkeypatch):
    result, g, k = submit(monkeypatch, "2.5", "2.5")
    assert result["outcome"] == "PARTIAL_FILL"
    assert result["hard_halt_required"] is True
    assert g.flattened == [D("2.5")]
    assert k.flattened == [D("2.5")]


def test_fill_above_target_is_invalid_and_halts_without_guessing(monkeypatch):
    result, g, k = submit(monkeypatch, "5.01", "5")
    assert result["outcome"] == "INVALID_FILL_QUANTITY"
    assert result["hard_halt_required"] is True
    assert result["flatten"] is None
    assert not g.flattened and not k.flattened
