"""Three overlays on the same recorded book. Shadow, theoretical, Model A untouched."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import overlays as OV

BOOK = {"yes": {"bid": "0.31", "ask": "0.38"},
        "no": {"bid": "0.62", "ask": "0.69"}}
DEPTH = {"readable": True, "bids": {"best": "0.31", "levels": [
    {"price": "0.31", "qty": "245"}, {"price": "0.25", "qty": "10"}]}}


def _p(ms, price, amount, typ="sell"):
    return {"tid": ms, "utc_ms": ms, "price": price, "amount": amount,
            "type": typ}


# ================================================= maker-only or refused

def test_IMPROVE_IS_REFUSED_WHEN_IT_WOULD_NOT_BE_MAKER_ONLY():
    """Crossing is not authorized on this venue. An improvement that reaches
    the ask is a cross and does not get evaluated."""
    tight = {"yes": {"bid": "0.31", "ask": "0.32"}}
    got = OV.candidates(tight, "YES", "0.31")[OV.IMPROVE]
    assert got["exists"] is False and got["maker_only"] is False
    assert "not maker-only" in got["why"]


def test_improve_is_one_tick_and_stays_inside_the_spread():
    got = OV.candidates(BOOK, "YES", "0.31")[OV.IMPROVE]
    assert got["exists"] is True and got["price"] == "0.32"
    assert got["maker_only"] is True


def test_a_one_sided_book_cannot_prove_maker_only():
    got = OV.candidates({"yes": {"bid": "0.31"}}, "YES", "0.31")[OV.IMPROVE]
    assert got["exists"] is False and "one side" in got["why"]


def test_the_native_price_is_carried_as_one_of_the_three():
    got = OV.candidates(BOOK, "YES", "0.28")
    assert got[OV.NATIVE]["price"] == "0.28"
    assert got["ticks_native_behind_best"] == "3"


def test_a_cycle_where_the_policy_quoted_nothing_says_so():
    got = OV.candidates(BOOK, "YES", None)[OV.NATIVE]
    assert got["exists"] is False and "chose no price" in got["why"]


# ================================================= the queue is respected

def test_a_reaching_print_smaller_than_the_QUEUE_is_not_a_fill():
    """THE MECHANISM THE STUDY EXISTS TO MEASURE. Joining a 245-deep bid and
    then seeing a 3.66-contract sell is not a fill; being in front of it is."""
    prints = [_p(1000, "0.31", "3.66", "sell")]
    join = OV.counterfactual_fill(price="0.31", leg="YES", prints=prints,
                                  from_ms=0, to_ms=8000, ahead=D("245"))
    assert join["reaching_prints"] == 1
    assert join["filled_ignoring_queue"] is True
    assert join["filled_respecting_queue"] is False
    assert join["needed_to_reach_us"] == "245.01"

    improve = OV.counterfactual_fill(price="0.32", leg="YES", prints=prints,
                                     from_ms=0, to_ms=8000, ahead=D("0"))
    assert improve["filled_respecting_queue"] is True


def test_a_buy_print_does_not_hit_a_resting_bid():
    """A resting BUY is hit by an aggressor SELLING. A buy print lifted
    someone's offer and never reached our bid."""
    got = OV.counterfactual_fill(price="0.31", leg="YES",
                                 prints=[_p(1000, "0.31", "9", "buy")],
                                 from_ms=0, to_ms=8000, ahead=D("0"))
    assert got["reaching_prints"] == 0


def test_a_print_above_our_bid_does_not_reach_us():
    got = OV.counterfactual_fill(price="0.31", leg="YES",
                                 prints=[_p(1000, "0.40", "9", "sell")],
                                 from_ms=0, to_ms=8000, ahead=D("0"))
    assert got["reaching_prints"] == 0


def test_a_print_outside_the_interval_does_not_count():
    got = OV.counterfactual_fill(price="0.31", leg="YES",
                                 prints=[_p(99000, "0.20", "9", "sell")],
                                 from_ms=0, to_ms=8000, ahead=D("0"))
    assert got["reaching_prints"] == 0


def test_an_UNREADABLE_QUEUE_gets_no_queue_respecting_answer():
    """The venue publishes one ladder, the YES side. A NO-leg queue is not
    readable and is not guessed."""
    got = OV.counterfactual_fill(price="0.62", leg="NO",
                                 prints=[_p(1000, "0.40", "9", "buy")],
                                 from_ms=0, to_ms=8000, ahead=None)
    assert got["queue_readable"] is False
    assert got["filled_respecting_queue"] is None
    assert got["filled_ignoring_queue"] is True


# =============================== the tape is priced in YES, both legs

def test_A_NO_RUNG_IS_REACHED_BY_AN_AGGRESSOR_BUYING_YES():
    """CHECKED AGAINST CONTINUOUS-5. Our passive NO buy at 0.46 appears on the
    tape as an aggressor YES *buy* at 0.54, because selling NO to us IS buying
    YES. Using the YES test for a NO rung counts the wrong prints entirely."""
    got = OV.counterfactual_fill(price="0.46", leg="NO",
                                 prints=[_p(1000, "0.54", "0.01", "buy")],
                                 from_ms=0, to_ms=8000, ahead=None)
    assert got["tape_side_tested"] == "buy"
    assert got["yes_price_threshold"] == "0.54"
    assert got["reaching_prints"] == 1
    assert got["prints"][0]["our_leg_price"] == "0.46"


def test_a_YES_sell_does_not_reach_a_NO_rung():
    got = OV.counterfactual_fill(price="0.46", leg="NO",
                                 prints=[_p(1000, "0.30", "9", "sell")],
                                 from_ms=0, to_ms=8000, ahead=None)
    assert got["reaching_prints"] == 0


def test_a_NO_rung_is_not_reached_by_a_buy_BELOW_its_threshold():
    got = OV.counterfactual_fill(price="0.46", leg="NO",
                                 prints=[_p(1000, "0.50", "9", "buy")],
                                 from_ms=0, to_ms=8000, ahead=None)
    assert got["reaching_prints"] == 0


def test_the_side_rule_is_stated_on_every_answer():
    got = OV.counterfactual_fill(price="0.31", leg="YES", prints=[],
                                 from_ms=0, to_ms=1, ahead=D("0"))
    assert "priced in YES and records the aggressor" in got["side_why"]


def test_a_theoretical_fill_is_labelled_theoretical():
    got = OV.counterfactual_fill(price="0.31", leg="YES", prints=[],
                                 from_ms=0, to_ms=1, ahead=D("0"))
    assert "THEORETICAL" in got["labels"]
    assert "nothing was submitted, filled, settled or realized" in got["labels"]


# ================================================= size ahead

def test_size_ahead_is_the_size_AT_our_price():
    assert OV.size_ahead(DEPTH, "0.31") == D("245")
    assert OV.size_ahead(DEPTH, "0.32") == D("0")
    assert OV.size_ahead(None, "0.31") is None


def test_improving_the_touch_puts_nothing_ahead_of_us():
    row = OV.overlay_cycle(utc_ms=0, next_utc_ms=8000, leg="YES",
                           native_price="0.31", book=BOOK, depth=DEPTH,
                           prints=[_p(1000, "0.31", "3.66", "sell")])
    assert row["overlays"][OV.JOIN]["size_ahead"] == "245"
    assert row["overlays"][OV.IMPROVE]["size_ahead"] == "0"
    assert row["overlays"][OV.IMPROVE]["improves_the_touch"] is True
    assert row["overlays"][OV.JOIN]["improves_the_touch"] is False


# ================================================= survival and adverse

def test_survival_stops_the_moment_someone_bids_above_us():
    later = [{"book": {"yes": {"bid": "0.31", "ask": "0.38"}}},
             {"book": {"yes": {"bid": "0.31", "ask": "0.38"}}},
             {"book": {"yes": {"bid": "0.33", "ask": "0.38"}}}]
    got = OV.survival("0.31", "YES", later)
    assert got["cycles_still_best"] == 2 and got["seconds_still_best"] == 16.0
    assert got["outbid"] is True and got["survived_to_end_of_record"] is False


def test_survival_is_an_upper_bound_not_a_fill_model():
    got = OV.survival("0.31", "YES", [])
    assert got["cycles_still_best"] == 0
    assert "not a fill model" in got["why"]


def test_adverse_movement_is_marked_never_realized():
    later = [{"book": {"yes": {"bid": "0.20", "ask": "0.22"}}}]
    got = OV.adverse_after("0.31", "YES", later)
    assert got["computable"] is True and got["last"] == "-0.10"
    assert "NOT realized" in got["labels"]


def test_adverse_movement_with_no_later_book_is_refused():
    assert OV.adverse_after("0.31", "YES", [])["computable"] is False


# ================================================= the census and caveats

def test_the_census_keeps_every_caveat_attached():
    row = OV.overlay_cycle(utc_ms=0, next_utc_ms=8000, leg="YES",
                           native_price="0.31", book=BOOK, depth=DEPTH,
                           prints=[])
    got = OV.overlay_census([row])
    joined = " ".join(got["caveats"])
    assert "OUR OWN SIZE WOULD HAVE CHANGED THE BOOK" in joined
    assert "YES SIDE ONLY" in joined
    assert "A REACHING PRINT IS NOT A FILL" in joined
    assert "THEORETICAL" in got["labels"]


def test_the_census_counts_where_each_overlay_even_exists():
    tight = {"yes": {"bid": "0.31", "ask": "0.32"}}
    rows = [OV.overlay_cycle(utc_ms=0, next_utc_ms=8000, leg="YES",
                             native_price="0.31", book=tight, depth=DEPTH,
                             prints=[])]
    got = OV.overlay_census(rows)
    assert got[OV.IMPROVE]["cycles_where_it_exists"] == 0
    assert got[OV.IMPROVE]["refused_or_absent"] == 1
    assert got[OV.JOIN]["cycles_where_it_exists"] == 1


# ================================================= Model A is untouched

def test_the_overlays_are_not_reachable_from_the_controller():
    import ast
    import inspect

    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import ws_continuous as WC
    for mod in (CP, WC):
        tree = ast.parse(inspect.getsource(mod))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert "overlays" not in n.module
                assert not any("overlays" in a.name for a in n.names)


def test_the_overlay_module_sends_nothing():
    import inspect
    src = inspect.getsource(OV)
    for bad in ("order.place", "order.cancel", "ws_session", "place(",
                "cancel("):
        assert bad not in src, bad


def test_the_complement_is_recorded_for_every_cycle_filled_or_not():
    row = OV.overlay_cycle(utc_ms=0, next_utc_ms=8000, leg="YES",
                           native_price="0.31", book=BOOK, depth=DEPTH,
                           prints=[])
    assert row["complement"]["leg"] == "NO"
    assert row["complement"]["passive_price"] == "0.62"
    assert "not just filled ones" in row["complement"]["why"]


@pytest.mark.parametrize("bad", [None, "", "abc"])
def test_garbage_in_is_refused(bad):
    got = OV.counterfactual_fill(price=bad, leg="YES", prints=[],
                                 from_ms=0, to_ms=1)
    assert got["computable"] is False


@pytest.mark.parametrize("bad", ["5", "0", "1", "-0.2"])
def test_a_price_outside_zero_to_one_is_refused(bad):
    """A binary contract cannot trade there. Answering would put a number on
    something that cannot exist."""
    got = OV.counterfactual_fill(price=bad, leg="YES", prints=[],
                                 from_ms=0, to_ms=1)
    assert got["computable"] is False
    assert "outside (0, 1)" in got["why"]
