"""THE 2026-10-07T20:39:36Z ZERO-BID DEFECT.

E1 reached eligibility on a dead book -- first leg bid 0.00 / ask 0.01 --
serialized `price: {"currency": "USD", "value": "0.00"}` and sent it to the
venue. The durable rejection record proves it verbatim:

    "e1_observed_best_bid": "0.00"
    "e1_observed_best_ask": "0.01"
    "e1_price_local":       "0.00"

ROOT CAUSE. `spec.py` declares "E8 the book must be two-sided and
executable on both tokens" and NOTHING EVALUATED IT -- grep for E8 across
the package's non-test code hit only that spec string. The one adjacent
check, `_ws_ok`, tests PRESENCE (`best_bid in (None, "")`), and "0.00" is
neither None nor empty. E9's four clauses then all pass on a zero bid:
price == best_bid, 0.00 is on the 0.01 tick, 0.00 <= $0.60, and
0.00 < 0.01 so it cannot cross. There was no positivity floor anywhere.

It was NOT websocket parsing, NOT snapshot construction and NOT default
initialisation: `price_change` frames carry `best_bid`/`best_ask`
directly, and the recorded ask of 0.01 beside the bid of 0.00 shows the
parse was faithful to a genuinely dead book.

These tests prove every unexecutable shape is refused BEFORE preview and
therefore before any latch slot can be claimed.
"""
from __future__ import annotations

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP

from analysis.l5.e1_cohort.test_e1_live_path import (        # noqa: F401
    FakeTransport, _isolate, arm, fake_pin, _snapshot_from, BASE)


def snap(**over):
    """The healthy snapshot, with the named leaves overridden."""
    s = _snapshot_from(fake_pin(int(BASE)))
    fl = dict(s["first_leg"])
    cp = dict(s["complement"])
    for k, v in over.items():
        if k.startswith("fl_"):
            fl[k[3:]] = v
        elif k.startswith("cp_"):
            cp[k[3:]] = v
        else:
            s[k] = v
    # the ws_state mirror must agree, or E7 fires instead of E8 and the
    # test would pass for the wrong reason
    ws = dict(s["ws_state"])
    ws["first_leg"] = {"best_bid": fl.get("best_bid"),
                       "best_ask": fl.get("best_ask")}
    ws["complement"] = {"best_bid": cp.get("best_bid"),
                        "best_ask": cp.get("best_ask")}
    s["ws_state"] = ws
    s["first_leg"] = fl
    s["complement"] = cp
    return s


def refusals(monkeypatch, tmp_path, s):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    out = R.e1_preflight(s, SP.CohortTracker(), now=BASE + 10)
    return out["ok"], out["refusals"]


# =============== THE EXACT PRODUCTION FAILURE, FIRST ====================

def test_the_production_zero_bid_is_now_refused(monkeypatch, tmp_path):
    """bid 0.00 / ask 0.01, exactly as recorded on the tape."""
    ok, r = refusals(monkeypatch, tmp_path,
                     snap(fl_best_bid="0.00", fl_best_ask="0.01",
                          first_leg_price="0.00"))
    assert ok is False
    assert "E8_FIRST_LEG_BID_NOT_POSITIVE:0.00" in r
    assert "E9_PRICE_NOT_POSITIVE:0.00" in r


def test_the_old_code_would_have_let_it_through():
    """Documents WHY this needed a new rule: every pre-existing E9 clause
    passes on a zero bid. If this ever fails, E9 changed and the reason
    for E8 should be revisited."""
    from decimal import Decimal as D
    price, bid, ask = D("0.00"), D("0.00"), D("0.01")
    assert price == bid                       # at the same-side best bid
    assert price % R.VENUE_TICK == 0          # on the tick
    assert price <= R.MAX_FIRST_LEG_PRICE     # under the $0.60 ceiling
    assert price < ask                        # cannot cross
    # ...and yet:
    assert price <= 0


# ===================== THE FULL UNEXECUTABLE MATRIX =====================

@pytest.mark.parametrize("bid,token", [
    ("0", "E8_FIRST_LEG_BID_NOT_POSITIVE"),
    ("0.00", "E8_FIRST_LEG_BID_NOT_POSITIVE"),
    ("-0.05", "E8_FIRST_LEG_BID_NOT_POSITIVE"),
    ("NaN", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("nan", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("sNaN", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("Infinity", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("-Infinity", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("abc", "E8_FIRST_LEG_BID_NOT_FINITE"),
    ("", "E8_FIRST_LEG_BID_ABSENT"),
    (None, "E8_FIRST_LEG_BID_ABSENT"),
    ("1.00", "E8_FIRST_LEG_PRICE_OUT_OF_RANGE"),
    ("1.50", "E8_FIRST_LEG_PRICE_OUT_OF_RANGE"),
])
def test_every_unexecutable_first_leg_bid_is_refused(monkeypatch, tmp_path,
                                                     bid, token):
    ok, r = refusals(monkeypatch, tmp_path, snap(fl_best_bid=bid,
                                                 first_leg_price=bid))
    assert ok is False
    assert any(x.startswith(token) for x in r), (token, r)


@pytest.mark.parametrize("ask,token", [
    ("0", "E8_FIRST_LEG_ASK_NOT_POSITIVE"),
    ("-1", "E8_FIRST_LEG_ASK_NOT_POSITIVE"),
    ("NaN", "E8_FIRST_LEG_ASK_NOT_FINITE"),
    ("", "E8_FIRST_LEG_ASK_ABSENT"),
    (None, "E8_FIRST_LEG_ASK_ABSENT"),
    ("0.50", "E8_FIRST_LEG_BOOK_CROSSED_OR_LOCKED"),
    ("0.40", "E8_FIRST_LEG_BOOK_CROSSED_OR_LOCKED"),
])
def test_every_unexecutable_first_leg_ask_is_refused(monkeypatch, tmp_path,
                                                     ask, token):
    ok, r = refusals(monkeypatch, tmp_path, snap(fl_best_ask=ask))
    assert ok is False
    assert any(x.startswith(token) for x in r), (token, r)


@pytest.mark.parametrize("bid,token", [
    ("0", "E8_COMPLEMENT_BID_NOT_POSITIVE"),
    ("-0.1", "E8_COMPLEMENT_BID_NOT_POSITIVE"),
    ("NaN", "E8_COMPLEMENT_BID_NOT_FINITE"),
    ("", "E8_COMPLEMENT_BID_ABSENT"),
    (None, "E8_COMPLEMENT_BID_ABSENT"),
])
def test_the_complement_token_is_checked_too(monkeypatch, tmp_path, bid,
                                             token):
    """E8 says BOTH tokens. A dead complement means the pair cannot be
    completed, which is the whole strategy."""
    ok, r = refusals(monkeypatch, tmp_path, snap(cp_best_bid=bid))
    assert ok is False
    assert any(x.startswith(token) for x in r), (token, r)


def test_an_entirely_empty_book_is_refused(monkeypatch, tmp_path):
    ok, r = refusals(monkeypatch, tmp_path,
                     snap(fl_best_bid=None, fl_best_ask=None,
                          cp_best_bid=None, cp_best_ask=None))
    assert ok is False
    assert "E8_FIRST_LEG_BID_ABSENT" in r
    assert "E8_COMPLEMENT_BID_ABSENT" in r


def test_a_malformed_book_object_is_refused(monkeypatch, tmp_path):
    for bad in ({}, None, {"side": "UP"}):
        s = snap()
        s["first_leg"] = bad if bad is not None else {}
        ok, r = refusals(monkeypatch, tmp_path, s)
        assert ok is False
        assert any(x.startswith("E8_FIRST_LEG_BID_ABSENT") for x in r), r


@pytest.mark.parametrize("price,token", [
    (None, "E9_PRICE_ABSENT"),
    ("", "E9_PRICE_ABSENT"),
    ("NaN", "E9_PRICE_NOT_FINITE"),
    ("Infinity", "E9_PRICE_NOT_FINITE"),
    ("zzz", "E9_PRICE_NOT_FINITE"),
    ("0", "E9_PRICE_NOT_POSITIVE"),
    ("-0.20", "E9_PRICE_NOT_POSITIVE"),
])
def test_every_unusable_price_is_refused_without_raising(monkeypatch,
                                                         tmp_path, price,
                                                         token):
    """A non-finite Decimal raises InvalidOperation on `<` and `%`, so the
    old code would have CRASHED on NaN rather than refusing. Eligibility
    must return a refusal, never propagate an exception."""
    ok, r = refusals(monkeypatch, tmp_path, snap(first_leg_price=price))
    assert ok is False
    assert any(x.startswith(token) for x in r), (token, r)


def test_a_stale_book_is_still_refused(monkeypatch, tmp_path):
    """Pre-existing behaviour, re-asserted here so the new E8 code cannot
    accidentally mask it."""
    s = snap()
    s["ws_state"] = dict(s["ws_state"], book_unix=BASE - 3600)
    ok, r = refusals(monkeypatch, tmp_path, s)
    assert ok is False
    assert any(x.startswith("E7_BOOK_STALE") for x in r), r


def test_a_wrong_market_book_is_refused_by_the_provider():
    """Market identity is the provider's gate, not eligibility's, and it
    returns no snapshot at all -- so nothing can reach preview.

    ON PMUS the identity to match is the SLUG and the OUTCOME SIDE, not a
    CTF token id: a PMUS order carries no token id, so a token check here
    would assert a Polymarket.com fact about a Polymarket US order. The
    behaviour is proven end to end in
    `test_e1_live_path.py::test_FAILS_CLOSED_wrong_market_or_wrong_outcome`;
    this is the structural half.
    """
    import os
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pmus_provider.py"), encoding="utf8").read()
    for refusal in ("BOOK_IS_FOR_ANOTHER_MARKET",
                    "LEG_SLUG_MISMATCH_",
                    "BOTH_LEGS_NAME_THE_SAME_OUTCOME_SIDE",
                    "OUTCOME_SIDES_ARE_NOT_THE_AUTHORIZED_PAIR",
                    "TIMING_AUTHORITY_IS_NOT_THE_PMUS_AUTHENTICATED_WS",
                    "REST_CLAIMED_AS_TIMING_AUTHORITY"):
        assert refusal in src, refusal


# ============ NOTHING UNEXECUTABLE REACHES PREVIEW OR A LATCH ===========

@pytest.mark.parametrize("over", [
    {"fl_best_bid": "0.00", "first_leg_price": "0.00"},
    {"fl_best_bid": None},
    {"fl_best_bid": "NaN", "first_leg_price": "NaN"},
    {"fl_best_bid": "-1", "first_leg_price": "-1"},
    {"fl_best_ask": "0.10"},
    {"cp_best_bid": "0"},
    {"first_leg_price": None},
])
def test_no_unexecutable_book_reaches_preview_or_consumes_a_latch(
        monkeypatch, tmp_path, over):
    """THE SAFETY CLAIM, through the REAL run_cohort: zero transport calls,
    zero latch slots, zero placements."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    FakeTransport.cancels = []
    calls = []

    class Spy:
        def request(self, method, path, body=None, base=None):
            calls.append((method, path))
            raise AssertionError("preview was reached: %s %s"
                                 % (method, path))

    arm(monkeypatch)
    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=lambda: snap(**over),
                       tracker=tracker, transport_factory=Spy,
                       now_fn=lambda: BASE + 10, max_iterations=12)
    assert calls == [], calls
    assert FakeTransport.posts == []
    assert out["placement_count"] == 0
    assert tracker.placements == 0
    assert tracker.venue_errors == 0
    d = tmp_path / "e1_cohort"
    assert not d.exists() or list(d.glob("*.submitted")) == []


def test_a_healthy_book_still_reaches_preview(monkeypatch, tmp_path):
    """THE CONTROL. If E8 refused everything the tests above would pass
    vacuously, so prove the healthy book is still eligible and still
    reaches the transport."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    FakeTransport.cancels = []
    arm(monkeypatch)
    ok, r = refusals(monkeypatch, tmp_path, snap())
    assert ok is True, r
    out = R.run_cohort(snapshot_provider=lambda: snap(),
                       transport_factory=FakeTransport,
                       now_fn=lambda: BASE + 10, max_iterations=3)
    assert out["placement_count"] >= 1
    assert FakeTransport.posts
    assert all(p["quantity"] == 0.13 for p in FakeTransport.posts)
    assert all(p["post_only"] is True for p in FakeTransport.posts)


def test_the_frozen_economics_are_untouched():
    from analysis.l5.e1_cohort import spec as SPC
    assert str(SPC.FIRST_LEG_QTY) == "0.13"
    assert str(SPC.MAX_FIRST_LEG_PRICE) == "0.60"
    assert str(SPC.PAIR_COST_CEILING) == "0.96"
    assert SPC.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SPC.WINDOWS == 12
    assert SPC.MAX_PLACEMENTS == 240
