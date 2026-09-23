"""The blindness invariant must not be able to declare itself fixed.

Every test here exists because a specific way of getting this wrong would let
a still-blind window score as clean:

  * measuring first-to-last span instead of coverage would report ~100% for a
    tape with a ten-minute hole in it;
  * an identity check that compares a slug with itself passes by construction,
    so a book recorded under one market and trades under another would read as
    one market;
  * counting non-OK book states as usable would let a one-sided or stale
    snapshot stand in for the two-sided book the faithful rule needs;
  * forward-filling between two states, or backfilling before the first one,
    would manufacture exactly the opening-sequence observation whose absence
    is the thing being measured.
"""
from __future__ import annotations

import datetime as dt

from src import l5_authenticated_observation as A

SLUG = "cpc-btc-updown-15m-2026-09-23-2115z"
OTHER = "cpc-btc-updown-15m-2026-09-23-2130z"
WS = dt.datetime(2026, 9, 23, 21, 15, tzinfo=dt.timezone.utc).timestamp()


def book(offset, status="OK", bid="0.40", ask="0.60",
         bid_qty="100", ask_qty="100"):
    return {"t": WS + offset, "bid": bid, "ask": ask,
            "bid_qty": bid_qty, "ask_qty": ask_qty, "status": status}


def action(offset, slug=SLUG):
    return {"action_key": "k%s" % offset,
            "receipt_utc": dt.datetime.fromtimestamp(
                WS + offset, dt.timezone.utc).isoformat(),
            "pmus": {"slug": slug}}


def ev(books=(), trades=(), actions=(), slug=SLUG):
    return A.evaluate(slug, {SLUG: list(books)}, {SLUG: list(trades)},
                      list(actions))


# ------------------------------------------------------------ window anchor

def test_window_start_comes_from_the_slug():
    assert A.window_start_epoch(SLUG) == WS


def test_an_unparseable_slug_yields_no_anchor_rather_than_a_guess():
    assert A.window_start_epoch("cpc-nfl-buf-mia-2026-09-23") is None
    assert ev([book(1.0)], slug="cpc-nfl-buf-mia")["first_book_latency_s"] \
        is None


# --------------------------------------------------------------- blindness

def test_book_before_first_trade_is_not_blind():
    r = ev([book(6.0)], trades=[WS + 9.0], actions=[action(6.0)])
    assert r["first_book_latency_s"] == 6.0
    assert r["first_trade_latency_s"] == 9.0
    assert r["book_observed_before_first_trade"] is True
    assert r["verdict"] == A.OK


def test_trading_before_we_can_see_the_book_is_blind_at_open():
    r = ev([book(120.0)], trades=[WS + 9.0], actions=[action(120.0)])
    assert r["book_observed_before_first_trade"] is False
    assert r["verdict"] == A.BLIND


def test_no_usable_book_at_all_is_blind_not_merely_partial():
    r = ev([], trades=[WS + 9.0])
    assert r["first_book_latency_s"] is None
    assert r["verdict"] == A.BLIND


def test_only_two_sided_ok_states_count_as_usable():
    states = [book(5.0, status="ONE_SIDED"), book(7.0, status="STALE"),
              book(9.0, status="NO_BOOK"), book(11.0, status="DISCONNECTED"),
              book(30.0)]
    r = ev(states, trades=[WS + 9.0])
    assert r["usable_book_states"] == 1
    assert r["book_states_total"] == 5
    assert r["status_breakdown"]["ONE_SIDED"] == 1
    assert r["first_book_latency_s"] == 30.0
    assert r["verdict"] == A.BLIND          # 30.0 > 9.0


# ---------------------------------------------------------------- coverage

def test_coverage_is_freshness_not_first_to_last_span():
    # a ten-minute hole: span would say 66%, coverage says 3.3%
    r = ev([book(0.0), book(600.0)], trades=[WS + 1.0])
    assert r["coverage_pct_of_window"] == 3.3
    assert r["max_book_gap_s"] == 585.0


def test_overlapping_freshness_is_unioned_not_summed():
    r = ev([book(0.0), book(10.0)], trades=[WS + 1.0])
    # [0,15] u [10,25] = 25s, not 30s
    assert r["coverage_pct_of_window"] == round(100.0 * 25 / 900, 1)


def test_coverage_is_clipped_to_the_window_and_never_backfilled():
    # a state arriving 5s before the window opens covers only the 5s inside it
    r = ev([book(-10.0)], trades=[WS + 1.0])
    assert r["coverage_pct_of_window"] == round(100.0 * 5 / 900, 1)
    # and a state near the close does not spill past it
    r2 = ev([book(897.0)], trades=[WS + 1.0])
    assert r2["coverage_pct_of_window"] == round(100.0 * 3 / 900, 1)


def test_a_fully_covered_window_reports_no_gap():
    books = [book(float(t)) for t in range(0, 900, 10)]
    r = ev(books, trades=[WS + 1.0], actions=[action(450.0)])
    assert r["coverage_pct_of_window"] == 100.0
    assert r["max_book_gap_s"] == 0.0


def test_trades_alone_produce_no_book_and_no_coverage():
    r = ev([], trades=[WS + 5.0, WS + 6.0])
    assert r["usable_book_states"] == 0
    assert r["coverage_pct_of_window"] is None
    assert r["authenticated_trades"] == 2


# ---------------------------------------------------------------- identity

def test_book_and_trades_under_different_markets_do_not_pass_as_one():
    r = A.evaluate(SLUG, {SLUG: [book(5.0)]}, {OTHER: [WS + 9.0]},
                   [action(5.0)])
    assert r["identity"]["book_present_for_market"] is True
    assert r["identity"]["trades_present_for_market"] is False
    assert r["identity"]["book_and_trades_same_market"] is False
    assert r["verdict"] == A.PARTIAL


def test_an_l5_action_pointing_at_another_market_is_not_clean():
    r = ev([book(5.0)], trades=[WS + 9.0], actions=[action(5.0, slug=OTHER)])
    assert r["identity"]["l5_actions_same_market"] is False
    assert r["identity"]["l5_action_markets"] == [OTHER]
    assert r["verdict"] == A.PARTIAL


# -------------------------------------------------------------- attachment

def test_an_action_outside_the_attach_window_has_no_contemporaneous_book():
    r = ev([book(5.0)], trades=[WS + 9.0],
           actions=[action(5.0), action(400.0)])
    assert r["actions_with_contemporaneous_book"] == 1
    assert r["actions_without"] == 1
    assert r["verdict"] == A.PARTIAL


def test_an_action_with_no_receipt_timestamp_counts_as_unattached():
    r = A.evaluate(SLUG, {SLUG: [book(5.0)]}, {SLUG: [WS + 9.0]},
                   [{"action_key": "k", "pmus": {"slug": SLUG}}])
    assert r["actions_without"] == 1
    assert r["verdict"] == A.PARTIAL


def test_a_window_with_no_l5_actions_is_not_reported_as_clean():
    r = ev([book(5.0)], trades=[WS + 9.0])
    assert r["verdict"] == A.PARTIAL      # nothing was attached to anything


# ------------------------------------------------------------- aggregation

def test_one_blind_window_fails_the_gate_however_many_are_clean():
    clean = ev([book(5.0)], trades=[WS + 9.0], actions=[action(5.0)])
    blind = ev([book(120.0)], trades=[WS + 9.0], actions=[action(120.0)])
    s = A.summarize([clean, clean, clean, blind])
    assert s["clean"] == 3
    assert s["windows_blind_at_open"] == 1
    assert s["gate"] == "FAIL"


def test_the_gate_needs_at_least_one_clean_window():
    partial = ev([book(5.0)], trades=[WS + 9.0])
    assert A.summarize([partial])["gate"] == "FAIL"
    clean = ev([book(5.0)], trades=[WS + 9.0], actions=[action(5.0)])
    assert A.summarize([clean])["gate"] == "PASS"


def test_summary_reports_both_latencies_so_they_can_be_compared():
    ws = [ev([book(6.0)], trades=[WS + 9.0], actions=[action(6.0)]),
          ev([book(8.0)], trades=[WS + 20.0], actions=[action(8.0)]),
          ev([book(4.0)], trades=[WS + 11.0], actions=[action(4.0)])]
    s = A.summarize(ws)
    assert s["median_first_book_latency_s"] == 6.0
    assert s["median_first_trade_latency_s"] == 11.0
    assert s["gate"] == "PASS"


# -------------------------------------------------------------- extraction

def test_book_rows_are_grouped_by_market_and_time_sorted():
    docs = [{"markets": {SLUG: [{"t": WS + 9, "bid": "0.4", "ask": "0.6",
                                 "status": "OK"},
                                {"t": WS + 3, "bid": "0.4", "ask": "0.6",
                                 "status": "OK"}],
                         OTHER: [{"t": WS + 5, "status": "NO_BOOK"}]}}]
    got = A.book_states(docs)
    assert [b["t"] for b in got[SLUG]] == [WS + 3, WS + 9]
    assert list(got[OTHER])[0]["status"] == "NO_BOOK"


def test_a_book_row_without_a_receipt_timestamp_is_dropped():
    docs = [{"markets": {SLUG: [{"bid": "0.4", "ask": "0.6", "status": "OK"}]}}]
    assert A.book_states(docs).get(SLUG) is None


def test_trade_times_prefer_the_venue_trade_time_over_our_receipt():
    docs = [{"markets": {SLUG: [{"trade_time": WS + 9, "t": WS + 99}]}}]
    assert A.trade_times(docs)[SLUG] == [WS + 9]


# ------------------------------------------- seam onto the frozen rule

def test_depth_is_renamed_to_what_the_frozen_rule_actually_reads():
    from src import l5_execution_translations as X
    b = A.to_bbo(book(5.0, bid_qty="37", ask_qty="41"))
    assert b["bid_shares"] == "37" and b["ask_shares"] == "41"
    # the frozen rule must be able to size from it on both sides
    assert X.displayed_size("UP", b) == 41
    assert X.displayed_size("DOWN", b) == 37


def test_depth_the_venue_never_served_stays_missing_not_borrowed():
    b = A.to_bbo(book(5.0, ask_qty=None))
    assert b["ask_shares"] is None
    from src import l5_execution_translations as X
    assert X.displayed_size("UP", b) is None       # refused, never guessed
    assert X.displayed_size("DOWN", b) == 100


def test_a_non_usable_state_is_not_offered_to_the_frozen_rule():
    assert A.to_bbo(book(5.0, status="STALE")) is None
    assert A.to_bbo(book(5.0, status="ONE_SIDED")) is None
    assert A.to_bbo(book(5.0, ask=None)) is None
    assert A.to_bbo(None) is None


def test_the_book_in_force_is_never_one_observed_after_the_action():
    books = [book(0.0, bid="0.30"), book(10.0, bid="0.40"),
             book(400.0, bid="0.50")]
    assert A.book_at(books, WS + 12.0)["bid"] == "0.40"
    # a state 5s in the future must not be used
    assert A.book_at(books, WS + 5.0)["bid"] == "0.30"


def test_a_book_older_than_the_attach_window_is_not_in_force():
    assert A.book_at([book(0.0)], WS + 100.0) is None
    assert A.book_at([], WS + 1.0) is None
