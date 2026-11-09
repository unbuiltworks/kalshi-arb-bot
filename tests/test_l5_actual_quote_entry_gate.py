"""Regression checks for the actual-quote entry guard; no live orders."""
from decimal import Decimal as D
from src.l5_second_leg_economics import actual_quote_entry_gate


def _args():
    return dict(quantity="1", p_both="0.525", p_up_only="0.2115",
                p_down_only="0.2115", p_neither="0.052",
                p_up_settles_given_up_only="0.35",
                p_up_settles_given_down_only="0.65",
                book_age_s="0.5", up_depth="5", down_depth="5",
                existing_pair_ceiling="0.96")


def test_actual_quotes_used_and_no_half_pair_substitution():
    a = _args()
    low_up = actual_quote_entry_gate(up_price="0.40", down_price="0.55", **a)
    high_up = actual_quote_entry_gate(up_price="0.55", down_price="0.40", **a)
    assert low_up["actual_quote_prices_used"]
    assert high_up["actual_quote_prices_used"]
    assert D(low_up["expected_net"]) != D(high_up["expected_net"])


def test_missing_book_evidence_refuses():
    a = _args()
    a["book_age_s"] = None
    assert actual_quote_entry_gate(up_price="0.5", down_price="0.45", **a)["reason"] == "BOOK_EVIDENCE_MISSING"


def test_existing_ceiling_cannot_widen():
    a = _args()
    assert actual_quote_entry_gate(up_price="0.55", down_price="0.45", **a)["reason"] == "EXISTING_PAIR_CEILING"


def test_missing_calibration_refuses():
    a = _args()
    a["p_up_settles_given_down_only"] = None
    assert not actual_quote_entry_gate(up_price="0.50", down_price="0.45", **a)["eligible"]


def test_stale_book_refuses():
    a = _args()
    a["book_age_s"] = "3"
    assert actual_quote_entry_gate(up_price="0.50", down_price="0.45", **a)["reason"] == "STALE_BOOK"
