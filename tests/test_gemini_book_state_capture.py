"""The book each decision was made against, recorded rather than inferred.

WHY THIS EXISTS. CONTINUOUS-7 quoted YES 0.01 and NO 0.79->0.95, filled
nothing, and the only way to say why was to read the book BACKWARDS out of our
own prices plus `ticks_behind_bid: 0`. That inference happened to be sound and
it is still an inference, which is the substitution this program refuses
everywhere else. The controller always had the real book -- `state_from_venue`
fetches it -- used it, and threw it away.

So these tests pin two things: the record carries the state the controller
ACTUALLY decided on, and an unreadable source is recorded as unreadable rather
than replaced with an empty book.
"""
import json

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import run_continuous as RC


# a REAL outcome_book shape, as the venue publishes it -- measured on
# GEMI-BTC05M2609190015-UP at 00:13Z
BOOK = {
    "readable": True, "ticker": "BTC05M2609190015",
    "instrumentSymbol": "GEMI-BTC05M2609190015-UP",
    "yes": {"bid": "0.83", "ask": "0.99"},
    "no": {"bid": "0.01", "ask": "0.17"},
    "passive_pair_cost": "0.84", "crossing_pair_cost": "1.16",
    "last_trade_price": "0.90",
    "complement_identity": {"no_bid_vs_1_minus_yes_ask": "0",
                            "no_ask_vs_1_minus_yes_bid": "0"},
    "identity_consistent": True,
    "source": "/v1/prediction-markets/events/{eventTicker} contract prices",
}


def _state(book=BOOK, tte=265.0, monkeypatch=None):
    """The REAL state_from_venue object, not a hand-built lookalike."""
    inv = CP.Inventory()
    return CP.state_from_venue("GEMI-BTC05M2609190015-UP", tte, inv,
                               now=1789776806.0, book=book)


# ============================================== the presented book, as presented

def test_it_reports_the_book_the_controller_decided_on(monkeypatch):
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    p = RC.presented_book(_state())
    assert p["present"] is True and p["readable"] is True
    assert p["yes"] == {"bid": "0.83", "ask": "0.99", "spread": "0.16",
                        "qty": None}
    assert p["no"] == {"bid": "0.01", "ask": "0.17", "spread": "0.16",
                       "qty": None}
    assert p["tte_s"] == 265.0
    assert p["passive_pair_cost"] == "0.84"
    assert p["crossing_pair_cost"] == "1.16"
    assert p["identity_consistent"] is True
    assert p["source"] == BOOK["source"]


def test_size_is_absent_WITH_A_REASON_rather_than_zero(monkeypatch):
    """The contract object publishes prices only. A 0 here would read as 'no
    resting interest', which is a different and much stronger claim than 'this
    endpoint does not say'."""
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    p = RC.presented_book(_state())
    assert p["yes"]["qty"] is None and p["no"]["qty"] is None
    assert "PRICES ONLY" in p["qty_why"]


def test_an_unreadable_book_is_recorded_as_unreadable(monkeypatch):
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    bad = {"readable": False, "why": "the contract does not publish both "
                                     "outcomes' prices"}
    p = RC.presented_book(_state(book=bad))
    assert p["present"] is True and p["readable"] is False
    assert "does not publish" in p["why"]
    # and NOTHING is invented in its place
    assert "yes" not in p and "no" not in p


def test_a_book_that_fails_its_own_identity_check_is_not_reported_as_a_book(
        monkeypatch):
    """state_from_venue refuses to quote into it, so the record must not show
    prices as though they had been used."""
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    skew = dict(BOOK, identity_consistent=False,
                complement_identity={"no_bid_vs_1_minus_yes_ask": "0.03",
                                     "no_ask_vs_1_minus_yes_bid": "0"})
    p = RC.presented_book(_state(book=skew))
    assert p["readable"] is False or p.get("identity_consistent") is False
    assert "yes" not in p
    assert p["complement_identity"]["no_bid_vs_1_minus_yes_ask"] == "0.03"


def test_no_market_state_is_a_stated_absence(monkeypatch):
    p = RC.presented_book(None)
    assert p["present"] is False and "returned before building one" in p["why"]


#: prose fields. A note may legitimately NAME the thing it refuses to use --
#: the first version of the test below scanned its own explanatory text and
#: failed on the word `ticks_behind_bid` inside "not derived from
#: ticks_behind_bid". The invariant is about DATA, so the data is what is
#: scanned.
_PROSE = ("labels", "why", "qty_why", "no_side_depth_why", "source")


def _data_keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            if str(k) not in _PROSE:
                _data_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _data_keys(v, out)
    return out


def test_the_record_never_contains_our_own_order_fields(monkeypatch):
    """THE WHOLE POINT. The book record must come from the venue's book, not
    from the rungs we chose, so no rung field may appear as DATA in it -- and
    the prices must be the BOOK's even when our own quotes differ."""
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    rec = RC.book_state(_state(), None)
    keys = _data_keys(rec)
    for ours in ("ticks_behind_bid", "rung_index", "client_order_id",
                 "stage", "action", "leg", "qty_filled"):
        assert ours not in keys, ours
    # we quoted YES 0.01 in C7; this book says 0.83, and the book wins
    assert rec["presented"]["yes"]["bid"] == "0.83"


# ============================================================ the size read

DEPTH_BODY = json.dumps({
    "bids": [{"price": "0.83", "amount": "10.0", "timestamp": "1789776806"},
             {"price": "0.74", "amount": "202.0", "timestamp": "1789776806"}],
    "asks": [{"price": "0.99", "amount": "10.0", "timestamp": "1789776806"}]})


def _venues(monkeypatch, result):
    from src.l5_shadow import venues as V
    monkeypatch.setattr(V, "get", lambda url, timeout=15.0: result)
    return V


def test_depth_parses_the_ladder_the_venue_publishes(monkeypatch):
    _venues(monkeypatch, {"ok": True, "body": DEPTH_BODY, "rtt_s": 0.5})
    d = MD.contract_depth("GEMI-BTC05M2609190015-UP")
    assert d["readable"] is True
    assert d["bids"]["best"] == "0.83" and d["bids"]["best_qty"] == "10.0"
    assert d["bids"]["n_levels"] == 2 and d["bids"]["qty_shown"] == "212.0"
    assert d["asks"]["best"] == "0.99" and d["asks"]["best_qty"] == "10.0"


def test_depth_does_NOT_invent_a_no_side(monkeypatch):
    """A NO buy rests against this same ladder by the complement identity, but
    relabelling an ask size as a NO bid size would put an inferred number
    where no observed one exists."""
    _venues(monkeypatch, {"ok": True, "body": DEPTH_BODY, "rtt_s": 0.5})
    d = MD.contract_depth("GEMI-BTC05M2609190015-UP")
    assert d["no_side_depth"] is None
    assert "NOT derived" in d["no_side_depth_why"]


@pytest.mark.parametrize("result,expect", [
    ({"ok": False, "err": "URLError: timed out", "rtt_s": 2.5},
     "timed out"),
    ({"ok": True, "body": "<html>nope</html>", "rtt_s": 0.1},
     "unparseable"),
])
def test_an_unreadable_depth_read_is_a_result_with_a_reason(
        monkeypatch, result, expect):
    _venues(monkeypatch, result)
    d = MD.contract_depth("GEMI-BTC05M2609190015-UP")
    assert d["readable"] is False and expect in d["why"]
    assert "bids" not in d and "asks" not in d


# ====================================================== the agreement check

def test_agreement_is_checked_per_cycle_not_assumed(monkeypatch):
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    _venues(monkeypatch, {"ok": True, "body": DEPTH_BODY, "rtt_s": 0.5})
    depth = MD.contract_depth("GEMI-BTC05M2609190015-UP")
    bs = RC.book_state(_state(), depth)
    a = bs["agreement"]
    assert a["checked"] is True and a["agrees"] is True
    assert a["ladder_best_bid"] == "0.83" == a["presented_yes_bid"]
    assert a["ladder_best_ask"] == "0.99" == a["presented_yes_ask"]


def test_a_disagreement_is_REPORTED_and_not_smoothed(monkeypatch):
    """Two endpoints, two clocks. When they caught different instants the
    sizes must not be read as the presented book's sizes, and the record has
    to say so rather than quietly pairing them."""
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    moved = json.dumps({"bids": [{"price": "0.80", "amount": "3.0"}],
                        "asks": [{"price": "0.99", "amount": "10.0"}]})
    _venues(monkeypatch, {"ok": True, "body": moved, "rtt_s": 0.5})
    depth = MD.contract_depth("GEMI-BTC05M2609190015-UP")
    a = RC.book_state(_state(), depth)["agreement"]
    assert a["checked"] is True and a["agrees"] is False
    assert a["bid_agrees"] is False and a["ask_agrees"] is True


def test_no_depth_means_unchecked_not_agreed(monkeypatch):
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    a = RC.book_state(_state(), None)["agreement"]
    assert a["checked"] is False and "agrees" not in a


def test_a_CROSSED_book_is_recorded_as_refused_not_as_a_readable_book():
    """Soak #2, 20 of 87 cycles. A crossed book PARSES perfectly -- the venue
    served it, both sides have prices -- and the state refuses it. The record
    said `readable: True` beside a -0.11 spread, which would have told a
    later reader the controller had a usable book. The state's verdict wins.
    """
    crossed = {"readable": True, "identity_consistent": True,
               "yes": {"bid": "0.16", "ask": "0.05"},
               "no": {"bid": "0.95", "ask": "0.84"}}
    p = RC.presented_book(_state(book=crossed))
    assert p["present"] is True
    assert p["readable"] is False                      # the state's verdict
    assert p["raw_book_parsed"] is True                # the read itself was ok
    assert "CROSSED" in (p.get("state_verdict_why") or "")
    # no spread is derived from numbers that are not a book
    assert "yes" not in p and "no" not in p
    # but the published numbers are kept so the refusal can be audited
    assert p["refused_prices"]["yes"] == {"bid": "0.16", "ask": "0.05"}
