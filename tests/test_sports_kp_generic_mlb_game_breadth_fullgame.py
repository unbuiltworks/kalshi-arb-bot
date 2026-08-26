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
