import asyncio
from decimal import Decimal as D
import time
from pathlib import Path

import src.gemini_l5_inventory_shadow_v4 as v4
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


def test_online_v4_switches_from_directional_open_to_pair_completion_only():
    state = v4.OnlineMarketState(event())
    rows = state.on_book(D("0.64"), D("0.66"), "t0")
    plan = rows[-1]
    assert plan["tape"]["quote_side"] == "YES"
    assert plan["tape"]["quote_mode"] == "ACCUMULATE_Q1"

    fills = state.on_trade(D("0.64"), D("1"), True, "t1")
    assert len(fills) == 1
    assert fills[0]["role"] == "DIRECTIONAL_OPEN"
    assert fills[0]["ticker"] == state.event.ticker

    rows = state.on_book(D("0.39"), D("0.41"), "t2")
    plan = rows[-1]
    assert plan["tape"]["quote_side"] is None
    assert plan["tape"]["quote_mode"] == "WAIT_FOR_COMPLEMENT"
    assert plan["tape"]["residual_side"] == "YES"


def test_online_v4_emits_locked_edge_on_profitable_pair_completion():
    state = v4.OnlineMarketState(event())
    state.on_book(D("0.64"), D("0.66"), "t0")
    state.on_trade(D("0.64"), D("1"), True, "t1")
    rows = state.on_book(D("0.70"), D("0.72"), "t2")
    plan = rows[-1]
    no_quote = D(plan["tape"]["quote_price"])
    fills = state.on_trade(D("1") - no_quote, D("1"), False, "t3")
    assert len(fills) == 1
    assert fills[0]["role"] == "PAIR_COMPLETION"
    assert D(fills[0]["locked_edge_after_fees"]) >= D("0.03")
    assert state.market.tape.residual_side is None


def test_online_v4_summary_is_read_only_and_tracks_pair_economics():
    state = v4.OnlineMarketState(event())
    state.on_book(D("0.64"), D("0.66"), "t0")
    state.on_trade(D("0.64"), D("1"), True, "t1")
    state.on_book(D("0.70"), D("0.72"), "t2")
    no_quote = state.market.tape.quote_price
    assert no_quote is not None
    state.on_trade(D("1") - no_quote, D("1"), False, "t3")
    summary = v4.build_summary(
        stats={"errors": 0},
        states={state.event.symbol.upper(): state},
        seconds=60,
        discover_s=10,
        status="RUNNING",
    )
    assert summary["classification"] == "SHADOW_NOT_REALIZED"
    assert summary["read_only"] is True
    assert summary["order_entry"] is False
    assert summary["credentials_loaded"] is False
    assert summary["realized_pnl"] is None
    assert summary["tape_touch"]["completed_pairs"] == 1
    assert D(summary["tape_touch"]["locked_edge_total_after_fees"]) >= D("0.03")


def test_online_v4_reconnects_after_transient_websocket_failure(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(v4.websockets, "connect", fake_connect)
    monkeypatch.setattr(v4, "discover", fake_discover)

    output = tmp_path / "capture.jsonl"
    summary_path = tmp_path / "summary.json"
    summary = asyncio.run(
        v4.capture(
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
