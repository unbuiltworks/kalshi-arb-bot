from decimal import Decimal

from src.three_venue_mlb_prop_supervisor_one_sided import poly_top_book_one_sided
from src.strict_mlb_prop_snapshot import TopBook, best_conservative_plan

D = Decimal


def test_bids_only_exposes_only_executable_no_side():
    book = poly_top_book_one_sided({
        "marketData": {"bids": [{"px": {"value": "0.41"}, "qty": "7"}], "offers": []}
    })
    assert book is not None
    assert book.no_ask == D("0.59")
    assert book.no_depth == D("7")
    assert book.yes_depth == D("0")


def test_offers_only_exposes_only_executable_yes_side():
    book = poly_top_book_one_sided({
        "marketData": {"bids": [], "offers": [{"px": "0.44", "quantity": {"value": "5"}}]}
    })
    assert book is not None
    assert book.yes_ask == D("0.44")
    assert book.yes_depth == D("5")
    assert book.no_depth == D("0")


def test_missing_side_cannot_be_used_by_optimizer():
    poly = poly_top_book_one_sided({"bids": [{"px": "0.30", "qty": "4"}], "offers": []})
    assert poly is not None
    kalshi = TopBook(yes_ask=D("0.30"), yes_depth=D("4"), no_ask=D("0.80"), no_depth=D("4"))
    plan = best_conservative_plan(poly, kalshi, kalshi_fee_multiplier=D("1"), max_quantity=4)
    assert plan is not None
    assert plan["direction"] == "KALSHI_YES_POLY_NO"


def test_crossed_two_sided_book_fails_closed():
    book = poly_top_book_one_sided({
        "bids": [{"px": "0.60", "qty": "2"}],
        "offers": [{"px": "0.55", "qty": "2"}],
    })
    assert book is None
