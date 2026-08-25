from types import SimpleNamespace

from src.polymarket_kalshi_shadow_v5_gateway import _selected_depth


def test_selected_depth_uses_exact_plan_side() -> None:
    quote = SimpleNamespace(
        yes_depth=7,
        no_depth=11,
    )

    assert _selected_depth({"poly_side": "YES"}, quote, venue="poly") == 7.0
    assert _selected_depth({"poly_side": "NO"}, quote, venue="poly") == 11.0
    assert _selected_depth({"kalshi_side": "YES"}, quote, venue="kalshi") == 7.0
    assert _selected_depth({"kalshi_side": "NO"}, quote, venue="kalshi") == 11.0


def test_selected_depth_fails_closed_for_missing_side() -> None:
    quote = SimpleNamespace(yes_depth=7, no_depth=11)
    assert _selected_depth({}, quote, venue="poly") == 0.0
