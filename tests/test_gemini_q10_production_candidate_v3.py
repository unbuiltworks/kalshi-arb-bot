import asyncio
from decimal import Decimal as D

from src import gemini_q10_production_candidate_v3 as v3


def test_disarmed_v3_never_opens_public_socket(monkeypatch):
    monkeypatch.delenv(v3.v1.LIVE_ENV, raising=False)
    monkeypatch.delenv(v3.v1.RUN_ID_ENV, raising=False)
    monkeypatch.delenv(v3.v1.ARM_ENV, raising=False)
    opened = {'value':False}

    async def bad_start(self, **kwargs):
        opened['value'] = True
        raise AssertionError('must not start websocket while disarmed')

    monkeypatch.setattr(v3.GeminiSpotBookTicker, 'start', bad_start)
    asyncio.run(v3.run())
    assert opened['value'] is False


def test_armed_v3_injects_websocket_midpoint(monkeypatch):
    monkeypatch.setenv(v3.v1.LIVE_ENV, '1')
    monkeypatch.setenv(v3.v1.RUN_ID_ENV, 'run-x')
    monkeypatch.setenv(v3.v1.ARM_ENV, 'run-x')
    observed = {}

    class FakeFeed:
        async def start(self, **kwargs):
            observed['started'] = True
        def latest(self):
            return D('78000.5')
        async def close(self):
            observed['closed'] = True

    original = v3.v1._spot

    async def fake_v2_run():
        observed['spot'] = await v3.v1._spot(None)

    monkeypatch.setattr(v3, 'GeminiSpotBookTicker', FakeFeed)
    monkeypatch.setattr(v3.v2, 'run', fake_v2_run)
    asyncio.run(v3.run())
    assert observed == {'started':True,'spot':D('78000.5'),'closed':True}
    assert v3.v1._spot is original
