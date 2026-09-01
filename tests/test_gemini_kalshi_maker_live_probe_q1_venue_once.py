import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import time

import pytest

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe
from src import gemini_kalshi_maker_live_probe_q1_venue_once as venue

D = Decimal


def test_venue_once_arm_does_not_require_persistent_volume(monkeypatch, tmp_path):
    monkeypatch.setattr(venue, "STATE_ROOT", tmp_path)
    monkeypatch.delenv(base.VOLUME_ENV, raising=False)
    monkeypatch.setenv(base.LIVE_ENV, "YES")
    monkeypatch.setenv(base.ARM_ENV, "YES")
    monkeypatch.setenv(base.DEADLINE_ENV, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    monkeypatch.setattr(base, "live_probe_authorized", lambda requested: bool(requested))

    arm = venue.maybe_create_arm("venue-test")
    assert arm is not None and arm.exists()
    result = venue.claim_before_order("venue-test")
    assert result.name.endswith(".result.json")
    assert not arm.exists()
    with pytest.raises(PermissionError):
        venue.claim_before_order("venue-test")


def _trigger_candidate(price="0.85"):
    return base.Candidate(
        direction="GEMINI_YES_KALSHI_NO",
        maker_outcome="NO",
        hedge_outcome="YES",
        maker_price=D(price),
        hedge_price=D("0.13"),
        locked_profit=D("0.01"),
        best_bid=D("0.84"),
        best_ask=D("0.86"),
        queue_ahead=D("0"),
        observed_ms=123,
    )


def test_improve_trigger_becomes_join_priced_state_machine_candidate(monkeypatch):
    monkeypatch.setattr(venue, "_trigger_candidate_fn", lambda **kwargs: _trigger_candidate())
    monkeypatch.setattr(base, "POLICY", "IMPROVE_1T")
    monkeypatch.setattr(base, "TARGET_PROFIT", D("0.01"))
    monkeypatch.setattr(
        base,
        "quote_economics",
        lambda **kwargs: SimpleNamespace(hedge_price=D("0.13"), locked_profit=D("0.02")),
    )
    monkeypatch.setattr(base, "maker_side_book", lambda *args, **kwargs: (D("0.84"), D("16"), D("0.86")))

    candidate = venue.candidate_for_submission(
        gemini=object(), kalshi=object(), fee_meta=object(), now_ms=456
    )
    assert candidate is not None
    assert candidate.maker_price == D("0.84")
    assert candidate.locked_profit == D("0.02")
    assert candidate.queue_ahead == D("16")


def test_join_trigger_keeps_candidate_unchanged(monkeypatch):
    trigger = _trigger_candidate("0.84")
    monkeypatch.setattr(venue, "_trigger_candidate_fn", lambda **kwargs: trigger)
    monkeypatch.setattr(base, "POLICY", "JOIN")
    assert venue.candidate_for_submission(
        gemini=object(), kalshi=object(), fee_meta=object(), now_ms=456
    ) is trigger


def test_maker_payload_uses_exact_state_machine_price_and_short_expiration(monkeypatch):
    before = int(time.time())
    payload = venue.maker_payload_with_expiry(
        ticker="KXXRP15M-TEST",
        direction="KALSHI_YES_GEMINI_NO",
        maker_price=D("0.84"),
        client_order_id="makerprobe-venue-test-k",
    )
    assert payload["post_only"] is True
    assert payload["price"] == "0.8400"
    assert payload["time_in_force"] == "good_till_canceled"
    assert before + venue.ORDER_TTL_S <= payload["expiration_time"] <= int(time.time()) + venue.ORDER_TTL_S


def test_install_replaces_runtime_hooks(monkeypatch):
    old_candidate = safe.candidate_from_books
    old_arm = safe._maybe_create_arm
    old_claim = safe._claim_before_order
    old_payload = safe.kalshi_maker_payload
    old_wait = base._wait_candidate
    try:
        venue.install()
        assert venue._trigger_candidate_fn is old_candidate
        assert safe.candidate_from_books is venue.candidate_for_submission
        assert safe._maybe_create_arm is venue.maybe_create_arm
        assert safe._claim_before_order is venue.claim_before_order
        assert safe.kalshi_maker_payload is venue.maker_payload_with_expiry
        assert base._wait_candidate is venue.wait_candidate_safe
    finally:
        safe.candidate_from_books = old_candidate
        safe._maybe_create_arm = old_arm
        safe._claim_before_order = old_claim
        safe.kalshi_maker_payload = old_payload
        base._wait_candidate = old_wait


def test_candidate_wait_timeout_returns_cleanly(monkeypatch):
    async def quiet_stream(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(base, "_gemini_stream", quiet_stream)
    monkeypatch.setattr(base, "_kalshi_stream", quiet_stream)
    pair = SimpleNamespace(gemini_symbol="g", kalshi_ticker="k")

    async def run_test():
        candidate, g, k, queue, tasks = await venue.wait_candidate_safe(
            pair=pair, fee_meta=object(), timeout_s=0.02
        )
        try:
            assert candidate is None
            assert g is None
            assert k is None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run_test())
