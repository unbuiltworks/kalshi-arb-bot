"""On a SHARED account, ownership must be proven before anything is cancelled.

The owner's correction: "Account-wide open orders and age alone are
insufficient on a shared account. Never cancel unrelated manual orders."

Two independent requirements, both necessary:
  1. the id is in the executor's own durable live-order record;
  2. the VENUE's row agrees on the market and reports AUTOMATIC.

D23N1EHTTZ7D is the owner's real manual BTC15m order, which this programme was
told never to cancel or modify. It appears here by name on purpose.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_live_execution as EX
from src import l5_order_registry_server as REG
from src import l5_watchdog as W

NOW = dt.datetime(2026, 10, 12, 22, 30, 0, tzinfo=dt.timezone.utc)
MARKET = "cpc-btc-updown-15m-2026-10-12-2230z"
OURS = "D1OURS0001"
MANUAL = "D23N1EHTTZ7D"          # the owner's order. Never ours to cancel.


def open_row(oid, *, created="2026-10-12T22:10:00.000000000Z",
             slug=MARKET, leaves="1.00"):
    return {"id": oid, "marketSlug": slug, "state": "ORDER_STATE_OPEN",
            "leavesQuantity": leaves, "cumQuantity": "0",
            "createTime": created, "insertTime": created}


def detail(oid, *, slug=MARKET, indicator=W.AUTOMATIC, leaves="1.00",
           state="ORDER_STATE_OPEN"):
    return {"id": oid, "marketSlug": slug, "state": state,
            "leavesQuantity": leaves, "cumQuantity": "0",
            "manualOrderIndicator": indicator}


class Venue:
    def __init__(self, open_rows=(), reads=None):
        self._open = list(open_rows)
        self._reads = dict(reads or {})
        self.cancelled = []

    def open_orders(self):
        return list(self._open)

    def order(self, oid):
        return self._reads[oid]

    def cancel(self, oid, market_slug=None):
        self.cancelled.append(oid)
        return {"ok": True}


class Oracle:
    """A stand-in for the executor's registry endpoint."""

    def __init__(self, rows):
        self.rows = dict(rows)

    def claimed(self):
        return dict(self.rows)


def wd(tmp_path, venue, oracle, **kw):
    store = HG.FileStore(str(tmp_path))
    kw.setdefault("cancel_enabled", True)
    kw.setdefault("max_order_age_s", 300)
    return W.Watchdog(venue, HG.HaltAsserter(store, who="test"),
                      oracle=oracle, **kw), store


def gate(tmp_path):
    return HG.HaltGate(HG.FileStore(str(tmp_path)))


# ------------------------------------------------------- no oracle, no cancel

def test_without_an_oracle_the_cancel_power_does_not_exist(tmp_path):
    """Not a default a flag can override: unavailable."""
    v = Venue([open_row(OURS)], {OURS: detail(OURS)})
    w = W.Watchdog(v, HG.HaltAsserter(HG.FileStore(str(tmp_path)), who="t"),
                   max_order_age_s=300, cancel_enabled=True, oracle=None)
    assert w.cancel_enabled is False
    assert w.cancel_requested is True
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert out["cancel_unavailable_no_oracle"] is True
    assert "STILL RESTING" in out["cancel_skipped"]
    # but the halt is still asserted: the executor stops either way
    assert gate(tmp_path).submissions_permitted() is False


def test_an_unreachable_registry_cancels_nothing(tmp_path):
    class Dead:
        def claimed(self):
            return {}
    v = Venue([open_row(OURS)], {OURS: detail(OURS)})
    w, _ = wd(tmp_path, v, Dead())
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert out["claimed_ids"] == 0
    assert "NOTHING is cancellable" in out["cancel_skipped"]
    assert gate(tmp_path).submissions_permitted() is False


# ------------------------------------------- the owner's manual order is safe

def test_the_owners_manual_order_is_never_cancelled_when_unclaimed(tmp_path):
    """The exact hazard: it is old, it is resting, and it is not ours."""
    v = Venue([open_row(MANUAL)],
              {MANUAL: detail(MANUAL,
                              indicator="MANUAL_ORDER_INDICATOR_MANUAL")})
    w, _ = wd(tmp_path, v, Oracle({}))
    out = w.sweep(now=NOW)
    assert v.cancelled == [], "we cancelled the owner's order"
    assert gate(tmp_path).submissions_permitted() is False, (
        "an unmanaged order still halts us -- we just do not touch it")


def test_a_manual_order_is_not_cancellable_even_if_our_record_claims_it(
        tmp_path):
    """A corrupt or buggy claim must not be able to point the cancel at it."""
    v = Venue([open_row(MANUAL)],
              {MANUAL: detail(MANUAL,
                              indicator="MANUAL_ORDER_INDICATOR_MANUAL")})
    w, _ = wd(tmp_path, v, Oracle({MANUAL: {"venue_order_id": MANUAL,
                                            "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "VENUE_SAYS_MANUAL"
    assert out["not_ours"][0]["manual_order_indicator"] == \
        "MANUAL_ORDER_INDICATOR_MANUAL"


def test_an_id_absent_from_the_registry_is_left_alone(tmp_path):
    """Another automated desk on this shared account is AUTOMATIC too."""
    other = "D1OTHER001"
    v = Venue([open_row(other)], {other: detail(other)})
    w, _ = wd(tmp_path, v, Oracle({OURS: {"venue_order_id": OURS,
                                          "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "NOT_IN_OWNERSHIP_REGISTRY"


def test_a_market_mismatch_between_our_claim_and_the_venue_refuses(tmp_path):
    v = Venue([open_row(OURS)], {OURS: detail(OURS, slug="aec-nfl-bal-atl")})
    w, _ = wd(tmp_path, v, Oracle({OURS: {"venue_order_id": OURS,
                                          "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "MARKET_MISMATCH"


def test_an_unreadable_venue_row_sends_no_post_at_all(tmp_path):
    class Blind(Venue):
        def order(self, oid):
            raise TimeoutError("slow")
    v = Blind([open_row(OURS)], {})
    w, _ = wd(tmp_path, v, Oracle({OURS: {"venue_order_id": OURS,
                                          "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == [], "ownership unconfirmed, so nothing was sent"
    assert out["unverified"][0]["why"] == \
        "OWNERSHIP_UNCONFIRMED_NO_POST_SENT"


# ------------------------------------------------------ ours IS cancellable

def test_an_order_in_both_witnesses_is_cancelled_and_verified(tmp_path):
    reads = {OURS: detail(OURS)}
    v = Venue([open_row(OURS)], reads)

    calls = {"n": 0}
    real_order = v.order

    def order(oid):
        calls["n"] += 1
        # first read is the ownership pre-read, second is the verification
        if calls["n"] >= 2:
            return detail(oid, leaves="0", state="ORDER_STATE_CANCELED")
        return real_order(oid)
    v.order = order

    w, _ = wd(tmp_path, v, Oracle({OURS: {"venue_order_id": OURS,
                                          "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == [OURS]
    assert [c["venue_order_id"] for c in out["cancelled"]] == [OURS]
    assert out["not_ours"] == []


def test_ours_and_the_owners_resting_together_cancels_exactly_one(tmp_path):
    """The case that decides it."""
    reads = {OURS: detail(OURS),
             MANUAL: detail(MANUAL,
                            indicator="MANUAL_ORDER_INDICATOR_MANUAL")}
    v = Venue([open_row(OURS), open_row(MANUAL)], reads)
    seen = {"n": 0}

    def order(oid):
        seen["n"] += 1
        if oid == OURS and seen["n"] > 1:
            return detail(oid, leaves="0", state="ORDER_STATE_CANCELED")
        return reads[oid]
    v.order = order

    w, _ = wd(tmp_path, v, Oracle({OURS: {"venue_order_id": OURS,
                                          "market": MARKET}}))
    out = w.sweep(now=NOW)
    assert v.cancelled == [OURS]
    assert MANUAL not in v.cancelled
    assert [n["venue_order_id"] for n in out["not_ours"]] == [MANUAL]


def test_the_ownership_registry_is_read_once_per_sweep(tmp_path):
    class Counting(Oracle):
        def __init__(self, rows):
            super().__init__(rows)
            self.reads = 0

        def claimed(self):
            self.reads += 1
            return super().claimed()

    reads = {OURS: detail(OURS), "D1B": detail("D1B")}
    v = Venue([open_row(OURS), open_row("D1B")], reads)
    o = Counting({OURS: {"venue_order_id": OURS, "market": MARKET},
                  "D1B": {"venue_order_id": "D1B", "market": MARKET}})
    w, _ = wd(tmp_path, v, o)
    w.sweep(now=NOW)
    assert o.reads == 1, "a per-id read lets the answer change mid-loop"


# --------------------------------------------------- the executor's publisher

def test_the_registry_publishes_only_live_ids_with_venue_ids(tmp_path):
    s = EX.InventoryStore(str(tmp_path / "inv.json"))
    s.record_submit("c1", {"market": MARKET, "side": "UP",
                           "qty": "1", "price": "0.6"},
                    session_id="sess-1", mode="MAKER")
    s.attach_venue_id("c1", OURS)
    s.record_submit("c2", {"market": MARKET, "side": "UP",
                           "qty": "1", "price": "0.6"})     # never ACKed
    s.record_submit("c3", {"market": MARKET, "side": "DOWN",
                           "qty": "1", "price": "0.4"})
    s.attach_venue_id("c3", "D1GONE001")
    s.terminate_order("c3", reason="ORDER_STATE_FILLED")

    doc = REG.registry(s)
    assert doc["readable"] is True
    ids = [r["venue_order_id"] for r in doc["orders"]]
    assert ids == [OURS], "only live, ACKed orders are published"
    assert doc["orders"][0]["market"] == MARKET
    assert doc["orders"][0]["session_id"] == "sess-1"
    assert "NOT SUFFICIENT" in doc["labels"]


def test_an_unreadable_ledger_publishes_unknown_not_empty(tmp_path):
    class Broken:
        def live_orders(self):
            raise RuntimeError("volume gone")
    doc = REG.registry(Broken())
    assert doc["readable"] is False and doc["orders"] == []
    assert "UNKNOWN" in doc["labels"]


def test_the_oracle_treats_unreadable_as_owning_nothing(tmp_path):
    served = json.dumps({"readable": False, "orders": [
        {"venue_order_id": OURS, "market": MARKET}]}).encode()
    o = W.OwnershipOracle("http://x", opener=lambda u, t: served)
    assert o.claimed() == {}, (
        "an unreadable ledger must not have its stale list believed")


def test_the_oracle_fails_closed_on_every_transport_error():
    def boom(url, timeout):
        raise OSError("no route")
    assert W.OwnershipOracle("http://x", opener=boom).claimed() == {}
    assert W.OwnershipOracle(
        "http://x", opener=lambda u, t: b"not json").claimed() == {}
    assert W.OwnershipOracle(
        "http://x", opener=lambda u, t: b"[]").claimed() == {}


def test_the_registry_server_is_get_only(tmp_path):
    import urllib.error
    import urllib.request
    s = EX.InventoryStore(str(tmp_path / "inv.json"))
    s.record_submit("c1", {"market": MARKET, "side": "UP",
                           "qty": "1", "price": "0.6"})
    s.attach_venue_id("c1", OURS)
    httpd = REG.serve(s, port=0)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        o = W.OwnershipOracle(base)
        assert list(o.claimed()) == [OURS], "end to end over HTTP"
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            req = urllib.request.Request(
                "%s/%s" % (base, REG.REGISTRY_KEY), data=b"{}",
                method=method)
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            assert e.value.code in (405, 501)
    finally:
        httpd.shutdown()


def code_of(path):
    """Source with docstrings and comments stripped.

    Third time I have written a source-grep that tripped over a docstring
    explaining why the thing it names is forbidden. The module explains the
    shared-account hazard by naming the route it refuses, as it should.
    """
    import ast
    import io
    import tokenize
    src = open(path).read()
    docs = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            d = ast.get_docstring(node)
            if d:
                docs.add(d)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and any(
                tok.string.strip("rbuf\"'" ) == d or d in tok.string
                for d in docs):
            continue
        out.append(tok.string)
    # joined with spaces, not newlines: a multi-token phrase like
    # "oracle is not None" must still be findable. Found by this file's own
    # both-witnesses test, which could not see the expression it was about.
    return " ".join(out)


def test_the_publisher_holds_nothing_that_can_cancel_or_submit():
    """No venue route, no transport, no write verb.

    The word "cancel" itself is NOT forbidden: the served `labels` string
    tells the reader that a claim does not make a MANUAL order cancellable,
    and that sentence is worth more than the grep. What must be absent is
    anything that could issue a request.
    """
    code = code_of("src/l5_order_registry_server.py")
    for forbidden in ("/v1/order", "l5_pmus_transport", "pmus_auth",
                      "do_POST", "do_PUT", "do_DELETE", ".cancel(",
                      "urlopen", "signed_headers"):
        assert forbidden not in code, forbidden


def test_the_watchdog_requires_both_witnesses_in_code():
    """Neither the registry nor the venue flag alone may authorise a cancel."""
    code = code_of("src/l5_watchdog.py")
    assert "NOT_IN_OWNERSHIP_REGISTRY" in code
    assert "VENUE_SAYS_MANUAL" in code
    assert "MARKET_MISMATCH" in code
    assert "oracle is not None" in code, (
        "cancel must be unavailable without an oracle, not merely defaulted "
        "off")


def test_serving_is_off_unless_a_port_is_configured(monkeypatch, tmp_path):
    s = EX.InventoryStore(str(tmp_path / "inv.json"))
    monkeypatch.delenv(REG.PORT_ENV, raising=False)
    assert REG.serve_from_env(s) is None
    monkeypatch.setenv(REG.PORT_ENV, "")
    assert REG.serve_from_env(s) is None


def test_the_oracle_is_absent_unless_a_url_is_configured(monkeypatch):
    monkeypatch.delenv("L5_ORDER_REGISTRY_URL", raising=False)
    assert W.oracle_from_env() is None
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://wd:8081")
    assert isinstance(W.oracle_from_env(), W.OwnershipOracle)


# ------------------------------------------- the executor's side is off by default

def test_the_executor_does_not_serve_the_registry_unless_told_to():
    """Default behaviour is unchanged: no port, no server, no thread."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.__init__)
    assert "serve_from_env" in src, "the runner goes through the env gate"
    # and that gate is off when the port is unset -- asserted on behaviour,
    # not by grepping the source, which only ever caught my own comments
    import os as _os
    from src import l5_order_registry_server as _REG
    had = _os.environ.pop(_REG.PORT_ENV, None)
    try:
        class _S:
            def live_orders(self):
                return {}
        assert _REG.serve_from_env(_S()) is None
    finally:
        if had is not None:
            _os.environ[_REG.PORT_ENV] = had


def test_a_broken_registry_server_alarms_and_never_stops_the_executor():
    """It is an observability path, not a trading dependency."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.__init__)
    i = src.index("l5_order_registry_server")
    #: WIDENED from 1400. The window was arbitrary and a second alarm was
    #: added between the import and the `except`: a REQUESTED port that
    #: produced no listener. `serve_from_env` swallows its own exceptions
    #: and returns None, so a failed bind used to emit nothing at all --
    #: which is why l5-pmus-live read UNREACHABLE with no log saying why.
    #: The assertions are about PRESENCE, not position.
    tail = src[i:i + 2600]
    assert "except Exception" in tail
    assert "order_registry_unavailable" in tail
    assert "cannot cancel" in tail
    #: both failure modes are now loud, and an UNSET port stays silent
    assert "order_registry_not_listening" in tail
    assert "if _want:" in tail


def test_the_image_carries_the_registry_key_and_no_venue_route():
    """Checked over COPY lines only.

    The Dockerfile's header lists what is DELIBERATELY ABSENT by name --
    l5_pmus_transport, l5_live_execution -- and that list is the safety
    argument. A test that greps the whole file forbids the file from making
    its case, which is the same mistake I have now made four times.
    """
    lines = [ln.strip() for ln in
             open("deploy/l5_halt_watchdog/Dockerfile").read().splitlines()]
    copied = " ".join(ln for ln in lines if ln.startswith("COPY "))
    assert "src/l5_order_registry_server.py" in copied
    for forbidden in ("l5_pmus_transport", "l5_live_execution",
                      "l5_order_body", "pmus_q1_executor"):
        assert forbidden not in copied, forbidden
    body = "\n".join(lines)
    assert "L5_ORDER_REGISTRY_URL" in body, "the contract is documented in it"
