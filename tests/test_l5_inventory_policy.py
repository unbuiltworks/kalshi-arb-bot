"""The continuous desired-inventory candidate — BTC15m, MODEL_CANDIDATE.

Every previous arm waited for L5 to fill before it had an opinion, and 54.3% of
his complements are done inside our observation lag. This one does not wait.

The tests that matter are the ones that stop it cheating: it must never cross,
never reprice, never widen past the ceiling to manufacture a pair, and never
choose a level after seeing what filled.
"""
from decimal import Decimal as D

import pytest

from src.l5_shadow import inventory_policy as IP
from src.l5_shadow import terms as T
from src.l5_shadow import two_sided as TS

PM = T.pm_terms({"fee_rate": 0.07, "fee_taker_only": True, "order_min_size": 5,
                 "tick": "0.01", "terms_source": "t"})


def _st(**kw):
    s = {"market_key": "m", "cohort": "BTC15m", "tte_s": 800.0,
         "inv_up": "0", "inv_dn": "0"}
    s.update(kw)
    return s


def _plan(arm, up, dn, *, state=None, terms=PM):
    return IP.plan(state or _st(), arm=arm, terms=terms, up_rest=up,
                   dn_rest=dn, now=1.0)


# --------------------------------- it wants both sides, before either fills

def test_the_target_is_symmetric_because_asymmetry_would_be_a_view():
    d = IP.desired_inventory(_st())
    assert d["desired_up"] == d["desired_dn"] == str(IP.DESIRED_PER_SIDE)
    assert d["quoting"] is True


def test_it_quotes_both_sides_with_no_fill_on_either():
    p = _plan("S", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert {q["outcome"] for q in p["quotes"]} == {"Up", "Down"}
    assert p["pairs"][0]["pair_cost"] == "0.950000"


def test_holding_one_side_reduces_only_that_side_of_the_target():
    d = IP.desired_inventory(_st(inv_up="10"))
    assert d["want_up"] == "0" and d["want_dn"] == str(IP.DESIRED_PER_SIDE)


# ------------------------------------- the deep grid is why D might work

def test_joining_the_touch_cannot_reach_a_pair_inside_the_ceiling_here():
    """0.45 + 0.52 = 0.97, outside the 0.96 ceiling. Arm S still rests -- what
    it cannot do is produce a pair we could execute, and that is the live
    situation: the measured touch sum was 0.9930."""
    p = _plan("S", [(D("0.45"), D("100"))], [(D("0.52"), D("80"))])
    assert len(p["quotes"]) == 2
    assert p["combinations_within_ceiling"] == 0


def test_resting_deeper_reaches_one_on_the_same_book():
    """The mechanism in miniature: L5 gets 0.9624 where the touch sum is
    0.9930, and joining the touch cannot produce that."""
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.52"), D("80"))])
    assert p["combinations_within_ceiling"] > 0
    assert D(p["cheapest_pair_if_all_filled"]) <= \
        TS.ADAPTED_EXECUTABLE_PAIR_CEILING


def test_the_grid_is_frozen_in_advance():
    assert IP.DEEP_GRID_TICKS == (0, 1, 2, 3, 4)
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert {x["up_ticks_below"] for x in p["pairs"]} == {0, 1, 2, 3, 4}


def test_the_whole_ladder_rests_rather_than_one_chosen_pair():
    """Choosing the cheapest admissible pair optimised toward NEVER FILLING,
    and a live Gemini book proved it: a 1-cent Down bid on a market worth ~0.38
    made a 0.636 'pair' look admissible whose Down leg could never be hit."""
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert len(p["quotes"]) == 10          # 5 levels x 2 sides
    assert {q["ticks_below_touch"] for q in p["quotes"]} == {0, 1, 2, 3, 4}
    assert "chosen_pair" not in p


def test_a_one_cent_bid_still_rests_but_is_not_dressed_up_as_a_good_pair():
    p = _plan("D", [(D("0.62"), D("50"))], [(D("0.01"), D("99"))])
    dn = [q for q in p["quotes"] if q["outcome"] == "Down"]
    assert [q["price"] for q in dn] == ["0.01"]   # nothing below a cent
    # the cheap combination is RECORDED, not selected and not acted on
    assert p["cheapest_pair_if_all_filled"] is not None
    assert all("within_ceiling_if_it_forms" in x for x in p["pairs"])


def test_every_combination_is_priced_but_none_is_selected():
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert len(p["pairs"]) == 25
    assert "IF both" in p["reason"] and "not" in p["reason"]


def test_arms_r_and_s_rest_only_at_the_touch():
    for arm in ("R", "S"):
        lv = IP.levels([(D("0.45"), D("100"))], tick=D("0.01"), arm=arm)
        assert [x["ticks_below_touch"] for x in lv] == [0]


def test_the_ceiling_is_applied_to_the_pair_that_forms_not_one_chosen_early():
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert all("within_ceiling_if_it_forms" in x for x in p["pairs"])
    assert "which is what we are measuring, not" in p["reason"]


# ------------------------------------------------ and it may not cheat

def test_it_never_rests_above_the_touch():
    """Resting above the bid is crossing toward the offer."""
    for arm in ARMS_ALL:
        lv = IP.levels([(D("0.45"), D("100"))], tick=D("0.01"), arm=arm)
        assert all(D(x["price"]) <= D("0.45") for x in lv)


ARMS_ALL = IP.ARMS


def test_a_pair_outside_the_ceiling_is_recorded_as_outside_it():
    p = _plan("S", [(D("0.50"), D("100"))], [(D("0.50"), D("80"))])
    bad = p["pairs"][0]
    assert bad["within_ceiling_if_it_forms"] is False
    assert bad["pair_economics"] == "POSITIVE_BUT_ABOVE_OUR_CEILING"


def test_every_pair_carries_its_economics_classification():
    p = _plan("D", [(D("0.60"), D("100"))], [(D("0.60"), D("80"))])
    assert any(x["pair_economics"] == "ECONOMICALLY_NEGATIVE"
               for x in p["pairs"])
    assert all(x["eligible_for_live_promotion"] is False
               for x in p["pairs"] if D(x["pair_cost"]) > D("1"))


def test_an_unreadable_book_yields_no_level_rather_than_an_invented_one():
    assert IP.levels(None, tick=D("0.01"), arm="D") == []
    p = _plan("D", None, [(D("0.50"), D("80"))])
    assert p["quotes"] == [] and "cannot host" in p["reason"]


def test_an_empty_side_is_reported_as_the_venue_not_hosting_it():
    p = _plan("D", [], [(D("0.50"), D("80"))])
    assert p["quotes"] == [] and "Up" in p["reason"]


def test_undeclared_venue_terms_refuse_rather_than_assume():
    bad = T.pm_terms({"fee_rate": None, "fee_taker_only": None, "tick": None,
                      "terms_source": "t"})
    p = _plan("S", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))],
              terms=bad)
    assert p["quotes"] == [] and "did not declare" in p["reason"]


def test_a_level_below_zero_is_dropped():
    lv = IP.levels([(D("0.02"), D("10"))], tick=D("0.01"), arm="D")
    assert all(D(x["price"]) > 0 for x in lv)


# ------------------------------- predetermined lifetime, not an arbitrary one

def test_it_quotes_from_the_window_opening_because_l5_does():
    """L5 BTC15m is in within ~13s of the open, TTE median 887 of 900."""
    assert IP.desired_inventory(_st(tte_s=890.0))["quoting"] is True
    assert IP.desired_inventory(_st(tte_s=901.0))["quoting"] is False


def test_it_stops_inside_the_final_minute_where_the_venue_withdraws():
    d = IP.desired_inventory(_st(tte_s=30.0))
    assert d["quoting"] is False and "final" in d["why_not"]


def test_a_quote_is_cancelled_by_state_not_by_a_post_fill_timer():
    q = {"outcome": "Up", "price": "0.45"}
    assert IP.should_cancel(q, _st(tte_s=800.0)) is None
    assert "final" in IP.should_cancel(q, _st(tte_s=30.0))
    assert "desired inventory" in IP.should_cancel(
        q, _st(inv_up=str(IP.DESIRED_PER_SIDE)))
    assert "window closed" in IP.should_cancel(
        q, _st(window_closed=True))


def test_a_moving_book_never_cancels_a_resting_quote():
    """Repricing on movement is how a maker becomes a taker in slow motion,
    and it would make the deep grid meaningless."""
    import inspect
    src = inspect.getsource(IP.should_cancel)
    assert "best" not in src and "touch" not in src
    assert "Nothing cancels because the book" in inspect.getdoc(IP.should_cancel)


def test_it_is_labelled_a_candidate_everywhere():
    p = _plan("D", [(D("0.45"), D("100"))], [(D("0.50"), D("80"))])
    assert p["label"] == IP.LABEL == "MODEL_CANDIDATE/continuous_desired_inventory"
    assert IP.ARMS == ("R", "S", "D")


# --------------------- the service wiring: continuous, and never repriced

def test_the_candidate_runs_only_on_the_maker_cohort(tmp_path):
    """BTC5m is a predominantly-taker regime; running a maker-inventory
    candidate there would imitate nothing."""
    import src.l5_shadow.service as S
    assert S.observe_inventory(str(tmp_path), window=None, cohort="BTC5m",
                               market_key="m", tte_s=800.0,
                               inv={"up": "0", "dn": "0"}, now=1.0) == []


def test_an_inventory_quote_is_decided_at_the_end_of_its_life_not_a_horizon():
    """A fixed 300s horizon was an artefact of quoting after an L5 fill, and it
    would truncate exactly the long rest this candidate exists to test."""
    import inspect
    import src.l5_shadow.service as S
    src = inspect.getsource(S.main)
    assert "IP.should_cancel(q, st)" in src
    assert "rest for their FULL predetermined life" in src


def test_arm_s_and_d_outcomes_do_not_land_in_the_reactive_store(tmp_path,
                                                                monkeypatch):
    import json

    import src.l5_shadow.service as S
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_trades", lambda *a, **k: [])
    for arm, store in (("R", S.MAKER_OUT), ("S", S.TWO_SIDED_OUT),
                       ("D", S.INVENTORY_OUT)):
        S.resolve_maker(str(tmp_path),
                        {"venue": "PMUS", "intent_id": f"i{arm}", "arm": arm,
                         "price": "0.45", "qty": "10", "queue_ahead": "0",
                         "placed_at": 1.0, "condition_id": "c",
                         "cohort": "BTC15m"},
                        now=400.0)
        rows = [json.loads(l) for l in
                (tmp_path / store).read_text().splitlines() if l.strip()]
        assert rows[-1]["intent_id"] == f"i{arm}" and rows[-1]["arm"] == arm


def test_an_inventory_outcome_records_why_it_was_cancelled_and_how_long_it_rested(
        tmp_path, monkeypatch):
    import json

    import src.l5_shadow.service as S
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_trades", lambda *a, **k: [])
    S.resolve_maker(str(tmp_path),
                    {"venue": "PMUS", "intent_id": "i", "arm": "D",
                     "price": "0.43", "qty": "10", "queue_ahead": "0",
                     "placed_at": 1.0, "condition_id": "c",
                     "cohort": "BTC15m", "ticks_below_touch": 2,
                     "cancel_reason": "window closed", "rested_s": 812.0},
                    now=900.0)
    r = json.loads((tmp_path / S.INVENTORY_OUT).read_text().splitlines()[0])
    assert r["cancel_reason"] == "window closed" and r["rested_s"] == 812.0
    assert r["ticks_below_touch"] == 2 and r["cohort"] == "BTC15m"
    assert r["horizon_s"] is None        # not a fixed-horizon quote
