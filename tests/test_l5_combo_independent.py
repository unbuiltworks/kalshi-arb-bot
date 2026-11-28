"""PMUS_COMBO_INDEPENDENT: cross-game legs priced from an independent fair;
same-game still refused; default unchanged."""
from decimal import Decimal as D

from src import pmus_combo_maker as CM

LEGS = [{"symbol": "aec-nba-orl-was-2026-11-28", "side": "BUY"},
        {"symbol": "aec-nhl-mon-veg-2026-11-28", "side": "SELL"}]
BOOKS = {"aec-nba-orl-was-2026-11-28": {"bid": "0.60", "ask": "0.62",
                                        "fair_yes": "0.70"},
         "aec-nhl-mon-veg-2026-11-28": {"bid": "0.40", "ask": "0.42"}}


def test_default_uses_venue_mids(monkeypatch):
    monkeypatch.setattr(CM, "INDEPENDENT", "off")
    assert CM.combo_fair(LEGS, BOOKS) == D("0.61") * (1 - D("0.41"))


def test_prefer_uses_independent_where_present(monkeypatch):
    monkeypatch.setattr(CM, "INDEPENDENT", "prefer")
    assert CM.combo_fair(LEGS, BOOKS) == D("0.70") * (1 - D("0.41"))


def test_require_refuses_a_leg_without_independent_fair(monkeypatch):
    monkeypatch.setattr(CM, "INDEPENDENT", "require")
    assert CM.combo_fair(LEGS, BOOKS) is None
    b = dict(BOOKS)
    b["aec-nhl-mon-veg-2026-11-28"] = dict(b["aec-nhl-mon-veg-2026-11-28"],
                                           fair_yes="0.45")
    assert CM.combo_fair(LEGS, b) == D("0.70") * (1 - D("0.45"))


def test_same_game_still_refused(monkeypatch):
    monkeypatch.setattr(CM, "INDEPENDENT", "require")
    legs = [{"symbol": "aec-nba-orl-was-2026-11-28", "side": "BUY"},
            {"symbol": "tsc-nba-orl-was-2026-11-28-221pt5", "side": "BUY"}]
    q = CM.price_quote({"qtyDecimal": "1"}, legs, BOOKS, "0.01")
    assert q["why"] == "same_game"


def test_bad_independent_fair_refuses_leg(monkeypatch):
    monkeypatch.setattr(CM, "INDEPENDENT", "prefer")
    b = {"aec-nba-orl-was-2026-11-28": {"bid": "0.60", "ask": "0.62",
                                        "fair_yes": "1.5"}}
    assert CM.leg_prob(LEGS[0], b["aec-nba-orl-was-2026-11-28"]) is None
