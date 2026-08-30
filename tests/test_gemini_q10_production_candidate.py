from decimal import Decimal as D
import asyncio

from src import gemini_q10_production_candidate as p


def test_client_ids_are_bounded_and_distinct():
    y = p.client_id('approval-12345678901234567890','YES')
    n = p.client_id('approval-12345678901234567890','NO')
    r = p.client_id('approval-12345678901234567890','NO',3)
    assert y != n != r
    assert len(y) <= 36 and len(n) <= 36 and len(r) <= 36


def test_disarmed_performs_no_account_or_venue_calls(monkeypatch, capsys):
    monkeypatch.delenv(p.LIVE_ENV, raising=False)
    monkeypatch.delenv(p.RUN_ID_ENV, raising=False)
    monkeypatch.delenv(p.ARM_ENV, raising=False)

    async def boom(*args, **kwargs):
        raise AssertionError('must not call venue/account while disarmed')

    monkeypatch.setattr(p.local, 'account_exposure_snapshot', boom)
    monkeypatch.setattr(p.probe, '_wait_candidate', boom)
    asyncio.run(p.run())
    out = capsys.readouterr().out
    assert 'DISARMED_NO_VENUE_CALLS' in out


def test_q10_constants_keep_hard_residual_bound():
    assert p.Q == D('10')
    assert p.risk.MAX_RESIDUAL == D('10')
    assert p.MAX_REPRICES == 6
    assert p.STOP_BEFORE_EXPIRY_SECONDS >= 20


def test_dynamic_completion_reprices_missing_side_and_pairs(monkeypatch):
    monkeypatch.setattr(p, 'REPRICE_INTERVAL_SECONDS', 0.0)
    posted = []
    cancelled = []

    async def fake_order(cid, symbol):
        if '-r1' in cid:
            return {'clientOrderId': cid, 'filledQuantity': '10'}
        return {'clientOrderId': cid, 'filledQuantity': '0'}

    async def fake_top(symbol):
        return D('0.48'), D('100'), D('0.61'), D('100')

    async def fake_cancel(maker, *, cid, symbol, row):
        cancelled.append(cid)
        return {'ok': True}, D('0'), 5.0

    async def fake_post(maker, *, symbol, outcome, price, cid):
        posted.append((outcome, price, cid))
        return {'status': 200}, {'clientOrderId': cid, 'filledQuantity': '0'}, 6.0

    monkeypatch.setattr(p.base, '_order_by_client', fake_order)
    monkeypatch.setattr(p.base, '_filled', lambda row: D(str((row or {}).get('filledQuantity') or '0')))
    monkeypatch.setattr(p.base, 'fresh_top', fake_top)
    monkeypatch.setattr(p, '_cancel', fake_cancel)
    monkeypatch.setattr(p, '_post', fake_post)

    class Event:
        symbol = 'GEMI-BTC05M-X-UP'
        price_increment = D('0.01')
        seconds_to_expiry = 180.0

    out = asyncio.run(p._dynamic_complete(
        object(), event=Event(), run_id='test-run', filled_outcome='YES',
        filled_price=D('0.48'), complement_id='q10p-test-run-no-i',
        complement_price=D('0.39'), complement_row={'filledQuantity': '0'},
    ))
    assert out['paired'] is True
    assert cancelled == ['q10p-test-run-no-i']
    assert posted and posted[0][0] == 'NO'
    assert posted[0][1] > D('0.39')
