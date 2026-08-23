from __future__ import annotations

from decimal import Decimal

from src import gemini_kalshi_basis_risk_q1_child_v3 as telemetry


def test_identity_is_deterministic_and_blocker_sensitive() -> None:
    payload = {
        "asset": "XRP",
        "basis_window_end_utc": "2026-08-22T21:15:00Z",
        "direction": "KALSHI_YES_GEMINI_NO",
        "blocker": "PROTECTIVE_GAP_BELOW_MINIMUM",
        "gemini_symbol": "GEMI-XRP15M2608222115-UP",
        "kalshi_ticker": "KXXRP15M-26AUG221715-15",
    }
    first = telemetry._identity(payload)
    second = telemetry._identity(dict(payload))
    assert first == second
    changed = dict(payload)
    changed["blocker"] = "DANGEROUS_THRESHOLD_ORIENTATION"
    assert telemetry._identity(changed) != first


def test_persistence_failure_is_best_effort(monkeypatch) -> None:
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(telemetry.child_v2, "_connect", boom)
    assert telemetry.persist_observation_best_effort({"asset": "BTC"}) is False


def test_install_wraps_without_changing_min_gap(monkeypatch) -> None:
    seen = {}

    def fake_original(min_gap_bps: Decimal) -> None:
        seen["gap"] = min_gap_bps

    monkeypatch.setattr(telemetry, "_ORIGINAL_INSTALL_DOMINANCE", fake_original)

    class Exact:
        async def _probe(self, **kwargs):
            return "unchanged"

    monkeypatch.setattr(telemetry.child.v2.readiness, "exact", Exact())
    telemetry._install_dominance_probe_with_telemetry(Decimal("10"))
    assert seen["gap"] == Decimal("10")
