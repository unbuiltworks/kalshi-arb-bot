"""The preflight that runs where the CREDENTIALS live must not be able to
trade, and must not be able to pass a check it could not evaluate.

Every test here exists because a specific failure would put real money at
risk on the one service that holds the keys:

  * a read that raises must FAIL its gate, not skip it -- a preflight whose
    unknowns are silently dropped reports PASSED for a venue it never reached;
  * the process must refuse a non-GET structurally, so the guarantee does not
    depend on a disarmed flag being in the right state;
  * a credential must not reach the report, which is printed to a log;
  * the gate names must track the executor's, or the evidence collected here
    stops meaning the same thing as the evidence collected there.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import pmus_q1_executor as Q1
from scripts import pmus_auth_preflight_readonly as P

SLUG = "cpc-btc-updown-15m-2026-09-23-2115z"
NOW = dt.datetime(2026, 9, 23, 21, 20, tzinfo=dt.timezone.utc)


class FakeHTTP:
    def __init__(self, **over):
        self.calls = []
        self.data = {
            "/v1/account/balances": {"balances": [{"buyingPower": "42.50"}]},
            "/v1/market/slug/%s" % SLUG: {"market": {
                "slug": SLUG, "seriesSlug": "btc-updown-15m",
                "active": True, "closed": False, "archived": False,
                "outcomes": '["Up", "Down"]',
                "orderPriceMinTickSize": "0.01",
                "minimumTradeQty": "1"}},
            "/v1/markets/%s/book" % SLUG: {"marketData": {
                "bid": "0.40", "ask": "0.60",
                "bid_shares": "100", "ask_shares": "100"}},
            "/v1/portfolio/positions": {"positions": []},
            "/v1/orders/open": {"orders": []},
        }
        self.data.update(over)

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if path not in self.data:
            raise RuntimeError("404 %s" % path)
        return self.data[path]


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")


def run(**over):
    return P.preflight(SLUG, P.GetOnly(FakeHTTP(**over)), now=NOW)


def failed(rep):
    return rep["blocking_failures"]


# ------------------------------------------------- mutation-incapable by build

def test_a_non_get_is_refused_structurally():
    g = P.GetOnly(FakeHTTP())
    with pytest.raises(P.MutationAttempted):
        g.request("POST", "/v1/orders", {"qty": 1})
    with pytest.raises(P.MutationAttempted):
        g.request("DELETE", "/v1/orders/x")
    assert g.calls == []


def test_the_script_exposes_no_submit_or_cancel_path():
    names = dir(P)
    assert "submit" not in names and "cancel" not in names
    assert not any("order" in n.lower() and n.startswith(("place", "send"))
                   for n in names)


def test_a_body_is_never_forwarded_even_on_a_get():
    inner = FakeHTTP()
    P.GetOnly(inner).request("GET", "/v1/account/balances", {"qty": "99"})
    assert inner.calls == [("GET", "/v1/account/balances")]


def test_only_gets_are_issued_during_a_full_preflight(creds):
    http = P.GetOnly(FakeHTTP())
    P.preflight(SLUG, http, now=NOW)
    assert all(c.startswith("GET ") for c in http.calls)


def test_a_preflight_against_a_mutating_transport_is_not_a_pass(creds):
    rep = P.preflight(SLUG, FakeHTTP(), now=NOW)     # NOT wrapped
    assert rep["preflight"] == "FAILED"
    assert "transport_is_get_only" in failed(rep)


# --------------------------------------------------------- gates fail closed

def test_the_happy_path_passes(creds):
    rep = run()
    assert rep["preflight"] == "PASSED"
    assert failed(rep) == []
    assert rep["armed_after_preflight"] is False


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv(Q1.KEY_ENV, raising=False)
    monkeypatch.delenv(Q1.SECRET_ENV, raising=False)
    assert "credentials_present" in failed(run())


@pytest.mark.parametrize("path,bad,check", [
    ("/v1/market/slug/%s" % SLUG,
     {"market": {"slug": SLUG, "seriesSlug": "btc-updown-15m",
                 "active": False, "closed": True,
                 "outcomes": '["Up", "Down"]',
                 "orderPriceMinTickSize": "0.01", "minimumTradeQty": "1"}},
     "market_open_tradable"),
    ("/v1/market/slug/%s" % SLUG,
     {"market": {"slug": SLUG, "seriesSlug": "btc-updown-15m",
                 "active": True, "closed": False,
                 "outcomes": '["Yes", "No"]',
                 "orderPriceMinTickSize": "0.01", "minimumTradeQty": "1"}},
     "outcome_mapping_is_up_down"),
    ("/v1/market/slug/%s" % SLUG,
     {"market": {"slug": SLUG, "seriesSlug": "btc-updown-15m",
                 "active": True, "closed": False,
                 "outcomes": '["Up", "Down"]',
                 "minimumTradeQty": "1"}},
     "tick_constraint_known"),
    ("/v1/markets/%s/book" % SLUG, {"marketData": {"bid": None, "ask": "0.6"}},
     "book_two_sided"),
    ("/v1/orders/open", {"orders": [{"id": "stale-1"}]},
     "no_unexpected_open_orders"),
    ("/v1/portfolio/positions", {"positions": [{"market": SLUG, "side": "UP"}]},
     "positions_reconciled"),
])
def test_each_gate_fails_closed(creds, path, bad, check):
    rep = run(**{path: bad})
    assert rep["preflight"] == "FAILED"
    assert check in failed(rep)


def test_an_unreachable_endpoint_fails_its_gate_rather_than_skipping_it(creds):
    http = FakeHTTP()
    http.data.pop("/v1/account/balances")                 # raises
    rep = P.preflight(SLUG, P.GetOnly(http), now=NOW)
    assert rep["preflight"] == "FAILED"
    assert "account_readable" in failed(rep)


def test_an_unreachable_book_is_a_failure_not_an_empty_book(creds):
    http = FakeHTTP()
    http.data.pop("/v1/markets/%s/book" % SLUG)
    rep = P.preflight(SLUG, P.GetOnly(http), now=NOW)
    assert "book_readable" in failed(rep)
    assert "book_two_sided" in failed(rep)


def test_the_wrong_series_is_refused(creds):
    rep = P.preflight("cpc-nfl-buf-mia-2026-09-23", P.GetOnly(FakeHTTP()),
                      now=NOW)
    assert "slug_is_btc15m_series" in failed(rep)


def test_the_wrong_account_is_refused(creds):
    rep = P.preflight(SLUG, P.GetOnly(FakeHTTP()),
                      expect_account="deadbeefcafe", now=NOW)
    assert "account_matches_expected" in failed(rep)


def test_binding_is_proven_by_account_scoped_funds(creds):
    rep = run()
    assert "account_binding_proven" not in failed(rep)
    det = [c["detail"] for c in rep["checks"]
           if c["check"] == "account_binding_proven"][0]
    assert "42.50" in str(det)
    assert "key-aaaaaaaaaaaa" not in json.dumps(rep)


def test_owner_arm_is_reported_but_never_blocking(creds):
    rep = run()
    arm = [c for c in rep["checks"] if c["check"] == "owner_arm_token_present"]
    assert arm and arm[0]["blocking"] is False
    assert rep["preflight"] == "PASSED"          # absent arm does not fail it
    assert rep["armed_after_preflight"] is False


# --------------------------------------------------------------- credentials

def test_no_credential_value_appears_in_the_report(creds):
    blob = json.dumps(run(), default=str)
    assert "key-aaaaaaaaaaaa" not in blob
    assert "secret-bbbbbbbbbbbb" not in blob


def test_redact_strips_headers_and_secrets(creds):
    s = P.redact("X-PM-Signature: abcdef123 plus key-aaaaaaaaaaaa")
    assert "abcdef123" not in s and "key-aaaaaaaaaaaa" not in s


def test_a_credential_in_an_error_string_is_redacted(creds):
    http = FakeHTTP()

    def boom(method, path, body=None, base=None):
        raise RuntimeError("401 for key-aaaaaaaaaaaa")

    http.request = boom
    rep = P.preflight(SLUG, P.GetOnly(http), now=NOW)
    assert "key-aaaaaaaaaaaa" not in json.dumps(rep, default=str)


# ----------------------------------------------------------- worst case/slug

def test_worst_case_comes_from_the_fee_implementation(creds):
    from src import pmus_fees as fees
    from decimal import Decimal as D
    wc = run()["worst_case"]
    assert wc["fee_maximising_price"] == "0.5"
    assert wc["contracts_at_that_price"] == "10"
    assert D(wc["max_taker_fee_usd"]) == fees.taker_fee(D("0.5"), D("10"))
    assert D(wc["worst_case_total_usd"]) == D("5") + D(wc["max_taker_fee_usd"])
    assert wc["tick_from_market"] == "0.01"


def test_the_market_slug_tracks_the_clock_not_a_hardcoded_window():
    at = dt.datetime(2026, 9, 23, 21, 22, 31, tzinfo=dt.timezone.utc)
    assert P.current_window_slug(at) == SLUG
    nxt = dt.datetime(2026, 9, 23, 21, 30, 0, tzinfo=dt.timezone.utc)
    assert P.current_window_slug(nxt).endswith("-2130z")


# ------------------------------------------------------------- drift guard

def test_the_gate_names_still_track_the_executor_transport(creds):
    """Evidence gathered here must mean what it means in the executor."""
    from src import l5_pmus_transport as T

    t = T.PmusTransport(market_slug=SLUG, http=FakeHTTP(),
                        now_fn=lambda: NOW)
    theirs = {c["check"] for c in t.preflight()["checks"]
              if c.get("blocking", True)}
    mine = {c["check"] for c in run()["checks"] if c.get("blocking", True)}
    missing = theirs - mine
    assert not missing, "executor gates not covered by the preflight: %s" % (
        sorted(missing),)


# ------------------------------------------------- always emits a verdict

def test_a_non_exception_failure_still_produces_a_refusal(creds):
    """A missing crypto backend raises a pyo3 PanicException, which is not an
    Exception. It must become a failed gate, not a traceback."""
    class Panic(BaseException):
        pass

    http = FakeHTTP()

    def boom(method, path, body=None, base=None):
        raise Panic("backend unavailable")

    http.request = boom
    rep = P.preflight(SLUG, P.GetOnly(http), now=NOW)
    assert rep["preflight"] == "FAILED"
    assert "account_readable" in failed(rep)


def test_keyboard_interrupt_is_never_swallowed(creds):
    http = FakeHTTP()

    def boom(method, path, body=None, base=None):
        raise KeyboardInterrupt

    http.request = boom
    with pytest.raises(KeyboardInterrupt):
        P.preflight(SLUG, P.GetOnly(http), now=NOW)


def test_main_always_prints_a_verdict_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(P.Q1, "ReadOnlyTransport",
                        lambda: (_ for _ in ()).throw(
                            BaseException("no backend")))
    rc = P.main([SLUG])
    out = capsys.readouterr().out
    assert rc != 0
    assert out.startswith("PMUS_PREFLIGHT ")
    rep = json.loads(out.split(" ", 1)[1])
    assert rep["preflight"] == "FAILED"
    assert rep["armed_after_preflight"] is False


# ------------------------------- the credential-side read resolves BTC15m too

class CatalogueHTTP(FakeHTTP):
    """The gateway for a BTC15m window: the singular resource 404s and the
    catalogue listing is what carries the metadata."""

    def __init__(self, **over):
        super().__init__(**over)
        row = self.data.pop("/v1/market/slug/%s" % SLUG)["market"]
        self.data["/v1/markets?limit=500&offset=0"] = {
            "markets": [{"slug": "aec-nfl-lac-ten-2025-11-02"}, row]}


def test_the_credential_side_preflight_resolves_a_btc15m_market(creds):
    """It runs where the keys are, so it must not be the one still reading
    the resource that 404s for this series."""
    rep = P.preflight(SLUG, P.GetOnly(CatalogueHTTP()), now=NOW)
    assert rep["preflight"] == "PASSED"
    assert failed(rep) == []


def test_an_unserved_market_still_fails_market_readable_here(creds):
    http = FakeHTTP()
    http.data.pop("/v1/market/slug/%s" % SLUG)          # and no catalogue
    rep = P.preflight(SLUG, P.GetOnly(http), now=NOW)
    assert rep["preflight"] == "FAILED"
    assert "market_readable" in failed(rep)
