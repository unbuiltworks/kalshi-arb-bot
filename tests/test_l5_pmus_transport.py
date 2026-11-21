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
    # CANCEL IS NO LONGER REFUSED FOR WANT OF ARMING. That was the live
    # defect of 2026-10-14: three orphan sweeps could not even ask the venue
    # to withdraw a real resting order, each failing NotArmed, because
    # cancel shared submit's guard. A fresh transport still refuses -- it
    # cannot prove the order is ours without the ledger's oracle -- but the
    # refusal is CancelRefused, about OWNERSHIP, not about arming.
    with pytest.raises(T.CancelRefused):
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
     None),          # Yes/No is this venue's vocabulary, not a failure
    ("/v1/market/slug/%s" % SLUG,
     {"market": {"slug": SLUG, "seriesSlug": "btc-updown-15m",
                 "active": True, "closed": False,
                 "outcomes": '["Up", "Down"]',
                 "minimumTradeQty": "1"}},
     "tick_constraint_known"),
    ("/v1/markets/%s/book" % SLUG,
     {"marketData": {"bid": None, "ask": "0.60"}},
     None),          # one-sided at preflight no longer condemns a later order
    ("/v1/orders/open", {"orders": [{"id": "stale-1"}]},
     "no_unexpected_open_orders"),
])
def test_each_gate_fails_closed(creds, path, bad, check):
    t = tp(**{path: bad})
    if check is None:
        # a DOCUMENTED non-failure: this input used to block and no longer
        # should. Asserted rather than deleted so the change is visible.
        rep = t.preflight()
        failed = [c["check"] for c in rep["checks"] if not c["passed"]
                  and c.get("blocking", True)]
        assert "outcome_mapping_is_up_down" not in failed
        assert "book_two_sided" not in failed
        assert t.armed is False
        return
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
    # CORRECTED. This asserted p=0.5 on the reasoning that Theta*C*p*(1-p)
    # peaks there. It does -- PER CONTRACT. Under a fixed PRINCIPAL the
    # contract count is cap/p, so fee = Theta*cap*(1-p), which is monotone
    # DECREASING in p and therefore maximised at the CHEAPEST admissible
    # price. The old expectation understated the bound by 90% ($0.1738
    # against $0.3301 on $5), which is the wrong direction for a figure a
    # campaign is activated on.
    #
    # The test's own intent -- that the fee comes from the fee
    # IMPLEMENTATION and not from a literal -- is unchanged and still
    # asserted below.
    from src import pmus_fees as fees
    from src import l5_pmus_transport as _T
    floor = D(str(_T.FEE_FLOOR_PRICE))
    assert wc["fee_maximising_price"] == "0.05"
    assert D(wc["contracts_at_that_price"]) == D("5") / floor
    assert D(wc["max_taker_fee_usd"]) == fees.taker_fee(
        floor, D("5") / floor)
    assert D(wc["worst_case_total_usd"]) == D("5") + D(wc["max_taker_fee_usd"])
    # no price above the floor can beat it
    for p in ("0.1", "0.25", "0.5", "0.9"):
        assert fees.taker_fee(D(p), D("5") / D(p)) <= D(wc["max_taker_fee_usd"])
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

class VenueHTTP:
    """The venue as probed on 2026-09-24, with 1245z live.

    The public gateway hides this series: /v1/market/slug/ and /v1/market/id/
    both answer "not found" for a BTC15m window that is trading, while
    /bbo and /book serve it. The event resource is what carries the metadata,
    under the slug WITHOUT the cpc- prefix.
    """

    def __init__(self, *, events=None, singular=None, api_events=None,
                 api_raises=None):
        self.events = events or {}
        self.api_events = api_events or {}
        self.singular = singular
        self.api_raises = api_raises
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((base, path))
        if base == Q1.API and self.api_raises:
            raise self.api_raises("api host refused")
        table = self.api_events if base == Q1.API else self.events
        if path.startswith("/v1/market/slug/"):
            if self.singular is None or base == Q1.API:
                raise RuntimeError("404 Market not found")
            return {"market": self.singular}
        if path.startswith("/v1/events/slug/"):
            ev = table.get(path.rsplit("/", 1)[-1])
            if ev is None:
                raise RuntimeError("404 Event not found")
            return {"event": ev}
        raise RuntimeError("404 %s" % path)


def row(slug, **over):
    d = {"slug": slug, "outcomes": '["Up", "Down"]', "active": True,
         "closed": False, "archived": False, "status": "OPEN",
         "orderPriceMinTickSize": "0.01", "minimumTradeQty": "0.01"}
    d.update(over)
    return d


def event(*markets, slug=None):
    return {"slug": slug or T.event_slug_for(markets[0]["slug"]),
            "seriesSlug": "btc-updown-15m", "markets": list(markets)}


def tx(http):
    return T.PmusTransport(market_slug=SLUG, http=http, now_fn=NOW)


# ------------------------------------------------------- the derived slug

def test_the_event_slug_drops_the_product_prefix():
    """market cpc-btc-updown-15m-...-1245z lives under event
    btc-updown-15m-...-1245z, so the event needs no id lookup."""
    assert T.event_slug_for("cpc-btc-updown-15m-2026-09-24-1245z") == \
        "btc-updown-15m-2026-09-24-1245z"


def test_a_slug_without_the_prefix_is_left_alone():
    assert T.event_slug_for("aec-nfl-lac-ten-2025-11-02") == \
        "aec-nfl-lac-ten-2025-11-02"


# ------------------------------------------------------------- resolution

def test_the_event_resource_supplies_what_the_gates_read():
    mk = tx(VenueHTTP(events={T.event_slug_for(SLUG): event(row(SLUG))})
            ).market(SLUG)
    assert mk["slug"] == SLUG
    assert mk["orderPriceMinTickSize"] == "0.01"
    assert mk["minimumTradeQty"] == "0.01"
    assert "Up" in mk["outcomes"] and "Down" in mk["outcomes"]


def test_the_right_market_is_picked_out_of_a_multi_market_event():
    """An event carries several markets; taking the first would hand
    preflight another market's tick and outcomes."""
    ev = event(row("cpc-other-market"), row(SLUG, orderPriceMinTickSize="0.05"),
               slug=T.event_slug_for(SLUG))
    mk = tx(VenueHTTP(events={T.event_slug_for(SLUG): ev})).market(SLUG)
    assert mk["slug"] == SLUG and mk["orderPriceMinTickSize"] == "0.05"


def test_an_event_that_does_not_contain_our_market_fails_closed():
    ev = event(row("cpc-some-other-window"), slug=T.event_slug_for(SLUG))
    with pytest.raises(LookupError):
        tx(VenueHTTP(events={T.event_slug_for(SLUG): ev})).market(SLUG)


def test_the_singular_resource_still_serves_sports_first():
    """Sports resolve in one request; the event route is the fallback."""
    http = VenueHTTP(singular=row(SLUG, seriesSlug="btc-updown-15m"))
    assert tx(http).market(SLUG)["seriesSlug"] == "btc-updown-15m"
    # the host that answered did so from the singular resource; it never had
    # to fall through to the event route
    assert not any(b == Q1.GATEWAY and "/v1/events/" in p
                   for b, p in http.calls)


def test_the_authenticated_host_is_tried_before_the_gateway():
    http = VenueHTTP(api_events={T.event_slug_for(SLUG): event(row(SLUG))})
    assert tx(http).market(SLUG)["slug"] == SLUG
    assert http.calls[0][0] == Q1.API


def test_an_api_host_that_refuses_does_not_prevent_the_gateway_resolving():
    http = VenueHTTP(events={T.event_slug_for(SLUG): event(row(SLUG))},
                     api_raises=RuntimeError)
    assert tx(http).market(SLUG)["slug"] == SLUG


def test_a_crypto_backend_panic_on_one_host_does_not_lose_the_other():
    """Signing imports the Ed25519 backend, and an unloadable one raises a
    pyo3 PanicException -- a BaseException, not an Exception. Caught live in
    this container: it propagated and lost the gateway answer."""
    class Panic(BaseException):
        pass

    http = VenueHTTP(events={T.event_slug_for(SLUG): event(row(SLUG))},
                     api_raises=Panic)
    assert tx(http).market(SLUG)["slug"] == SLUG


def test_keyboard_interrupt_is_never_swallowed():
    http = VenueHTTP(api_raises=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        tx(http).market(SLUG)


def test_neither_host_serving_it_fails_closed_naming_both():
    with pytest.raises(LookupError) as e:
        tx(VenueHTTP()).market(SLUG)
    assert Q1.API in str(e.value) and Q1.GATEWAY in str(e.value)
    assert T.event_slug_for(SLUG) in str(e.value)


def test_the_catalogue_listing_is_never_walked():
    """Paged properly it runs past 40,000 rows ordered by ascending id, so a
    bounded walk can never reach a continuously created 15m window -- and an
    earlier version concluded the series was absent after seeing the oldest
    4,000. It must not come back."""
    http = VenueHTTP(events={T.event_slug_for(SLUG): event(row(SLUG))})
    tx(http).market(SLUG)
    assert not any("/v1/markets?" in p for _, p in http.calls)


def test_market_readable_fails_closed_when_no_host_serves_it(creds):
    class WithBalances(VenueHTTP):
        def request(self, method, path, body=None, base=None):
            if path == "/v1/account/balances":
                return {"balances": [{"buyingPower": "42.50"}]}
            return super().request(method, path, body, base)

    with pytest.raises(T.PreflightFailed) as e:
        tx(WithBalances()).preflight()
    rep = json.loads(str(e.value))
    assert "market_readable" in [c["check"] for c in rep["checks"]
                                 if not c["passed"]]


class TestPreflightManagedLaneExemption:
    """`no_unexpected_open_orders` on a SHARED account.

    WHAT HAPPENED, 2026-11-14. The sports two-sided lane held 8 resting
    completions by owner direction. This gate required len(open_orders)==0
    account-wide, counted them as unexpected, and refused to arm: six
    attempts from t=3.6s to t=75.3s, BTC5m placed nothing for a whole
    window, $0 at risk. The gate was right and its premise was stale.

    ONE DEFINITION, THREE GUARDS. The same L5_WATCHDOG_MANAGED_LANE_URLS
    contract exempts these ids from the watchdog's age latch and from the
    reset ceremony's venue precondition. Freshness and document shape are
    tested where they are implemented (test_l5_watchdog_managed_lanes);
    these tests pin that THIS gate uses the result and stays strict about
    everything else.
    """

    def _oo(self, *ids):
        return {"/v1/orders/open": {"orders": [{"id": i} for i in ids]}}

    def _failed(self, t):
        with pytest.raises(T.PreflightFailed) as e:
            t.preflight()
        return [c["check"] for c in json.loads(str(e.value))["checks"]
                if not c["passed"]]

    def test_unset_means_unchanged_so_a_stray_order_still_refuses(
            self, creds, monkeypatch):
        monkeypatch.delenv("L5_WATCHDOG_MANAGED_LANE_URLS", raising=False)
        t = tp(**self._oo("stray-1"))
        assert "no_unexpected_open_orders" in self._failed(t)

    def test_an_order_a_fresh_lane_names_does_not_block_arming(
            self, creds, monkeypatch):
        monkeypatch.setattr(
            T.PmusTransport, "_managed_lane_ids",
            lambda self: ({"sports-1", "sports-2"}, ["lane:OK:2"]))
        t = tp(**self._oo("sports-1", "sports-2"))
        rep = t.preflight()
        g = [c for c in rep["checks"]
             if c["check"] == "no_unexpected_open_orders"][0]
        assert g["passed"] is True
        # `gate()` passes detail through redact(), which renders it, so the
        # observable artifact is the rendered string -- assert on what is
        # actually logged rather than on a dict the log never carries.
        d = str(g["detail"])
        assert "'venue_open': 2" in d
        assert "'managed_lane_exempt': 2" in d
        assert "'unexpected': 0" in d
        assert "lane:OK:2" in d

    def test_an_order_no_lane_names_still_blocks(self, creds, monkeypatch):
        """THE PROPERTY THAT MATTERS. This narrows 'unexpected' to
        'unaccounted for'; it does not stop counting."""
        monkeypatch.setattr(
            T.PmusTransport, "_managed_lane_ids",
            lambda self: ({"sports-1"}, ["lane:OK:1"]))
        t = tp(**self._oo("sports-1", "who-is-this"))
        failed = self._failed(t)
        assert "no_unexpected_open_orders" in failed

    def test_a_dead_lane_exempts_nothing(self, creds, monkeypatch):
        """Fail closed: unreachable/stale/malformed contributes no ids, so
        the gate is exactly as strict as before this existed."""
        monkeypatch.setattr(
            T.PmusTransport, "_managed_lane_ids",
            lambda self: (set(), ["lane:UNREACHABLE:URLError"]))
        t = tp(**self._oo("sports-1"))
        assert "no_unexpected_open_orders" in self._failed(t)

    def test_the_helper_never_raises_and_degrades_to_strict(
            self, monkeypatch):
        """A preflight that crashes is a preflight that cannot refuse."""
        #: PATCHES l5_managed_lanes, NOT l5_watchdog. This test originally
        #: patched the watchdog and kept passing against the refactor that
        #: moved the contract out of it -- it was silently deselected by a
        #: `-k` filter, so the staleness only surfaced in a full run. The
        #: thing under test is that a raising dependency degrades to STRICT
        #: rather than propagating out of preflight.
        monkeypatch.setenv("L5_WATCHDOG_MANAGED_LANE_URLS",
                           "http://lane.invalid:8090")
        import src.l5_managed_lanes as ML
        monkeypatch.setattr(ML, "lane_ids",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("boom")))
        ids, notes = tp()._managed_lane_ids()
        assert ids == set()
        assert notes and notes[0].startswith("MANAGED_LANES_UNAVAILABLE")

    def test_an_unreachable_lane_url_also_degrades_to_strict(
            self, monkeypatch):
        """The REAL path, no patching: an invalid host must fail closed
        inside lane_ids rather than raise, so preflight stays strict."""
        monkeypatch.setenv("L5_WATCHDOG_MANAGED_LANE_URLS",
                           "http://lane.invalid:8090")
        ids, notes = tp()._managed_lane_ids()
        assert ids == set()
        assert notes and "UNREACHABLE" in notes[0]

    def test_no_urls_configured_short_circuits_without_a_fetch(
            self, monkeypatch):
        monkeypatch.delenv("L5_WATCHDOG_MANAGED_LANE_URLS", raising=False)
        assert tp()._managed_lane_ids() == (set(), [])
