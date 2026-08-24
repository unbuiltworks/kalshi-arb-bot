from decimal import Decimal

from src import gemini_kalshi_xrp_maker_live_canary_fill_wait as wait

D = Decimal


def test_fillable_choose_quote_rejects_quote_behind_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.48")})
    assert wait.fillable_choose_quote(maker_best_bid=D("0.63")) is None


def test_fillable_choose_quote_accepts_quote_at_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.63")})
    result = wait.fillable_choose_quote(maker_best_bid=D("0.63"))
    assert result is not None
    assert result["maker_quote"] == D("0.63")


def test_fillable_choose_quote_accepts_quote_above_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.64")})
    result = wait.fillable_choose_quote(maker_best_bid=D("0.63"))
    assert result is not None
