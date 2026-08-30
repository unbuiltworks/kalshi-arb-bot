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
