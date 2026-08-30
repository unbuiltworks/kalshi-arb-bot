import asyncio
from dataclasses import dataclass
from decimal import Decimal as D

import pytest

from src import gemini_q10_production_executor as p


@dataclass
class FakeEvent:
    symbol: str = 'GEMI-BTC05M-TEST-UP'
    price_increment: D = D('0.01')
    seconds_to_expiry: float = 180.0


class FakeMaker:
    pass


def test_live_arm_requires_dual_key(monkeypatch):
    monkeypatch.setenv(p.LIVE_ENV, 'YES')
    monkeypatch.setenv(p.ARM_ID_ENV, 'abc')
    monkeypatch.setenv(p.ARM_ENV, 'wrong')
    assert not p.live_armed()
    monkeypatch.setenv(p.ARM_ENV, 'abc')
    assert p.live_armed()


def test_continuous_mode_requires_separate_flag(monkeypatch):
    monkeypatch.setenv(p.LIVE_ENV, 'YES')
    monkeypatch.setenv(p.ARM_ID_ENV, 'abc')
    monkeypatch.setenv(p.ARM_ENV, 'abc')
    monkeypatch.delenv(p.CONTINUOUS_ENV, raising=False)
    assert not p.continuous_enabled()
    monkeypatch.setenv(p.CONTINUOUS_ENV, 'YES')
    assert p.continuous_enabled()


def test_non_continuous_mode_forbids_multiple_campaigns(monkeypatch):
    monkeypatch.setenv(p.LIVE_ENV, 'YES')
    monkeypatch.setenv(p.ARM_ID_ENV, 'abc')
    monkeypatch.setenv(p.ARM_ENV, 'abc')
    monkeypatch.delenv(p.CONTINUOUS_ENV, raising=False)
    monkeypatch.setenv(p.MAX_CAMPAIGNS_ENV, '2')
    with pytest.raises(RuntimeError):
        p._max_campaigns()


def test_entry_model_enforces_q10_one_sided_premium_cap():
    good = {
        'locked_edge': D('0.60'),
        'max_one_sided_premium': D('4.80'),
        'yes_quote': D('0.48'),
        'no_quote': D('0.45'),
    }
    assert p._entry_model_ok(good)
    bad = dict(good, max_one_sided_premium=D('5.10'))
    assert not p._entry_model_ok(bad)


@pytest.mark.asyncio
async def test_exact_partial_residual_quantity_is_used(monkeypatch):
    event = FakeEvent()
    posted = []
    rows = {}

    async def fake_post(maker, *, symbol, outcome, price, quantity, client_id):
        posted.append((outcome, price, quantity, client_id))
        row = {'filledQuantity': str(quantity)}
        rows[client_id] = row
        return {'status': 200}, row, 1.0

    async def fake_order_by_client(client_id, symbol):
        return rows.get(client_id)

    async def fake_fresh_top(symbol):
        return D('0.48'), D('100'), D('0.61'), D('100')

    monkeypatch.setattr(p, '_post', fake_post)
    monkeypatch.setattr(p.base, '_order_by_client', fake_order_by_client)
    monkeypatch.setattr(p.base, 'fresh_top', fake_fresh_top)

    result = await p._complete_residual(
        FakeMaker(), event=event, run_id='partial', filled_outcome='YES',
        filled_price=D('0.48'), quantity=D('4'), starting_price=D('0.39'),
    )
    assert result['paired'] is True
    assert posted
    assert posted[0][2] == D('4')
    assert all(x[2] <= D('4') for x in posted)


@pytest.mark.asyncio
async def test_rejected_completion_is_hard_halt(monkeypatch):
    event = FakeEvent()

    async def fake_post(maker, *, symbol, outcome, price, quantity, client_id):
        return {'status': 400}, None, 1.0

    async def fake_fresh_top(symbol):
        return D('0.48'), D('100'), D('0.61'), D('100')

    monkeypatch.setattr(p, '_post', fake_post)
    monkeypatch.setattr(p.base, 'fresh_top', fake_fresh_top)

    result = await p._complete_residual(
        FakeMaker(), event=event, run_id='reject', filled_outcome='YES',
        filled_price=D('0.48'), quantity=D('10'), starting_price=D('0.39'),
    )
    assert result['paired'] is False
    assert result['hard_halt'] is True
    assert 'REJECTED' in result['status']


@pytest.mark.asyncio
async def test_disarmed_run_never_starts_spot_or_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv(p.STATE_ENV, str(tmp_path))
    monkeypatch.delenv(p.LIVE_ENV, raising=False)
    calls = {'preflight': 0}

    async def fake_preflight():
        calls['preflight'] += 1
        return D('50'), {'orders': 0, 'positions': 0}

    monkeypatch.setattr(p, '_preflight_flat', fake_preflight)
    await p.run()
    assert calls['preflight'] == 1
    assert not p._state_file().exists()


@pytest.mark.asyncio
async def test_open_durable_state_blocks_any_new_live_work(monkeypatch, tmp_path):
    monkeypatch.setenv(p.STATE_ENV, str(tmp_path))
    p._write_state({'open': True, 'status': 'MATCHED_NOT_SETTLED'})
    monkeypatch.setenv(p.LIVE_ENV, 'YES')
    monkeypatch.setenv(p.ARM_ID_ENV, 'abc')
    monkeypatch.setenv(p.ARM_ENV, 'abc')

    async def should_not_run():
        raise AssertionError('preflight must not run when durable state is unresolved')

    monkeypatch.setattr(p, '_preflight_flat', should_not_run)
    await p.run()
