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


def test_improve_trigger_submits_one_tick_safer(monkeypatch):
    old_policy = base.POLICY
    try:
        base.POLICY = "IMPROVE_1T"
        assert venue.outbound_maker_price(D("0.61")) == D("0.60")
    finally:
        base.POLICY = old_policy


def test_join_trigger_keeps_same_price(monkeypatch):
    old_policy = base.POLICY
    try:
        base.POLICY = "JOIN"
        assert venue.outbound_maker_price(D("0.61")) == D("0.61")
    finally:
        base.POLICY = old_policy


def test_maker_payload_has_short_exchange_side_expiration(monkeypatch):
    old_policy = base.POLICY
    try:
        base.POLICY = "JOIN"
        before = int(time.time())
        payload = venue.maker_payload_with_expiry(
            ticker="KXXRP15M-TEST",
            direction="KALSHI_YES_GEMINI_NO",
            maker_price=D("0.61"),
            client_order_id="makerprobe-venue-test-k",
        )
        assert payload["post_only"] is True
        assert payload["time_in_force"] == "good_till_canceled"
        assert before + venue.ORDER_TTL_S <= payload["expiration_time"] <= int(time.time()) + venue.ORDER_TTL_S
    finally:
        base.POLICY = old_policy


def test_install_replaces_only_runtime_hooks(monkeypatch):
    old_arm = safe._maybe_create_arm
    old_claim = safe._claim_before_order
    old_payload = safe.kalshi_maker_payload
    old_wait = base._wait_candidate
    try:
        venue.install()
        assert safe._maybe_create_arm is venue.maybe_create_arm
        assert safe._claim_before_order is venue.claim_before_order
        assert safe.kalshi_maker_payload is venue.maker_payload_with_expiry
        assert base._wait_candidate is venue.wait_candidate_safe
    finally:
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
