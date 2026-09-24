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


class FakeHTTP:
    """Serves canned venue responses; records what was asked for."""

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
        if method != "GET":
            return {"order_id": "v1", "filled_qty": "0"}
        if path not in self.data:
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
    ("/v1/markets/%s/book" % SLUG,
     {"marketData": {"bid": None, "ask": "0.60"}},
     "book_two_sided"),
    ("/v1/orders/open", {"orders": [{"id": "stale-1"}]},
     "no_unexpected_open_orders"),
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
    t = T.PmusTransport(market_slug=SLUG, http=FakeHTTP(**{"/v1/account/balances": None}),
                        now_fn=NOW)
    t.http.data.pop("/v1/account/balances")           # raises on read
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


# --------------------------------------------- account binding, not identity

def test_binding_is_proven_by_account_scoped_funds(creds):
    rep = tp().preflight()
    checks = {c["check"]: c["passed"] for c in rep["checks"]}
    assert checks["account_binding_proven"] is True
    probe = rep["identity_probe"]
    assert probe["buying_power_readable"] is True
    assert probe["buying_power_usd"] == "42.50"
    assert probe["mechanism"] == "account_scoped_financial_state"


def test_unreadable_funds_fail_the_binding_gate(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp(**{"/v1/account/balances": {"balances": []}}).preflight()
    rep = json.loads(str(e.value))
    failed = [c["check"] for c in rep["checks"] if not c["passed"]]
    assert "account_binding_proven" in failed


def test_the_key_fingerprint_never_exposes_the_key(creds):
    rep = tp().preflight()
    fp = rep["identity_probe"]["api_key_fingerprint"]
    assert fp and len(fp) == 12
    assert "key-aaaaaaaaaaaa" not in json.dumps(rep)
    assert fp != "key-aaaaaaaaaaaa"


def test_the_binding_pins_the_same_account_across_runs(creds):
    a = tp().preflight()["identity_probe"]["api_key_fingerprint"]
    b = tp().preflight()["identity_probe"]["api_key_fingerprint"]
    assert a == b          # stable, so a silent account change is visible


def test_the_pin_is_on_the_fingerprint_not_on_the_balance(creds):
    """Buying power moves whenever the account trades or settles; pinning the
    whole binding would break the pin for a reason unrelated to identity."""
    fp = tp().preflight()["identity_probe"]["api_key_fingerprint"]
    rep = tp().preflight(expect_account=fp)          # matches, funds vary
    assert rep["preflight"] == "PASSED"
    rich = tp(**{"/v1/account/balances":
                 {"balances": [{"buyingPower": "999.99"}]}})
    assert rich.preflight(expect_account=fp)["preflight"] == "PASSED"


def test_a_different_account_fingerprint_is_refused(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp().preflight(expect_account="deadbeefcafe")
    assert "account_matches_expected" in str(e.value)


def test_missing_credentials_cannot_produce_a_binding(monkeypatch, creds):
    monkeypatch.delenv(Q1.KEY_ENV)
    t = tp()
    assert t.account_binding({"balances": [{"buyingPower": "1"}]}) is None


def test_position_drift_blocks_arming(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp().preflight(known_positions={"m|UP": "3"})
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


# ------------------------------- disarmed holds no POST-capable object

def test_a_disarmed_transport_cannot_even_construct_a_mutation(creds):
    t = T.PmusTransport(market_slug=SLUG, now_fn=NOW)     # no http injected
    from src import pmus_auth
    assert isinstance(t.http, pmus_auth.ReadOnlyTransport)
    with pytest.raises(PermissionError):
        t.http.request("POST", "/v1/orders", {"qty": 1})


def test_arming_is_what_installs_the_post_capable_transport(creds, monkeypatch):
    t = T.PmusTransport(market_slug=SLUG, http=FakeHTTP(), now_fn=NOW)
    t.preflight()
    owner_arm(monkeypatch)
    t.arm()
    # an injected transport is left alone so tests stay deterministic
    assert isinstance(t.http, FakeHTTP)


def test_the_executor_order_stack_is_not_imported_here():
    import inspect
    src = inspect.getsource(T)
    # the module may NAME it in prose; it must not import it
    assert "import pmus_q1_executor" not in src
    for fn in ("run_campaign", "cancel_all_deadman", "place_limit_buy"):
        assert not hasattr(T, fn)


def test_cancel_uses_the_venue_route_and_the_venue_order_id(creds, monkeypatch):
    t = tp()
    t.preflight()
    owner_arm(monkeypatch)
    t.arm()
    t.cancel("venue-order-7")
    assert ("POST", "/v1/order/venue-order-7/cancel") in t.http.calls


# ------------------------------- metadata comes from the right resource

def test_metadata_is_read_from_the_singular_market_resource(creds):
    t = tp()
    t.preflight()
    paths = [p for m, p in t.http.calls]
    assert "/v1/market/slug/%s" % SLUG in paths
    # /bbo carries QUOTE data and must not be the metadata source
    assert "/v1/markets/%s/bbo" % SLUG not in paths


def test_outcomes_parse_from_a_json_encoded_string(creds):
    mk = {"outcomes": '["Up", "Down"]'}
    assert T.PmusTransport.outcome_names(mk) == ("UP", "DOWN")


def test_outcomes_parse_from_a_plain_list_and_from_mappings(creds):
    assert T.PmusTransport.outcome_names({"outcomes": ["Up", "Down"]}) == \
        ("UP", "DOWN")
    assert T.PmusTransport.outcome_names(
        {"outcomes": [{"outcome": "Up"}, {"outcome": "Down"}]}) == \
        ("UP", "DOWN")


def test_unparseable_outcomes_yield_nothing_rather_than_a_guess(creds):
    assert T.PmusTransport.outcome_names({"outcomes": None}) == ()
    assert T.PmusTransport.outcome_names({}) == ()


def test_tradability_accepts_active_closed_as_well_as_the_state_enum(creds):
    rep = tp().preflight()
    checks = {c["check"]: c["passed"] for c in rep["checks"]}
    assert checks["market_open_tradable"] is True


def test_an_archived_market_is_not_tradable(creds):
    with pytest.raises(T.PreflightFailed) as e:
        tp(**{"/v1/market/slug/%s" % SLUG: {"market": {
            "slug": SLUG, "seriesSlug": "btc-updown-15m",
            "active": True, "closed": False, "archived": True,
            "outcomes": '["Up", "Down"]',
            "orderPriceMinTickSize": "0.01",
            "minimumTradeQty": "1"}}}).preflight()
    assert "market_open_tradable" in str(e.value)


def test_the_minimum_quantity_field_of_this_resource_is_read(creds):
    rep = tp().preflight()
    checks = {c["check"]: c["passed"] for c in rep["checks"]}
    assert checks["quantity_constraint_known"] is True


# ------------------------------------------------- market metadata resolution

class CatalogueHTTP:
    """The gateway as it actually behaved when probed on 2026-09-24.

    Two facts drive every test below. The singular resource 404s for a
    BTC15m window even when that window has traded and settled; and the
    listing silently IGNORES filter parameters it does not know, answering
    200 with the unfiltered default page.
    """

    def __init__(self, rows, *, singular=None):
        self.rows = rows
        self.singular = singular
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if path == "/v1/account/balances":
            return {"balances": [{"buyingPower": "42.50"}]}
        if path.startswith("/v1/market/slug/"):
            if self.singular is None:
                raise RuntimeError("404 Market not found")
            return {"market": self.singular}
        if path.startswith("/v1/markets?"):
            q = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&")
                     if "=" in p)
            # unknown filters are ignored by the venue, so they are ignored
            # here too -- that is the trap being guarded against
            lim, off = int(q.get("limit", 500)), int(q.get("offset", 0))
            return {"markets": self.rows[off:off + lim]}
        raise RuntimeError("404 %s" % path)


def row(slug, **over):
    d = {"slug": slug, "outcomes": '["Up", "Down"]', "active": True,
         "closed": False, "archived": False, "status": "OPEN",
         "orderPriceMinTickSize": "0.01", "minimumTradeQty": "1"}
    d.update(over)
    return d


def tx(http):
    return T.PmusTransport(market_slug=SLUG, http=http, now_fn=NOW)


@pytest.fixture(autouse=True)
def _clear_catalogue():
    """The catalogue cache is process-wide, so one test's pages would
    otherwise answer another test's walk."""
    T._CATALOGUE.clear()
    yield
    T._CATALOGUE.clear()


def test_the_catalogue_answers_when_the_singular_resource_404s():
    """The regression that failed market_readable on 35 consecutive windows:
    /v1/market/slug/{slug} serves sports, not cpc-* crypto windows."""
    mk = tx(CatalogueHTTP([row("aec-nfl-lac-ten-2025-11-02"), row(SLUG)])
            ).market(SLUG)
    assert mk["slug"] == SLUG
    assert mk["orderPriceMinTickSize"] == "0.01"


def test_an_ignored_filter_can_never_pass_off_another_market():
    """The listing returns 200 with an unrelated page when it does not know
    a filter. Trusting that would hand preflight some NFL market's tick and
    outcomes while looking like a clean read."""
    http = CatalogueHTTP([row("aec-nfl-lac-ten-2025-11-02"),
                          row("atc-uecl-ggk-zil-2026-07-30-ggk")])
    with pytest.raises(LookupError):
        tx(http).market(SLUG)


def test_a_singular_hit_for_the_WRONG_slug_is_not_accepted():
    http = CatalogueHTTP([row(SLUG)], singular=row("some-other-market"))
    assert tx(http).market(SLUG)["slug"] == SLUG


def test_the_singular_resource_is_still_used_when_it_does_answer():
    """Sports still resolve in one request; the catalogue is the fallback,
    not a replacement."""
    http = CatalogueHTTP([], singular=row(SLUG, seriesSlug="btc-updown-15m"))
    assert tx(http).market(SLUG)["seriesSlug"] == "btc-updown-15m"
    assert not any("limit=" in p for _, p in http.calls)


def test_a_market_absent_from_the_venue_fails_closed():
    """No metadata must never become invented metadata: preflight has to
    fail rather than proceed on a market the venue is not serving."""
    with pytest.raises(LookupError):
        tx(CatalogueHTTP([])).market(SLUG)


def test_the_catalogue_walk_is_bounded():
    """A preflight must not turn into an unbounded crawl."""
    http = CatalogueHTTP([row("filler-%d" % i) for i in range(100_000)])
    with pytest.raises(LookupError):
        tx(http).market(SLUG)
    pages = [p for _, p in http.calls if "limit=" in p]
    # two hosts are walked (api then gateway), each bounded independently
    assert len(pages) <= T.PmusTransport.CATALOGUE_MAX_PAGES * 2


def test_a_later_page_still_resolves():
    rows = [row("filler-%d" % i) for i in range(700)] + [row(SLUG)]
    assert tx(CatalogueHTTP(rows)).market(SLUG)["slug"] == SLUG


def test_market_readable_fails_closed_when_metadata_is_unavailable(creds):
    """The gate stays failed rather than being skipped or weakened."""
    t = tx(CatalogueHTTP([]))
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    rep = json.loads(str(e.value))
    assert rep["preflight"] == "FAILED"
    assert "market_readable" in [c["check"] for c in rep["checks"]
                                 if not c["passed"]]


# ------------------------------------------- the walk must not stall the loop

def test_repeated_misses_do_not_refetch_the_catalogue():
    """The preflight calling this runs inside the realtime probe's 5.0s poll
    loop, and the full walk measured 6.1s against the live gateway. Uncached,
    13 retries a window would stall observation for ~9% of it and degrade the
    first-book and coverage gates this read path exists to satisfy."""
    T._CATALOGUE.clear()
    http = CatalogueHTTP([row("filler-%d" % i) for i in range(2000)])
    for _ in range(5):
        with pytest.raises(LookupError):
            tx(http).market(SLUG)
    pages = [p for _, p in http.calls if "limit=" in p]
    assert len(pages) <= T.CATALOGUE_MAX_PAGES * 2, (
        "%d page fetches across 5 attempts" % len(pages))


def test_a_market_that_appears_later_is_still_found():
    """Caching must not make a miss permanent: a window published mid-session
    has to resolve on the next preflight attempt."""
    T._CATALOGUE.clear()
    http = CatalogueHTTP([row("filler")])
    with pytest.raises(LookupError):
        tx(http).market(SLUG)
    http.rows = [row("filler"), row(SLUG)]
    T._CATALOGUE.clear()                    # TTL expiry, simulated
    assert tx(http).market(SLUG)["slug"] == SLUG


def test_the_cache_cannot_outlive_a_preflight_retry_cycle():
    """A page must never be older than the retry interval that reads it, or
    a market could be reported absent using evidence from a previous cycle."""
    from scripts import l5_pmus_realtime_probe as P
    assert T.CATALOGUE_TTL_S < P.PREFLIGHT_RETRY_S


def test_a_cached_page_still_verifies_the_slug():
    """Reuse must not relax the identity check."""
    T._CATALOGUE.clear()
    http = CatalogueHTTP([row("aec-nfl-lac-ten-2025-11-02")])
    with pytest.raises(LookupError):
        tx(http).market(SLUG)
    with pytest.raises(LookupError):        # served from cache, same answer
        tx(http).market(SLUG)


# ------------------------------- the authenticated host is tried, and signed

class TwoHostHTTP:
    """Gateway and api are different surfaces. The gateway does not carry
    btc-updown-15m at all; the api answers 401 unless the request is signed,
    which only the credential-holding recorder can do."""

    def __init__(self, api_rows=(), gateway_rows=()):
        self.api_rows, self.gateway_rows = list(api_rows), list(gateway_rows)
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((base, path))
        rows = self.api_rows if base == Q1.API else self.gateway_rows
        if path.startswith("/v1/market/slug/"):
            raise RuntimeError("404 Market not found")
        if path.startswith("/v1/markets?"):
            q = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&")
                     if "=" in p)
            lim, off = int(q.get("limit", 500)), int(q.get("offset", 0))
            return {"markets": rows[off:off + lim]}
        raise RuntimeError("404 %s" % path)


def test_a_market_only_the_api_host_serves_is_still_resolved():
    """The gateway carries 0 of 2,499 markets for this series even while the
    window is live and trading, so the account-scoped host must be tried."""
    T._CATALOGUE.clear()
    http = TwoHostHTTP(api_rows=[row(SLUG)], gateway_rows=[row("nfl-thing")])
    assert T.resolve_market(http, SLUG)["slug"] == SLUG
    assert any(b == Q1.API for b, _ in http.calls)


def test_the_api_host_is_tried_before_the_gateway():
    T._CATALOGUE.clear()
    http = TwoHostHTTP(api_rows=[row(SLUG)], gateway_rows=[row(SLUG)])
    T.resolve_market(http, SLUG)
    assert http.calls[0][0] == Q1.API


def test_an_api_host_that_401s_does_not_prevent_the_gateway_resolving():
    """Sports still resolve from the public gateway when the signed host
    refuses, so adding the api host cannot break what already worked."""
    T._CATALOGUE.clear()

    class Api401(TwoHostHTTP):
        def request(self, method, path, body=None, base=None):
            if base == Q1.API:
                raise RuntimeError("401 Missing required API key headers")
            return super().request(method, path, body, base)

    http = Api401(gateway_rows=[row(SLUG)])
    assert T.resolve_market(http, SLUG)["slug"] == SLUG


def test_neither_host_serving_it_still_fails_closed():
    T._CATALOGUE.clear()
    http = TwoHostHTTP(api_rows=[row("a")], gateway_rows=[row("b")])
    with pytest.raises(LookupError) as e:
        T.resolve_market(http, SLUG)
    assert Q1.API in str(e.value) and Q1.GATEWAY in str(e.value)


def test_the_two_hosts_never_share_a_cached_page():
    """Keying the cache by page alone would let the gateway's listing answer
    a walk of the api host, and the two carry different markets."""
    T._CATALOGUE.clear()
    http = TwoHostHTTP(api_rows=[row("api-only")],
                       gateway_rows=[row(SLUG)])
    assert T.resolve_market(http, SLUG)["slug"] == SLUG
    keys = {k[0] for k in T._CATALOGUE}
    assert keys == {Q1.API, Q1.GATEWAY}


def test_a_crypto_backend_panic_on_one_host_does_not_lose_the_other():
    """Signing imports the Ed25519 backend, and an unloadable one raises a
    pyo3 PanicException -- a BaseException, not an Exception. Caught live in
    this container: it propagated out of resolve_market and would have lost
    the gateway answer that was available all along."""
    T._CATALOGUE.clear()

    class Panic(BaseException):
        pass

    class PanickingApi(TwoHostHTTP):
        def request(self, method, path, body=None, base=None):
            if base == Q1.API:
                raise Panic("Python API call failed")
            return super().request(method, path, body, base)

    http = PanickingApi(gateway_rows=[row(SLUG)])
    assert T.resolve_market(http, SLUG)["slug"] == SLUG


def test_keyboard_interrupt_is_never_swallowed_by_the_host_loop():
    T._CATALOGUE.clear()

    class Interrupting(TwoHostHTTP):
        def request(self, method, path, body=None, base=None):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        T.resolve_market(Interrupting(), SLUG)
