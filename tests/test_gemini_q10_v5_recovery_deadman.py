from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from src import gemini_q10_v5_recovery as r
from src import gemini_q10_v5_deadman as d
from src import gemini_q10_v5_runtime as runtime


def audit_obj(**kw):
    base = dict(
        status='OPEN_STATE_EXACT_RESIDUAL', open_state=True,
        symbol='GEMI-BTC05M-TEST-UP', yes_fill=D('10'), no_fill=D('0'),
        residual_outcome='YES', residual_quantity=D('10'), exact_residual=True,
        active_known_orders=0, account_positions=1, account_orders=0, reason=None,
    )
    base.update(kw)
    return r.RecoveryAudit(**base)


def test_recovery_requires_independent_arm(monkeypatch):
    monkeypatch.setenv(r.v5.v1.LIVE_ENV, 'YES')
    monkeypatch.setenv(r.v5.v1.ARM_ID_ENV, 'abc')
    monkeypatch.setenv(r.v5.v1.ARM_ENV, 'abc')
    monkeypatch.delenv(r.RECOVERY_ARM_ENV, raising=False)
    assert r.v5.live_armed()
    assert not r.recovery_armed()
    monkeypatch.setenv(r.RECOVERY_ARM_ENV, 'abc')
    assert r.recovery_armed()


@pytest.mark.asyncio
async def test_audit_classifies_pure_one_sided_fill_exact(monkeypatch):
    state = {
        'open': True, 'status': 'RESTING', 'symbol': 'GEMI-BTC05M-TEST-UP',
        'trade_id': 't', 'yes_client_order_id': 'y', 'no_client_order_id': 'n',
        'yes_quote': '0.48', 'no_quote': '0.40', 'price_increment': '0.01',
    }
    monkeypatch.setattr(r.v5.v1, '_read_state', lambda: state)

    async def flat():
        return {'orders': 0, 'positions': 1, 'order_symbols': [], 'position_symbols': ['GEMI-BTC05M-TEST-UP']}
    async def reconcile(*args):
        return {'filledQuantity':'10'}, {'filledQuantity':'0'}, D('10'), D('0')
    monkeypatch.setattr(r.v5.v1, '_flat_snapshot', flat)
    monkeypatch.setattr(r.v5.v1.probe, '_reconcile', reconcile)
    report, _, _, _ = await r.audit()
    assert report.exact_residual is True
    assert report.residual_outcome == 'YES'
    assert report.residual_quantity == D('10')


@pytest.mark.asyncio
async def test_audit_refuses_unequal_two_sided_fill_as_ambiguous(monkeypatch):
    state = {
        'open': True, 'status': 'RESTING', 'symbol': 'GEMI-BTC05M-TEST-UP',
        'trade_id': 't', 'yes_client_order_id': 'y', 'no_client_order_id': 'n',
    }
    monkeypatch.setattr(r.v5.v1, '_read_state', lambda: state)
    async def flat():
        return {'orders': 0, 'positions': 2, 'order_symbols': [], 'position_symbols': []}
    async def reconcile(*args):
        return {'filledQuantity':'10'}, {'filledQuantity':'3'}, D('10'), D('3')
    monkeypatch.setattr(r.v5.v1, '_flat_snapshot', flat)
    monkeypatch.setattr(r.v5.v1.probe, '_reconcile', reconcile)
    report, _, _, _ = await r.audit()
    assert report.exact_residual is False
    assert report.status == 'OPEN_STATE_AMBIGUOUS_RESIDUAL'


@pytest.mark.asyncio
async def test_recover_not_armed_never_connects_mutation_session(monkeypatch):
    report = audit_obj()
    state = {'open': True, 'status':'RESTING'}
    async def fake_audit():
        return report, state, None, None
    monkeypatch.setattr(r, 'audit', fake_audit)
    monkeypatch.setattr(r, 'recovery_armed', lambda: False)

    class NeverMaker:
        def __init__(self):
            raise AssertionError('must not create maker session while recovery disarmed')
    monkeypatch.setattr(r.v5.v1.base, 'GeminiMakerSession', NeverMaker)
    result = await r.recover()
    assert result['status'] == 'RECOVERY_REQUIRED_BUT_NOT_ARMED'
    assert result['order_entry'] is False


@pytest.mark.asyncio
async def test_exact_recovery_unwinds_and_closes_state(monkeypatch):
    state = {
        'open': True, 'status': 'RESTING', 'symbol': 'GEMI-BTC05M-TEST-UP',
        'trade_id': 't', 'yes_client_order_id': 'y', 'no_client_order_id': 'n',
        'yes_quote': '0.48', 'no_quote': '0.40', 'price_increment': '0.01',
    }
    report = audit_obj()
    yrow = {'filledQuantity':'10', 'avgExecutionPrice':'0.48', 'status':'FILLED'}
    nrow = {'filledQuantity':'0', 'status':'CANCELLED'}
    async def fake_audit():
        return report, state, yrow, nrow
    monkeypatch.setattr(r, 'audit', fake_audit)
    monkeypatch.setattr(r, 'recovery_armed', lambda: True)

    class Maker:
        async def connect(self): pass
        async def close(self): pass
    monkeypatch.setattr(r.v5.v1.base, 'GeminiMakerSession', Maker)

    async def reconcile(*args):
        return yrow, nrow, D('10'), D('0')
    monkeypatch.setattr(r.v5.v1.probe, '_reconcile', reconcile)

    class Cache:
        def __init__(self, *args, **kwargs): pass
        async def run(self, stop):
            await stop.wait()
        async def wait_fresh(self, **kwargs): return object()
    monkeypatch.setattr(r, 'GeminiTopCache', Cache)

    async def fok(*args, **kwargs):
        return {'status':'RESCUE_UNWIND_FILLED','flattened':True,'hard_halt':False,'route':{'action':'UNWIND'}}
    monkeypatch.setattr(r.v5, '_bounded_fok', fok)
    async def flat():
        return {'orders':0,'positions':0,'order_symbols':[],'position_symbols':[]}
    monkeypatch.setattr(r.v5.v1, '_flat_snapshot', flat)
    written = []
    monkeypatch.setattr(r.v5.v1, '_write_state', lambda s: written.append(dict(s)))

    result = await r.recover()
    assert result['status'] == 'RECOVERY_UNWIND_FLAT'
    assert written[-1]['open'] is False


def test_deadman_heartbeat_age():
    now = datetime.now(timezone.utc)
    state = {'heartbeat_at_utc': (now - timedelta(seconds=2)).isoformat()}
    age = d.heartbeat_age_seconds(state, now=now)
    assert 1.99 <= age <= 2.01


@pytest.mark.asyncio
async def test_deadman_fresh_heartbeat_does_not_recover(monkeypatch):
    state = {'open':True, 'status':'RESTING', 'heartbeat_at_utc':datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(d.v5.v1, '_read_state', lambda: state)
    async def never():
        raise AssertionError('recovery must not run with fresh heartbeat')
    monkeypatch.setattr(d.recovery, 'recover', never)
    result = await d.check_once()
    assert result['status'] == 'HEALTHY_HEARTBEAT'
    assert result['intervened'] is False


@pytest.mark.asyncio
async def test_deadman_stale_armed_invokes_recovery(monkeypatch):
    state = {'open':True, 'status':'RESTING', 'heartbeat_at_utc':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()}
    monkeypatch.setattr(d.v5.v1, '_read_state', lambda: state)
    monkeypatch.setattr(d, 'watchdog_armed', lambda: True)
    monkeypatch.setattr(d.recovery, 'recovery_armed', lambda: True)
    async def recover(): return {'status':'RECOVERED_FLAT'}
    monkeypatch.setattr(d.recovery, 'recover', recover)
    result = await d.check_once()
    assert result['status'] == 'DEADMAN_INTERVENTION'
    assert result['intervened'] is True


@pytest.mark.asyncio
async def test_runtime_never_starts_new_campaign_when_open_state_exists(monkeypatch):
    async def audit(): return audit_obj(), {'open':True}, None, None
    async def recover(): return {'status':'RECOVERED_FLAT'}
    async def never_v5(): raise AssertionError('V5 must not start fresh campaign after recovery')
    monkeypatch.setattr(runtime.recovery, 'audit', audit)
    monkeypatch.setattr(runtime.recovery, 'recover', recover)
    monkeypatch.setattr(runtime.v5, 'run', never_v5)
    result = await runtime.run()
    assert result['status'] == 'RECOVERED_FLAT'
