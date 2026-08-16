from collections import deque
from decimal import Decimal as D

from src import gemini_kalshi_prewarmed_basis_shadow as basis


def _snapshot(abs_margin: str, age_ms: int):
    margin = D(abs_margin)
    return {
        "spot_abs_margin_bps": str(margin),
        "spot_quote_age_ms": age_ms,
        "proxy_fresh_1000ms": age_ms <= 1000,
        "research_proxy_gate_2bp_fresh_1000ms": age_ms <= 1000 and margin >= D("2"),
        "research_proxy_gate_5bp_fresh_1000ms": age_ms <= 1000 and margin >= D("5"),
    }


def test_latest_at_or_before_never_uses_future_quote():
    rows = [(1000, D("100")), (1500, D("101")), (2000, D("999"))]
    assert basis.latest_at_or_before(rows, 1750) == (1500, D("101"))


def test_basis_episode_requires_gate_to_hold_for_entire_interval():
    state = basis.BasisEpisodeState(asset="BTC", direction="x", start_ms=1000)
    state.observe(_snapshot("7.5", 10))
    assert state.entry_gate_2bp is True
    assert state.entry_gate_5bp is True
    assert state.all_events_gate_5bp is True

    # Still fresh, but distance moves inside 5bp. The 5bp cohort must permanently fail
    # for this episode while 2bp can remain valid.
    state.observe(_snapshot("3.0", 12))
    assert state.min_abs_margin_bps == D("3.0")
    assert state.all_events_gate_2bp is True
    assert state.all_events_gate_5bp is False


def test_basis_episode_stale_or_missing_context_fails_closed():
    stale = basis.BasisEpisodeState(asset="ETH", direction="x", start_ms=1000)
    stale.observe(_snapshot("8", 1200))
    assert stale.entry_gate_5bp is False
    assert stale.all_events_fresh_1000ms is False
    assert stale.all_events_gate_2bp is False
    assert stale.all_events_gate_5bp is False

    missing = basis.BasisEpisodeState(asset="SOL", direction="x", start_ms=1000)
    missing.observe(None)
    assert missing.all_events_fresh_1000ms is False
    assert missing.all_events_gate_2bp is False
    assert missing.all_events_gate_5bp is False


def test_basis_snapshot_uses_only_knowable_quote(monkeypatch):
    state = basis.SpotState("XRP")
    state.history = deque([
        (1000, D("1.001")),
        (1500, D("1.002")),
        (2100, D("9.999")),  # future relative to candidate
    ])
    monkeypatch.setitem(basis._STATES, "XRP", state)
    monkeypatch.setitem(
        basis._STRIKES,
        "XRP",
        ("2026-08-16T21:15:00+00:00", D("1.000"), {"agency": "Kaiko"}),
    )
    snap = basis.basis_snapshot("XRP", 2000)
    assert snap is not None
    assert snap["spot_mid"] == "1.002"
    assert snap["spot_quote_age_ms"] == 500
    assert D(snap["spot_abs_margin_bps"]) == D("20")
    assert snap["research_proxy_gate_5bp_fresh_1000ms"] is True
