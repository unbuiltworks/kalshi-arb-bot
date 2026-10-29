"""An L5 action belongs to a market, or it does not enter. Time cannot say.

Probe 05 sent two real orders into cpc-btc-updown-15m-2026-09-25-1500z on
actions from other markets, because the window filter matched on timestamp
alone. The 110 actions of that window carried FIVE distinct CTF tokens; the
venue publishes exactly TWO for it.
"""
from __future__ import annotations

import json

import pytest

from src import l5_market_binding as MB
from src import l5_window_actions as WA

WS = 1790348400                                    # 2026-09-25 15:00:00Z
UP = "36092651826964879140403691207603479989558552878378083380799356774486420969266"
DOWN = "98059272625858233471777381822726790303125691750609406709251258068210821412541"
CID = "0xa7480344ad27676dac9df78abaf0d76208259569a419468d60163416197de185"
# the three that actually leaked in, verbatim from the Probe 05 journal
FOREIGN = ("376591946851592987588125889925915500822408689192807802853776254095024003736",
           "110486712637881650458824413800000000000000000000000000000000000000000000000",
           "417331760296437386617440236400000000000000000000000000000000000000000000000")


def event_payload(**over):
    m = {"slug": "btc-updown-15m-%d" % WS, "conditionId": CID,
         "clobTokenIds": json.dumps([UP, DOWN]),
         "outcomes": json.dumps(["Up", "Down"]),
         "endDate": "2026-09-25T15:15:00Z",
         "question": "Bitcoin Up or Down - September 25, 11:00AM-11:15AM ET"}
    m.update(over)
    return [{"markets": [m]}]


def getter(payload):
    return lambda url: payload


def binding(**over):
    return MB.fetch_binding(WS, getter=getter(event_payload(**over)))


def action(token=UP, ts=WS + 60, **kw):
    a = {"asset": token, "timestamp": ts, "side": "BUY", "price": 0.5,
         "size": 10, "outcome": "Up"}
    a.update(kw)
    return a


# ------------------------------------------------------------- the binding

def test_the_binding_comes_from_the_venue_not_from_the_slug():
    b = binding()
    assert b.condition_id == CID
    assert b.token_up == UP and b.token_down == DOWN
    assert b.window_start == WS and b.window_end == WS + 900


def test_outcomes_listed_down_first_are_not_read_positionally():
    b = MB.fetch_binding(WS, getter=getter(event_payload(
        clobTokenIds=json.dumps([DOWN, UP]),
        outcomes=json.dumps(["Down", "Up"]))))
    assert b.token_up == UP and b.token_down == DOWN


def test_a_record_whose_end_time_is_not_this_window_is_refused():
    # the venue answered about a DIFFERENT window; using it would rebind us
    assert binding(endDate="2026-09-25T15:30:00Z") is None


def test_unrecognised_outcome_labels_are_refused_not_guessed():
    assert binding(outcomes=json.dumps(["Yes", "No"])) is None


def test_a_feed_failure_yields_no_binding_rather_than_a_default():
    def boom(url):
        raise OSError("network")
    assert MB.fetch_binding(WS, getter=boom) is None


# ------------------------------------------------------------- admission

def test_both_of_the_markets_own_tokens_are_admitted():
    b = binding()
    assert MB.admit(action(UP), b)["side"] == "UP"
    assert MB.admit(action(DOWN), b)["side"] == "DOWN"


@pytest.mark.parametrize("tok", FOREIGN)
def test_every_token_that_actually_leaked_is_now_refused(tok):
    v = MB.admit(action(tok), binding())
    assert v["verdict"] == MB.MISMATCH


def test_the_side_comes_from_the_token_not_from_the_actions_own_label():
    # a foreign action's `outcome` string is meaningless here, and a correct
    # one must not be trusted over the token either
    v = MB.admit(action(DOWN, outcome="Up"), binding())
    assert v["side"] == "DOWN"


def test_no_binding_admits_nothing():
    assert MB.admit(action(UP), None)["verdict"] == MB.UNKNOWN


def test_an_action_with_no_token_is_unknown_not_admitted():
    a = action()
    del a["asset"]
    assert MB.admit(a, binding())["verdict"] == MB.UNKNOWN


def test_a_conditionid_that_disagrees_with_the_token_disqualifies():
    v = MB.admit(action(UP, conditionId="0xdeadbeef"), binding())
    assert v["verdict"] == MB.MISMATCH


def test_the_right_token_outside_its_own_window_is_still_refused():
    assert MB.admit(action(UP, ts=WS + 5000),
                    binding())["verdict"] == MB.MISMATCH


# ------------------------- simultaneous markets with overlapping timestamps

def test_two_of_l5s_markets_trading_at_the_same_instant_are_separated():
    """The actual failure mode, as a test.

    L5 trades many markets at once. Here every action shares one timestamp,
    so nothing about time can tell them apart -- only the token can.
    """
    b = binding()
    same_instant = WS + 300
    rows = ([action(UP, ts=same_instant) for _ in range(3)]
            + [action(DOWN, ts=same_instant) for _ in range(2)]
            + [action(f, ts=same_instant) for f in FOREIGN])
    verdicts = [MB.admit(r, b) for r in rows]
    assert sum(1 for v in verdicts if v["verdict"] == MB.ADMITTED) == 5
    assert sum(1 for v in verdicts if v["verdict"] == MB.MISMATCH) == 3


def test_no_foreign_action_ever_reaches_translation(monkeypatch):
    """If a foreign token reaches L5_NATIVE, this test fails. That is its job."""
    b = binding()
    monkeypatch.setattr(WA, "_binding_for", lambda ws, **kw: b)
    reached = []

    def activity(limit=100):
        rows = []
        for tok in (UP, DOWN) + FOREIGN:
            rows.append({"asset": tok, "timestamp": WS + 120,
                         "side": "BUY", "price": 0.5, "size": 10,
                         "outcome": "Up" if tok != DOWN else "Down",
                         "conditionId": CID if tok in (UP, DOWN) else "0xzz",
                         "transactionHash": "0x%s" % tok[:8]})
        return rows

    out, ok = WA.new_l5_actions("cpc-btc-updown-15m-2026-09-25-1500z",
                                set(), activity=activity)
    assert ok is True
    for a in out:
        reached.append(a["l5"])
    admitted_tokens = {str(a.get("asset")) for a in out} if out and "asset" in out[0] else None
    assert len(out) == 2, "a foreign-market action reached translation"
    assert len(reached) == 2


def test_the_gate_admits_nothing_when_the_venue_will_not_identify_the_window(
        monkeypatch):
    monkeypatch.setattr(WA, "_binding_for", lambda ws, **kw: None)

    def activity(limit=100):
        return [{"asset": UP, "timestamp": WS + 60, "side": "BUY",
                 "price": 0.5, "size": 10, "outcome": "Up",
                 "transactionHash": "0xa"}]

    out, ok = WA.new_l5_actions("cpc-btc-updown-15m-2026-09-25-1500z",
                                set(), activity=activity)
    assert out == []
    assert ok is True                    # the FEED was fine; identity was not


# ------------------------------------------------ distinct, not per-poll

def test_a_foreign_action_is_counted_once_however_often_the_feed_serves_it(
        monkeypatch):
    """The 1615z window counted 147 refusals against 97 distinct actions.

    The feed re-serves the same rows every poll. A refused action lands in
    neither `seen` nor the output, so without its own memory it is re-judged
    -- and re-counted -- on every single poll.
    """
    b = binding()
    monkeypatch.setattr(WA, "_binding_for", lambda ws, **kw: b)
    WA._REFUSED.pop("cpc-btc-updown-15m-2026-09-25-1500z", None)
    rows = [{"asset": FOREIGN[0], "timestamp": WS + 30, "side": "BUY",
             "price": 0.5, "size": 10, "outcome": "Up",
             "transactionHash": "0xf1"},
            {"asset": UP, "timestamp": WS + 31, "side": "BUY",
             "price": 0.5, "size": 10, "outcome": "Up",
             "transactionHash": "0xa1"}]
    h = WA.FeedHealth()
    seen = set()
    for _ in range(10):                       # ten polls, same two rows
        WA.new_l5_actions("cpc-btc-updown-15m-2026-09-25-1500z", seen,
                          activity=lambda limit=100: rows, health=h)
    d = h.as_dict()
    assert d["distinct_identity_refused"] == 1
    assert d["distinct_admitted"] == 1
    assert d["rejected_tokens"] == {FOREIGN[0]: 1}
    assert sum(d["admitted_tokens"].values()) == 1


def test_the_admitted_action_is_still_handled_exactly_once(monkeypatch):
    b = binding()
    monkeypatch.setattr(WA, "_binding_for", lambda ws, **kw: b)
    WA._REFUSED.pop("cpc-btc-updown-15m-2026-09-25-1500z", None)
    rows = [{"asset": UP, "timestamp": WS + 40, "side": "BUY", "price": 0.5,
             "size": 10, "outcome": "Up", "transactionHash": "0xb1"}]
    seen = set()
    first, _ = WA.new_l5_actions("cpc-btc-updown-15m-2026-09-25-1500z", seen,
                                 activity=lambda limit=100: rows)
    again, _ = WA.new_l5_actions("cpc-btc-updown-15m-2026-09-25-1500z", seen,
                                 activity=lambda limit=100: rows)
    assert len(first) == 1 and again == []
