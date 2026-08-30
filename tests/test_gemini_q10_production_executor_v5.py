from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import gemini_q10_production_executor_v5 as p
from src.gemini_prediction_book_cache import BookSnapshot


class FakeCache:
    def __init__(self, snap):
        self.snap = snap

    async def wait_fresh(self, **kwargs):
        return self.snap


class ErrorCache:
    async def wait_fresh(self, **kwargs):
        raise TimeoutError('synthetic stale book')


class FakeMaker:
    def __init__(self):
        self.calls = []
        self.events = None

    async def request(self, method, params, timeout=3.0):
        self.calls.append((method, params, timeout))
        return {'status': 200}


def snap(bid='0.48', bid_size='100', ask='0.61', ask_size='100'):
    return BookSnapshot(
        bid=D(bid), bid_size=D(bid_size), ask=D(ask), ask_size=D(ask_size),
        age_ms=0.1, received_ns=1, sequence=1,
    )


def test_v5_default_risk_stack_is_strict(monkeypatch):
    for key in (p.MAX_RESCUE_LOSS_ENV, p.CAMPAIGN_LOSS_ENV, p.DAILY_LOSS_ENV):
        monkeypatch.delenv(key, raising=False)
    assert p.max_rescue_loss() == D('0.30')
    assert p.max_campaign_loss() == D('1.00')
    assert p.max_daily_loss() == D('3.00')
    p._config_ok()


def test_rescue_loss_cannot_exceed_campaign_stop(monkeypatch):
    monkeypatch.setenv(p.MAX_RESCUE_LOSS_ENV, '1.10')
    monkeypatch.setenv(p.CAMPAIGN_LOSS_ENV, '1.00')
    with pytest.raises(RuntimeError):
        p._config_ok()


@pytest.mark.asyncio
async def test_no_candidate_is_normal_noop(monkeypatch):
    async def no_candidate():
        raise RuntimeError('no qualifying zero-competition BTC5m reward market within bounded wait')

    monkeypatch.setattr(p.v1.probe, '_wait_candidate', no_candidate)
    assert await p._wait_candidate_or_none() is None


@pytest.mark.asyncio
async def test_bounded_fok_prefers_unwind_and_uses_loss_floor(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    maker = FakeMaker()
    state = {'open': True}

    async def filled_row(client_id, symbol):
        return {'filledQuantity': '10', 'avgExecutionPrice': '0.48'}

    monkeypatch.setattr(p.v1.base, '_find_row', filled_row)
    result = await p._bounded_fok(
        maker,
        cache=FakeCache(snap()),
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5test',
        state=state,
    )
    assert result['flattened'] is True
    assert result['status'] == 'RESCUE_UNWIND_FILLED'
    assert len(maker.calls) == 1
    method, params, _ = maker.calls[0]
    assert method == 'order.place'
    assert params['side'] == 'SELL'
    assert params['eventOutcome'] == 'YES'
    assert params['type'] == 'LIMIT'
    assert params['timeInForce'] == 'FOK'
    assert params['price'] == '0.48'
    assert params['quantity'] == '10'


@pytest.mark.asyncio
async def test_bounded_fok_refuses_to_send_when_one_tick_beyond_loss_bound(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    maker = FakeMaker()
    result = await p._bounded_fok(
        maker,
        cache=FakeCache(snap(bid='0.47')),
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5unsafe',
        state={'open': True},
    )
    assert result['flattened'] is False
    assert result['hard_halt'] is True
    assert result['status'] == 'RESCUE_BOUND_NO_LONGER_AVAILABLE'
    assert maker.calls == []


@pytest.mark.asyncio
async def test_rescue_stale_book_halts_before_order_send(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    maker = FakeMaker()
    result = await p._bounded_fok(
        maker,
        cache=ErrorCache(),
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5stale',
        state={'open': True},
    )
    assert result['status'] == 'RESCUE_BOOK_NOT_FRESH'
    assert result['hard_halt'] is True
    assert maker.calls == []


@pytest.mark.asyncio
async def test_rescue_ack_timeout_is_ambiguous_hard_halt(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))

    class TimeoutMaker(FakeMaker):
        async def request(self, method, params, timeout=3.0):
            self.calls.append((method, params, timeout))
            raise TimeoutError('synthetic ack timeout')

    maker = TimeoutMaker()
    result = await p._bounded_fok(
        maker,
        cache=FakeCache(snap()),
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5ack',
        state={'open': True},
    )
    assert result['status'] == 'RESCUE_FOK_ACK_AMBIGUOUS'
    assert result['hard_halt'] is True
    assert len(maker.calls) == 1


@pytest.mark.asyncio
async def test_fok_partial_fill_is_treated_as_exchange_ambiguity(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    maker = FakeMaker()

    async def partial_row(client_id, symbol):
        return {'filledQuantity': '3', 'avgExecutionPrice': '0.48'}

    monkeypatch.setattr(p.v1.base, '_find_row', partial_row)
    result = await p._bounded_fok(
        maker,
        cache=FakeCache(snap()),
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5partial',
        state={'open': True},
    )
    assert result['status'] == 'RESCUE_FOK_PARTIAL_AMBIGUITY'
    assert result['hard_halt'] is True
    assert result['filled'] == '3'


@pytest.mark.asyncio
async def test_cancel_race_reconciles_fill_after_cancel(monkeypatch):
    calls = {'cancel': 0, 'reconcile': 0}

    async def cancel_pair(*args, **kwargs):
        calls['cancel'] += 1

    async def reconcile(yes_id, no_id, symbol):
        calls['reconcile'] += 1
        return (
            {'filledQuantity': '10', 'avgExecutionPrice': '0.48', 'status': 'FILLED'},
            {'filledQuantity': '0', 'status': 'CANCELLED'},
            D('10'), D('0'),
        )

    monkeypatch.setattr(p.v1, '_cancel_pair', cancel_pair)
    monkeypatch.setattr(p.v1.probe, '_reconcile', reconcile)
    yf, nf, yrow, nrow = await p._cancel_original_pair(
        object(), yes_id='yes', no_id='no', symbol='GEMI-BTC05M-TEST-UP',
        yes_row={'filledQuantity': '0'}, no_row={'filledQuantity': '0'},
    )
    assert calls == {'cancel': 1, 'reconcile': 1}
    assert yf == D('10')
    assert nf == D('0')
    assert yrow['status'] == 'FILLED'
    assert nrow['status'] == 'CANCELLED'


@pytest.mark.asyncio
async def test_toxic_first_fill_skips_maker_grace(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    maker = FakeMaker()
    guard = SimpleNamespace(decision=lambda: SimpleNamespace(ready=True, dangerous=True))
    result = await p._calm_maker_grace(
        maker,
        cache=FakeCache(snap()),
        guard=guard,
        symbol='GEMI-BTC05M-TEST-UP',
        filled_outcome='YES',
        filled_price=D('0.48'),
        quantity=D('10'),
        increment=D('0.01'),
        trade_id='v5toxic',
        state={'open': True},
    )
    assert result['status'] == 'ZERO_GRACE_TO_RESCUE'
    assert result['remaining'] == '10'
    assert maker.calls == []


@pytest.mark.asyncio
async def test_disarmed_v5_does_not_start_live_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv(p.v1.STATE_ENV, str(tmp_path))
    monkeypatch.delenv(p.v1.LIVE_ENV, raising=False)
    monkeypatch.delenv(p.v1.ARM_ID_ENV, raising=False)
    monkeypatch.delenv(p.v1.ARM_ENV, raising=False)
    calls = {'preflight': 0}

    async def fake_preflight():
        calls['preflight'] += 1
        return D('52.31'), {'orders': 0, 'positions': 0}

    monkeypatch.setattr(p.v1, '_preflight_flat', fake_preflight)
    await p.run()
    assert calls['preflight'] == 1
    assert not p.v1._state_file().exists()
