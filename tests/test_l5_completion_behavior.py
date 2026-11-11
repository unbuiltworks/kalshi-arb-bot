"""What L5 does when completing a pair, and what the fills cannot tell us."""
from __future__ import annotations

from src import l5_completion_behavior as cb


def _fill(wid, seq, ts, outcome, price, tts=100):
    return {"window_id": wid, "trade_seq": str(seq), "timestamp": str(ts),
            "outcome": outcome, "side": "BUY", "price": str(price),
            "market_slug": "m", "duration_minutes": "5",
            "seconds_to_settlement": str(tts)}


def test_a_window_that_never_bought_the_other_side_counts_as_incomplete():
    """Dropping it would flatter L5's completion rate exactly the way
    conditioning on success always does."""
    by = {"w1": [_fill("w1", 1, 100, "Up", 0.4),
                 _fill("w1", 2, 110, "Up", 0.41)]}
    ev = cb.completion_events(by)
    assert len(ev) == 1 and ev[0]["completed"] is False


def test_time_to_complete_is_first_leg_to_first_opposite_fill():
    by = {"w1": [_fill("w1", 1, 100, "Up", 0.4),
                 _fill("w1", 2, 153, "Down", 0.58)]}
    e = cb.completion_events(by)[0]
    assert e["completed"] and e["seconds_to_complete"] == 53.0


def test_single_fill_completions_are_excluded_from_the_repricing_number():
    """A completion with one fill has ONE price by arithmetic. Counting it
    as evidence of single-pricing is simply wrong, and counting it in the
    denominator understates repricing -- the confounded first version of
    this number read 78.6% where the real one is 98.5%."""
    done = [{"completing_fills": 1, "completing_distinct_prices": 1},
            {"completing_fills": 4, "completing_distinct_prices": 3},
            {"completing_fills": 2, "completing_distinct_prices": 1}]
    r = cb._repricing_deconfounded(done)
    assert r["completions_with_at_least_two_fills"] == 2
    assert r["of_those_at_more_than_one_price"] == 1
    assert r["share"] == 0.5
    assert r["single_fill_completions_excluded"] == 1


def test_fast_completions_are_compared_against_our_own_window():
    """The split is at 31 s because that is what our implementation does,
    not because 31 is special to L5."""
    done = [{"seconds_to_complete": 10.0, "completing_distinct_prices": 7,
             "completing_fills": 8},
            {"seconds_to_complete": 90.0, "completing_distinct_prices": 3,
             "completing_fills": 3}]
    s = cb._by_speed(done)
    assert s["within_31s"]["n"] == 1 and s["within_31s"]["median_distinct_prices"] == 7
    assert s["over_31s"]["n"] == 1 and s["over_31s"]["median_distinct_prices"] == 3


def test_sells_are_not_treated_as_pairing_legs():
    """L5 sold $2.18 in his entire lifetime; a SELL is not a completion."""
    by = cb.windows.__wrapped__ if hasattr(cb.windows, "__wrapped__") else None
    rows = [_fill("w1", 1, 100, "Up", 0.4)]
    rows.append({**_fill("w1", 2, 110, "Down", 0.6), "side": "SELL"})
    # completion_events consumes already-filtered rows, so assert the
    # filter itself lives in windows()
    import inspect
    src = inspect.getsource(cb.windows)
    assert 'side", "")).upper() != "BUY"' in src


def test_the_observability_limit_is_reported_not_buried():
    """Fills only: no orders, no cancels, no resting prices. Every timing
    number here is bounded by that and the report must say so."""
    import inspect
    src = inspect.getsource(cb)
    assert "FILLS ONLY" in src
    assert "not how fast he repriced" in src


def test_it_reads_and_never_acts():
    import inspect
    src = inspect.getsource(cb)
    body = src.split('"""', 2)[-1]
    for forbidden in ("place_limit_buy", "session_runner", "run_campaign(",
                      "os.environ[", "requests."):
        assert forbidden not in body, forbidden
