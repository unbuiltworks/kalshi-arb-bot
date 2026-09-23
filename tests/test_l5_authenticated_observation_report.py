"""The harvester must not quietly widen or invent its input.

Each test guards a way the report could look healthy on bad input: pulling in
the sports slate the recorder also watches, treating a truncated log line as
data, or letting a trade tape stand in for a book.
"""
from __future__ import annotations

import datetime as dt
import json

from scripts import l5_authenticated_observation_report as R

WS = dt.datetime(2026, 9, 23, 21, 15, tzinfo=dt.timezone.utc).timestamp()
SLUG = "cpc-btc-updown-15m-2026-09-23-2115z"
SPORT = "atc-ncaams-lip-ken-2026-09-22-ken"


def bk(off, status="OK"):
    return {"t": WS + off, "bid": "0.40", "ask": "0.60",
            "bid_qty": "100", "ask_qty": "80", "status": status}


def book_line(markets):
    return R.BOOK_PREFIX + json.dumps({"markets": markets})


def trade_line(markets):
    return R.TRADE_PREFIX + json.dumps({"markets": markets})


def test_only_btc15m_markets_are_scored():
    rep = R.build(since=None, lines=[book_line({SLUG: [bk(6.0)], SPORT: [bk(1.0)]}),
                   trade_line({SLUG: [{"trade_time": WS + 9.0}]})], actions=[])
    assert [w["market"] for w in rep["per_window"]] == [SLUG]
    assert rep["markets_seen"] == 1


def test_noise_and_truncated_lines_are_skipped_not_guessed():
    rep = R.build(since=None, lines=["a line that is not a tape row",
                   R.BOOK_PREFIX + "{truncated",
                   book_line({SLUG: [bk(6.0)]}),
                   trade_line({SLUG: [{"trade_time": WS + 9.0}]})], actions=[])
    w = rep["per_window"][0]
    assert w["usable_book_states"] == 1
    assert w["first_book_latency_s"] == 6.0


def test_a_window_trading_before_we_see_a_book_reports_blind():
    rep = R.build(since=None, lines=[book_line({SLUG: [bk(120.0)]}),
                   trade_line({SLUG: [{"trade_time": WS + 9.0}]})], actions=[])
    assert rep["per_window"][0]["verdict"] == "BLIND_AT_OPEN"
    assert rep["summary"]["gate"] == "FAIL"


def test_trades_without_a_book_never_produce_one():
    rep = R.build(since=None, lines=[trade_line({SLUG: [{"trade_time": WS + 9.0}]})], actions=[])
    w = rep["per_window"][0]
    assert w["usable_book_states"] == 0
    assert w["first_book_latency_s"] is None
    assert w["verdict"] == "BLIND_AT_OPEN"


def test_l5_actions_are_attached_by_market_and_receipt():
    act = {"action_key": "k1", "pmus": {"slug": SLUG},
           "receipt_utc": dt.datetime.fromtimestamp(
               WS + 8.0, dt.timezone.utc).isoformat()}
    rep = R.build(since=None, lines=[book_line({SLUG: [bk(6.0)]}),
                   trade_line({SLUG: [{"trade_time": WS + 9.0}]})], actions=[act])
    w = rep["per_window"][0]
    assert w["l5_actions"] == 1
    assert w["actions_with_contemporaneous_book"] == 1
    assert w["verdict"] == "OK"
    assert rep["summary"]["gate"] == "PASS"


# ------------------------------------- defective path is never pooled

def test_pre_fix_windows_are_preserved_separately_not_pooled():
    """Windows opening before the authenticated tape went live have trades
    and no book. They score BLIND_AT_OPEN for a reason that no longer
    applies, so they must not reach the gate."""
    rep = R.build(lines=[trade_line({SLUG: [{"trade_time": WS + 9.0}]})],
                  actions=[], since=R.AUTH_BOOK_LIVE_FROM)
    assert rep["per_window"] == []
    assert len(rep["superseded_per_window"]) == 1
    assert rep["superseded_per_window"][0]["observation_path"] == \
        "PUBLIC_DEFECTIVE_SUPERSEDED"
    # and the evidence is preserved, not discarded
    assert rep["superseded_summary"]["windows_blind_at_open"] == 1


def test_a_post_fix_window_reaches_the_gate():
    later = "cpc-btc-updown-15m-2026-09-23-2300z"
    ws = dt.datetime(2026, 9, 23, 23, 0, tzinfo=dt.timezone.utc).timestamp()
    b = {"t": ws + 5.0, "bid": "0.40", "ask": "0.60", "bid_qty": "100",
         "ask_qty": "80", "status": "OK"}
    act = {"action_key": "k", "pmus": {"slug": later},
           "receipt_utc": dt.datetime.fromtimestamp(
               ws + 5.0, dt.timezone.utc).isoformat()}
    rep = R.build(lines=[book_line({later: [b]}),
                         trade_line({later: [{"trade_time": ws + 9.0}]})],
                  actions=[act], since=R.AUTH_BOOK_LIVE_FROM)
    assert len(rep["per_window"]) == 1
    assert rep["per_window"][0]["observation_path"] == "AUTHENTICATED"
    assert rep["per_window"][0]["first_book_latency_s"] == 5.0
    assert rep["summary"]["gate"] == "PASS"
