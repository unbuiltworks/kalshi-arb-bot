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
