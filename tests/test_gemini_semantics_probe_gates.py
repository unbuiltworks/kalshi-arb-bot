"""Every gate on the execution-semantics probe, proved rather than asserted.

The narrow change under test: `makerOrCancel: False` is now REACHABLE, but
only inside the dedicated semantics-probe path. These tests exist to prove
that the widening is exactly that narrow -- that ordinary Gemini execution is
still post-only, that only the probe's own one-shot authorization can request
a crossing order, and that every bound the owner set is structural rather than
a matter of the caller behaving well.

MUTATION DISCIPLINE. Several tests below would still pass against a
permissive implementation, so each one is paired with its negative: the thing
that must be REFUSED is asserted to raise, not merely the thing that must be
allowed.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D

import pytest

from src.gemini_execution import arming, payload, semantics_probe as sp
from src.gemini_execution import spec, transport

PROBE_ID = payload.SEMANTICS_PROBE_AUTH_ID


# --------------------------------------------------------------------------
# 1. ordinary Gemini execution still requires makerOrCancel = True
# --------------------------------------------------------------------------

def test_default_order_is_post_only():
    o = payload.build_order(symbol="S", price="0.50", quantity="1")
    assert o["makerOrCancel"] is True
    assert type(o["makerOrCancel"]) is bool
    payload.assert_types(o)             # strict mode accepts it


def test_strict_assert_types_refuses_a_taker_order():
    """The pre-existing guarantee, unchanged: without opting in, False fails."""
    t = payload.build_order(symbol="S", price="0.50", quantity="1",
                            maker_or_cancel=False)
    with pytest.raises(payload.PayloadViolation):
        payload.assert_types(t)


def test_existing_callers_are_unchanged():
    """Nothing in the repo passes maker_or_cancel, so every existing order is
    byte-identical to before the change."""
    import inspect
    src = inspect.getsource(payload.build_order)
    assert "maker_or_cancel: bool = True" in src, "default must stay True"
    a = payload.build_order(symbol="S", price="0.33", quantity="2")
    b = dict(a)
    b["makerOrCancel"] = True
    assert a == b


def test_maker_or_cancel_must_be_a_real_bool():
    for bad in (1, 0, "true", "false", None):
        with pytest.raises(payload.PayloadViolation):
            payload.build_order(symbol="S", price="0.50", quantity="1",
                                maker_or_cancel=bad)


# --------------------------------------------------------------------------
# 2. ONLY the semantics probe can request False
# --------------------------------------------------------------------------

def _arm(monkeypatch, auth_id, live="YES"):
    import time as _t
    monkeypatch.setenv(arming.LIVE_ENV, live)
    monkeypatch.setenv(arming.AUTH_ENV, auth_id)
    # An authorization with no end is refused by `arming`, so a test arm must
    # carry a real future bound exactly as a live one does.
    monkeypatch.setenv(arming.EXPIRY_ENV,
                       _t.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   _t.gmtime(_t.time() + 3600)))
    monkeypatch.setattr(arming, "EXPECTED_AUTH_ID", auth_id, raising=False)


def test_allow_taker_refused_under_any_other_authorization(monkeypatch):
    _arm(monkeypatch, "OWNER-SOME-OTHER-STRATEGY-7")
    sent = []
    monkeypatch.setattr(transport, "_post",
                        lambda *a, **k: sent.append(1) or {})
    with pytest.raises(transport.VenueError) as e:
        asyncio.run(transport.place(symbol="S", side="buy", outcome="yes",
                                    price="0.50", quantity="1",
                                    allow_taker=True))
    assert "allow_taker is refused" in str(e.value)
    assert not sent, "nothing may reach the wire on a refused arm"


def test_allow_taker_permitted_only_under_the_probe_authorization(monkeypatch):
    _arm(monkeypatch, PROBE_ID)
    seen = {}

    async def fake_post(path, params=None, **k):
        seen["body"] = params
        return {"ok": True, "classification": "ACCEPTED", "status": 200,
                "body": {"orderId": "1"}}

    monkeypatch.setattr(transport, "_post", fake_post)
    res = asyncio.run(transport.place(symbol="S", side="buy", outcome="yes",
                                      price="0.50", quantity="1",
                                      allow_taker=True))
    assert res["classification"] == "ACCEPTED"
    assert seen["body"]["makerOrCancel"] is False


def test_the_same_path_is_still_post_only_without_allow_taker(monkeypatch):
    """Even the probe's own authorization sends post-only unless it asks."""
    _arm(monkeypatch, PROBE_ID)
    seen = {}

    async def fake_post(path, params=None, **k):
        seen["body"] = params
        return {"ok": True, "classification": "ACCEPTED", "status": 200,
                "body": {"orderId": "1"}}

    monkeypatch.setattr(transport, "_post", fake_post)
    asyncio.run(transport.place(symbol="S", side="buy", outcome="yes",
                                price="0.50", quantity="1"))
    assert seen["body"]["makerOrCancel"] is True


def test_taker_still_needs_the_arm_at_all(monkeypatch):
    """allow_taker is not a way around arming."""
    monkeypatch.delenv(arming.LIVE_ENV, raising=False)
    monkeypatch.delenv(arming.AUTH_ENV, raising=False)
    with pytest.raises(arming.NotArmed):
        asyncio.run(transport.place(symbol="S", side="buy", outcome="yes",
                                    price="0.50", quantity="1",
                                    allow_taker=True))


# --------------------------------------------------------------------------
# 3. the probe remains LIMIT-ORDER-ONLY
# --------------------------------------------------------------------------

def test_taker_order_is_still_a_limit_order():
    t = payload.build_order(symbol="S", price="0.50", quantity="1",
                            maker_or_cancel=False)
    assert t["orderType"] == "limit"
    assert t["timeInForce"] == spec.TIME_IN_FORCE_GTC
    payload.assert_types(t, allow_taker=True)


def test_allow_taker_does_not_widen_any_other_field():
    """Opting in widens ONE value. A market order, a stray field or a bad
    side must still fail."""
    t = payload.build_order(symbol="S", price="0.50", quantity="1",
                            maker_or_cancel=False)
    bad = dict(t); bad["orderType"] = "market"
    with pytest.raises(payload.PayloadViolation):
        payload.assert_types(bad, allow_taker=True)
    bad = dict(t); bad["side"] = "sideways"
    with pytest.raises(payload.PayloadViolation):
        payload.assert_types(bad, allow_taker=True)
    bad = dict(t); bad["clientOrderId"] = "x"
    with pytest.raises(payload.PayloadViolation):
        payload.validate_structure(bad)


# --------------------------------------------------------------------------
# 4. $5 maximum cash exposure per market
# --------------------------------------------------------------------------

def test_cash_cap_is_five_dollars():
    assert sp.MAX_CASH_PER_MARKET_USD == D("5.00")


def test_cash_cap_refuses_an_over_cap_order(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="100")     # 0.50 * 100 = $50
    assert rec["stop"].startswith("CASH_CAP_BREACH")
    assert rec["orders_submitted"] == 0


def test_cash_cap_admits_the_tiny_probe_order(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="0.01")
    assert "CASH_CAP_BREACH" not in (rec.get("stop") or "")
    assert D(rec["intended_action"]["cash_at_risk_usd"]) <= D("5.00")


# --------------------------------------------------------------------------
# 5. ONE outstanding probe at a time
# --------------------------------------------------------------------------

def test_an_existing_live_order_refuses_the_probe(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="0.01",
                     active=[{"orderId": "already-there"}])
    assert rec["stop"].startswith("ALREADY_OUTSTANDING")
    assert rec["orders_submitted"] == 0


def test_only_one_order_is_ever_sent(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="0.01", armed=True)
    assert rec["orders_submitted"] <= sp.MAX_ORDERS_PER_PROBE == 1
    assert rec["mutations"].count("place") <= 1


# --------------------------------------------------------------------------
# 6. no EXTEND, averaging, chasing, FLIP or complement
# --------------------------------------------------------------------------

def test_no_extend_average_chase_flip_or_complement_exists():
    import inspect
    src = inspect.getsource(sp)
    body = src.lower()
    for forbidden in ("def extend", "def average", "def chase", "def flip",
                      "def complement", "cancel_all"):
        assert forbidden not in body, forbidden
    # exactly one call site for place(), so a second order is not expressible
    assert src.count("transport.place(") == 1


def test_a_moved_book_stops_rather_than_chasing(monkeypatch):
    """Case A with an approved limit now BELOW the ask: refuse, never reprice
    upward. Since the limit must also be tied to the approved ask, a book that
    has moved trips that gate first -- either way nothing is sent and no price
    is adjusted."""
    rec = _run_probe(monkeypatch, favored="yes", ask="0.80", bid="0.40",
                     limit="0.50", qty="0.01", armed=True)
    assert rec["stop"].startswith("LIMIT_NOT_TIED_TO_APPROVED_ASK")
    assert rec["orders_submitted"] == 0

    # and with the limit correctly tied at approval, a LATER move up is caught
    # by the pre-submission re-read rather than chased.
    rec2 = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                      limit="0.50", qty="1", armed=True, approved_ask="0.50",
                      submission_ask="0.80")
    assert rec2["stop"].startswith("APPROVED_LIQUIDITY_GONE")
    assert rec2["orders_submitted"] == 0


def test_case_b_price_must_rest_not_cross(monkeypatch):
    """Case B's price rule, checked directly. Case B is NOT authorized for
    live submission (see test_case_b_market_stops_rather_than_becoming_a_fallback),
    so the rule is exercised as a function rather than through a run."""
    cls = sp.classify({"readable": True, "yes": {"ask": None, "bid": "0.40"},
                       "no": {"ask": None, "bid": None}}, "yes")
    assert cls["case"] == "B"
    assert not sp.price_does_what_the_case_needs("B", "0.45", cls)["ok"]
    assert sp.price_does_what_the_case_needs("B", "0.39", cls)["ok"]


# --------------------------------------------------------------------------
# 7. no automatic price selection
# --------------------------------------------------------------------------

def test_a_missing_predeclared_price_refuses_the_probe(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit=None, qty="0.01")
    assert rec["stop"].startswith("NO_PREDECLARED_LIMIT_PRICE")
    assert rec["orders_submitted"] == 0


def test_the_probe_never_derives_a_price_from_the_book():
    """The only price that reaches the order is the argument, verbatim.

    Checked on the AST rather than on a substring scan, so prose in a comment
    cannot pass or fail it: `limit_price` is never REBOUND anywhere in `run`,
    and the place() call passes that exact name as `price`.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(sp.run).lstrip())
    fn = tree.body[0]

    rebound = [n for n in ast.walk(fn)
               if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
               for t in (n.targets if isinstance(n, ast.Assign)
                         else [n.target])
               if isinstance(t, ast.Name) and t.id == "limit_price"]
    assert not rebound, "limit_price must never be recomputed inside run()"

    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "place"]
    assert len(calls) == 1, "exactly one place() call site"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw["price"], ast.Name) and kw["price"].id == "limit_price"


# --------------------------------------------------------------------------
# 8. one-shot authorization consumed BEFORE submission
# --------------------------------------------------------------------------

def test_latch_is_consumed_before_the_order_is_sent(monkeypatch, tmp_path):
    order = []
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="0.01", armed=True,
                     state_dir=str(tmp_path), trace=order)
    assert rec["orders_submitted"] == 1
    assert order[:2] == ["latch", "place"], order


def test_a_spent_authorization_cannot_submit_again(monkeypatch, tmp_path):
    for i in range(2):
        rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                         limit="0.50", qty="0.01", armed=True,
                         state_dir=str(tmp_path))
        if i == 1:
            assert rec["stop"].startswith("AUTHORIZATION_ALREADY_SPENT")
            assert rec["orders_submitted"] == 0


# --------------------------------------------------------------------------
# 9. any semantic / reconciliation anomaly halts further submissions
# --------------------------------------------------------------------------

def test_halt_file_blocks_every_later_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "HALT_FILE", tmp_path / "HALT")
    sp.halt("a contradiction we have not explained")
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit="0.50", qty="0.01", armed=True,
                     halt_file=tmp_path / "HALT")
    assert rec["stop"].startswith("HALTED")
    assert rec["orders_submitted"] == 0


def test_an_unreadable_halt_file_is_treated_as_halted(monkeypatch, tmp_path):
    d = tmp_path / "dir-not-file"
    d.mkdir()
    monkeypatch.setattr(sp, "HALT_FILE", d)
    assert sp.halted() is not None


def test_case_a_not_filling_is_an_anomaly():
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.50", "quantity": "0.01"},
           "observations": [{"t_s": 2.0, "order": {"order": {}}}],
           "classification": {"favored_ask": "0.50"}}
    r = sp._reconcile(rec, "A", "yes", "0.50", "0.01", D("0"))
    assert r["anomaly"] and r["kind"] == "SEMANTIC_CONTRADICTION"


def test_case_b_filling_immediately_is_an_anomaly():
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.30", "quantity": "0.01"},
           "observations": [{"t_s": 1.0, "order": {"order": {}}}],
           "classification": {"favored_ask": None}}
    r = sp._reconcile(rec, "B", "yes", "0.30", "0.01", D("0.01"))
    assert r["anomaly"] and r["kind"] == "SEMANTIC_CONTRADICTION"


def test_case_b_filling_later_is_NOT_an_anomaly():
    """Being taken while resting is the expected way to trade, not a
    contradiction. The mutation that matters: this must NOT halt."""
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.30", "quantity": "0.01"},
           "observations": [{"t_s": 40.0, "order": {"order": {}}}],
           "classification": {"favored_ask": None}}
    r = sp._reconcile(rec, "B", "yes", "0.30", "0.01", D("0.01"))
    assert not r["anomaly"]


def test_mapping_mismatch_is_an_anomaly():
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.50", "quantity": "0.01"},
           "observations": [{"t_s": 2.0,
                             "order": {"order": {"outcome": "no"}}}],
           "classification": {"favored_ask": "0.50"}}
    r = sp._reconcile(rec, "A", "yes", "0.50", "0.01", D("0.01"))
    assert r["anomaly"] and r["kind"] == "MAPPING_MISMATCH"


def test_unexplained_partial_is_an_anomaly():
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.50", "quantity": "1"},
           "observations": [{"t_s": 2.0, "order": {"order": {}}}],
           "classification": {"favored_ask": "0.50"}}
    r = sp._reconcile(rec, "A", "yes", "0.50", "1", D("0.4"))
    assert r["anomaly"] and r["kind"] == "UNEXPLAINED_PARTIAL"


def test_nothing_is_ever_labelled_realized(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.40",
                     limit=None, qty="0.01")
    assert rec["realized"].startswith("NOT_REALIZED")


# --------------------------------------------------------------------------
# 10. the classifier reads the book SHAPE metadata actually returns
# --------------------------------------------------------------------------

def test_classify_reads_outcome_book_shape():
    """REGRESSION. The first live preflight classified a two-sided market as
    case B -- "nothing buyable" -- while 750 contracts were offered at 0.50,
    because classify() read `prices.buy[favored]` instead of the per-outcome
    ladder `outcome_book` publishes. A probe testing our book interpretation
    must not itself misread the book.
    """
    real = {"readable": True, "crossed": False,
            "yes": {"ask": "0.5", "bid": "0.49"},
            "no": {"ask": "0.51", "bid": "0.5"},
            "identity_consistent": True}
    c = sp.classify(real, "yes")
    assert c["case"] == "A", "a visible 0.50 ask is case A"
    assert c["favored_ask"] == "0.5"
    assert c["favored_bid"] == "0.49"


def test_classify_refuses_an_unrecognised_book_shape():
    """The mutation that matters: absence must not be inferred from a shape we
    do not recognise. The OLD raw-contract shape must now RAISE, not quietly
    return case B."""
    stale_shape = {"readable": True,
                   "prices": {"buy": {"yes": "0.5"}, "sell": {"yes": "0.49"}}}
    with pytest.raises(sp.ProbeRefusal):
        sp.classify(stale_shape, "yes")


def test_classify_calls_a_genuinely_absent_ask_case_b():
    real = {"readable": True, "crossed": False,
            "yes": {"ask": None, "bid": "0.49"},
            "no": {"ask": "0.51", "bid": None},
            "identity_consistent": True}
    assert sp.classify(real, "yes")["case"] == "B"


# --------------------------------------------------------------------------
# 11. CASE A: the limit must be TIED to the approved ask (no universal 0.99)
# --------------------------------------------------------------------------

def test_limit_must_equal_the_approved_ask_or_one_tick_above():
    f = sp.limit_is_tied_to_the_approved_ask
    assert f("0.50", "0.50", "0.01")["ok"]          # the ask itself
    assert f("0.51", "0.50", "0.01")["ok"]          # one tick above
    assert not f("0.52", "0.50", "0.01")["ok"]      # two ticks: refused
    assert not f("0.99", "0.50", "0.01")["ok"]      # the old universal price
    assert not f("0.49", "0.50", "0.01")["ok"]      # below: would not cross


def test_a_universal_crossing_price_is_now_refused(monkeypatch):
    """The specific change asked for: 0.99 against a 0.50 ask must STOP."""
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.49",
                     limit="0.99", qty="1", armed=True, approved_ask="0.50")
    assert rec["stop"].startswith("LIMIT_NOT_TIED_TO_APPROVED_ASK")
    assert rec["orders_submitted"] == 0


def test_case_a_requires_an_approved_ask(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.49",
                     limit="0.50", qty="1", armed=True, approved_ask=None)
    assert rec["stop"].startswith("NO_APPROVED_ASK")
    assert rec["orders_submitted"] == 0


def test_no_tick_in_terms_means_one_tick_above_is_undefined():
    r = sp.limit_is_tied_to_the_approved_ask("0.50", "0.50", None)
    assert not r["ok"] and "undefined" in r["why"]


# --------------------------------------------------------------------------
# 12. the pre-submission re-read
# --------------------------------------------------------------------------

def _book(ask, bid="0.49", favored="yes"):
    return {"readable": True, "crossed": False,
            favored: {"ask": ask, "bid": bid},
            "no": {"ask": None, "bid": None}}


def _depth(levels):
    return {"asks": {"levels": levels}}


def test_still_executable_accepts_unchanged_liquidity():
    r = sp.still_executable_at_the_frozen_limit(
        _book("0.50"), _depth([{"price": "0.50", "qty": "750"}]),
        "yes", "0.50", "1")
    assert r["ok"] and r["displayed_size_at_or_below_limit"] == "750"


def test_still_executable_stops_when_the_ask_moved_up():
    r = sp.still_executable_at_the_frozen_limit(
        _book("0.52"), _depth([{"price": "0.52", "qty": "750"}]),
        "yes", "0.51", "1")
    assert not r["ok"] and "MOVED UP" in r["why"]


def test_still_executable_stops_when_the_offer_disappeared():
    r = sp.still_executable_at_the_frozen_limit(
        _book(None), _depth([]), "yes", "0.50", "1")
    assert not r["ok"] and "DISAPPEARED" in r["why"]


def test_still_executable_stops_on_insufficient_displayed_size():
    r = sp.still_executable_at_the_frozen_limit(
        _book("0.50"), _depth([{"price": "0.50", "qty": "0.4"}]),
        "yes", "0.50", "1")
    assert not r["ok"] and "insufficient displayed size" in r["why"]


def test_size_above_the_limit_does_not_count():
    """Depth sitting ABOVE the frozen limit is not takeable at that limit."""
    r = sp.still_executable_at_the_frozen_limit(
        _book("0.50"), _depth([{"price": "0.50", "qty": "0.5"},
                               {"price": "0.80", "qty": "900"}]),
        "yes", "0.50", "1")
    assert not r["ok"]


def test_a_vanished_offer_stops_the_run_and_sends_nothing(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.49",
                     limit="0.50", qty="1", armed=True, approved_ask="0.50",
                     submission_ask=None)
    assert rec["stop"].startswith("APPROVED_LIQUIDITY_GONE")
    assert rec["orders_submitted"] == 0
    assert "SPENT" in rec["authorization_note"]


# --------------------------------------------------------------------------
# 13. live terms are a hard pre-submit requirement
# --------------------------------------------------------------------------

GOOD_TERMS = {"readable": True, "missing_fields": [],
              "quantityMinimum": "0.01", "quantityIncrement": "0.01",
              "priceMinimum": "0.01", "priceIncrement": "0.01",
              "contract_status": "active", "market_state": "open"}


def test_live_terms_accepted_when_complete():
    r = sp.terms_are_live(GOOD_TERMS)
    assert r["ok"] and r["tick"] == "0.01"


@pytest.mark.parametrize("mutate", [
    {"readable": False},
    {"missing_fields": ["priceIncrement"]},
    {"priceIncrement": None},
    {"quantityMinimum": ""},
    {"contract_status": "halted"},
    {"market_state": "closed"},
])
def test_live_terms_refused_on_any_defect(mutate):
    t = dict(GOOD_TERMS); t.update(mutate)
    assert not sp.terms_are_live(t)["ok"]


def test_null_terms_are_a_hard_pre_submit_failure(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask="0.50", bid="0.49",
                     limit="0.50", qty="1", armed=True, approved_ask="0.50",
                     terms={"readable": True, "missing_fields": [],
                            "quantityMinimum": None, "quantityIncrement": None,
                            "priceMinimum": None, "priceIncrement": None,
                            "contract_status": None, "market_state": None})
    assert rec["stop"].startswith("TERMS_READ_FAILED")
    assert rec["orders_submitted"] == 0


# --------------------------------------------------------------------------
# 14. case B is NOT authorized, and is not a fallback
# --------------------------------------------------------------------------

def test_case_b_market_stops_rather_than_becoming_a_fallback(monkeypatch):
    rec = _run_probe(monkeypatch, favored="yes", ask=None, bid="0.49",
                     limit="0.01", qty="1", armed=True)
    assert rec["stop"].startswith("CASE_B_NOT_AUTHORIZED")
    assert "Not selecting another market" in rec["stop"]
    assert rec["orders_submitted"] == 0


# --------------------------------------------------------------------------
# 15. a fill above the approved limit is a contradiction, not a cost
# --------------------------------------------------------------------------

def test_fill_above_the_limit_is_an_anomaly():
    rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                              "price": "0.50", "quantity": "1"},
           "observations": [{"t_s": 2.0, "order": {"order": {
               "avgExecutionPrice": "0.62"}}}],
           "classification": {"favored_ask": "0.50"}}
    r = sp._reconcile(rec, "A", "yes", "0.50", "1", D("1"))
    assert r["anomaly"] and r["kind"] == "FILL_ABOVE_LIMIT"


def test_fill_at_or_below_the_limit_is_not_an_anomaly():
    for px in ("0.50", "0.48"):
        rec = {"submitted_body": {"side": "buy", "outcome": "yes",
                                  "price": "0.50", "quantity": "1"},
               "observations": [{"t_s": 2.0, "order": {"order": {
                   "avgExecutionPrice": px}}}],
               "classification": {"favored_ask": "0.50"}}
        r = sp._reconcile(rec, "A", "yes", "0.50", "1", D("1"))
        assert not r["anomaly"], px


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

_SENTINEL = object()


def _run_probe(monkeypatch, *, favored, ask, bid, limit, qty,
               active=None, armed=False, state_dir=None, trace=None,
               halt_file=None, approved_ask=_SENTINEL, terms=None,
               submission_ask=_SENTINEL, depth_qty="750"):
    """Drive sp.run with every venue read stubbed. No network, no credentials."""
    import tempfile
    from src.gemini_execution import latch, metadata

    if halt_file is None:
        halt_file = tempfile.mkdtemp() + "/NO_HALT"
    monkeypatch.setattr(sp, "HALT_FILE", __import__("pathlib").Path(halt_file))

    # The REAL shape metadata.outcome_book returns: each outcome's own ladder
    # under its own name. Reading prices.buy/prices.sell here instead is the
    # defect the first live preflight caught.
    other = "no" if favored == "yes" else "yes"
    book = {"readable": True, "crossed": False,
            favored: {"ask": ask, "bid": bid},
            other: {"ask": None, "bid": None},
            "identity_consistent": True}
    # the book as it reads at SUBMISSION time; defaults to unchanged
    sub_ask = ask if submission_ask is _SENTINEL else submission_ask
    books = iter([book])

    def _book_read(s):
        try:
            return next(books)
        except StopIteration:
            return {"readable": True, "crossed": False,
                    favored: {"ask": sub_ask, "bid": bid},
                    other: {"ask": None, "bid": None},
                    "identity_consistent": True}

    monkeypatch.setattr(metadata, "terms_for",
                        lambda s: terms if terms is not None else dict(
                            GOOD_TERMS))
    monkeypatch.setattr(metadata, "outcome_book", _book_read)
    monkeypatch.setattr(
        metadata, "contract_depth",
        lambda s, **k: {"asks": {"levels": (
            [] if sub_ask is None
            else [{"price": sub_ask, "qty": depth_qty}])}})
    monkeypatch.setattr(metadata, "resolution_for", lambda s, **k: {})
    monkeypatch.setattr(metadata, "smallest_valid_quantity", lambda t: qty)
    monkeypatch.setattr(metadata, "price_is_valid",
                        lambda p, t: {"valid": True})
    monkeypatch.setattr(metadata, "quantity_is_valid",
                        lambda q, t: {"valid": True})

    async def _positions(**k):
        return {}

    async def _history(**k):
        return {}

    monkeypatch.setattr(metadata, "positions", _positions)
    monkeypatch.setattr(metadata, "order_history", _history)

    async def _active(symbol=None):
        return {"ok": True, "orders": active or []}

    monkeypatch.setattr(transport, "active_orders", _active)
    monkeypatch.setattr(transport, "_orders_of",
                        lambda res: (res or {}).get("orders") or [])

    async def _find(**k):
        return {"resolved": True, "order": {"status": "filled",
                                            "filledQuantity": qty}}

    monkeypatch.setattr(transport, "find_order", _find)

    async def _cancel(*a, **k):
        return {"cancel": "CONFIRMED"}

    monkeypatch.setattr(transport, "cancel_and_confirm", _cancel)

    async def _place(**k):
        if trace is not None:
            trace.append("place")
        return {"ok": True, "classification": "ACCEPTED", "status": 200,
                "body": {"orderId": "probe-1"},
                "request": {"side": "buy", "outcome": favored,
                            "price": str(limit), "quantity": str(qty)}}

    monkeypatch.setattr(transport, "place", _place)

    real_consume = latch.consume

    def _consume(auth_id, intent, directory=None, **k):
        if trace is not None:
            trace.append("latch")
        return real_consume(auth_id, intent, directory=directory)

    monkeypatch.setattr(latch, "consume", _consume)
    monkeypatch.setattr(latch, "advance", lambda *a, **k: None)
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(sp.asyncio, "sleep", _no_sleep)

    if armed:
        _arm(monkeypatch, PROBE_ID)
    else:
        monkeypatch.delenv(arming.LIVE_ENV, raising=False)

    # default: the approved ask IS the observed ask, which is the normal case.
    aa = ask if approved_ask is _SENTINEL else approved_ask
    return asyncio.run(sp.run(symbol="GEMI-BTC05M2610030225-UP",
                              favored=favored, auth_id=PROBE_ID,
                              limit_price=limit, quantity=qty,
                              approved_ask=aa,
                              state_dir=state_dir or tempfile.mkdtemp()))
