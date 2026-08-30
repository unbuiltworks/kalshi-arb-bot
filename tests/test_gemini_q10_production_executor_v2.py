import asyncio
from dataclasses import dataclass
from decimal import Decimal as D

from src import gemini_q10_production_executor_v2 as v2
from src.btc_spot_toxicity_guard import SpotDecision
from src.gemini_prediction_book_cache import BookSnapshot


@dataclass
class Event:
    symbol: str = "GEMI-BTC05M-TEST-UP"


class Guard:
    def decision(self):
        return SpotDecision(True, False, False, D("80000"), 10.0, D("0.1"), 3, "CALM")


class FakeCache:
    def __init__(self, symbol, *, stale_ms=750):
        self.symbol = symbol
        self.stale_ms = stale_ms
        self._snap = BookSnapshot(D("0.41"), D("100"), D("0.54"), D("120"), 5.0, 1, 2)

    async def run(self, stop):
        await stop.wait()

    async def wait_fresh(self, *, timeout=2.0, max_age_ms=None):
        return self._snap

    def snapshot(self):
        return self._snap


def test_hot_campaign_routes_post_candidate_reads_through_cache(monkeypatch):
    async def scenario():
        event = Event()
        original_top_called = {"n": 0}

        async def original_wait_candidate():
            return event, {"event": {}}, {"locked_edge": D("1")}

        async def original_top(symbol, timeout=4.0):
            original_top_called["n"] += 1
            return D("0.10"), D("1"), D("0.90"), D("1")

        async def fake_post(*args, **kwargs):
            return {"status": 200}, {"clientOrderId": kwargs.get("client_id")}, 1.25

        async def fake_run_campaign(*, guard, campaign_no):
            found = await v2.v1.probe._wait_candidate()
            assert found[0].symbol == event.symbol
            top = await v2.v1.base.fresh_top(event.symbol)
            assert top == (D("0.41"), D("100"), D("0.54"), D("120"))
            decision = guard.decision()
            assert decision.ready and not decision.dangerous
            ack, _, _ = await v2.v1._post(
                object(), symbol=event.symbol, outcome="YES", price=D("0.41"), quantity=D("10"), client_id="test-hot"
            )
            assert ack["status"] == 200
            return {"status": "OK", "campaign_no": campaign_no}

        monkeypatch.setattr(v2, "GeminiTopCache", FakeCache)
        monkeypatch.setattr(v2.v1.probe, "_wait_candidate", original_wait_candidate)
        monkeypatch.setattr(v2.v1.base, "fresh_top", original_top)
        monkeypatch.setattr(v2.v1, "_post", fake_post)
        monkeypatch.setattr(v2.v1, "_run_campaign", fake_run_campaign)

        out = await v2._run_campaign_hot(guard=Guard(), campaign_no=1)
        assert out["status"] == "OK"
        assert original_top_called["n"] == 0

    asyncio.run(scenario())


def test_hot_campaign_marks_stale_prediction_book_dangerous(monkeypatch):
    class StaleCache(FakeCache):
        def snapshot(self):
            return BookSnapshot(D("0.41"), D("100"), D("0.54"), D("120"), 900.0, 1, 2)

    async def scenario():
        event = Event()

        async def original_wait_candidate():
            return event, {"event": {}}, {"locked_edge": D("1")}

        async def fake_run_campaign(*, guard, campaign_no):
            await v2.v1.probe._wait_candidate()
            decision = guard.decision()
            assert decision.dangerous is True
            assert decision.ready is False
            assert decision.reason == "GEMINI_BOOK_STALE"
            return {"status": "STALE_BLOCKED"}

        monkeypatch.setattr(v2, "GeminiTopCache", StaleCache)
        monkeypatch.setattr(v2.v1.probe, "_wait_candidate", original_wait_candidate)
        monkeypatch.setattr(v2.v1, "_run_campaign", fake_run_campaign)
        out = await v2._run_campaign_hot(guard=Guard(), campaign_no=1)
        assert out["status"] == "STALE_BLOCKED"

    asyncio.run(scenario())
