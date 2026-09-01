import asyncio
from decimal import Decimal as D

from src import gemini_l5_wide_book_q1 as m


def test_extreme_wide_book_is_eligible(monkeypatch):
    monkeypatch.setattr(m,"gemini_predictions_maker_fee",lambda p,q:D("0.01"))
    assert m.edge(D("0.02"),D("0.69")) == D("0.27")
    assert m.safe(D("0.02"),D("0.69"))


def test_normal_tight_book_is_rejected(monkeypatch):
    monkeypatch.setattr(m,"gemini_predictions_maker_fee",lambda p,q:D("0.01"))
    assert m.edge(D("0.48"),D("0.50")) == D("0.00")
    assert not m.safe(D("0.48"),D("0.50"))


def test_price_and_residual_bounds(monkeypatch):
    monkeypatch.setattr(m,"gemini_predictions_maker_fee",lambda p,q:D("0.01"))
    assert not m.safe(D("0.00"),D("0.69"))
    assert not m.safe(D("0.02"),D("0.81"))
    assert m.MAX_PRICE + D("0.01") == D("0.81")


def test_disarmed_is_no_venue_call(monkeypatch):
    monkeypatch.setattr(m,"armed",lambda:False)
    async def boom():
        raise AssertionError("venue call while disarmed")
    monkeypatch.setattr(m,"_gemini_terms_ok",boom)
    asyncio.run(m.run())
