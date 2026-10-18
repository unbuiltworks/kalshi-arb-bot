"""The wiring must hand the frozen rule the authenticated book and nothing else.

Each test guards a way this join could put real money at risk or make a
refusal mean the wrong thing:

  * pricing an action against a book observed AFTER it would let hindsight
    into an execution decision;
  * leaving the observer's PUBLIC bbo on the row would mean a decision was
    taken against the feed we already disqualified;
  * filtering a stale or bookless action out here would steal the refusal
    from the frozen rule and hide it from the layer attribution;
  * a would-submit order that is not actually gated by the transport is an
    order.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_authenticated_observation as OBS
from src import l5_faithful_translation as FT
from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W

MARKET = "cpc-btc-updown-15m-2026-09-23-2300z"
WS = dt.datetime(2026, 9, 23, 23, 0, tzinfo=dt.timezone.utc).timestamp()

#: the frozen normalization: 1/median(l5_size), so the median L5 action of
#: 22.13 shares becomes 1.00 share. Using lam=1 instead would make a single
#: median action cost $13.28 and the $5 cap would (correctly) refuse it.
LAM = D("0.04518752824220514525")


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def book(off, bid="0.40", ask="0.60", bq="100", aq="80", status="OK"):
    return {"t": WS + off, "bid": bid, "ask": ask,
            "bid_qty": bq, "ask_qty": aq, "status": status}


def action(off, side="UP", price="0.50", size="22.13", key=None):
    return {"action_key": key or ("k%s" % off),
            "receipt_utc": iso(WS + off),
            "l5": {"pmus_side": side, "l5_price": price, "l5_size": size},
            "pmus": {"bbo": {"bid": "0.99", "ask": "0.01",
                             "bid_shares": "PUBLIC", "ask_shares": "PUBLIC"}}}


@pytest.fixture()
def store(tmp_path):
    return EX.InventoryStore(str(tmp_path / "inv.json"))


def limits():
    return EX.SafetyLimits()


# ------------------------------------------------------------- attachment

def test_the_public_book_is_replaced_not_merged():
    r = W.attach(action(10.0), [book(9.0)])
    assert r["pmus"]["bbo"]["bid"] == "0.40"
    assert "PUBLIC" not in str(r["pmus"]["bbo"])
    assert r["book_source"] == "AUTHENTICATED"


def test_an_action_is_never_priced_against_a_later_book():
    books = [book(5.0, bid="0.30"), book(20.0, bid="0.80")]
    r = W.attach(action(10.0), books)
    assert r["pmus"]["bbo"]["bid"] == "0.30"      # the +5s state, not +20s
    assert r["pmus_snapshot_age_s"] == 5.0


def test_the_age_is_the_true_distance_to_that_book():
    r = W.attach(action(10.0), [book(8.4)])
    assert r["pmus_snapshot_age_s"] == 1.6


def test_no_prior_book_yields_an_empty_bbo_not_a_guess():
    r = W.attach(action(3.0), [book(50.0)])
    assert r["pmus"]["bbo"] == {}
    assert r["pmus_snapshot_age_s"] is None
    assert r["book_source"] == "AUTHENTICATED_NONE_AT_OR_BEFORE_ACTION"


def test_a_non_usable_book_state_is_not_attached():
    r = W.attach(action(10.0), [book(5.0, status="ONE_SIDED"),
                                book(6.0, status="NO_BOOK")])
    assert r["pmus"]["bbo"] == {}


def test_depth_reaches_the_frozen_rule_under_the_names_it_reads():
    r = W.attach(action(10.0), [book(9.0, bq="37", aq="41")])
    from src import l5_execution_translations as X
    assert X.displayed_size("UP", r["pmus"]["bbo"]) == 41
    assert X.displayed_size("DOWN", r["pmus"]["bbo"]) == 37


# ------------------------------- the frozen rule keeps making the decisions

def test_a_stale_book_is_refused_BY_THE_FROZEN_RULE(store):
    # 30s old: far beyond the frozen STALE_MAX_S of 2.0s
    out = W.run_window([action(40.0)], [book(10.0)], market=MARKET,
                       window="2300z", store=store, limits=limits(),
                       transport=EX.NullTransport(), lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 41.0, dt.timezone.utc))
    rec = out["records"][0]
    assert rec["refused_by_layer"] == "PMUS_TRANSLATION"
    assert rec["refused_reason"] == FT.T_STALE


def test_a_bookless_action_is_refused_NO_BOOK_not_dropped(store):
    out = W.run_window([action(3.0)], [book(50.0)], market=MARKET,
                       window="2300z", store=store, limits=limits(),
                       transport=EX.NullTransport(), lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 4.0, dt.timezone.utc))
    rec = out["records"][0]
    assert out["actions"] == 1                    # present, not filtered away
    assert rec["refused_by_layer"] == "PMUS_TRANSLATION"
    assert rec["refused_reason"] == FT.T_NO_BOOK


def test_a_restable_action_without_interaction_evidence_is_not_invented(store):
    # l5 price 0.50 strictly inside the 0.40/0.60 book -> maker, needs proof
    out = W.run_window([action(10.0, price="0.50")], [book(9.0)],
                       market=MARKET, window="2300z", store=store,
                       limits=limits(), transport=EX.NullTransport(),
                       lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 11.0, dt.timezone.utc))
    rec = out["records"][0]
    assert rec["refused_reason"] == FT.T_UNEVALUABLE


# ----------------------------------------------------------- DISARMED path

class Disarmed:
    """Stands in for PmusTransport before the owner arms it."""
    name = "PMUS"

    def submit(self, intent):
        raise EX.NotArmed("transport is DISARMED")

    def cancel(self, coid, market_slug=None):
        raise EX.NotArmed("transport is DISARMED")


def crossing_action(off):
    # l5 wants 0.70 for UP while the ask is 0.60 -> crosses, taker
    return action(off, side="UP", price="0.70")


def test_a_crossing_action_becomes_a_would_submit_order_and_is_not_sent(store):
    out = W.run_window([crossing_action(10.0)], [book(9.0)], market=MARKET,
                       window="2300z", store=store, limits=limits(),
                       transport=Disarmed(), lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 11.0, dt.timezone.utc))
    rec = out["records"][0]
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["reason"] == "NOT_ARMED"
    intent = rec["ORDER"]["intent"]
    assert intent["side"] == "UP" and intent["mode"] == FT.TAKER
    assert out["summary"]["would_submit"] == 1
    assert out["summary"]["submitted_live"] == 0


def test_nothing_is_ever_submitted_while_disarmed(store):
    rows = [crossing_action(float(i)) for i in range(5, 30, 5)]
    out = W.run_window(rows, [book(1.0)], market=MARKET, window="2300z",
                       store=store, limits=limits(), transport=Disarmed(),
                       lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 30.0, dt.timezone.utc))
    assert out["summary"]["submitted_live"] == 0
    assert store.already_submitted(EX.client_order_id("k5.0")) is False


def test_the_safety_overlay_refusal_is_its_own_layer(store):
    tight = EX.SafetyLimits(max_total_exposure_usd=D("0"))
    out = W.run_window([crossing_action(10.0)], [book(9.0)], market=MARKET,
                       window="2300z", store=store, limits=tight,
                       transport=Disarmed(), lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 11.0, dt.timezone.utc))
    rec = out["records"][0]
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    # and it is explicitly not evidence about L5 or about PMUS
    assert "never a statement about L5" in rec["SAFETY_OVERLAY"]["note"]


def test_a_duplicate_action_key_cannot_produce_two_orders(store):
    a = crossing_action(10.0)
    out = W.run_window([a, dict(a)], [book(9.0)], market=MARKET,
                       window="2300z", store=store, limits=limits(),
                       transport=Disarmed(), lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 11.0, dt.timezone.utc))
    coids = [r["ORDER"]["client_order_id"] for r in out["records"]]
    assert coids[0] == coids[1]        # deterministic, so dedup is possible


def test_actions_are_processed_in_receipt_order(store):
    rows = [crossing_action(20.0), crossing_action(5.0), crossing_action(12.0)]
    out = W.run_window(rows, [book(1.0)], market=MARKET, window="2300z",
                       store=store, limits=limits(), transport=Disarmed(),
                       lam=LAM,
                       now_fn=lambda: dt.datetime.fromtimestamp(
                           WS + 25.0, dt.timezone.utc))
    keys = [r["action_key"] for r in out["records"]]
    assert keys == ["k5.0", "k12.0", "k20.0"]
