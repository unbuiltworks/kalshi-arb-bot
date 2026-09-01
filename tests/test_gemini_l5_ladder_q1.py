import asyncio
from decimal import Decimal as D

from src import gemini_l5_ladder_q1 as m


def test_client_ids_are_unique_and_bounded():
    ids={m.cid("session-123","YES",i) for i in range(3)} | {m.cid("session-123","NO",i) for i in range(3)}
    assert len(ids)==6
    assert all(len(x)<=36 for x in ids)


def test_three_level_ladder_and_race_bound():
    assert m.prices(D("0.48")) == (D("0.48"),D("0.47"),D("0.46"))
    assert m.MAX_PRICE * D(str(len(m.OFFSETS))) == D("2.10")


def test_safe_requires_nonlosing_top_pair_and_price_band(monkeypatch):
    monkeypatch.setattr(m,"gemini_predictions_maker_fee",lambda p,q:D("0.01"))
    assert m.safe((D("0.48"),D("0.47"),D("0.46")),(D("0.50"),D("0.49"),D("0.48")))
    assert not m.safe((D("0.49"),D("0.48"),D("0.47")),(D("0.50"),D("0.49"),D("0.48")))
    assert not m.safe((D("0.71"),D("0.70"),D("0.69")),(D("0.27"),D("0.26"),D("0.25")))


def test_fresh_top_retry_tolerates_brief_depth_gap(monkeypatch):
    calls=0
    async def flaky(_symbol):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("no fresh depth")
        return D("0.48"),D("250"),D("0.50"),D("250")
    monkeypatch.setattr(m.base,"fresh_top",flaky)
    got=asyncio.run(m.fresh_top_retry("ETH",attempts=3,delay_s=0))
    assert got==(D("0.48"),D("250"),D("0.50"),D("250"))
    assert calls==3


def test_fresh_top_retry_fails_closed(monkeypatch):
    calls=0
    async def fail(_symbol):
        nonlocal calls
        calls += 1
        raise RuntimeError("no fresh depth")
    monkeypatch.setattr(m.base,"fresh_top",fail)
    try:
        asyncio.run(m.fresh_top_retry("ETH",attempts=2,delay_s=0))
    except RuntimeError as exc:
        assert "after 2 attempts" in str(exc)
    else:
        raise AssertionError("expected fail-closed RuntimeError")
    assert calls==2


def test_disarmed_is_no_venue_call(monkeypatch):
    monkeypatch.setattr(m,"armed",lambda:False)
    async def boom():
        raise AssertionError("venue call while disarmed")
    monkeypatch.setattr(m,"_gemini_terms_ok",boom)
    asyncio.run(m.run())
