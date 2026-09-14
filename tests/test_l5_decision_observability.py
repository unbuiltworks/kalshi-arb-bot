"""Observability must be COMPLETE at every decision. The failure it exists to
prevent is the one already in the record: econ_pair_cost present on 6 of 25
production fills, leaving every pair-economics question unanswerable on the
other 19."""
from src.l5_decision_observability import observe, SCHEMA

KEYS = ("schema", "inv_up", "inv_dn", "matched_qty", "residual_signed",
        "residual_abs", "imbalance", "avg_up", "avg_dn", "held_avg_cost",
        "held_avg_cost_reason", "complement_price", "complement_price_reason",
        "pair_cost", "pair_cost_reason", "book_implied_pair_cost", "spent")


def test_every_key_is_present_even_when_nothing_is_known():
    got = observe(inv_up=0, inv_dn=0)
    for k in KEYS:
        assert k in got, k
    assert got["schema"] == SCHEMA


def test_unknown_pair_is_null_with_a_reason_never_zero():
    """A fabricated 0.0 would read as the cheapest possible pair."""
    got = observe(inv_up=0, inv_dn=0)
    assert got["pair_cost"] is None
    assert got["pair_cost_reason"]
    assert got["pair_cost"] != "0"


def test_pair_is_held_cost_plus_complement_on_the_side_actually_held():
    # we hold DOWN at 0.37, the order buys UP at 0.60 -> pair 0.97
    got = observe(inv_up=0, inv_dn=10, avg_dn="0.37", complement_price="0.60",
                  buys_up=True)
    assert got["held_avg_cost"] == "0.37"
    assert got["pair_cost"] == "0.9700"
    assert got["pair_cost_reason"] is None


def test_held_side_follows_imbalance_when_direction_is_unknown():
    got = observe(inv_up=10, inv_dn=2, avg_up="0.41", avg_dn="0.55",
                  complement_price="0.50")
    assert got["held_avg_cost"] == "0.41"      # UP is the side we are long
    assert got["pair_cost"] == "0.9100"


def test_missing_complement_is_reported_not_imputed():
    got = observe(inv_up=0, inv_dn=10, avg_dn="0.37", complement_price=None,
                  buys_up=True)
    assert got["pair_cost"] is None
    assert "complement" in got["pair_cost_reason"]
    assert got["held_avg_cost"] == "0.37"      # what IS known is still reported


def test_matched_and_residual_are_always_computed():
    got = observe(inv_up="17.97", inv_dn="17.47")
    assert got["matched_qty"] == "17.47"
    assert got["residual_signed"] == "0.50"
    assert got["residual_abs"] == "0.50"


def test_residual_sign_is_preserved_not_absolute_only():
    """A signed residual says WHICH side is long; abs alone cannot."""
    got = observe(inv_up=2, inv_dn=9)
    assert got["residual_signed"] == "-7"
    assert got["residual_abs"] == "7"


def test_book_implied_pair_uses_both_legs_when_both_are_held():
    got = observe(inv_up=5, inv_dn=5, avg_up="0.4858", avg_dn="0.3684")
    assert got["book_implied_pair_cost"] == "0.8542"


def test_unparseable_venue_values_do_not_raise_and_report_unknown():
    got = observe(inv_up="n/a", inv_dn=None, avg_up="", complement_price="x")
    assert got["pair_cost"] is None
    assert got["matched_qty"] == "0"
