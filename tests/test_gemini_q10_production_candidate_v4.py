import asyncio
from decimal import Decimal as D

from src import gemini_q10_production_candidate_v4 as v4


def test_disarmed_v4_does_not_create_depth_manager(monkeypatch):
    monkeypatch.delenv(v4.v1.LIVE_ENV, raising=False)
    monkeypatch.delenv(v4.v1.RUN_ID_ENV, raising=False)
    monkeypatch.delenv(v4.v1.ARM_ENV, raising=False)
    created={'value':False}
    class BadManager:
        def __init__(self):
            created['value']=True
            raise AssertionError('must not create prediction depth while disarmed')
    async def fake_v3_run():
        return None
    monkeypatch.setattr(v4, 'DepthManager', BadManager)
    monkeypatch.setattr(v4.v3, 'run', fake_v3_run)
    asyncio.run(v4.run())
    assert created['value'] is False


def test_armed_v4_injects_persistent_depth_and_restores(monkeypatch):
    monkeypatch.setenv(v4.v1.LIVE_ENV,'1')
    monkeypatch.setenv(v4.v1.RUN_ID_ENV,'run-x')
    monkeypatch.setenv(v4.v1.ARM_ENV,'run-x')
    observed={}
    original=v4.v1.base.fresh_top

    class FakeManager:
        async def top(self, symbol, timeout=4.0):
            observed['symbol']=symbol
            return D('0.48'),D('10'),D('0.61'),D('10')
        async def close(self):
            observed['closed']=True

    async def fake_v3_run():
        observed['top']=await v4.v1.base.fresh_top('GEMI-BTC05M-X-UP')

    monkeypatch.setattr(v4,'DepthManager',FakeManager)
    monkeypatch.setattr(v4.v3,'run',fake_v3_run)
    asyncio.run(v4.run())
    assert observed['symbol']=='GEMI-BTC05M-X-UP'
    assert observed['top'][0]==D('0.48')
    assert observed['closed'] is True
    assert v4.v1.base.fresh_top is original
