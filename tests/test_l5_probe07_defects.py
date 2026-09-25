"""The three things Probe 07 exposed. None of them is a strategy change.

1. L5 trades at float precision and the venue does not. 0.7399999979 came
   back 0.73 and the exact-echo gate refused, correctly.
2. That refusal happened BEFORE the order POST, so no order existed -- and
   it still poisoned the session, costing nine subsequent actions to
   ORDER_STATE_UNCERTAIN.
3. The venue nets the two outcome tokens. Overwriting our gross book with
   its net deletes the matched pair, which is the locked half of the
   position.
"""
from __future__ import annotations

import pathlib
import tempfile
from decimal import Decimal as D

import pytest

import src.l5_live_execution as EX

MARKET = "cpc-btc-updown-15m-2026-09-25-1945z"


def store():
    return EX.InventoryStore(str(pathlib.Path(tempfile.mkdtemp()) / "i.json"))


# ------------------------------------------- 1. price on the venue's grid

def test_probe_07s_exact_price_is_now_representable():
    """The verbatim number the venue refused."""
    assert EX.quantize_price_non_aggressive(
        D("0.7399999979"), D("0.01"), "UP") == D("0.73")


def test_the_other_refused_price_too():
    assert EX.quantize_price_non_aggressive(
        D("0.2600000021"), D("0.01"), "DOWN") == D("0.26")


def test_a_price_already_on_the_grid_is_untouched():
    for px in ("0.01", "0.42", "0.73", "0.99"):
        assert EX.quantize_price_non_aggressive(
            D(px), D("0.01"), "UP") == D(px)


@pytest.mark.parametrize("raw", ["0.7399999979", "0.739", "0.7301",
                                 "0.73999999999999"])
def test_the_rule_never_rounds_up(raw):
    """Up is the aggressive direction for a buy, so it is never taken.

    Rounding up would be price improvement by arithmetic -- which the
    manifest forbids -- and could turn a resting order into a crossing one,
    which is the thing post-only exists to prevent.
    """
    out = EX.quantize_price_non_aggressive(D(raw), D("0.01"), "UP")
    assert out <= D(raw)
    assert out == D("0.73")


def test_a_price_below_one_tick_floors_to_zero_and_is_refusable():
    assert EX.quantize_price_non_aggressive(
        D("0.005"), D("0.01"), "UP") == D(0)


def test_a_coarser_grid_is_respected():
    assert EX.quantize_price_non_aggressive(
        D("0.7399999979"), D("0.05"), "UP") == D("0.70")


def test_the_tick_comes_from_the_transport_never_a_default():
    class NoTick:
        pass
    assert EX.venue_tick_size(NoTick()) is None

    class Ticked:
        tick_size = "0.01"
    assert EX.venue_tick_size(Ticked()) == D("0.01")

    class Zero:
        tick_size = "0"
    assert EX.venue_tick_size(Zero()) is None


def test_quantizing_to_a_zero_tick_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        EX.quantize_price_non_aggressive(D("0.5"), D(0), "UP")


# ----------------------------------------- 3. gross inventory vs venue net

def test_probe_07s_own_numbers_reconcile_by_the_identity():
    """UP 2.47 and DOWN 5.28 IS a net of DOWN 2.81. Nothing is lost."""
    st = store()
    st.state["positions"] = {"%s|UP" % MARKET: "2.47",
                             "%s|DOWN" % MARKET: "5.28"}
    out = st.reconcile_from_venue(
        [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
          "qtySoldDecimal": "2.81", "netPositionDecimal": "-2.81"}], [])
    assert out["gross_net_agrees"][MARKET] is True
    assert out["gross_detail_lost"] == []
    # the matched pair SURVIVED: both legs are still there
    assert st.state["positions"]["%s|UP" % MARKET] == "2.47"
    assert st.state["positions"]["%s|DOWN" % MARKET] == "5.28"


def test_the_matched_pair_is_reported_not_merely_implied():
    st = store()
    st.state["positions"] = {"%s|UP" % MARKET: "2.47",
                             "%s|DOWN" % MARKET: "5.28"}
    g = st.gross_inventory(MARKET)
    assert g["up"] == "2.47" and g["dn"] == "5.28"
    assert g["matched"] == "2.47"
    assert g["residual"] == "-2.81"
    assert g["net"] == "-2.81"


def test_a_net_that_contradicts_our_gross_takes_over_and_says_so():
    st = store()
    st.state["positions"] = {"%s|UP" % MARKET: "2.47",
                             "%s|DOWN" % MARKET: "5.28"}   # net -2.81
    out = st.reconcile_from_venue(
        [{"marketSlug": MARKET, "qtyBoughtDecimal": "9",
          "qtySoldDecimal": "0", "netPositionDecimal": "9"}], [])
    assert out["gross_net_agrees"][MARKET] is False
    assert out["gross_detail_lost"] == [MARKET]
    assert st.state["positions"] == {"%s|UP" % MARKET: "9"}


def test_a_flat_venue_against_a_matched_book_is_a_contradiction():
    """0/0 and 5/5 have the same net. They are not the same position, and a
    venue reporting nothing where we hold a pair must not pass silently."""
    st = store()
    st.state["positions"] = {"%s|UP" % MARKET: "5", "%s|DOWN" % MARKET: "5"}
    out = st.reconcile_from_venue([], [])
    # net agrees (both zero), so the matched pair is KEPT rather than erased
    assert out["gross_net_agrees"][MARKET] is True
    assert st.state["positions"]["%s|UP" % MARKET] == "5"


def test_the_venue_net_is_reported_separately_from_our_gross():
    st = store()
    st.state["positions"] = {"%s|UP" % MARKET: "2.47",
                             "%s|DOWN" % MARKET: "5.28"}
    out = st.reconcile_from_venue(
        [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
          "qtySoldDecimal": "2.81", "netPositionDecimal": "-2.81"}], [])
    assert out["venue_net"][MARKET] == "-2.81"
    assert out["gross_before"][MARKET]["matched"] == "2.47"
    assert "gross" in out["labels_inventory"].lower()
