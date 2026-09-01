from __future__ import annotations

from src import gemini_same_venue_crypto_execution_v3 as v3


def test_unique_maker_canary_seed_preserves_short_client_id(monkeypatch):
    monkeypatch.setenv(v3.live.base.CANARY_ID_ENV, "g4-long-canary-name")
    seed = v3.unique_maker_canary_id()
    assert 1 <= len(seed) <= 8
    trade_id = f"crypto-gem-ladder-{seed}-001"
    client_id = f"{trade_id}-mk"
    assert len(client_id) <= 36
    assert client_id.startswith("crypto-gem-ladder-")
    assert client_id.endswith("-mk")


def test_only_authoritative_no_fill_maker_states_continue():
    assert "MAKER_CANCELED_ZERO_FILL" in v3.MAKER_CONTINUE
    assert "EXCEPTION_RECOVERED_ZERO_FILL" in v3.MAKER_CONTINUE
    assert "BOTH_FILLED_RECONCILED_NOT_SETTLED" not in v3.MAKER_CONTINUE
    assert "HARD_HALT_EXCEPTION" not in v3.MAKER_CONTINUE


def test_taker_is_rechecked_after_safe_no_fill_only():
    assert "NO_TAKER_TAKER_3C" in v3.TAKER_CONTINUE
    assert "FIRST_FOK_ZERO_FILL" in v3.TAKER_CONTINUE
    assert "BOTH_FILLED_RECONCILED_NOT_SETTLED" not in v3.TAKER_CONTINUE
    assert "FIRST_FOK_AMBIGUOUS_HARD_HALT" not in v3.TAKER_CONTINUE
