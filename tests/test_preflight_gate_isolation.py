"""Each preflight read, failed on its own, and what it is allowed to stop.

The 2000z window (`nativeq10-20260928-2000z`) ended with `preflight FAILED`,
a null fingerprint and nothing else. Three gates were consistent with that
one word and no evidence on stdout could separate them. These tests fail
each authenticated read individually and assert two things every time: the
right blocking decision, and a printed cause naming the check, the
endpoint/host, the exception class and the HTTP status.

No policy is under test here. theta, LOCK5, clip, completion economics and
the frozen n=25 evidence are untouched by this file and by the change it
covers.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_pmus_transport as T
from src import pmus_q1_executor as Q1

SLUG = "cpc-btc-updown-15m-2026-09-28-2000z"
EVENT = "btc-updown-15m-2026-09-28-2000z"
NOW = lambda: dt.datetime(2026, 9, 28, 20, 0, tzinfo=dt.timezone.utc)

BOOK = "/v1/markets/%s/book" % SLUG
MARKET = "/v1/market/slug/%s" % SLUG
EVENTP = "/v1/events/slug/%s" % EVENT
POSITIONS = "/v1/portfolio/positions"
OPEN = "/v1/orders/open"
BALANCES = "/v1/account/balances"


class HTTPish(Exception):
    """An exception shaped like urllib's HTTPError: it carries `.code`."""

    def __init__(self, code, msg="venue said no"):
        super().__init__("HTTP Error %s: %s" % (code, msg))
        self.code = code


class FailingHTTP:
    """Every read succeeds except the paths named in `fail`."""

    def __init__(self, fail=(), exc=None, open_orders=(), positions=()):
        self.fail = set(fail)
        self.exc = exc or (lambda: HTTPish(503))
        self.calls = []
        self.data = {
            BALANCES: {"balances": [{"buyingPower": "42.50"}]},
            MARKET: {"market": {
                "slug": SLUG, "seriesSlug": "btc-updown-15m",
                "active": True, "closed": False, "archived": False,
                "outcomes": '["Up", "Down"]',
                "orderPriceMinTickSize": "0.01",
                "minimumTradeQty": "1"}},
            BOOK: {"marketData": {"bid": "0.40", "ask": "0.60",
                                  "bid_shares": "100", "ask_shares": "100"}},
            POSITIONS: {"positions": list(positions)},
            OPEN: {"orders": list(open_orders)},
        }

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if path in self.fail:
            raise self.exc()
        if path not in self.data:
            raise RuntimeError("404 %s" % path)
        return self.data[path]


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")


def mk(http, slug=SLUG):
    """A transport with a PRIVATE limiter.

    The 429 test below extends the PROCESS-WIDE cool-off, which is the
    correct production behaviour and poisons every later test in the same
    interpreter. Run after this file, test_l5_pmus_transport failed 27 tests
    with VenueThrottled; alphabetical ordering hid it in the full suite.
    """
    t = T.PmusTransport(market_slug=slug, http=http, now_fn=NOW)
    t.limiter = T.RateLimiter(rps=1e6, burst=1e6)
    return t


def run(http):
    t = mk(http)
    try:
        return t, t.preflight(), None
    except T.PreflightFailed as exc:
        return t, json.loads(str(exc)), exc


def printed(capsys, tag="PMUS_PREFLIGHT_FAIL"):
    rows = []
    for line in capsys.readouterr().out.splitlines():
        if line.startswith(tag + " "):
            rows.append(json.loads(line[len(tag) + 1:]))
    return rows


def check(rep, name):
    for c in rep.get("checks") or []:
        if c.get("check") == name:
            return c
    return None


# ----------------------------------------------------- account: must block

def test_account_failure_blocks(creds, capsys):
    _, rep, exc = run(FailingHTTP(fail=[BALANCES]))
    assert exc is not None
    assert rep["preflight"] == "FAILED"
    assert rep["failing_check"] == "account_readable"
    rows = printed(capsys)
    assert rows and rows[0]["check"] == "account_readable"
    assert rows[0]["http_status"] == 503
    assert "api.polymarket.us/v1/account/balances" in rows[0]["where"]
    assert rows[0]["exception"] == "HTTPish"


def test_missing_credentials_still_block(creds, monkeypatch, capsys):
    monkeypatch.setenv(Q1.KEY_ENV, "")
    monkeypatch.setenv(Q1.SECRET_ENV, "")
    _, rep, exc = run(FailingHTTP())
    assert exc is not None and rep["preflight"] == "FAILED"
    assert check(rep, "credentials_present")["passed"] is False
    assert "credentials_present" in printed(capsys)[0]["failing_checks"]


# --------------------------------------------- market identity: must block

def test_market_identity_failure_blocks_when_never_established(creds, capsys):
    _, rep, exc = run(FailingHTTP(fail=[MARKET, EVENTP]))
    assert exc is not None
    assert rep["failing_check"] == "market_readable"
    row = printed(capsys)[0]
    assert row["check"] == "market_readable"
    # the canonical route, named exactly, on the gateway
    assert row["where"] == ("GET https://gateway.polymarket.us"
                            "/v1/market/slug/%s" % SLUG)
    # MarketRowUnavailable IS a LookupError; the venue served no HTTP answer
    # that carried a status, so None is the honest value rather than zero.
    assert row["exception"] == "MarketRowUnavailable"
    assert row["http_status"] is None


def test_market_metadata_is_not_re_resolved_once_proven(creds, capsys):
    """Immutable metadata, established once, is not a per-attempt gate."""
    http = FailingHTTP()
    t = mk(http)
    assert t.preflight()["preflight"] == "PASSED"
    http.fail = {MARKET, EVENTP}
    rep = t.preflight()                       # second attempt, 60s later
    assert rep["preflight"] == "PASSED"
    deg = printed(capsys, "PMUS_PREFLIGHT_DEGRADED")
    assert deg and deg[-1]["check"] == "market_metadata_refresh"
    c = check(rep, "market_metadata_refresh")
    assert c["blocking"] is False and c["passed"] is False
    # and the constraints still come from the venue's own row
    assert str(t.tick_size) == "0.01"


def test_proven_metadata_is_still_re_read_every_attempt(creds):
    """The stored row is a FALLBACK, never a reason to skip the read.

    Without this the change would be a cache, and a market that closed
    mid-window would keep passing `market_open_tradable` off a stale row.
    """
    http = FailingHTTP()
    t = mk(http)
    t.preflight()
    n = http.calls.count(("GET", MARKET))
    assert n >= 1
    t.preflight()
    t.preflight()
    assert http.calls.count(("GET", MARKET)) == 3 * n


def test_a_closed_market_still_blocks_even_after_metadata_was_proven(creds):
    http = FailingHTTP()
    t = mk(http)
    assert t.preflight()["preflight"] == "PASSED"
    http.data[MARKET]["market"]["closed"] = True
    http.data[MARKET]["market"]["active"] = False
    with pytest.raises(T.PreflightFailed):
        t.preflight()


def test_a_different_slug_never_borrows_proven_metadata(creds):
    http = FailingHTTP()
    t = mk(http)
    t.preflight()
    other = "cpc-btc-updown-15m-2026-09-28-2015z"
    t.market_slug = other
    with pytest.raises(T.PreflightFailed):
        t.preflight()


# ------------------------------------- open orders / positions: must block

def test_unexplained_open_order_blocks(creds, capsys):
    _, rep, exc = run(FailingHTTP(open_orders=[{"orderId": "stray"}]))
    assert exc is not None
    assert check(rep, "no_unexpected_open_orders")["passed"] is False
    assert "no_unexpected_open_orders" in printed(capsys)[0]["failing_checks"]


def test_open_orders_read_failure_blocks(creds, capsys):
    _, rep, exc = run(FailingHTTP(fail=[OPEN]))
    assert exc is not None
    assert rep["failing_check"] == "open_orders_readable"
    assert printed(capsys)[0]["where"].endswith("/v1/orders/open")


def test_position_drift_blocks(creds):
    http = FailingHTTP(positions=[{"market": SLUG, "side": "UP", "qty": "3"}])
    t = mk(http)
    with pytest.raises(T.PreflightFailed):
        t.preflight(known_positions={"%s|UP" % SLUG: "0"})


def test_positions_read_failure_blocks_when_reconciliation_is_asked_for(
        creds, capsys):
    http = FailingHTTP(fail=[POSITIONS])
    t = mk(http)
    with pytest.raises(T.PreflightFailed):
        t.preflight(known_positions={"%s|UP" % SLUG: "0"})
    assert printed(capsys)[0]["check"] == "positions_readable"


def test_positions_is_not_read_when_there_is_nothing_to_reconcile(creds):
    """A read whose answer nothing consumes cannot be allowed to kill a
    window. Previously it was fetched every attempt and discarded."""
    http = FailingHTTP(fail=[POSITIONS])
    t = mk(http)
    assert t.preflight()["preflight"] == "PASSED"
    assert (("GET", POSITIONS) not in http.calls)


# --------------------------------------------- REST book: must NOT block

def test_rest_book_failure_does_not_kill_the_window(creds, capsys):
    _, rep, exc = run(FailingHTTP(fail=[BOOK]))
    assert exc is None
    assert rep["preflight"] == "PASSED"
    c = check(rep, "book_readable")
    assert c["passed"] is False and c["blocking"] is False
    deg = printed(capsys, "PMUS_PREFLIGHT_DEGRADED")
    assert deg[0]["check"] == "book_readable"
    assert deg[0]["http_status"] == 503
    assert "gateway.polymarket.us" in deg[0]["where"]
    assert check(rep, "book_readable_now")["blocking"] is False


def test_rest_book_is_called_by_nothing_in_the_packaged_artifact():
    """The elimination above is only safe because nothing trades on it.

    Scoped to the files `deploy/l5_pmus_lock5_probe/Dockerfile` actually COPYs,
    because that set IS the live capability graph. Inside it, the only
    `.book(` call is preflight's own.
    """
    import pathlib
    import re
    docker = pathlib.Path("deploy/l5_pmus_lock5_probe/Dockerfile").read_text()
    packaged = [pathlib.Path(m.group(1)) for m in
                re.finditer(r"^COPY (\S+\.py) ", docker, re.M)]
    assert packaged, "the artifact's file list must be readable"
    import ast
    hits = []
    for f in packaged:
        src = f.read_text()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "book"):
                hits.append((str(f), node.lineno,
                             ast.get_source_segment(src, node)))
    assert [h[0] for h in hits] == ["src/l5_pmus_transport.py"], hits
    assert hits[0][2] == "self.book(self.market_slug)"


# --------------------------------------------- the cause is always visible

@pytest.mark.parametrize("paths,expect", [
    ([BALANCES], "account_readable"),
    ([MARKET, EVENTP], "market_readable"),
    ([OPEN], "open_orders_readable"),
])
def test_every_blocking_failure_prints_its_cause(creds, capsys, paths, expect):
    _, rep, exc = run(FailingHTTP(fail=paths))
    assert exc is not None
    rows = printed(capsys)
    assert rows, "a FAILED preflight printed nothing"
    assert rows[0]["check"] == expect
    assert set(rows[0]) >= {"check", "where", "exception", "http_status",
                            "detail"}
    assert rep["failing_check"] == expect


def test_no_credential_value_appears_in_the_printed_cause(creds, capsys):
    http = FailingHTTP(fail=[BALANCES],
                       exc=lambda: HTTPish(401, "key-aaaaaaaaaaaa rejected"))
    run(http)
    out = capsys.readouterr().out
    assert "key-aaaaaaaaaaaa" not in out
    assert "[REDACTED]" in out


def test_rate_limiting_is_distinguishable_from_rejection(creds, capsys):
    run(FailingHTTP(fail=[BALANCES], exc=lambda: HTTPish(401)))
    assert printed(capsys)[0]["http_status"] == 401
    run(FailingHTTP(fail=[BALANCES], exc=lambda: TimeoutError("timed out")))
    row = printed(capsys)[0]
    assert row["http_status"] is None and row["exception"] == "TimeoutError"
    # last: a 429 extends the PROCESS-wide cool-off, which is the correct
    # behaviour and would distort any assertion made after it here.
    run(FailingHTTP(fail=[BALANCES], exc=lambda: HTTPish(429)))
    assert printed(capsys)[0]["http_status"] == 429
