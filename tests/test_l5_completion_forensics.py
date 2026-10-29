"""Tests for the completion forensics.

These drive the functions with synthetic rows rather than grepping the
source. An earlier study in this repo shipped a test that searched the module
text for a forbidden string and passed on the module's own docstring
describing the prohibition; every check here is paired with a companion that
proves it goes RED when the property it claims actually breaks.
"""

from decimal import Decimal as D

import pytest

from src.l5_completion_forensics import (abandonment, band_bid,
                                         band_blocked_while_holding,
                                         cover_by_market, flow_features,
                                         is_entry_band, residual_of, _d)


def order(slug, relation, filled="0", ts="2026-09-07T20:00:00Z", **kw):
    row = {"slug": slug, "relation": relation, "filled_qty": filled,
           "log_ts": ts, "rest_s": kw.pop("rest_s", 5.0),
           "end_reason": kw.pop("end_reason", "deadline")}
    row.update(kw)
    return row


def decision(slug, refusal=None, residual="0", recommended="complete", **kw):
    row = {"slug": slug, "refusal": refusal,
           "inventory_before": {"residual": residual,
                                "unmatched_usd": kw.pop("unmatched", "0")},
           "v2_recommended": recommended,
           "would_act_without_safety_cap": kw.pop("would_act", recommended)}
    row.update(kw)
    return row


# ------------------------------ cover cohorts ------------------------------

def test_cover_splits_full_partial_zero():
    orders = [
        order("full", "open", "10"), order("full", "complete", "10"),
        order("part", "open", "10"), order("part", "complete", "4"),
        order("zero", "open", "10"), order("zero", "complete", "0"),
    ]
    cov = cover_by_market(orders)
    assert cov["full"]["cohort"] == "FULL"
    assert cov["part"]["cohort"] == "PARTIAL"
    assert cov["zero"]["cohort"] == "ZERO"
    assert cov["part"]["cover"] == D("0.4")


def test_market_with_no_first_side_fill_is_excluded():
    """A market where nothing filled says nothing about completion."""
    cov = cover_by_market([order("nofill", "open", "0"),
                           order("nofill", "complete", "0")])
    assert "nofill" not in cov


def test_extend_counts_as_a_first_side_fill():
    """EXTEND buys more of the held side, so it enlarges what must be completed."""
    cov = cover_by_market([order("m", "open", "5"), order("m", "extend", "5"),
                           order("m", "complete", "5")])
    assert cov["m"]["first_side_filled"] == D("10")
    assert cov["m"]["cohort"] == "PARTIAL"


# ------------------------------ the entry band ------------------------------

def test_band_separates_flat_entry_refusals_from_residual_refusals():
    ds = [decision("a", "entry_band_left:bid=0.0600:q1_entry_ok=False", residual="0"),
          decision("b", "entry_band_left:bid=0.0800:q1_entry_ok=False", residual="-4.2"),
          decision("c", "entry_band_left:bid=0.1200:q1_entry_ok=False", residual="3.1")]
    b = band_blocked_while_holding(ds)
    assert b["fired"] == 3
    assert b["while_flat_a_true_entry_refusal"] == 1
    assert b["while_holding_residual"] == 2


def test_band_counts_a_negative_residual_as_holding():
    """Residual is signed: short one side is still an unmatched leg.

    Goes red if the split ever tests `residual > 0` instead of `!= 0`.
    """
    b = band_blocked_while_holding(
        [decision("m", "entry_band_left:bid=0.0600:x", residual="-9.62")])
    assert b["while_holding_residual"] == 1
    assert b["while_flat_a_true_entry_refusal"] == 0


def test_band_ignores_other_refusals():
    b = band_blocked_while_holding([decision("m", "spend_cap_reached", residual="-4"),
                                    decision("m", None, residual="-4")])
    assert b["fired"] == 0


def test_band_records_what_l5_wanted_and_what_the_shell_would_have_done():
    """The two must stay distinct: netting them hides that EXTEND != COMPLETE."""
    ds = [decision("m", "entry_band_left:bid=0.05:x", residual="-4",
                   recommended="complete", would_act="extend")]
    b = band_blocked_while_holding(ds)
    assert b["l5_recommended_on_holding_cycles"] == {"complete": 1}
    assert b["shell_would_have_acted_on_holding_cycles"] == {"extend": 1}


def test_band_bid_parses_and_rejects_non_band_refusals():
    assert band_bid("entry_band_left:bid=0.0350:q1_entry_ok=False") == 0.035
    assert band_bid("spend_cap_reached") is None
    assert band_bid(None) is None
    assert band_bid("entry_band_left:bid=nonsense:x") is None


def test_is_entry_band_does_not_match_a_lookalike():
    assert is_entry_band("entry_band_left:bid=0.06:x")
    assert not is_entry_band("residual_cap:fallback_complete:p=0.321")


# ------------------------------ abandonment ------------------------------

def test_completes_before_the_first_fill_do_not_count_as_persistence():
    """Only attempts AFTER a leg exists are attempts to complete that leg."""
    orders = [
        order("m", "complete", "0", "2026-09-07T20:00:00Z"),
        order("m", "open", "5", "2026-09-07T20:01:00Z"),
        order("m", "complete", "0", "2026-09-07T20:02:00Z"),
        order("m", "complete", "0", "2026-09-07T20:03:00Z"),
    ]
    a = abandonment(orders, "m")
    assert a["orders"] == 4
    assert a["completes_after_first_fill"] == 2
    assert a["minutes_first_fill_to_last_action"] == 2.0


def test_abandonment_reads_zero_when_we_walked_away_at_the_fill():
    a = abandonment([order("m", "open", "5", "2026-09-07T20:00:00Z")], "m")
    assert a["completes_after_first_fill"] == 0
    assert a["minutes_first_fill_to_last_action"] == 0.0


def test_abandonment_is_ordered_by_time_not_file_order():
    """Log dumps are concatenated out of order, so the window must be sorted.

    The last action is picked positionally, so without a chronological sort the
    window collapses to 0.0 minutes and a market we worked for four minutes
    reads as one we abandoned instantly.
    """
    orders = [
        order("m", "complete", "0", "2026-09-07T20:05:00Z"),
        order("m", "open", "5", "2026-09-07T20:01:00Z"),
    ]
    a = abandonment(orders, "m")
    assert a["completes_after_first_fill"] == 1
    assert a["minutes_first_fill_to_last_action"] == 4.0


# ------------------------------ flow features ------------------------------

def test_flow_features_are_independent_of_our_own_fills():
    """Drift and spread must come from the book, never from our fill outcome.

    Two markets with identical books and opposite fill outcomes must produce
    identical flow features; otherwise the feature is circular with cover.
    """
    ds = []
    for slug in ("filled", "unfilled"):
        ds.append({"slug": slug, "refusal": None,
                   "open_side": {"yes_bid": "0.40", "yes_ask": "0.41"}})
        ds.append(decision(slug, "entry_band_left:bid=0.1000:x", residual="-4"))
    book = {"max_touch": "0.42", "min_opp_ask": "0.44", "polls": 9}
    orders = [order("filled", "complete", "9", px="0.40", rest_book=dict(book)),
              order("unfilled", "complete", "0", px="0.40", rest_book=dict(book))]
    f = flow_features(ds, orders)
    assert f["filled"] == f["unfilled"]
    assert f["filled"]["entry_spread_median"] == pytest.approx(0.01)
    assert f["filled"]["bid_drift"] == pytest.approx(-0.30)
    assert f["filled"]["touch_to_oppask_span_median"] == pytest.approx(-0.02)


# ------------------------------ decimal handling ------------------------------

def test_unreadable_decimals_do_not_raise():
    """Decimal('junk') raises InvalidOperation, an ArithmeticError not ValueError."""
    assert _d("junk") is None
    assert _d(None) is None
    assert _d("venue_unreported") is None
    assert _d("1.25") == D("1.25")


def test_residual_of_tolerates_a_missing_inventory_block():
    assert residual_of({}) == D(0)
    assert residual_of({"inventory_before": None}) == D(0)
    assert residual_of({"inventory_before": {"residual": "-4.2"}}) == D("-4.2")
