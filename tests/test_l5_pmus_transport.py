"""The transport must refuse by default and fail closed on every gate.

Each test corresponds to a way real money is lost quietly: arming without a
preflight, passing a check we could not actually evaluate, trading the wrong
market or account, or leaking a credential into a report.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_pmus_transport as T
from src import pmus_q1_executor as Q1
from src.l5_live_execution import NotArmed

SLUG = "cpc-btc-updown-15m-2026-09-23-2100z"
NOW = lambda: dt.datetime(2026, 9, 23, 21, 0, tzinfo=dt.timezone.utc)


MARKET = {
    "slug": SLUG, "active": True, "closed": False,
    "status": "MARKET_STATUS_OPEN",
    "marketSides": [{"description": "Up", "long": True},
                    {"description": "Down", "long": False}],
    "outcomes": "[\"Up\",\"Down\"]",
    "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01}


class FakeHTTP:
    """Serves canned venue responses at the LIVE-PROVEN paths; records what
    was asked for."""

    def __init__(self, **over):
        self.calls = []
        self.posts = []
        self.data = {
            "/v1/account/balances": {"balances": [
                {"accountId": "acct-123", "buyingPower": {"value": "20.00"}}]},
            "/v1/market/slug/%s" % SLUG: {"market": dict(MARKET)},
            "/v1/markets/%s/book" % SLUG: {"marketData": {
                "bids": [{"px": {"value": "0.40"}, "qty": "100"}],
                "offers": [{"px": {"value": "0.60"}, "qty": "100"}]}},
            "/v1/portfolio/positions": {"positions": {}},
            "/v1/orders/open": {"orders": []},
            "/v1/order/V1": {"order": {"state": "ORDER_STATE_FILLED",
                                       "cumQuantity": "1",
                                       "avgPx": {"value": "0.60"}}},
        }
        self.data.update(over)

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if method != "GET":
            self.posts.append((path, body))
            if path == "/v1/order/preview":
                return {"order": dict(body["request"])}
            return {"id": "V1"}
        if path not in self.data or self.data[path] is None:
            raise RuntimeError("404 %s" % path)
        return self.data[path]


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")


def owner_arm(monkeypatch, sid="sess-1"):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)


def tp(**kw):
    return T.PmusTransport(market_slug=SLUG, http=FakeHTTP(**kw), now_fn=NOW)


# ------------------------------------------------------------- starts closed

def test_it_starts_disarmed_and_refuses(creds):
    t = tp()
    assert t.armed is False
    with pytest.raises(NotArmed):
        t.submit({"client_order_id": "x", "side": "UP", "price": D("0.5"),
                  "qty": D("1"), "mode": "MAKER"})
    with pytest.raises(NotArmed):
        t.cancel("x")


def test_preflight_alone_does_not_arm(creds):
    t = tp()
    rep = t.preflight()
    assert rep["preflight"] == "PASSED"
    assert rep["armed_after_preflight"] is False
    assert t.armed is False


def test_arming_requires_both_preflight_and_owner_token(creds, monkeypatch):
    t = tp()
    with pytest.raises(NotArmed, match="preflight"):
        t.arm()                                   # no preflight yet
    t.preflight()
    with pytest.raises(NotArmed, match="owner arm"):
        t.arm()                                   # preflight but no owner
    owner_arm(monkeypatch)
    assert t.arm()["armed"] is True
    assert t.armed is True


def test_owner_token_must_match_the_session_id(creds, monkeypatch):
    t = tp()
    t.preflight()
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-1")
    monkeypatch.setenv(Q1.ARM_ENV, "sess-DIFFERENT")
    with pytest.raises(NotArmed):
        t.arm()


# ------------------------------------------------------------ gates fail shut

def _mk(**kw):
    m = dict(MARKET)
    m.update(kw)
    return {"market": m}


@pytest.mark.parametrize("path,bad,check", [
    ("/v1/market/slug/%s" % SLUG, _mk(status="MARKET_STATUS_RESOLVED",
                                      closed=True), "market_open_tradable"),
    ("/v1/market/slug/%s" % SLUG,
     _mk(marketSides=[{"description": "Down", "long": True},
                      {"description": "Up", "long": False}], outcomes=None),
     "outcome_mapping_is_up_down"),
    ("/v1/market/slug/%s" % SLUG,
     _mk(marketSides=[{"description": "Yes", "long": True},
                      {"description": "No", "long": False}], outcomes=None),
     "outcome_mapping_is_up_down"),
    ("/v1/market/slug/%s" % SLUG, _mk(orderPriceMinTickSize=None),
     "tick_constraint_known"),
    ("/v1/market/slug/%s" % SLUG, _mk(minimumTradeQty=None),
     "quantity_constraint_known"),
    ("/v1/market/slug/%s" % SLUG, _mk(slug="cpc-btc-updown-15m-2026-09-23-2045z"),
     "market_resolves"),
    ("/v1/markets/%s/book" % SLUG,
     {"marketData": {"bids": [], "offers": [{"px": {"value": "0.60"}, "qty": "1"}]}},
     "book_two_sided"),
    ("/v1/orders/open", {"orders": [{"id": "stale-1"}]},
     "no_unexpected_open_orders"),
    ("/v1/portfolio/positions", {"positions": {SLUG: {"netPosition": "3"}}},
     "no_position_in_target_market"),
    ("/v1/account/balances", {"balances": [{"buyingPower": {"value": "1.00"}}]},
     "buying_power_covers_worst_case"),
])
def test_each_gate_fails_closed(creds, path, bad, check):
    t = tp(**{path: bad})
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    rep = json.loads(str(e.value))
    failed = [c["check"] for c in rep["checks"] if not c["passed"]]
    assert check in failed
    assert rep["armed_after_preflight"] is False
    assert t.armed is False


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv(Q1.KEY_ENV, raising=False)
    monkeypatch.delenv(Q1.SECRET_ENV, raising=False)
    with pytest.raises(T.PreflightFailed):
        tp().preflight()


def test_an_unreadable_endpoint_is_a_failure_not_a_pass(creds):
    t = T.PmusTransport(market_slug=SLUG,
                        http=FakeHTTP(**{"/v1/account/balances": None}),
                        now_fn=NOW)
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    assert "account_readable" in str(e.value)


def test_the_wrong_series_or_slug_shape_is_refused(creds):
    t = T.PmusTransport(market_slug="cpc-nfl-buf-mia-2026-09-23",
                        http=FakeHTTP(), now_fn=NOW)
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    assert "slug_is_btc15m_series" in str(e.value)


def test_account_mismatch_is_refused(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp().preflight(expect_account="someone-else")
    rep = json.loads(str(e.value))
    assert "account_matches_expected" in [
        c["check"] for c in rep["checks"] if not c["passed"]]


def test_position_drift_blocks_arming(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp().preflight(known_positions={SLUG: "3"})
    assert "positions_reconciled" in str(e.value)


# --------------------------------------------------------------- credentials

def test_no_credential_value_appears_in_the_report(creds):
    rep = tp().preflight()
    blob = json.dumps(rep)
    assert "key-aaaaaaaaaaaa" not in blob
    assert "secret-bbbbbbbbbbbb" not in blob


def test_redact_strips_headers_and_secrets(creds):
    s = T.redact("X-PM-Signature: abcdef123 and key-aaaaaaaaaaaa")
    assert "abcdef123" not in s
    assert "key-aaaaaaaaaaaa" not in s


# ------------------------------------------------------------- worst case

def test_worst_case_comes_from_the_fee_implementation(creds):
    rep = tp().preflight()
    wc = rep["worst_case"]
    # fee is maximised at p=0.5; $5 buys 10 contracts there
    assert wc["fee_maximising_price"] == "0.5"
    assert wc["contracts_at_that_price"] == "10"
    from src import pmus_fees as fees
    assert D(wc["max_taker_fee_usd"]) == fees.taker_fee(D("0.5"), D("10"))
    assert D(wc["worst_case_total_usd"]) == D("5") + D(wc["max_taker_fee_usd"])
    assert wc["tick_from_market"] == "0.01"


def test_only_get_is_issued_during_preflight(creds):
    t = tp()
    t.preflight()
    assert all(m == "GET" for m, _ in t.http.calls)
