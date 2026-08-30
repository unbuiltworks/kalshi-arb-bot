from decimal import Decimal as D
import asyncio

import pytest

from src import gemini_q10_production_candidate_v2 as v2


def test_durable_one_use_state_is_exclusive(tmp_path):
    p = tmp_path / 'state.json'
    v2._write_once(p, {'run_id':'x','status':'one'})
    with pytest.raises(FileExistsError):
        v2._write_once(p, {'run_id':'x','status':'two'})


def test_disarmed_v2_delegates_to_zero_call_v1(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(v2.v1.LIVE_ENV, raising=False)
    monkeypatch.delenv(v2.v1.RUN_ID_ENV, raising=False)
    monkeypatch.delenv(v2.v1.ARM_ENV, raising=False)
    monkeypatch.setenv(v2.STATE_DIR_ENV, str(tmp_path))

    async def boom(*args, **kwargs):
        raise AssertionError('disarmed must not call account or venue')

    monkeypatch.setattr(v2.v1.local, 'account_exposure_snapshot', boom)
    asyncio.run(v2.run())
    assert 'DISARMED_NO_VENUE_CALLS' in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_pre_cancel_missing_depth_keeps_live_complement(monkeypatch):
    monkeypatch.setattr(v2.v1, 'REPRICE_INTERVAL_SECONDS', 0.0)
    cancels = []
    top_calls = {'n':0}

    async def fake_order(cid, symbol):
        return {'clientOrderId':cid,'filledQuantity':'10' if '-r1' in cid else '0'}

    async def fake_top(symbol):
        top_calls['n'] += 1
        if top_calls['n'] == 1:
            raise RuntimeError('no fresh Gemini BTC5m depth')
        return D('0.48'), D('100'), D('0.61'), D('100')

    async def fake_cancel(maker, *, cid, symbol, row):
        cancels.append(cid)
        return {'ok':True}, D('0'), 2.0

    posts = []
    async def fake_post(maker, *, symbol, outcome, price, cid):
        posts.append((outcome,price,cid))
        return {'status':200}, {'clientOrderId':cid,'filledQuantity':'0'}, 3.0

    monkeypatch.setattr(v2.v1.base, '_order_by_client', fake_order)
    monkeypatch.setattr(v2.v1.base, '_filled', lambda r: D(str((r or {}).get('filledQuantity') or 0)))
    monkeypatch.setattr(v2, '_fresh_top_or_none', fake_top)
    monkeypatch.setattr(v2.v1, '_cancel', fake_cancel)
    monkeypatch.setattr(v2.v1, '_post', fake_post)
    monkeypatch.setattr(v2.v1, 'emit', lambda *args, **kwargs: None)

    class Event:
        symbol='GEMI-BTC05M-X-UP'
        price_increment=D('0.01')
        seconds_to_expiry=180.0

    task = asyncio.create_task(v2._dynamic_complete_v2(
        object(), event=Event(), run_id='x', filled_outcome='YES', filled_price=D('0.48'),
        complement_id='no0', complement_price=D('0.39'), complement_row={'filledQuantity':'0'},
    ))
    out = await asyncio.wait_for(task, timeout=1.0)
    assert out['paired'] is True
    # Missing depth on first check did not cancel. The later fresh book did.
    assert cancels == ['no0']
    assert posts


@pytest.mark.asyncio
async def test_post_cancel_missing_depth_never_blind_reposts(monkeypatch):
    monkeypatch.setattr(v2.v1, 'REPRICE_INTERVAL_SECONDS', 0.0)
    monkeypatch.setattr(v2.v1, 'STOP_BEFORE_EXPIRY_SECONDS', 20.0)
    calls = {'top':0}
    cancels=[]; posts=[]

    async def fake_order(cid, symbol):
        return {'clientOrderId':cid,'filledQuantity':'0'}

    async def fake_top(symbol):
        calls['top'] += 1
        if calls['top'] == 1:
            return D('0.48'), D('100'), D('0.61'), D('100')
        raise RuntimeError('no fresh Gemini BTC5m depth')

    async def fake_cancel(maker, *, cid, symbol, row):
        cancels.append(cid); return {'ok':True}, D('0'), 2.0

    async def fake_post(*args, **kwargs):
        posts.append(kwargs); return {'status':200}, {}, 3.0

    async def no_sleep(_): return None
    monkeypatch.setattr(v2.v1.base, '_order_by_client', fake_order)
    monkeypatch.setattr(v2.v1.base, '_filled', lambda r: D('0'))
    monkeypatch.setattr(v2, '_fresh_top_or_none', fake_top)
    monkeypatch.setattr(v2.v1, '_cancel', fake_cancel)
    monkeypatch.setattr(v2.v1, '_post', fake_post)
    monkeypatch.setattr(v2.v1, 'emit', lambda *args, **kwargs: None)
    monkeypatch.setattr(v2.asyncio, 'sleep', no_sleep)

    class Event:
        symbol='GEMI-BTC05M-X-UP'; price_increment=D('0.01')
        @property
        def seconds_to_expiry(self):
            return 21.0 if calls['top'] < 2 else 19.0

    out = await v2._dynamic_complete_v2(
        object(), event=Event(), run_id='x', filled_outcome='YES', filled_price=D('0.48'),
        complement_id='no0', complement_price=D('0.39'), complement_row={'filledQuantity':'0'},
    )
    assert cancels == ['no0']
    assert posts == []
    assert out['paired'] is False
