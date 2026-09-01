from decimal import Decimal as D

import pytest

from src import gemini_sports_l5_live_probe_q1 as probe
from src import gemini_sports_l5_inventory_local_budget as budget


def test_sports_pair_model_fails_closed_outside_band():
    def original(bid, ask):
        return {
            "yes_quote": D(str(bid)),
            "no_quote": D("1") - D(str(ask)),
            "modeled_edge": D("0.05"),
        }
    model = probe._sports_pair_model(original)
    assert model(D("0.10"), D("0.20"))["modeled_edge"] == D("-1")
    assert model(D("0.45"), D("0.50"))["modeled_edge"] == D("0.05")


def test_q1_metadata_gate():
    assert probe._q1({"quantityMinimum": "1", "quantityIncrement": "1"})
    assert probe._q1({"quantityMinimum": "0.1", "quantityIncrement": "0.1"})
    assert not probe._q1({"quantityMinimum": "2", "quantityIncrement": "1"})
    assert not probe._q1({"quantityMinimum": "1", "quantityIncrement": "0.3"})


def test_only_mlb_moneyline_family_is_admitted():
    assert probe.MLB_MONEYLINE_RE.fullmatch("GEMI-MLB-2608311905-PHI-NYM-M")
    assert not probe.MLB_MONEYLINE_RE.fullmatch("GEMI-MLB-2608311905-PHI-NYM-S")
    assert not probe.MLB_MONEYLINE_RE.fullmatch("GEMI-NFL-2608311905-PHI-NYG-M")


@pytest.mark.asyncio
async def test_disarmed_wrapper_makes_no_venue_calls(monkeypatch):
    monkeypatch.delenv(probe.LIVE_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ID_ENV, raising=False)
    monkeypatch.delenv(probe.PROBE_ARM_ENV, raising=False)

    async def boom(*args, **kwargs):
        raise AssertionError("venue/account call while disarmed")

    monkeypatch.setattr(probe, "discover_sports_moneyline", boom)
    monkeypatch.setattr(probe.local_base, "local_open_state", lambda: (_ for _ in ()).throw(
        AssertionError("local state call while disarmed")
    ))
    await probe.run()


def test_sports_budget_identity_is_not_crypto():
    assert "sports" in budget.STRATEGY
    assert "SPORTS" in budget.CLASSIFICATION
