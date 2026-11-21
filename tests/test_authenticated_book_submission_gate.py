"""OWNER AUTHORIZATION 2026-09-17 -- the authenticated-book freshness gate.

THE FAILURE THIS PREVENTS, from the CP1 13-order forensics. On
aec-dota2-yes-tm-2026-09-16 the gateway book read 0.45/0.49 while the venue's
own ask was 0.45. The authenticated book that would have caught it was 11.2s
old, so `_authenticated_trim` discarded it for being stale and failed OPEN --
leaving the gateway price in place. Five post-only BUY YES at 0.45 crossed the
real ask and were refused at insertion (leavesQuantity 0, cumQuantity 0,
insertTime == lastTransactTime). Order 6 is the control: a fresh read arrived,
the trim applied, 0.45 -> 0.44, and the order rested.

THE INVARIANT: a live order is not submitted when the authenticated executable
book required to validate post-only placement is stale, missing, unreadable or
otherwise outside the freshness standard. The controller may still WANT the
action; the execution adapter refuses to send it.

MODEL WANTED ACTION != EXECUTION COULD SAFELY SUBMIT ACTION.
"""
from __future__ import annotations

import inspect
import json

import pytest

from src import pmus_spring_l5_session as spring
from tests.test_pmus_spring_l5_session import (SLUG, Fake, _arm,
                                               _script_proposals, _ticking)

def _status(age, *, bid="0.5000", ask="0.5050", slug=SLUG, key="spring_watch"):
    """A recorder status document with a controllable authenticated age."""
    return {"t": "2026-09-17T00:00:00+00:00",
            key: [{"slug": slug, "bid": bid, "ask": ask,
                   "bid_qty": "100.0", "ask_qty": "100.0",
                   "both_sides_served": True,
                   "last_authenticated_update_age_s": age}]}


def _book(monkeypatch, doc, source="status_url:test"):
    """Script what the recorder serves, or raise to simulate an outage."""
    from src import pmus_spring_l5_probe_entry as entry

    def fake_load(_):
        if isinstance(doc, Exception):
            raise doc
        return doc, source
    monkeypatch.setattr(entry, "load_status_document", fake_load)


def _run(monkeypatch, tmp_path, doc, *, relations=("open",), fake=None):
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, list(relations))
    _book(monkeypatch, doc)
    fake = fake or Fake(fill_all=False)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    return rep, fake


def _wire_orders(fake):
    """Every POST /v1/orders the session actually made. The wire, not intent."""
    return [b for p, b in fake.posts if p == "/v1/orders"]


def _gated(rep):
    return [d for d in (rep.get("decisions") or []) if d.get("execution_gate")]


# ---------------------------------------------------------------- 1 + 5
def test_1_fresh_authenticated_book_permits_submission(monkeypatch, tmp_path):
    """The gate must not be a blanket refusal. A fresh book that agrees the
    price is non-crossing lets the order through to the wire."""
    rep, fake = _run(monkeypatch, tmp_path, _status(0.2))
    assert _wire_orders(fake), f"nothing submitted on a fresh book: {rep.get('status')}"
    assert not _gated(rep), "a fresh book must not be gated"


def test_5_fresh_and_safely_non_crossing_permits_submission(monkeypatch,
                                                            tmp_path):
    """Explicitly the `already_non_crossing_on_both_books` verdict: the book
    was read, it was fresh, and it said we are safe. That is a definite
    answer and submission is permitted on it."""
    rep, fake = _run(monkeypatch, tmp_path, _status(0.0, bid="0.5000",
                                                    ask="0.9000"))
    assert _wire_orders(fake), "a fresh, safely non-crossing book must submit"
    trims = [d.get("authenticated_trim") for d in rep.get("decisions") or []]
    assert any((t or {}).get("why") == "already_non_crossing_on_both_books"
               or (t or {}).get("applied") for t in trims), trims


# ---------------------------------------------------------------- 2
@pytest.mark.parametrize("age", [10.01, 11.2, 60.0, 734.5])
def test_2_stale_authenticated_book_sends_nothing_to_the_wire(monkeypatch,
                                                              tmp_path, age):
    """11.2s is the exact live value that produced five insertion rejections."""
    rep, fake = _run(monkeypatch, tmp_path, _status(age))
    assert _wire_orders(fake) == [], f"submitted on a {age}s-stale book"
    g = _gated(rep)
    assert g, "a stale book must be recorded as a gated skip"
    assert g[0]["execution_gate"]["why"].startswith("stale:")


# ---------------------------------------------------------------- 3
@pytest.mark.parametrize("doc,why", [
    (_status(0.2, slug="a-different-market"), "unreadable:slug_not_in_status_document"),
    ({"t": "2026-09-17T00:00:00+00:00"}, "unreadable:slug_not_in_status_document"),
    (None, "unreadable:status_document_unavailable"),
    (RuntimeError("recorder down"), "unreadable:RuntimeError"),
    (_status(None), "stale:None"),
])
def test_3_missing_or_unreadable_book_sends_nothing_to_the_wire(
        monkeypatch, tmp_path, doc, why):
    """Absent slug, absent document, a recorder outage, and a row with no age
    at all. None of them is a book, so none of them may submit."""
    rep, fake = _run(monkeypatch, tmp_path, doc)
    assert _wire_orders(fake) == [], f"submitted with {why}"
    g = _gated(rep)
    assert g and g[0]["execution_gate"]["why"].startswith(why.split(":")[0]), g


# ---------------------------------------------------------------- 4
def test_4_fresh_gateway_does_not_rescue_a_stale_authenticated_book(
        monkeypatch, tmp_path):
    """THE LIVE REGRESSION FIXTURE, reproduced exactly.

    The gateway is fresh and confident and WRONG: it serves 0.45/0.49, so the
    touch computes to 0.45. The venue's own ask is 0.45 -- our price would
    cross -- but that book is 11.2s old. Before this gate the stale book was
    discarded, the gateway price was kept, and the order was refused at
    insertion. There must now be no submission at all."""
    fake = Fake(bid="0.4500", ask="0.4900", fill_all=False)
    rep, fake = _run(monkeypatch, tmp_path,
                     _status(11.2, bid="0.4300", ask="0.4500"), fake=fake)
    assert _wire_orders(fake) == [], (
        "the fresher-but-wrong gateway book was used as a fallback and an "
        "order was sent -- this is the exact 2026-09-16 failure")
    g = _gated(rep)
    assert g, "the live failure shape must be recorded as a gated skip"
    assert g[0]["execution_gate"]["why"] == "stale:11.2"
    # and the reason names the real condition, not a category
    assert "stale" in g[0]["refusal"]


# ---------------------------------------------------------------- 6
def test_6_restart_cannot_bypass_the_freshness_gate(monkeypatch, tmp_path):
    """Durable state survives a restart; the gate is not part of that state.
    A resumed session re-reads the book and refuses on the same terms, so
    there is no 'already decided, just send it' path across a restart."""
    for attempt in (1, 2):
        rep, fake = _run(monkeypatch, tmp_path, _status(11.2))
        assert _wire_orders(fake) == [], f"restart {attempt} submitted"
        assert _gated(rep), f"restart {attempt} lost the gate"
    # The gate reads the book every time rather than caching a verdict.
    # Checked by ORDER, not by a character window -- a lookback window is
    # exactly what broke three sibling tests when this change added comments.
    body = inspect.getsource(spring)
    trim_at = body.index("_trim = _authenticated_trim(")
    gate_at = body.index("_ok, _why = authenticated_book_usable(_trim)")
    assert trim_at < gate_at, "the gate must consume a trim computed in THIS pass"
    # CODE between the two points, with comments stripped -- the explanation
    # of this very gate says "submitted" in prose, and scanning prose for it
    # is how the sibling tests broke. Scan what executes.
    between = "\n".join(
        ln for ln in body[trim_at:gate_at].splitlines()
        if not ln.strip().startswith("#"))
    for danger in ("/v1/orders", "place_order", "submit_order", ".request("):
        assert danger not in between, (
            f"{danger} executes between computing the trim and gating on it")


# ---------------------------------------------------------------- 7
def test_7_a_skipped_action_is_observable_and_costs_nothing(monkeypatch,
                                                            tmp_path):
    """The owner must be able to measure how much opportunity the gate
    removes, and a skip must move no money and create no exposure."""
    rep, fake = _run(monkeypatch, tmp_path, _status(11.2))
    g = _gated(rep)
    assert g, "a skip that leaves no record is not measurable"
    gate = g[0]["execution_gate"]

    # observable, and it names what the model wanted
    assert gate["submitted"] is False
    assert gate["model_wanted"] == "open"
    assert gate["gate"] == "authenticated_book_freshness"
    assert rep.get("execution_gate_skips") == len(g)

    # costs nothing: no wire order, no cash, no inventory, no exposure
    assert _wire_orders(fake) == []
    inv = rep.get("inventory") or {}
    assert inv.get("spent") in ("0", "0.00", None), inv
    assert inv.get("residual") in ("0", "0.00", None), inv
    assert rep.get("filled_actions") in (0, None)


def test_7b_a_skip_is_not_silently_a_wait_prediction(monkeypatch, tmp_path):
    """MODEL WANTED ACTION != EXECUTION COULD SAFELY SUBMIT ACTION.

    The controller drew OPEN. The adapter refused. Fidelity scoring must see
    BOTH: the model's own draw is untouched, and the hold carries `attempted`
    plus a named refusal so it aggregates as a skip and never as evidence
    that the model predicted WAIT."""
    rep, _ = _run(monkeypatch, tmp_path, _status(11.2))
    dec = _gated(rep)[0]
    assert dec["acted"] == "hold"
    assert dec["attempted"] == "open", "what the model wanted must survive"
    assert dec["refusal"].startswith("authenticated_book_unusable:")
    # the model's own draw is untouched by the execution refusal
    assert (dec.get("shadow") or {}).get("stochastic", {}).get("sampled") \
        or dec.get("v2_recommended") == "open"
    # classified as an execution adaptation, NOT a safety cap and NOT a
    # model deficiency -- so it cannot be read as the model choosing to wait
    assert dec["divergence_class"] == (
        "venue/execution adaptation (authenticated book not fresh enough to "
        "validate post-only placement)")


def test_7c_the_skip_reason_is_countable_per_condition(monkeypatch, tmp_path):
    """`hold_refusals` buckets by the refusal string, so the sub-reason has to
    be IN the string or every cause collapses into one number and the
    counterfactual cannot be measured."""
    seen = set()
    for doc in (_status(11.2), _status(0.2, slug="elsewhere")):
        rep, _ = _run(monkeypatch, tmp_path, doc)
        seen.add(_gated(rep)[0]["refusal"])
    assert len(seen) == 2, seen
    assert any("stale" in r for r in seen)
    assert any("unreadable" in r for r in seen)


# ---------------------------------------------------------------- provenance
def test_raw_source_timestamps_and_provenance_survive_a_refusal(monkeypatch,
                                                                tmp_path):
    """OWNER: "preserve the raw source timestamps and source provenance used
    to make every freshness decision" -- above all on the branches that
    REFUSE, which previously kept nothing but a `why` string."""
    rep, _ = _run(monkeypatch, tmp_path, _status(11.2))
    prov = _gated(rep)[0]["execution_gate"]["provenance"]
    assert prov["source"] == "status_url:test"
    assert prov["status_utc"] == "2026-09-17T00:00:00+00:00"
    assert prov["authenticated_age_s"] == 11.2
    assert prov["freshness_standard_s"] == spring.AUTH_TRIM_MAX_AGE_S
    assert prov["yes_bid"] == "0.5000" and prov["yes_ask"] == "0.5050"
    # the decision is reproducible from the record alone
    assert float(prov["authenticated_age_s"]) > float(
        prov["freshness_standard_s"])


def test_the_trim_still_only_ever_lowers_a_price(monkeypatch, tmp_path):
    """The gate is additive. The pricing half is unchanged: the trim still
    fails open and is still arithmetically incapable of raising a price."""
    src = inspect.getsource(spring)
    body = src[src.index("def _authenticated_trim"):]
    body = body[:body.index("\n        def ", 1)]
    assert "safe = min(gw, auth_ask)" in body
    assert "refused:would_raise_price" in body
    assert body.count('"applied": True') == 1


# ------------------------------------------------------ measured counterfactual
#: The trim verdicts the venue actually produced for all 13 CP1 orders, taken
#: verbatim from the session_end decisions[] of deployments 51114cbc and
#: 9bd90901. Inlined so the fixture outlives the log retention window.
CP1_TRIMS = [
    ("aec-dota2-yes-tm", "open_1", {"applied": False, "why": "stale:11.2"}, True),
    ("aec-dota2-yes-tm", "open_2", {"applied": False, "why": "stale:11.2"}, True),
    ("aec-dota2-yes-tm", "open_3", {"applied": False, "why": "stale:11.2"}, True),
    ("aec-dota2-yes-tm", "open_4", {"applied": False, "why": "stale:11.2"}, True),
    ("aec-dota2-yes-tm", "open_5", {"applied": False, "why": "stale:11.2"}, True),
    ("aec-dota2-yes-tm", "open_6", {"applied": True, "px_in": "0.45",
                                    "px_out": "0.44"}, False),
    ("atc-lib-lqu-pal", "open_1", {"applied": False,
                                   "why": "already_non_crossing_on_both_books"}, False),
    ("atc-lib-lqu-pal", "open_2", {"applied": False,
                                   "why": "unreadable:slug_not_in_status_document"}, False),
    ("atc-lib-lqu-pal", "open_3", {"applied": False,
                                   "why": "unreadable:slug_not_in_status_document"}, False),
    ("atc-lib-lqu-pal", "open_4", {"applied": False,
                                   "why": "unreadable:slug_not_in_status_document"}, False),
    ("atc-lib-lqu-pal", "open_5", {"applied": False,
                                   "why": "unreadable:slug_not_in_status_document"}, False),
    ("atc-lib-lqu-pal", "open_6", {"applied": False,
                                   "why": "unreadable:slug_not_in_status_document"}, False),
    ("aec-county-nor-der", "open_1", {"applied": False,
                                      "why": "already_non_crossing_on_both_books"}, False),
]


def test_counterfactual_every_observed_rejection_would_have_been_prevented():
    """THE DELIVERABLE the owner asked for, pinned as a regression.

    Replay the real trim verdicts from all 13 CP1 orders through the real
    gate. All five orders that the venue refused at insertion were refused
    for the same reason -- an 11.2s-stale authenticated book -- and the gate
    skips every one of them. If this ever stops being 5/5, the gate no longer
    addresses the failure it was authorized to fix."""
    rejected = [r for r in CP1_TRIMS if r[3]]
    assert len(rejected) == 5, "the observed rejection count is a fixed fact"
    prevented = [r for r in rejected
                 if not spring.authenticated_book_usable(r[2])[0]]
    assert len(prevented) == 5, [r[1] for r in rejected
                                 if r not in prevented]


def test_counterfactual_the_gate_is_not_a_blanket_refusal():
    """It must also still let real orders through, or it is just an off
    switch. Three of the thirteen had a definite authenticated verdict."""
    submit = [r for r in CP1_TRIMS if spring.authenticated_book_usable(r[2])[0]]
    skip = [r for r in CP1_TRIMS if not spring.authenticated_book_usable(r[2])[0]]
    assert len(submit) == 3, [r[:2] for r in submit]
    assert len(skip) == 10, len(skip)
    # and the three are the ones whose book actually answered
    assert [r[1] for r in submit] == ["open_6", "open_1", "open_1"]
    # no order that was rejected is in the submit set
    assert not any(r[3] for r in submit)


def test_counterfactual_opportunity_cost_is_stated_not_hidden():
    """The gate removes opportunity and the owner asked to measure it. On
    this sample it is 10/13 actions -- the honest cost of not submitting on a
    book we could not read. Market 2 alone loses 5 of its 6."""
    by_market: dict[str, list[bool]] = {}
    for market, _stage, trim, _rej in CP1_TRIMS:
        by_market.setdefault(market, []).append(
            spring.authenticated_book_usable(trim)[0])
    assert by_market["aec-dota2-yes-tm"] == [False] * 5 + [True]
    assert by_market["atc-lib-lqu-pal"] == [True] + [False] * 5
    assert by_market["aec-county-nor-der"] == [True]
