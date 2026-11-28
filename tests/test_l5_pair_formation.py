"""Why the clone ends up naked — the pair-formation forensics.

11 evidenced fills produced 7 positions, 0 paired, $0.00 matched-pair P&L and
−$102.54 of directional residual. That is not L5's mechanism performing badly;
it is a different mechanism, and raising the generic fill rate is not
guaranteed to fix it.

So the thing under test is the CLASSIFICATION. Nine causes point at different
work, and three of them are not our failure at all — collapsing any of them
into "we missed a fill" would send the whole programme after the wrong thing.
"""
import json

import pytest

import analysis.l5.pair_formation.study as P


def _root(tmp_path, *, intents=(), places=(), outcomes=(), routes=(), evos=()):
    for name, rows in ((P.INTENTS, intents), (P.EVALS, places),
                       (P.MAKER_OUT, outcomes), (P.ROUTES, routes),
                       (P.EVOLUTION, evos)):
        with open(tmp_path / name, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return str(tmp_path)


def _intent(iid, outcome, *, t=100.0, market="m", ceiling="0.45", qty="10"):
    return {"id": iid, "market_key": market, "desired_outcome": outcome,
            "t_utc": t, "max_acceptable_price": ceiling,
            "incremental_qty": qty, "action": "OPEN",
            "schema": "l5_shadow/intent"}


def _route(iid, *, cohort="BTC15m", expired=False, age=15.0):
    return {"intent_id": iid, "cohort": cohort, "intent_expired": expired,
            "intent_age_at_decision_s": age, "intent_valid_for_s": 30.0}


def _place(iid, outcome, *, venue="PMUS", t=110.0, market="m",
           submittable=True, price="0.45", bid="0.44", ahead="0", reason="ok"):
    return {"schema": "l5_shadow/maker_placement", "intent_id": iid,
            "venue": venue, "market_key": market, "desired_outcome": outcome,
            "placed_at": t, "submittable": submittable, "price": price,
            "best_bid": bid, "queue_ahead": ahead, "reason": reason}


def _out(iid, outcome, fill, *, venue="PMUS", t=110.0, market="m",
         price="0.45", qty="10", thru="0", ahead="0"):
    return {"intent_id": iid, "venue": venue, "market_key": market,
            "desired_outcome": outcome, "evidenced_fill": fill,
            "placed_at": t, "quote_price": price, "quote_qty": qty,
            "traded_through": thru, "queue_ahead": ahead, "reason": "r"}


def _one(tmp_path, **kw):
    eps = P.episodes(_root(tmp_path, **kw))
    assert len(eps) == 1
    return eps[0]


# ---------------------------------------- only an evidenced fill opens one

def test_an_episode_opens_only_on_an_evidenced_fill(tmp_path):
    eps = P.episodes(_root(tmp_path,
                           intents=[_intent("a", "Up")],
                           routes=[_route("a")],
                           outcomes=[_out("a", "Up", False)]))
    assert eps == []


def test_only_the_first_fill_in_a_market_opens_an_episode(tmp_path):
    eps = P.episodes(_root(
        tmp_path, intents=[_intent("a", "Up"), _intent("b", "Up", t=200.0)],
        routes=[_route("a"), _route("b")],
        outcomes=[_out("a", "Up", True, t=110.0),
                  _out("b", "Up", True, t=210.0)]))
    assert len(eps) == 1 and eps[0]["first_fill"]["intent_id"] == "a"


def test_the_two_venues_are_separate_episodes(tmp_path):
    eps = P.episodes(_root(
        tmp_path, intents=[_intent("a", "Up")], routes=[_route("a")],
        outcomes=[_out("a", "Up", True, venue="PMUS"),
                  _out("a", "Up", True, venue="GEMINI")]))
    assert {e["venue"] for e in eps} == {"PMUS", "GEMINI"}


# ------------------------------------- the source, not us, stayed one-sided

def test_l5_never_going_the_other_way_is_not_our_failure(tmp_path):
    e = _one(tmp_path, intents=[_intent("a", "Up")], routes=[_route("a")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "NO_OPPOSITE_L5_INTENT"
    assert e["paired"] is False
    assert "not late" in e["why"] or "stayed one-sided" in e["why"]
    assert e["transfer_failure"] is False


def test_an_opposite_intent_before_our_fills_own_event_does_not_count(tmp_path):
    """Anchored on L5's event clock. A Down he took BEFORE the Up we mirrored
    is not the complement to that Up."""
    e = _one(tmp_path,
             intents=[_intent("z", "Down", t=50.0), _intent("a", "Up", t=100.0)],
             routes=[_route("z"), _route("a")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "NO_OPPOSITE_L5_INTENT"


# ------------------------------------------------- where the complement died

def test_a_decayed_complement_intent_is_named_as_such(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b", expired=True, age=45.0)],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "OPPOSITE_INTENT_EXPIRED"
    assert "45.0s" in e["why"]


def test_an_empty_opposite_book_is_a_transfer_failure_not_a_price_we_declined(
        tmp_path):
    """The venue could not host the complement at ANY price. That is a fact
    about the destination, and it is not answerable by paying more."""
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", submittable=False, bid=None,
                            price=None,
                            reason="no bid to join at or under the ceiling")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "VENUE_LIQUIDITY_WITHDRAWAL"
    assert e["transfer_failure"] is True
    assert "any price" in e["why"]


def test_a_bid_above_the_ceiling_is_unreachable_and_paying_it_is_forbidden(
        tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", submittable=False, bid="0.60",
                            price=None,
                            reason="no bid to join at or under the ceiling 0.45")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "OPPOSITE_PRICE_UNREACHABLE"
    assert e["transfer_failure"] is False
    assert "forbidden" in e["why"]


def test_a_venue_minimum_that_rejects_the_complement_is_a_transfer_failure(
        tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", submittable=False,
                            reason="venue minimum order size is 5; 2 cannot "
                                   "rest here at all")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "OPPOSITE_ORDER_UNSUBMITTABLE"
    assert e["transfer_failure"] is True


def test_resting_at_a_good_price_with_no_flow_is_not_a_queue_problem(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=160.0)],
             outcomes=[_out("a", "Up", True),
                       _out("b", "Down", False, t=160.0, thru="0", ahead="50")])
    assert e["cause"] == "OPPOSITE_QUOTE_NO_FLOW"
    assert "Queue position is irrelevant" in e["why"]


def test_flow_absorbed_by_the_queue_ahead_is_the_one_a_better_queue_converts(
        tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=160.0)],
             outcomes=[_out("a", "Up", True),
                       _out("b", "Down", False, t=160.0, thru="30",
                            ahead="50")])
    assert e["cause"] == "OPPOSITE_QUEUE_BOUND"


def test_a_newer_intent_superseding_an_undecided_quote_is_named(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0),
                      _intent("c", "Down", t=200.0)],
             routes=[_route("a"), _route("b"), _route("c")],
             places=[_place("b", "Down", t=160.0),
                     _place("c", "Down", t=210.0)],
             outcomes=[_out("a", "Up", True),
                       _out("b", "Down", None, t=160.0)])
    assert e["cause"] == "OPPOSITE_QUOTE_CANCELLED_BY_NEW_INTENT"


def test_an_unreadable_verdict_is_data_unknown_never_a_no_fill(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=160.0)],
             outcomes=[_out("a", "Up", True),
                       _out("b", "Down", None, t=160.0)])
    assert e["cause"] == "DATA_UNKNOWN"
    assert "never a no-fill" in e["why"]


def test_a_missing_placement_row_is_data_unknown_not_a_guess(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             outcomes=[_out("a", "Up", True)])
    assert e["cause"] == "DATA_UNKNOWN"
    assert "will not guess" in e["why"]


def test_every_cause_produced_is_in_the_declared_tuple():
    import inspect
    src = inspect.getsource(P)
    for token in src.split('_fail(ep, "')[1:]:
        assert token.split('"')[0] in P.CAUSES, token[:40]


# ------------------------------------------------- and when a pair does form

def test_a_formed_pair_is_priced_because_forming_one_badly_is_not_success(
        tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=160.0)],
             outcomes=[_out("a", "Up", True, t=110.0, price="0.45"),
                       _out("b", "Down", True, t=160.0, price="0.50")])
    assert e["paired"] is True and e["cause"] is None
    assert e["effective_pair_cost"] == "0.950000"
    assert e["matched_qty"] == "10"
    assert e["residual_duration_s"] == 50.0


def test_the_matched_quantity_is_the_smaller_leg(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up"), _intent("b", "Down", t=150.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=160.0)],
             outcomes=[_out("a", "Up", True, qty="10"),
                       _out("b", "Down", True, t=160.0, qty="4")])
    assert e["matched_qty"] == "4"


# --------------------------------------------------------- the conditionals

def test_the_two_conditionals_separate_never_tried_from_tried_and_missed():
    """P(opposite | first fill) mixes in every episode where no complement
    quote ever rested. The second conditional is the execution question."""
    eps = [{"paired": True, "venue": "PMUS", "cohort": "BTC15m",
            "opposite_outcome": {}, "effective_pair_cost": "0.95"},
           {"paired": False, "cause": "OPPOSITE_QUOTE_NO_FLOW",
            "venue": "PMUS", "cohort": "BTC15m", "opposite_outcome": {}},
           {"paired": False, "cause": "NO_OPPOSITE_L5_INTENT",
            "venue": "PMUS", "cohort": "BTC15m"}]
    s = P.summarise(eps)
    assert s["P_opposite_fill_given_first_fill"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["complement_quote_rested"] == 2
    assert s["P_opposite_fill_given_complement_rested"] == 0.5


def test_transfer_failures_are_counted_apart_from_our_own_misses():
    eps = [{"paired": False, "cause": "VENUE_LIQUIDITY_WITHDRAWAL",
            "transfer_failure": True, "venue": "GEMINI", "cohort": "BTC5m"},
           {"paired": False, "cause": "OPPOSITE_QUEUE_BOUND",
            "transfer_failure": False, "venue": "GEMINI", "cohort": "BTC5m"}]
    s = P.summarise(eps)
    assert s["transfer_failures"] == 1
    assert s["by_venue_cohort"]["GEMINI|BTC5m"] == {
        "VENUE_LIQUIDITY_WITHDRAWAL": 1, "OPPOSITE_QUEUE_BOUND": 1}


def test_summarising_nothing_reports_none_not_zero():
    s = P.summarise([])
    assert s["P_opposite_fill_given_first_fill"] is None
    assert s["effective_pair_cost"] is None


def test_the_report_forbids_the_easy_answers():
    r = P.report("/nonexistent")
    for banned in ("widening price", "crossing the spread",
                   "extending an expired intent", "more residual risk"):
        assert banned in r["labels"]


# ------------------- the two defects the service caught by disagreeing

def test_a_complement_that_filled_is_a_pair_even_with_no_joinable_intent(
        tmp_path):
    """The defect: walking the intent chain FIRST filed a complement reached
    via an unjoinable intent as DATA_UNKNOWN. A SUCCESS misclassified as an
    unexplained failure — the worst direction for a diagnostic to be wrong in,
    and how this module came to report 0 paired while the arm comparison, on
    the same store, reported 2."""
    e = _one(tmp_path,
             intents=[_intent("a", "Up")], routes=[_route("a")],
             outcomes=[_out("a", "Up", True, t=110.0, price="0.45"),
                       # no intent row, no placement row, no route for "ghost"
                       _out("ghost", "Down", True, t=160.0, price="0.50")])
    assert e["paired"] is True and e["cause"] is None
    assert e["effective_pair_cost"] == "0.950000"


def test_the_earliest_complement_fill_is_the_one_that_pairs(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up")], routes=[_route("a")],
             outcomes=[_out("a", "Up", True, t=110.0, price="0.45"),
                       _out("x", "Down", True, t=300.0, price="0.90"),
                       _out("y", "Down", True, t=160.0, price="0.50")])
    assert e["effective_pair_cost"] == "0.950000"
    assert e["residual_duration_s"] == 50.0


def test_a_complement_fill_in_another_market_does_not_pair(tmp_path):
    """It opens its own episode instead — a fill in another window is another
    window's first leg, never this one's complement."""
    eps = P.episodes(_root(tmp_path, intents=[_intent("a", "Up")],
                           routes=[_route("a")],
                           outcomes=[_out("a", "Up", True),
                                     _out("b", "Down", True, market="other")]))
    assert {e["market_key"] for e in eps} == {"m", "other"}
    assert all(e["paired"] is False for e in eps)


def test_a_complement_fill_on_the_other_venue_does_not_pair(tmp_path):
    """Inventory is per venue. Up on Polymarket and Down on Gemini is two
    directional bets, not a matched pair."""
    eps = P.episodes(_root(
        tmp_path, intents=[_intent("a", "Up")], routes=[_route("a")],
        outcomes=[_out("a", "Up", True, venue="PMUS"),
                  _out("b", "Down", True, venue="GEMINI")]))
    pm = [e for e in eps if e["venue"] == "PMUS"][0]
    assert pm["paired"] is False


def test_the_complement_timing_is_on_l5s_clock_at_both_ends(tmp_path):
    """The defect: differencing L5's event time against OUR placement time
    produced medians of -172s — a complement intent 'arriving' three minutes
    before the fill it follows. Our placement is ~15s of observation lag plus
    a 300s horizon after his event, so the two were never comparable."""
    e = _one(tmp_path,
             intents=[_intent("a", "Up", t=1000.0),
                      _intent("b", "Down", t=1054.0)],
             routes=[_route("a"), _route("b")],
             outcomes=[_out("a", "Up", True, t=1315.0)])
    assert e["l5_first_to_opposite_intent_s"] == 54.0
    assert "t_first_fill_to_opposite_intent_s" not in e


def test_the_cross_clock_latency_is_labelled_as_cross_clock(tmp_path):
    e = _one(tmp_path,
             intents=[_intent("a", "Up", t=1000.0),
                      _intent("b", "Down", t=1054.0)],
             routes=[_route("a"), _route("b")],
             places=[_place("b", "Down", t=1070.0)],
             outcomes=[_out("a", "Up", True, t=1315.0)])
    assert e["t_opposite_intent_to_quote_s"] == 16.0
    assert e["t_opposite_intent_to_quote_s_is_cross_clock"] is True


def test_the_forensics_and_the_arm_comparison_agree_about_what_a_pair_is(
        tmp_path):
    """They read the same store and disagreed once. They must not again."""
    import analysis.l5.pair_formation.arms as A
    outcomes = [_out("a", "Up", True, t=110.0, price="0.45"),
                _out("b", "Down", True, t=160.0, price="0.50"),
                _out("c", "Up", True, market="solo", t=200.0)]
    root = _root(tmp_path, intents=[_intent("a", "Up")], routes=[_route("a")],
                 outcomes=outcomes)
    eps = P.episodes(root)
    forensic_paired = sum(1 for e in eps if e.get("paired"))
    arm = A.arm_pairs(outcomes)
    assert forensic_paired == arm["paired"] == 1
    assert len(eps) == arm["markets_with_a_fill"] == 2
