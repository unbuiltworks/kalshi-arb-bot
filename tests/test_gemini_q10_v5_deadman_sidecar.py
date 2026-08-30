import pytest

from src import gemini_q10_v5_deadman_sidecar as s


@pytest.mark.asyncio
async def test_sidecar_waits_without_open_campaign(monkeypatch):
    monkeypatch.setattr(s.v5.v1, '_read_state', lambda: None)
    result = await s.tick()
    assert result == {'status':'WAITING_FOR_OPEN_CAMPAIGN','intervened':False}


@pytest.mark.asyncio
async def test_sidecar_delegates_when_campaign_open(monkeypatch):
    monkeypatch.setattr(s.v5.v1, '_read_state', lambda: {'open':True,'status':'BOTH_POSTED'})
    async def check(): return {'status':'HEALTHY_HEARTBEAT','intervened':False}
    monkeypatch.setattr(s.deadman, 'check_once', check)
    result = await s.tick()
    assert result['status'] == 'HEALTHY_HEARTBEAT'


@pytest.mark.asyncio
async def test_sidecar_exits_after_stale_matched_pair_without_recovery(monkeypatch):
    calls = {'tick': 0, 'sleep': 0}
    async def tick():
        calls['tick'] += 1
        return {'status':'STALE_HEARTBEAT_NO_DIRECTIONAL_RESIDUAL','intervened':False}
    async def never_sleep(_):
        calls['sleep'] += 1
        raise AssertionError('sidecar should exit before sleeping again')
    monkeypatch.setattr(s, 'tick', tick)
    monkeypatch.setattr(s.asyncio, 'sleep', never_sleep)
    await s.run()
    assert calls == {'tick': 1, 'sleep': 0}
