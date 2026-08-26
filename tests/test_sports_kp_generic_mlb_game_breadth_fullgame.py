from types import SimpleNamespace

from src import sports_kp_generic_mlb_game_breadth_fullgame as fullgame
from src.sports_kp_generic_mlb_game_breadth_fullgame import is_clearly_partial_game_label


def test_rejects_explicit_first_five_variants() -> None:
    labels = [
        "Phillies vs Royals - First 5 Innings Total",
        "Phillies vs Royals | 1st 5 Innings Spread",
        "First Five Innings Moneyline",
        "MLB F5 Total",
        "MLB F-5 Total",
    ]
    for label in labels:
        assert is_clearly_partial_game_label(label), label


def test_rejects_other_explicit_partial_game_scopes() -> None:
    assert is_clearly_partial_game_label("First 3 Innings Total")
    assert is_clearly_partial_game_label("1st Inning Moneyline")


def test_does_not_reject_normal_full_game_labels() -> None:
    labels = [
        "Phillies vs Royals - Moneyline",
        "Phillies vs Royals - Run Line",
        "Phillies vs Royals - Total 8.5",
        "MLB Game Winner",
    ]
    for label in labels:
        assert not is_clearly_partial_game_label(label), label


def test_normalized_filter_rejects_f5_slug_when_label_is_generic(monkeypatch) -> None:
    full = SimpleNamespace(source_label="Game Total 6.5", venue_market_id="tsc-mlb-a-b-2026-08-26-6pt5")
    f5 = SimpleNamespace(source_label="Game Total 6.5", venue_market_id="tsc-mlb-a-b-2026-08-26-f5-6pt5")
    monkeypatch.setattr(fullgame, "_ORIGINAL_NORMALIZE_POLYMARKET", lambda *args, **kwargs: [full, f5])
    assert fullgame._normalize_polymarket_full_game([]) == [full]


def test_normalized_filter_handles_list_return_shape(monkeypatch) -> None:
    full = SimpleNamespace(source_label="MLB Game Winner", venue_market_id="aec-mlb-a-b-2026-08-26")
    monkeypatch.setattr(fullgame, "_ORIGINAL_NORMALIZE_POLYMARKET", lambda *args, **kwargs: [full])
    assert fullgame._normalize_polymarket_full_game([]) == [full]
