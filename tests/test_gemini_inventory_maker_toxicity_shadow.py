from decimal import Decimal as D

from src.gemini_inventory_maker_toxicity_shadow import Inventory


def test_balanced_inventory_quotes_two_sides():
    inv = Inventory()
    inv.on_book(D("0.48"), D("0.61"), residual_cap=D("10"))
    assert inv.yes_quote == D("0.48")
    assert inv.no_quote == D("0.39")


def test_first_fill_stops_overweight_side_and_works_complement():
    inv = Inventory()
    inv.on_book(D("0.48"), D("0.61"), residual_cap=D("10"))
    inv.on_trade(D("0.47"), D("10"), True, residual_cap=D("10"))
    assert inv.yes_qty == D("10")
    assert inv.no_qty == D("0")
    inv.on_book(D("0.49"), D("0.60"), residual_cap=D("10"))
    assert inv.yes_quote is None
    assert inv.no_quote == D("0.40")


def test_residual_cap_blocks_more_overweight_inventory():
    inv = Inventory()
    inv.on_book(D("0.48"), D("0.61"), residual_cap=D("10"))
    inv.on_trade(D("0.47"), D("10"), True, residual_cap=D("10"))
    inv.yes_quote = D("0.50")  # synthetic stale quote; cap must still prevent add
    inv.on_trade(D("0.49"), D("10"), True, residual_cap=D("10"))
    assert inv.yes_qty == D("10")


def test_toxicity_reduces_matched_trade_edge_monotonically():
    inv = Inventory()
    inv._add("YES", D("0.45"), D("10"))
    inv._add("NO", D("0.45"), D("10"))
    zero = inv.economics(D("0"))
    stressed = inv.economics(D("0.01"))
    assert stressed["trade_edge_after_toxicity"] < zero["trade_edge_after_toxicity"]
    assert stressed["toxicity_penalty"] == D("0.20")


def test_stale_midpoint_move_pulls_quotes_before_fill():
    inv = Inventory()
    inv.on_book(D("0.48"), D("0.52"), residual_cap=D("10"), stale_pull_bps=D("50"))
    assert inv.yes_quote is not None
    inv.on_book(D("0.50"), D("0.54"), residual_cap=D("10"), stale_pull_bps=D("50"))
    assert inv.yes_quote is None
    assert inv.no_quote is None
