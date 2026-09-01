from decimal import Decimal as D
import asyncio

from src import gemini_liquidity_reward_live_probe_q10 as q10


def test_q10_model_is_quantity_aware():
    m = q10._model(D('0.40'), D('0.55'))
    assert m['yes_quote'] == D('0.40')
    assert m['no_quote'] == D('0.45')
    assert m['max_one_sided_premium'] == D('4.50')
    assert m['locked_edge'] > D('1.0')


def test_disarmed_makes_no_venue_calls(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(q10.LIVE_ENV, raising=False)
    monkeypatch.delenv(q10.PROBE_ID_ENV, raising=False)
    monkeypatch.delenv(q10.PROBE_ARM_ENV, raising=False)
    monkeypatch.setenv(q10.STATE_ENV, str(tmp_path))

    async def boom(*args, **kwargs):
        raise AssertionError('venue call should not occur while disarmed')

    monkeypatch.setattr(q10.local, 'account_exposure_snapshot', boom)
    asyncio.run(q10.run())
    out = capsys.readouterr().out
    assert 'DISARMED_NO_VENUE_CALLS' in out
    assert not (tmp_path / 'gemini-liquidity-reward-q10-probe.json').exists()
