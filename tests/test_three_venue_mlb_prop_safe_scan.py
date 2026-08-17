from decimal import Decimal

from src.three_venue_mlb_prop_safe_scan import _canonical_teams, _discrete_threshold_and_side


def test_discrete_over_half_maps_to_ge_threshold():
    assert _discrete_threshold_and_side("GT", Decimal("1.5")) == (Decimal("2"), "CONTRACT")


def test_discrete_under_half_maps_to_complement_of_ge_threshold():
    assert _discrete_threshold_and_side("LT", Decimal("1.5")) == (Decimal("2"), "NO")


def test_discrete_integer_over_moves_to_next_count():
    assert _discrete_threshold_and_side("GT", Decimal("2")) == (Decimal("3"), "CONTRACT")


def test_discrete_le_maps_to_complement_of_next_count():
    assert _discrete_threshold_and_side("LE", Decimal("2")) == (Decimal("3"), "NO")


def test_mlb_team_aliases_are_canonicalized():
    assert _canonical_teams(("CHW", "TBR")) == ("CWS", "TB")
