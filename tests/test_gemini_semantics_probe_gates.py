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
    """Case A with a predeclared limit now BELOW the ask: refuse, never
    reprice upward."""
    rec = _run_probe(monkeypatch, favored="yes", ask="0.80", bid="0.40",
                     limit="0.50", qty="0.01", armed=True)
    assert rec["stop"].startswith("PRICE_NO_LONGER_FITS_CASE")
    assert "chasing" in rec["intended_action"]["price_fits_case"]["why"]
    assert rec["orders_submitted"] == 0


def test_case_b_price_must_rest_not_cross(monkeypatch):
    """Case B with a limit at or above the bid could cross: refuse."""
    rec = _run_probe(monkeypatch, favored="yes", ask=None, bid="0.40",
                     limit="0.45", qty="0.01", armed=True)
    assert rec["classification"]["case"] == "B"
    assert rec["stop"].startswith("PRICE_NO_LONGER_FITS_CASE")


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
# harness
# --------------------------------------------------------------------------

def _run_probe(monkeypatch, *, favored, ask, bid, limit, qty,
               active=None, armed=False, state_dir=None, trace=None,
               halt_file=None):
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
    monkeypatch.setattr(metadata, "terms_for", lambda s: {"tick_size": "0.01"})
    monkeypatch.setattr(metadata, "outcome_book", lambda s: book)
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: {})
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

    return asyncio.run(sp.run(symbol="GEMI-BTC05M2610030225-UP",
                              favored=favored, auth_id=PROBE_ID,
                              limit_price=limit,
                              state_dir=state_dir or tempfile.mkdtemp()))
