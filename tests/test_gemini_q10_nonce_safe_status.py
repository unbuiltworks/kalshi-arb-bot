from decimal import Decimal as D
import asyncio

import pytest

from src import gemini_q10_production_candidate_v2 as v2


class FakeMaker:
    def __init__(self):
        self.events = asyncio.Queue()


@pytest.mark.asyncio
async def test_wait_fill_event_preserves_unrelated_events():
    maker = FakeMaker()
    await maker.events.put({'c':'other','Z':'10','X':'FILLED'})
    await maker.events.put({'c':'target','Z':'10','X':'FILLED'})
    fill = await v2._wait_fill_event(maker, 'target', 0.05)
    assert fill == D('10')
    leftover = await maker.events.get()
    assert leftover['c'] == 'other'


@pytest.mark.asyncio
async def test_dynamic_complement_pairs_from_ws_without_rest_poll(monkeypatch):
    maker = FakeMaker()
    await maker.events.put({'c':'no0','Z':'10','X':'FILLED'})

    async def rest_must_not_run(*args, **kwargs):
        raise AssertionError('hot path must not REST poll before WS fill')

    monkeypatch.setattr(v2.v1.base, '_order_by_client', rest_must_not_run)

    class Event:
        symbol='GEMI-BTC05M-X-UP'
        price_increment=D('0.01')
        seconds_to_expiry=180.0

    out = await v2._dynamic_complete_v2(
        maker, event=Event(), run_id='x', filled_outcome='YES', filled_price=D('0.32'),
        complement_id='no0', complement_price=D('0.62'),
        complement_row={'orderId':'123','filledQuantity':'0'},
    )
    assert out['paired'] is True
    assert out['fill_source'] == 'WS'


@pytest.mark.asyncio
async def test_cancel_known_uses_known_row_and_ws_race_fill(monkeypatch):
    maker = FakeMaker()
    await maker.events.put({'c':'no0','Z':'10','X':'FILLED'})
    calls=[]

    async def fake_cancel(maker_obj, *, row):
        calls.append(row['orderId'])
        return {'method':'WS_ORDER_CANCEL','ok':True}

    monkeypatch.setattr(v2.v1.base, '_cancel_maker', fake_cancel)
    result, fill, _ = await v2._cancel_known(
        maker, cid='no0', symbol='GEMI-BTC05M-X-UP',
        row={'orderId':'123','clientOrderId':'no0'},
    )
    assert result['ok'] is True
    assert calls == ['123']
    assert fill == D('10')


@pytest.mark.asyncio
async def test_cancel_known_fails_closed_without_order_id(monkeypatch):
    maker = FakeMaker()

    async def must_not_cancel(*args, **kwargs):
        raise AssertionError('must not session-cancel when order id is unknown')

    monkeypatch.setattr(v2.v1.base, '_cancel_maker', must_not_cancel)
    result, fill, ms = await v2._cancel_known(
        maker, cid='no0', symbol='GEMI-BTC05M-X-UP', row={'clientOrderId':'no0'},
    )
    assert result['ok'] is False
    assert fill == D('0')
    assert ms == 0.0