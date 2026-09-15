"""P1 — the same frozen intent under two observation clocks.

The chain carries L5's fills 15.16s ahead of publication. That is a fact about
OBSERVATION. Whether it is worth anything depends on what the extra 15 seconds
lets us do, and the only way to answer that without fooling ourselves is to
freeze the intent and change nothing but when we could first act on it.

Hindsight would enter in exactly one place: pricing arm B against a book we
read fifteen seconds later. These tests pin that it cannot.
"""
import pytest

import analysis.l5.dual_clock.study as DC


def _place(t, *, venue="PMUS", market="m", outcome="Up", join="0.45",
           bid="0.44", ahead="0", submittable=True, iid="i1"):
    return {"schema": "l5_shadow/maker_placement", "venue": venue,
            "market_key": market, "desired_outcome": outcome,
            "intent_id": iid, "placed_at": t, "submittable": submittable,
            "join_price": join, "best_bid": bid, "queue_ahead": ahead,
            "qty": "10"}


def _route(iid="i1", *, age=15.9, ceiling="0.45", market="m"):
    return {"intent_id": iid, "cohort": "BTC15m", "market_key": market,
            "desired_outcome": "Up", "max_acceptable_price": ceiling,
            "intent_age_at_decision_s": age}


# ------------------------------------------------- the clocks themselves

def test_the_chain_arm_is_dated_from_the_anchors_own_block_time():
    """Not the 15.16s average: the feed's timestamp IS the block timestamp, so
    the per-anchor chain moment is exact."""
    r = DC.compare_one(_route(age=15.9), _place(1000.0), [], None)
    assert r["t_event"] == pytest.approx(984.1)
    assert r["t_chain"] == pytest.approx(984.1 + DC.CHAIN_READABLE_S)
    assert r["recovery_s"] == pytest.approx(15.53, abs=0.01)


def test_the_chain_arm_sees_a_nearly_fresh_intent():
    r = DC.compare_one(_route(age=15.9), _place(1000.0), [], None)
    assert r["A_age_s"] == pytest.approx(15.9)
    assert r["B_age_s"] == DC.CHAIN_READABLE_S


def test_an_intent_dead_on_arrival_under_the_feed_is_alive_under_the_chain():
    """The blocker this was meant to recover: 83% of intents decay in 30s."""
    r = DC.compare_one(_route(age=45.0), _place(1000.0), [], None)
    assert r["A_valid_when_observable"] is False
    assert r["B_valid_when_observable"] is True


# --------------------------------------------------- and no hindsight

def test_arm_b_is_never_priced_from_a_later_book():
    """The one place hindsight could enter. A snapshot taken AFTER arm A's own
    read is not arm B's book at any price."""
    ev = [_place(1000.0), _place(1010.0, join="0.20")]
    routes = {"i1": _route(age=15.9)}
    snaps = DC.snapshots(DC.placements(ev))
    key = ("PMUS", "m", "Up")
    earlier = [s for s in snaps[key] if s["placed_at"] < 1000.0]
    assert earlier == []
    r = DC.compare_one(routes["i1"], ev[0], earlier, None)
    assert r["B_state"] == "UNKNOWN_NO_BOOK_AT_THAT_MOMENT"
    assert r["B_join_price"] is None


def test_arm_b_is_unknown_rather_than_approximated_when_no_book_is_near():
    r = DC.compare_one(_route(age=15.9), _place(1000.0),
                       [_place(900.0)], None)
    assert r["B_state"] == "UNKNOWN_NO_BOOK_AT_THAT_MOMENT"
    assert "not approximated" in r["why"]


def test_a_book_recorded_at_the_chain_moment_prices_arm_b():
    snap = _place(984.5, join="0.42", bid="0.41")
    r = DC.compare_one(_route(age=15.9), _place(1000.0), [snap], None)
    assert r["B_state"] == "PRICED_FROM_A_RECORDED_BOOK"
    assert r["B_join_price"] == "0.42"
    assert r["pair_error_s"] == pytest.approx(0.03, abs=0.02)


def test_the_pairing_error_is_reported_on_every_priced_row():
    r = DC.compare_one(_route(age=15.9), _place(1000.0),
                       [_place(987.0)], None)
    assert r["pair_error_s"] is not None
    assert r["B_state"] == "PRICED_FROM_A_RECORDED_BOOK"


def test_the_nearest_earlier_read_wins():
    r = DC.compare_one(_route(age=15.9), _place(1000.0),
                       [_place(980.0, join="0.30"),
                        _place(984.4, join="0.42")], None)
    assert r["B_join_price"] == "0.42"


# --------------------------------------- placeability and reachability

def test_a_quote_placeable_only_under_the_chain_clock_is_counted():
    snap = _place(984.5, join="0.44")
    r = DC.compare_one(_route(age=15.9),
                       _place(1000.0, submittable=False, join=None), [snap],
                       None)
    s = DC.summarise([r])
    assert s["placeable_only_under_chain"] == 1
    assert s["placeable_only_under_feed"] == 0


def test_reachability_is_measured_against_l5s_own_achieved_price():
    snap = _place(984.5, join="0.50")           # above the ceiling
    r = DC.compare_one(_route(age=15.9, ceiling="0.45"), _place(1000.0),
                       [snap], None)
    assert r["A_reachable"] is True             # A joins at 0.45
    assert r["B_reachable"] is False


def test_an_unplaceable_arm_is_not_reachable():
    r = DC.compare_one(_route(age=15.9), _place(1000.0),
                       [_place(984.5, join=None, submittable=False)], None)
    assert r["B_reachable"] is False


# --------------------------------- fills are evidenced, never modelled

def _outcome(fill, prints, qty="10"):
    return {"evidenced_fill": fill, "quote_qty": qty, "evidence": prints}


def test_arm_b_is_not_determinable_when_it_would_rest_below_arm_a():
    """The stored prints are only those that crossed arm A. For a lower price
    that list is a SUBSET, so absence in it is not evidence of no fill."""
    snap = _place(984.5, join="0.40")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(True, [{"t": 990.0, "price": "0.44",
                                        "size": "50"}]))
    assert r["B_fill"] == "NOT_DETERMINABLE"
    assert "subset" in r["B_fill_why"]


def test_arm_b_is_decided_when_it_rests_at_or_above_arm_a():
    snap = _place(984.5, join="0.45", ahead="0")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(True, [{"t": 990.0, "price": "0.44",
                                        "size": "50"}]))
    assert r["B_fill"] == "FILLED"
    assert r["B_traded_through"] == "50"


def test_prints_before_the_chain_moment_do_not_fill_arm_b():
    """A trade that already happened cannot fill an order that did not exist."""
    snap = _place(984.5, join="0.45")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(True, [{"t": 980.0, "price": "0.44",
                                        "size": "50"}]))
    assert r["B_fill"] == "NO_FILL"


def test_the_queue_ahead_of_arm_b_is_respected():
    snap = _place(984.5, join="0.45", ahead="100")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(True, [{"t": 990.0, "price": "0.44",
                                        "size": "50"}]))
    assert r["B_fill"] == "NO_FILL"


def test_when_arm_a_saw_no_prints_neither_arm_is_decided():
    snap = _place(984.5, join="0.45")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(None, []))
    assert r["A_fill"] == "UNKNOWN" and r["B_fill"] == "UNKNOWN"


def test_a_fill_gained_only_by_the_earlier_clock_is_counted():
    snap = _place(984.5, join="0.45")
    r = DC.compare_one(_route(age=15.9), _place(1000.0, join="0.45"), [snap],
                       _outcome(False, [{"t": 990.0, "price": "0.44",
                                         "size": "50"}]))
    s = DC.summarise([r])
    assert r["A_fill"] == "NO_FILL" and r["B_fill"] == "FILLED"
    assert s["filled_only_under_chain"] == 1


# ------------------------------------------------------------ the report

def test_the_report_keeps_unknown_arms_visible(tmp_path):
    import json
    (tmp_path / "evaluations.jsonl").write_text(
        json.dumps(_place(1000.0)) + "\n")
    (tmp_path / "routes.jsonl").write_text(json.dumps(_route()) + "\n")
    (tmp_path / "maker_outcomes.jsonl").write_text("")
    rep = DC.report(str(tmp_path))
    assert rep["compared"] == 1
    assert rep["arm_B_priced"] == 0 and rep["arm_B_unknown"] == 1
    assert rep["B_unknown_reasons"]


def test_the_report_says_the_intent_is_frozen_and_the_observer_unpromoted(
        tmp_path):
    (tmp_path / "evaluations.jsonl").write_text("")
    rep = DC.report(str(tmp_path))
    assert "FROZEN" in rep["labels"]
    assert "UNPROMOTED" in rep["labels"]
    from src.l5_chain_observer import observer as O
    assert O.PROMOTED is False


def test_summarising_nothing_reports_none_not_zero():
    s = DC.summarise([])
    assert s["A_placeable_frac"] is None and s["median_recovery_s"] is None
