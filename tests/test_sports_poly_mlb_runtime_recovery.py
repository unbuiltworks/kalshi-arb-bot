from __future__ import annotations

import asyncio
from datetime import timezone

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_structural_live_supervisor_recovered as recovered
from src.sports_poly_mlb_startup_recovery import _parse_ts, _trade_ts


class _SilentStream:
    healthy = True
    updates = 0

    def register_fallback(self, order_id, fallback):
        self.fallback = (order_id, dict(fallback))

    def seen(self, order_id):
        return False

    def get(self, order_id):
        return None

    async def wait_for_update(self, order_id, timeout):
        raise RuntimeError("private order websocket did not observe new order during bootstrap")

    async def wait_terminal(self, order_id, timeout):
        return None


def test_silent_ws_bootstrap_preserves_order_identity_and_falls_back_to_rest():
    original_refresh = v4._refresh_order
    original_cancel = v4._cancel_scoped
    calls = []

    async def authoritative_rest(client, oid, slug, fallback):
        calls.append((oid, slug))
        return {
            "id": oid,
            "marketSlug": slug,
            "state": "ORDER_STATE_OPEN",
            "cumQuantity": "0",
            "quantity": "1",
        }

    async def noop_cancel(client, oid, slug):
        return None

    v4._refresh_order = authoritative_rest
    v4._cancel_scoped = noop_cancel
    restore = recovered.install_recovered_private_order_transport(_SilentStream())
    patched = v4._refresh_order
    try:
        row = asyncio.run(
            patched(
                object(),
                "known-order-id",
                "astatc-mlb-bos-mia-test",
                {"id": "known-order-id", "state": "ORDER_STATE_OPEN", "cumQuantity": "0"},
            )
        )
        assert row["id"] == "known-order-id"
        assert row["cumQuantity"] == "0"
        assert calls == [("known-order-id", "astatc-mlb-bos-mia-test")]
    finally:
        restore()
        v4._refresh_order = original_refresh
        v4._cancel_scoped = original_cancel


def test_recovery_timestamp_parsing_is_timezone_aware():
    ts = _parse_ts("2026-08-24T22:42:11Z")
    assert ts is not None
    assert ts.tzinfo is not None
    assert ts.astimezone(timezone.utc).isoformat().startswith("2026-08-24T22:42:11")
    assert _trade_ts({"event_ts": "2026-08-24T22:42:11Z"}) == ts
