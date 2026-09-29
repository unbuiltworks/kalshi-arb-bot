"""The seam every other suite mocked: guard -> transport -> HTTP.

The 2345z window on 2026-09-28 pinned its metadata before the window, passed
preflight, armed, and produced one 10-contract native intent -- then died
with `AttributeError: 'PmusTransport' object has no attribute 'request'`,
having submitted nothing. `pmus_native_money_path._post` called
`transport.request("POST", "/v1/orders", body)`; `GuardedTransport` admitted
the order against the mandate and then forwarded to `self._inner.request`,
which `PmusTransport` does not define. Its submit entry points are
`submit(intent)` and now `submit_body(body)`.

18 money-path integration tests and 54 wire-level tests were green because
every one of them injected a double that DID define `request`. They mocked
the boundary instead of crossing it.

So this file composes the REAL objects -- `MANDATE.guard(PmusTransport(...))`
-- and fakes only the HTTP layer. Nothing here changes policy: theta,
variant B, hold, residual cap, LOCK5, floor guard, complement bias, taker
completion behaviour, the q10 clip and the risk limits are untouched, and
the frozen parity is re-asserted at the end.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_order_body as OB
from src import l5_pmus_transport as T
from src import l5_preview_gate as PG
from src import pmus_auth as Q1
from src import pmus_l5_native_engine as E
from src import pmus_live_mandate as MANDATE
from src import pmus_native_money_path as NMP

SLUG = "cpc-btc-updown-15m-2026-09-28-2345z"
ORDERS = "/v1/orders"
PREVIEW = "/v1/order/preview"


class FakeHTTP:
    """ONLY the wire is fake. Records every call in order."""

    def __init__(self, *, echo=None, order_id="v-1"):
        self.calls = []
        self.echo = echo                    # None -> echo the body faithfully
        self.order_id = order_id

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path, None if body is None else dict(body)))
        if method == "POST" and path == PREVIEW:
            sent = dict((body or {}).get("request") or {})
            order = dict(sent)
            order["cumQuantity"] = "0"
            if self.echo is not None:
                order.update(self.echo)
            return {"order": order}
        if method == "POST" and path == ORDERS:
            return {"id": self.order_id, "filled_qty": "0"}
        raise AssertionError(("unexpected wire call", method, path))

    def posts_to(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]


def mandate(auth_id="OWNER-TEST-COMPOSITION", spend="81.00"):
    m, err = MANDATE.parse_mandate({
        "authorization_id": auth_id,
        "authorized_at": "2026-09-28T23:38:34Z",
        "service": "l5-live-probe-oneshot",
        "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
        "strategy_version": "test-composition",
        "max_markets": 1, "max_total_markets": 1,
        "max_spend_per_market": spend, "max_total_spend": spend,
        "max_residual": "20.00", "max_daily_realized_loss": "20.00",
        "expiry": "2099-01-01T00:00:00Z"})
    assert err is None, err
    return m


@pytest.fixture()
def armed(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "sess-composition")
    monkeypatch.setenv(Q1.ARM_ENV, "sess-composition")


def real_stack(tmp_path, http, *, auth_id="OWNER-TEST-COMPOSITION",
               spend="81.00"):
    """The production composition: guard(PmusTransport), real both."""
    tp = T.PmusTransport(market_slug=SLUG, http=http)
    tp.limiter = T.RateLimiter(rps=1e6, burst=1e6)
    tp.preflight_report = {"preflight": "PASSED"}     # preflight is not under test
    tp.arm()
    guarded = MANDATE.guard(tp, mandate(auth_id, spend), tmp_path)
    return tp, guarded


def rest_intent(clip=D(10), price="0.40"):
    return E.Intent(E.REST, side="up", price=D(price), qty=clip, mode="MAKER")


# ------------------------------------------------- the whole chain, for real

def test_an_admitted_q10_intent_reaches_the_wire_through_the_real_stack(
        armed, tmp_path):
    http = FakeHTTP()
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    rec = mp._post(rest_intent())

    # _post returns its own record; the venue ACK is carried inside it
    assert rec["ack"] == {"id": "v-1", "filled_qty": "0"}
    assert rec["oid"] == "v-1"
    assert rec["body"]["marketSlug"] == SLUG
    # preview first, then exactly one order POST, in that order
    assert [(m, p) for m, p, _ in http.calls] == [
        ("POST", PREVIEW), ("POST", ORDERS)]
    assert len(http.posts_to(ORDERS)) == 1
    # and the mandate admitted it BEFORE the wire saw anything
    assert len(guarded.admissions) == 1
    assert guarded.admissions[0]["admits"] is True
    assert guarded.refusals == []


def test_the_guard_admits_before_the_transport_is_reached(armed, tmp_path):
    """Order of operations, not just presence: a refusal must leave the wire
    untouched."""
    http = FakeHTTP()
    tp, guarded = real_stack(tmp_path, http, spend="1.00")   # 10 x 0.40 = $4
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    with pytest.raises(MANDATE.MandateRefusal):
        mp._post(rest_intent())
    assert http.calls == []
    assert len(guarded.refusals) == 1 and guarded.admissions == []


def test_exactly_one_mutation_reaches_v1_orders(armed, tmp_path):
    http = FakeHTTP()
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    mp._post(rest_intent())
    assert len(http.posts_to(ORDERS)) == 1
    assert len(http.posts_to(PREVIEW)) == 1
    # the previewed body and the posted body are the SAME body
    previewed = http.posts_to(PREVIEW)[0][2]["request"]
    posted = http.posts_to(ORDERS)[0][2]
    assert previewed == posted


def test_a_preview_echo_mismatch_refuses_before_the_order_post(armed,
                                                               tmp_path):
    """The venue was observed silently changing an off-tick price rather than
    rejecting it. A different price back means no order at all."""
    http = FakeHTTP(echo={"price": {"value": "0.41", "currency": "USD"}})
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    with pytest.raises(PG.PreviewRefused):
        mp._post(rest_intent())
    assert http.posts_to(ORDERS) == []
    assert len(http.posts_to(PREVIEW)) == 1


def test_a_venue_that_would_reject_never_gets_the_order(armed, tmp_path):
    http = FakeHTTP(echo={"state": "ORDER_STATE_REJECTED"})
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    with pytest.raises(PG.PreviewRefused):
        mp._post(rest_intent())
    assert http.posts_to(ORDERS) == []


def test_a_taker_completion_keeps_its_ioc_through_the_certified_submit(
        armed, tmp_path):
    """`OB.from_intent` always leaves tif at GTC, which is why the native path
    builds its own body. Routing through `submit_body` must not quietly turn
    a taker leg into a resting order."""
    http = FakeHTTP()
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    intent = E.Intent(E.COMPLETE, side="down", price=D("0.55"), qty=D(10),
                      mode="TAKER", limit_price=D("0.56"))
    mp._post(intent)
    posted = http.posts_to(ORDERS)[0][2]
    assert posted["tif"] == OB.TIF_IOC
    assert posted["participateDontInitiate"] is False


def test_the_guard_is_not_bypassed_by_the_submit_entry_points(armed, tmp_path):
    """__getattr__ forwards everything unknown straight to the inner
    transport, so `submit` and `submit_body` had to be named explicitly or
    the mandate would not have seen them at all."""
    http = FakeHTTP()
    tp, guarded = real_stack(tmp_path, http, spend="1.00")
    body = OB.build(slug=SLUG, side="UP", price=D("0.40"), qty=D(10),
                    post_only=True)
    with pytest.raises(MANDATE.MandateRefusal):
        guarded.submit_body(body)
    with pytest.raises(MANDATE.MandateRefusal):
        guarded.submit({"side": "UP", "price": D("0.40"), "qty": D(10),
                        "mode": "MAKER", "market": SLUG})
    assert http.calls == []


def test_the_commitment_is_linked_to_the_order_the_venue_created(armed,
                                                                tmp_path):
    http = FakeHTTP(order_id="v-linked")
    tp, guarded = real_stack(tmp_path, http)
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    mp._post(rest_intent())
    rows = [json.loads(l) for l in
            (tmp_path / "ledger.jsonl").read_text().splitlines() if l.strip()]
    commits = [r for r in rows if r["kind"] == MANDATE.COMMIT_KIND]
    links = [r for r in rows if r["kind"] == MANDATE.LINK_KIND]
    assert len(commits) == 1 and len(links) == 1
    assert links[0]["order_id"] == "v-linked"
    assert links[0]["commit_id"] == commits[0]["commit_id"]


# ------------------------------------------- the 2345z orphan, reproduced

def test_a_submit_that_dies_after_admission_leaves_an_ORPHAN_commitment(
        armed, tmp_path):
    """Exactly what the 2345z window left behind: admitted, never linked.

    The guard writes the commitment before the wire and the link after, so a
    failure between them charges the full notional forever. That is the safe
    direction and is deliberate -- but it means the row exists and must be
    accounted for.
    """
    class Dies(FakeHTTP):
        def request(self, method, path, body=None, base=None):
            if method == "POST" and path == ORDERS:
                raise AttributeError(
                    "'PmusTransport' object has no attribute 'request'")
            return super().request(method, path, body, base)

    http = Dies()
    tp, guarded = real_stack(tmp_path, http, auth_id="OWNER-R3-ORPHAN")
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    with pytest.raises(AttributeError):
        mp._post(rest_intent())

    rows = [json.loads(l) for l in
            (tmp_path / "ledger.jsonl").read_text().splitlines() if l.strip()]
    commits = [r for r in rows if r["kind"] == MANDATE.COMMIT_KIND]
    links = [r for r in rows if r["kind"] == MANDATE.LINK_KIND]
    assert len(commits) == 1, "the admission IS persisted"
    assert links == [], "and nothing linked it to an order"

    # it is charged in full against ITS OWN authorization
    own = MANDATE.consumption(tmp_path, "OWNER-R3-ORPHAN")
    assert D(str(own["total"])) == D("4.00")          # 10 x 0.40

    # AND NOT against a different one. consumption() filters COMMIT rows by
    # authorization_id, so a fresh authorization starts at zero over the SAME
    # ledger file -- which is what /state/pmus-ws/ledger.jsonl is.
    fresh = MANDATE.consumption(tmp_path, "OWNER-2026-09-29-NATIVEQ10-R4")
    assert D(str(fresh["total"])) == D("0")
    assert not fresh.get("per_market")


def test_a_fresh_authorization_can_still_spend_its_whole_envelope(armed,
                                                                 tmp_path):
    """The orphan must not shrink the next run's headroom in practice, not
    only in the consumption arithmetic."""
    class Dies(FakeHTTP):
        def request(self, method, path, body=None, base=None):
            if method == "POST" and path == ORDERS:
                raise AttributeError("boom")
            return super().request(method, path, body, base)

    tp, guarded = real_stack(tmp_path, Dies(), auth_id="OWNER-R3-ORPHAN")
    mp = NMP.build(market=SLUG, transport=guarded, feed=None, venue=None,
                   journal=None)
    with pytest.raises(AttributeError):
        mp._post(rest_intent())

    http2 = FakeHTTP()
    tp2, guarded2 = real_stack(tmp_path, http2,
                              auth_id="OWNER-2026-09-29-NATIVEQ10-R4")
    mp2 = NMP.build(market=SLUG, transport=guarded2, feed=None, venue=None,
                    journal=None)
    mp2._post(rest_intent())
    assert len(http2.posts_to(ORDERS)) == 1
    assert guarded2.admissions[0]["admits"] is True


# --------------------------------------------------------- nothing moved

def test_the_serializer_is_unchanged():
    body = OB.build(slug=SLUG, side="UP", price="0.40", qty="10",
                    post_only=True)
    assert body["marketSlug"] == SLUG
    assert body["price"] == {"value": "0.40", "currency": "USD"}
    assert body["tif"] == OB.TIF_GTC
    assert body["participateDontInitiate"] is True
    assert body["type"] == "ORDER_TYPE_LIMIT"
    assert body["action"] == "ORDER_ACTION_BUY"
    assert OB.STATIC_CONTRACT["yes_is"] == "UP"
    assert OB.PREVIEW_ONLY_FIELDS == ()


def test_submit_and_submit_body_are_one_implementation(armed, tmp_path):
    """`submit(intent)` must be `submit_body(from_intent(...))`, not a second
    POST path."""
    http_a = FakeHTTP()
    tp_a, _ = real_stack(tmp_path / "a", http_a)
    tp_a.submit({"side": "UP", "price": D("0.40"), "qty": D(10),
                 "mode": "MAKER", "market": SLUG})
    http_b = FakeHTTP()
    tp_b, _ = real_stack(tmp_path / "b", http_b)
    tp_b.submit_body(OB.build(slug=SLUG, side="UP", price=D("0.40"),
                              qty=D(10), post_only=True))
    assert http_a.calls == http_b.calls


def test_frozen_q50_parity_is_untouched():
    from scripts.audit_economic_complete import load, USABLE
    from src import pmus_native_parity as P
    assert (str(E.THETA), E.VARIANT, E.HOLD_S, str(E.CLIP),
            str(E.RESIDUAL_CAP_K), str(E.PROFIT_LOCK_USD),
            E.FLOOR_GUARD, E.COMPLEMENT_BIAS_C) == \
        ("0.96", "B", 60.0, "50", "2", "5", False, None)
    out = P.compare(load(), USABLE)
    assert out["frozen_mean_net"] == out["live_mean_net"]
    assert round(float(out["live_mean_net"]), 10) == 6.1167253591
    assert [x["window"] for x in out["per_window"] if x["delta_net"] != 0] == []
