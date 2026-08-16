from collections import deque
from decimal import Decimal as D

from src import gemini_kalshi_basis_annotated_shadow as basis
from src.gemini_kaiko_proxy_calibration import SpotState


def test_latest_at_or_before_never_uses_future_quote():
    rows = [(1000, D("100")), (1500, D("101")), (2000, D("999"))]
    assert basis.latest_at_or_before(rows, 1750) == (1500, D("101"))


def test_twap_at_or_before_ignores_future_observations():
    rows = [
        (0, D("100")),
        (30_000, D("102")),
        (60_000, D("1000")),  # future relative to target
    ]
    twap, span = basis.twap_at_or_before(rows, 45_000, 60_000)
    assert span == 45_000
    # 30s at 100 + 15s at 102 = 100.666...
    assert twap == D("100.6666666666666666666666667")


def test_basis_snapshot_is_prospective_and_marks_research_gates(monkeypatch):
    state = SpotState("BTC")
    state.history = deque([
        (1_000, D("100")),
        (1_500, D("100.10")),
        (2_100, D("200")),  # must not leak into candidate at 2,000ms
    ])
    monkeypatch.setitem(basis._STATES, "BTC", state)
    monkeypatch.setitem(
        basis._STRIKES,
        "BTC",
        ("2026-08-16T21:00:00+00:00", D("100"), {"agency": "Kaiko"}),
    )

    row = basis.basis_snapshot("BTC", 2_000)
    assert row is not None
    assert row["spot_mid"] == "100.10"
    assert row["spot_quote_age_ms"] == 500
    assert D(row["spot_mid_margin_bps"]) == D("10")
    assert row["proxy_fresh_250ms"] is False
    assert row["proxy_fresh_1000ms"] is True
    assert row["research_proxy_gate_2bp_fresh_1000ms"] is True
    assert row["research_proxy_gate_5bp_fresh_1000ms"] is True
    assert row["official_live_kaiko"] is False
