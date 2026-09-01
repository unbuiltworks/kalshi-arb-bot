import asyncio
import time
from decimal import Decimal as D

from src import gemini_l5_crypto5m_selector as m
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base


def event(asset: str, minutes: int = 5):
    return base.Event(
        ticker=f"{asset}05M2609011300",
        symbol=f"GEMI-{asset}05M2609011300-UP",
        expiry_ms=int(time.time() * 1000) + minutes * 60_000,
        quantity_minimum=D("1"),
        quantity_increment=D("1"),
        price_increment=D("0.01"),
    )


def test_asset_parser_and_allowlist():
    assert m.asset_from_ticker("ETH05M2609011300") == "ETH"
    assert m.asset_from_ticker("BTC05M2609011300") == "BTC"
    assert m.asset_from_ticker("ETH15M2609011300") is None
    assert m.normalize_assets(["eth", "BTC", "eth", "DOGE"]) == ("ETH", "BTC")


def test_select_best_candidate_prefers_more_edge(monkeypatch):
    events = {"ETH": event("ETH"), "BTC": event("BTC")}

    async def fake_discover(**_kwargs):
        return events

    async def fake_top(symbol, attempts, delay_s):
        if "ETH" in symbol:
            return D("0.48"), D("500"), D("0.52"), D("500")
        return D("0.45"), D("900"), D("0.55"), D("900")

    def fake_choose(bid, ask, **_kwargs):
        edge = D("0.02") if bid == D("0.48") else D("0.05")
        return {"yes": bid, "no": D("1") - ask, "edge": edge,
                "yes_backoff": D("0"), "no_backoff": D("0"),
                "total_backoff": D("0"), "preferred_backoff_side": "YES",
                "preferred_backoff": D("0"), "completion_reserve_ticks": 1}

    monkeypatch.setattr(m, "discover_nearest_events", fake_discover)
    monkeypatch.setattr(m.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m.pair, "choose_pair", fake_choose)

    out = asyncio.run(m.select_best_candidate())
    assert out is not None
    assert out.asset == "BTC"
    assert out.edge == D("0.05")


def test_select_best_candidate_uses_queue_as_edge_tiebreak(monkeypatch):
    events = {"ETH": event("ETH"), "SOL": event("SOL")}

    async def fake_discover(**_kwargs):
        return events

    async def fake_top(symbol, attempts, delay_s):
        size = D("200") if "SOL" in symbol else D("700")
        return D("0.47"), size, D("0.53"), size

    def fake_choose(bid, ask, **_kwargs):
        return {"yes": bid, "no": D("1") - ask, "edge": D("0.03"),
                "yes_backoff": D("0"), "no_backoff": D("0"),
                "total_backoff": D("0"), "preferred_backoff_side": "YES",
                "preferred_backoff": D("0"), "completion_reserve_ticks": 1}

    monkeypatch.setattr(m, "discover_nearest_events", fake_discover)
    monkeypatch.setattr(m.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m.pair, "choose_pair", fake_choose)

    out = asyncio.run(m.select_best_candidate())
    assert out is not None
    assert out.asset == "SOL"


def test_select_best_candidate_skips_unsafe_models(monkeypatch):
    events = {"ETH": event("ETH")}

    async def fake_discover(**_kwargs):
        return events

    async def fake_top(symbol, attempts, delay_s):
        return D("0.85"), D("1"), D("0.90"), D("1")

    monkeypatch.setattr(m, "discover_nearest_events", fake_discover)
    monkeypatch.setattr(m.ladder, "fresh_top_retry", fake_top)
    monkeypatch.setattr(m.pair, "choose_pair", lambda *_args, **_kwargs: None)
    assert asyncio.run(m.select_best_candidate()) is None
