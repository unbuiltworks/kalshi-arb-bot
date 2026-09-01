import asyncio
from decimal import Decimal as D
import json
from pathlib import Path
import time

import src.gemini_l5_tape_dynamic_shadow_v3 as v3
from src.gemini_l5_tape_dynamic_shadow import MarketState
from src.gemini_l5_threshold_shadow import Event


def event() -> Event:
    return Event(
        asset="BTC",
        horizon="5m",
        ticker="BTC05M9999999999",
        symbol="GEMI-BTC05M9999999999-UP",
        expiry_ms=int(time.time() * 1000) + 300_000,
        price_increment=D("0.01"),
    )


def test_fill_rows_are_enriched_with_contract_identity():
    state = MarketState(event())
    row = {
        "type": "SHADOW_FILL",
        "ledger": "STRICT_TRADE_THROUGH",
        "side": "YES",
        "price": "0.61",
        "quantity": "1",
    }
    enriched = v3.enrich_market_row(row, state)
    assert enriched["ticker"] == state.event.ticker
    assert enriched["symbol"] == state.event.symbol
    assert enriched["asset"] == "BTC"
    assert enriched["horizon"] == "5m"


def test_checkpoint_is_atomic_and_preserves_shadow_classification(tmp_path: Path):
    state = MarketState(event())
    summary = v3.build_summary(
        stats={"errors": 0},
        states={state.event.symbol.upper(): state},
        seconds=60,
        discover_s=10,
        status="RUNNING",
    )
    path = tmp_path / "summary.json"
    v3.write_checkpoint(path, summary)
    saved = json.loads(path.read_text())
    assert saved["status"] == "RUNNING"
    assert saved["classification"] == "SHADOW_NOT_REALIZED"
    assert saved["order_entry"] is False
    assert saved["credentials_loaded"] is False
    assert saved["markets_detail"][0]["symbol"] == state.event.symbol
    assert not (tmp_path / "summary.json.tmp").exists()


def test_capture_retries_after_transient_websocket_failure(monkeypatch, tmp_path: Path):
    attempts = {"count": 0}

    class FakeWS:
        async def send(self, _payload):
            return None

        async def recv(self):
            await asyncio.sleep(1)
            return "{}"

    class FakeConnect:
        async def __aenter__(self):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("simulated service restart")
            return FakeWS()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_connect(*args, **kwargs):
        return FakeConnect()

    async def fake_discover(_http):
        return {}

    monkeypatch.setattr(v3.websockets, "connect", fake_connect)
    monkeypatch.setattr(v3, "discover", fake_discover)

    output = tmp_path / "capture.jsonl"
    summary_path = tmp_path / "summary.json"
    summary = asyncio.run(
        v3.capture(
            seconds=0.18,
            discover_s=0.01,
            output=output,
            summary_output=summary_path,
            checkpoint_s=0.01,
            reconnect_s=0.01,
        )
    )
    assert attempts["count"] >= 2
    assert summary["status"] == "COMPLETED"
    assert summary["ws_disconnects"] >= 1
    assert summary["ws_connections"] >= 1
    assert summary_path.exists()
    text = output.read_text()
    assert "WS_DISCONNECTED_RETRYING" in text
    assert "WS_CONNECTED" in text
