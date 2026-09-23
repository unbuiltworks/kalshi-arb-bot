"""The production path, end to end, without a network.

Each test pins a way the live probe could lose money or lie about itself:
the wrong wire price for a DOWN order, a preflight that passes on the public
book, an unarmed runner that transmits, a crash that doubles an order, or an
L5 action priced against a book observed after it.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_auth_book as AB
from src import l5_pmus_live_runner as RN
from src import l5_pmus_transport as T
from src import pmus_q1_executor as Q1
from tests.test_l5_pmus_transport import FakeHTTP, SLUG, NOW, owner_arm

WS = int(dt.datetime(2026, 9, 23, 21, 0, tzinfo=dt.timezone.utc).timestamp())


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")
    monkeypatch.delenv("L5_LIVE_HALT", raising=False)
    for e in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
        monkeypatch.delenv(e, raising=False)


def ws_book(bid="0.40", ask="0.60", bq="50", aq="70"):
    return {"marketData": {"marketSlug": SLUG,
                           "bids": [{"px": {"value": bid}, "qty": bq}],
                           "offers": [{"px": {"value": ask}, "qty": aq}]}}


def live_book(t0=WS + 5.0):
    clock = {"t": t0}
    b = AB.AuthBook(clock=lambda: clock["t"])
    b.connected = True
    b.ingest(ws_book(), clock["t"])
    return b, clock


# ------------------------------------------------------------- auth book

def test_ws_book_is_folded_by_the_recorders_own_parser():
    b, clock = live_book()
    snap = b.snapshot(SLUG)
    assert snap["bid"] == "0.40" and snap["ask"] == "0.60"
    # depth renamed ONCE to the frozen rule's names, per state
    assert snap["bid_shares"] == "50" and snap["ask_shares"] == "70"
    assert snap["source"] == "authenticated_ws"
    rep = b.window_report(SLUG)
    assert rep["first_book_offset_s"] == 5.0
    assert rep["book_before_first_trade"] is True


def test_a_disconnected_stream_serves_no_book():
    b, _ = live_book()
    b.connected = False
    assert b.snapshot(SLUG) is None


def test_stream_age_is_time_since_the_last_frame():
    b, clock = live_book()
    clock["t"] += 12.5
    assert b.snapshot(SLUG)["stream_age_s"] == 12.5


def test_next_window_is_subscribed_before_it_opens():
    assert AB.wanted_slugs(WS + 100) == [AB.slug_for(WS)]
    got = AB.wanted_slugs(WS + 900 - 60)
    assert got == [AB.slug_for(WS), AB.slug_for(WS + 900)]
    assert AB.slug_for(WS) == SLUG
    assert AB.slug_start(SLUG) == WS


# -------------------------------------------------------------- wire body

def armed_transport(monkeypatch, http=None, book=None):
    owner_arm(monkeypatch)
    t = T.PmusTransport(market_slug=SLUG, http=http or FakeHTTP(), now_fn=NOW,
                        book_source=book, sleep=lambda s: None)
    t.preflight()
    t.arm()
    return t


def test_preflight_prefers_the_authenticated_ws_book(creds, monkeypatch):
    b, _ = live_book()
    t = T.PmusTransport(market_slug=SLUG, http=FakeHTTP(), now_fn=NOW,
                        book_source=b.snapshot)
    rep = t.preflight()
    src = [c for c in rep["checks"] if c["check"] == "book_readable"][0]
    assert src["detail"] == "authenticated_ws"
    # and the public gateway was never asked for anything
    assert all(p.startswith(("/v1/account", "/v1/market/slug", "/v1/portfolio",
                             "/v1/orders/open")) for _, p in t.http.calls)


def test_a_stale_ws_book_fails_preflight(creds):
    b, clock = live_book()
    clock["t"] += T.BOOK_MAX_STREAM_AGE_S + 1
    t = T.PmusTransport(market_slug=SLUG, http=FakeHTTP(), now_fn=NOW,
                        book_source=b.snapshot)
    with pytest.raises(T.PreflightFailed) as e:
        t.preflight()
    assert "book_fresh" in json.loads(str(e.value))["blocking_failures"]


def test_up_taker_wire_body_is_the_live_verified_shape(creds, monkeypatch):
    t = armed_transport(monkeypatch)
    body = t.order_body({"side": "UP", "price": D("0.60"), "qty": D("1.237"),
                         "mode": "TAKER", "market": SLUG})
    assert body == {
        "marketSlug": SLUG, "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "0.60", "currency": "USD"},
        "quantity": 1.23,                       # floored to the 0.01 grid
        "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
        "participateDontInitiate": False,
        "outcomeSide": "OUTCOME_SIDE_YES", "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}


def test_down_price_goes_on_the_wire_as_the_long_side_value(creds, monkeypatch):
    # the frozen rule prices a DOWN cross at 1 - bid = 0.60 in NO terms; the
    # venue reads price.value as the LONG side, so the wire value is 0.40
    t = armed_transport(monkeypatch)
    body = t.order_body({"side": "DOWN", "price": D("0.60"), "qty": D("1"),
                         "mode": "TAKER"})
    assert body["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert body["price"]["value"] == "0.40"


def test_maker_is_post_only_good_till_cancel(creds, monkeypatch):
    t = armed_transport(monkeypatch)
    body = t.order_body({"side": "UP", "price": D("0.55"), "qty": D("1"),
                         "mode": "MAKER"})
    assert body["participateDontInitiate"] is True
    assert body["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"


def test_below_minimum_quantity_is_refused_not_rounded_up(creds, monkeypatch):
    t = armed_transport(monkeypatch)
    with pytest.raises(ValueError):
        t.order_body({"side": "UP", "price": D("0.60"), "qty": D("0.004"),
                      "mode": "TAKER"})


def test_armed_submit_previews_then_sends_once_and_reads_the_fill(creds,
                                                                  monkeypatch):
    t = armed_transport(monkeypatch)
    ack = t.submit({"client_order_id": "c1", "side": "UP", "price": D("0.60"),
                    "qty": D("1"), "mode": "TAKER", "market": SLUG})
    assert [p for p, _ in t.http.posts] == ["/v1/order/preview", "/v1/orders"]
    assert ack["order_id"] == "V1" and ack["filled_qty"] == "1"
    assert ack["fill_price"] == "0.60"


def test_a_down_fill_is_reported_in_no_terms(creds, monkeypatch):
    http = FakeHTTP(**{"/v1/order/V1": {"order": {
        "state": "ORDER_STATE_FILLED", "cumQuantity": "1",
        "avgPx": {"value": "0.40"}}}})
    t = armed_transport(monkeypatch, http=http)
    ack = t.submit({"client_order_id": "c1", "side": "DOWN", "price": D("0.60"),
                    "qty": D("1"), "mode": "TAKER", "market": SLUG})
    assert ack["fill_price"] == "0.60"


def test_submit_refuses_a_market_it_did_not_preflight(creds, monkeypatch):
    t = armed_transport(monkeypatch)
    with pytest.raises(LX.NotArmed):
        t.submit({"client_order_id": "c1", "side": "UP", "price": D("0.60"),
                  "qty": D("1"), "mode": "TAKER",
                  "market": "cpc-btc-updown-15m-2026-09-23-2115z"})


def test_cancel_uses_the_venue_id_and_the_proven_route(creds, monkeypatch):
    t = armed_transport(monkeypatch)
    t.submit({"client_order_id": "c1", "side": "UP", "price": D("0.55"),
              "qty": D("1"), "mode": "MAKER", "market": SLUG})
    t.cancel("c1")
    t.cancel_all()
    paths = [p for p, _ in t.http.posts]
    assert "/v1/order/V1/cancel" in paths
    assert "/v1/orders/open/cancel" in paths


# ----------------------------------------------------------------- runner

def l5_fill(ts, outcome="Up", price="0.61", size="22.13", tx="0xa"):
    return {"slug": "btc-updown-15m-%d" % WS, "timestamp": ts,
            "outcome": outcome, "side": "BUY", "price": price, "size": size,
            "transactionHash": tx, "asset": "a-" + outcome}


def make_runner(tmp_path, feed_rows, http=None, book=None):
    b, clock = book or live_book()
    feed = {"rows": []}
    r = RN.Runner(root=str(tmp_path), book=b, http=http or FakeHTTP(),
                  feed=lambda: list(feed["rows"]))
    return r, feed, clock


def test_runner_is_disarmed_without_the_owner_token(creds, tmp_path, capsys):
    r, feed, clock = make_runner(tmp_path, [])
    r.tick(WS + 5)                               # seeds on empty history
    assert r.preflight_status == "PASSED"
    assert r.transport.armed is False
    feed["rows"] = [l5_fill(WS + 6)]
    r.tick(WS + 7)
    rec = json.loads(open(tmp_path / "decisions.jsonl").readline())["record"]
    # the frozen rule translated it as a TAKER at the authenticated ask ...
    assert rec["PMUS_TRANSLATION"]["mode"] == "TAKER"
    assert rec["PMUS_TRANSLATION"]["price"] == "0.60"
    # ... safety permitted it, and the transport refused: nothing was sent
    assert rec["SAFETY_OVERLAY"]["permitted"] is True
    assert rec["ORDER"]["reason"] == "NOT_ARMED"
    assert rec["ORDER"]["submitted"] is False
    assert not [p for p, _ in r.http.posts]
    assert r.store.state["submitted"] == {}


def test_runner_seeds_history_without_acting_on_it(creds, tmp_path):
    r, feed, _ = make_runner(tmp_path, [])
    feed["rows"] = [l5_fill(WS - 400, tx="0xold")]
    r.tick(WS + 5)
    assert r.stats["seeded"] == 1
    assert not (tmp_path / "decisions.jsonl").exists()


def test_armed_runner_sends_exactly_the_frozen_rules_order(creds, tmp_path,
                                                           monkeypatch):
    owner_arm(monkeypatch)
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    assert r.transport.armed is True
    feed["rows"] = [l5_fill(WS + 6)]
    r.tick(WS + 7)
    orders = [b for p, b in r.http.posts if p == "/v1/orders"]
    assert len(orders) == 1
    assert orders[0]["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert orders[0]["price"]["value"] == "0.60"
    # lambda maps the median L5 action (22.13) to ~1 share
    assert orders[0]["quantity"] == 0.99 or orders[0]["quantity"] == 1.0
    assert r.store.position(SLUG, "UP") > 0


def test_replay_after_crash_never_doubles_the_order(creds, tmp_path,
                                                    monkeypatch):
    owner_arm(monkeypatch)
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    feed["rows"] = [l5_fill(WS + 6)]
    r.tick(WS + 7)
    # a new process over the same state dir sees the same action again
    r2, feed2, _ = make_runner(tmp_path, [])
    r2.tick(WS + 8)
    r2.seen.clear()
    r2.seeded = True
    feed2["rows"] = [l5_fill(WS + 6)]
    r2.tick(WS + 9)
    assert not [p for p, _ in r2.http.posts if p == "/v1/orders"]
    last = open(tmp_path / "decisions.jsonl").readlines()[-1]
    assert "DUPLICATE_SUPPRESSED" in last


def test_exposure_cap_stops_the_probe_at_five_dollars(creds, tmp_path,
                                                      monkeypatch):
    owner_arm(monkeypatch)
    http = FakeHTTP(**{"/v1/order/V1": {"order": {
        "state": "ORDER_STATE_FILLED", "cumQuantity": "4.51",
        "avgPx": {"value": "0.60"}}}})
    r, feed, _ = make_runner(tmp_path, [], http=http)
    r.tick(WS + 5)
    # 100 * lambda = 4.52 shares at 0.60 = $2.71 each: the second breaches $5
    feed["rows"] = [l5_fill(WS + 6 + i, size="100", tx="0x%d" % i)
                    for i in range(3)]
    r.tick(WS + 20)
    committed = r.store.committed()[SLUG]
    assert D("0") < committed <= D("5")
    assert len([p for p, _ in r.http.posts if p == "/v1/orders"]) == 1
    assert r.stats["SAFETY_OVERLAY:MAX_USD_PER_MARKET"] >= 1


def test_a_sell_is_refused_by_our_scope_not_attributed_to_l5(creds, tmp_path):
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    row = l5_fill(WS + 6)
    row["side"] = "SELL"
    feed["rows"] = [row]
    r.tick(WS + 7)
    assert r.stats["SAFETY_OVERLAY:SELL_NOT_IN_PROBE_SCOPE"] == 1


def test_a_dead_stream_is_stale_under_the_frozen_bound(creds, tmp_path):
    b, clock = live_book()
    r, feed, _ = make_runner(tmp_path, [], book=(b, clock))
    r.tick(WS + 5)
    clock["t"] += 10                  # no frame for 10s > the rule's 2.0s
    feed["rows"] = [l5_fill(WS + 14)]
    r.tick(WS + 15)
    assert r.stats["PMUS_TRANSLATION:STALE_OBSERVATION"] == 1


def test_halt_refuses_and_never_arms(creds, tmp_path, monkeypatch):
    owner_arm(monkeypatch)
    monkeypatch.setenv("L5_LIVE_HALT", "1")
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    assert r.transport.armed is False
    feed["rows"] = [l5_fill(WS + 6)]
    r.tick(WS + 7)
    assert r.stats["SAFETY_OVERLAY:OPERATOR_HALT"] == 1
    assert not [p for p, _ in r.http.posts if p == "/v1/orders"]


def test_window_summary_reports_observability(creds, tmp_path, capsys):
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    feed["rows"] = [l5_fill(WS + 6)]
    r.tick(WS + 7)
    r.tick(WS + 905)                  # roll to the next window
    out = [l for l in capsys.readouterr().out.splitlines()
           if l.startswith("L5_LIVE_WINDOW ")]
    doc = json.loads(out[-1].split(" ", 1)[1])
    assert doc["market"] == SLUG
    assert doc["observability"]["first_book_offset_s"] == 5.0
    assert doc["book_before_first_l5_action"] is True


def test_a_restable_action_is_refused_by_the_frozen_rule_live(creds, tmp_path):
    # L5 at 0.58 against our ask of 0.60 can rest. The frozen rule acquires a
    # maker leg only on STRICT evidence, which cannot exist at decision time,
    # so it refuses. That is the frozen rule's behaviour and is NOT adjusted.
    r, feed, _ = make_runner(tmp_path, [])
    r.tick(WS + 5)
    feed["rows"] = [l5_fill(WS + 6, price="0.58")]
    r.tick(WS + 7)
    assert r.stats["PMUS_TRANSLATION:STRICT_NOT_EVALUABLE"] == 1


def test_the_frozen_modules_are_imported_not_copied():
    import src.l5_pmus_live_runner as m
    src = open(m.__file__).read()
    assert "def pmus_translate" not in src and "def l5_native" not in src
    assert m.FT.__name__ == "src.l5_faithful_translation"
