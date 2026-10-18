"""What the CANCEL-CAPABLE watchdog does when the executor fails.

The owner's question, exactly: "Verify the ownership oracle's behavior if the
executor crashes or its registry becomes unavailable. Never cancel orders
without reliable ownership proof."

These tests are deliberately NOT the oracle in isolation -- that is
tests/test_l5_ownership_oracle_staleness.py. Each one drives the real
`Watchdog.sweep()` against:

  * a REAL `l5_order_registry_server` on a real socket, so the failure modes
    are produced the way production produces them (a closed port for a
    crashed executor, a real document that has stopped moving for a wedged
    one, the server's own `readable: false` when the ledger cannot be read);
  * a recording venue that FAILS THE TEST if a cancel POST is ever built.

The assertion that matters is the same in every failure case and is checked
three ways: no POST reached the venue, `cancelled` is empty, and the halt
latched anyway. An uncancelled order with the executor already stopped is
recoverable by hand. Somebody else's cancelled order is not.

The last test is the POSITIVE CONTROL. Refusals that are really a dead code
path would pass every test above, so one case proves the cancel does fire
when ownership IS proven -- which is what makes the refusals a bound.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import l5_halt_gate as HG
from src import l5_order_registry_server as REG
from src import l5_watchdog as W

MARKET = "cpc-btc-updown-15m-2026-10-18-1600z"
VID = "D4XOWNED00ZZ0"
OLD = "2026-10-18T15:00:00+00:00"          # older than any max_order_age_s


class Ledger:
    """Stands in for InventoryStore. `live_orders` is all the registry uses."""

    def __init__(self, rows=None, *, raises=False):
        self._rows = rows if rows is not None else {}
        self._raises = raises

    def live_orders(self):
        if self._raises:
            raise RuntimeError("volume unreadable")
        return dict(self._rows)


def owned_ledger():
    return Ledger({"c-1": {"venue_order_id": VID, "market": MARKET,
                           "side": "BUY", "session_id": "s-1", "t": OLD}})


class Venue:
    """The venue witness. Any cancel is recorded, and recorded loudly."""

    name = "CANCEL_ONLY_FAKE"

    def __init__(self, *, indicator=W.AUTOMATIC, market=MARKET,
                 terminal_after_cancel=True):
        self.posts = []
        self.gets = []
        self._indicator = indicator
        self._market = market
        self._terminal = terminal_after_cancel

    # NOTE: no `report_age_s`. The venue does not report its own age, which
    # is what makes this the venue-reading mode rather than the report mode.

    def open_orders(self):
        return [{"id": VID, "marketSlug": MARKET, "createTime": OLD,
                 "leavesQuantity": "0.01", "state": "ORDER_STATE_OPEN"}]

    def order(self, order_id):
        self.gets.append(order_id)
        if self.posts and self._terminal:
            return {"id": order_id, "marketSlug": self._market,
                    "manualOrderIndicator": self._indicator,
                    "state": "ORDER_STATE_CANCELED", "leavesQuantity": "0",
                    "cumQuantity": "0"}
        return {"id": order_id, "marketSlug": self._market,
                "manualOrderIndicator": self._indicator,
                "state": "ORDER_STATE_OPEN", "leavesQuantity": "0.01",
                "cumQuantity": "0"}

    def cancel(self, order_id, market_slug=None):
        self.posts.append(order_id)
        return {"ok": True}


def serve(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


def serve_registry(ledger):
    return serve(REG.make_handler(ledger))


def serve_raw(status, body):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                  # noqa: N802
            raw = body if isinstance(body, bytes) else body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            return

    return serve(H)


def watchdog(tmp_path, url, venue, *, skew_s=0.0):
    """A cancel-capable watchdog, wired as `from_env` wires one."""
    now_fn = None
    if skew_s:
        def now_fn():
            return dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                seconds=skew_s)
    return W.Watchdog(
        venue,
        W.asserter_for(HG.FileStore(str(tmp_path)), who="l5-watchdog-test"),
        max_order_age_s=300.0,
        report_max_age_s=W.DEFAULT_REPORT_MAX_AGE_S,
        cancel_enabled=True,
        oracle=W.OwnershipOracle(url, timeout_s=2.0, now_fn=now_fn))


def assert_refused_but_halted(out, venue, *, refusal_prefix):
    """The invariant under every executor failure, checked three ways."""
    assert venue.posts == [], "a cancel POST was built without ownership proof"
    assert out["cancelled"] == []
    assert out["still_open"] == []
    assert out["not_ours"] == []
    assert out["claimed_ids"] == 0
    assert "STILL RESTING" in out["cancel_skipped"]
    assert str(out["claim_refused"] or "").startswith(refusal_prefix), \
        out.get("claim_refused")
    # the halt is NOT skipped just because the cancel was
    assert out["halted"] is not None
    assert out["overdue"] and out["overdue"][0]["venue_order_id"] == VID


# ------------------------------------------------------- the executor crashed

def test_a_crashed_executor_takes_its_registry_with_it_and_nothing_cancels(
        tmp_path):
    """A crash closes the port. The read fails, so ownership is UNKNOWN.

    Produced the way production produces it: a real server is started, its
    URL captured, and then the process behind it is shut down -- exactly the
    state a container restart leaves behind.
    """
    httpd, url = serve_registry(owned_ledger())
    httpd.shutdown()
    httpd.server_close()
    venue = Venue()
    out = watchdog(tmp_path, url, venue).sweep()
    assert_refused_but_halted(out, venue, refusal_prefix="REGISTRY_UNREACHABLE")


def test_the_crash_refusal_names_the_exception_rather_than_owning_nothing(
        tmp_path):
    """"We could not find out" must not arrive as "we own nothing"."""
    httpd, url = serve_registry(owned_ledger())
    httpd.shutdown()
    httpd.server_close()
    out = watchdog(tmp_path, url, Venue()).sweep()
    assert out["claim_refused"] != ""
    assert out["claim_refused"] is not None
    assert ":" in out["claim_refused"], "the exception type must be named"


def test_an_unroutable_registry_host_refuses_rather_than_hangs(tmp_path):
    """`L5_ORDER_REGISTRY_URL` pointing at nothing is the same answer."""
    venue = Venue()
    out = watchdog(tmp_path, "http://127.0.0.1:9", venue).sweep()
    assert_refused_but_halted(out, venue, refusal_prefix="REGISTRY_UNREACHABLE")


# -------------------------------------------------------- the executor wedged

def test_a_wedged_executor_serving_a_stale_claim_cancels_nothing(tmp_path):
    """Alive, answering, writer stalled -- the case a crash does NOT cover.

    The document is the REAL one the real server built; the watchdog's clock
    is moved past the claim bound, which is what a stalled writer looks like
    from the outside.
    """
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue,
                       skew_s=W.OwnershipOracle.MAX_CLAIM_AGE_S + 60).sweep()
        assert_refused_but_halted(out, venue, refusal_prefix="CLAIM_STALE")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_claim_bound_matches_the_report_bound(tmp_path):
    """One bound, so "stopped publishing" cannot mean "confirms these are
    mine" on one path and not the other."""
    assert W.OwnershipOracle.MAX_CLAIM_AGE_S == W.DEFAULT_REPORT_MAX_AGE_S


def test_a_claim_just_inside_the_bound_is_still_proof(tmp_path):
    """The staleness refusal must be a bound, not a blanket refusal."""
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue,
                       skew_s=W.OwnershipOracle.MAX_CLAIM_AGE_S - 30).sweep()
        assert out["claimed_ids"] == 1
        assert venue.posts == [VID]
    finally:
        httpd.shutdown()
        httpd.server_close()


# ------------------------------------------- the registry itself says unknown

def test_an_unreadable_ledger_publishes_no_claim_and_cancels_nothing(tmp_path):
    """The real server's own `readable: false` branch, from a raising store.

    The executor is up and answering; it is its VOLUME that cannot be read.
    An empty list here would read as "we own nothing", which is a different
    claim, and the server is written not to make it.
    """
    httpd, url = serve_registry(Ledger(raises=True))
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert_refused_but_halted(out, venue,
                                  refusal_prefix="REGISTRY_NOT_READABLE")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_readable_but_empty_registry_is_a_different_fact(tmp_path):
    """"We own nothing" is safe and must not be reported as a failed read."""
    httpd, url = serve_registry(Ledger({}))
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == []
        assert out["claimed_ids"] == 0
        assert out["claim_refused"] is None, \
            "an empty claim is not a refusal and must not be dressed as one"
        assert out["halted"] is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


# ----------------------------------------------- malformed / hostile documents

@pytest.mark.parametrize("body,prefix", [
    (b"not json at all", "REGISTRY_UNREACHABLE"),
    (b"[]", "REGISTRY_NOT_READABLE"),
    (b"{}", "REGISTRY_NOT_READABLE"),
    (b'{"readable": "true", "orders": []}', "REGISTRY_NOT_READABLE"),
    (b'{"readable": true, "orders": []}', "CLAIM_AGE_UNREADABLE"),
    (b'{"readable": true, "as_of_utc": "", "orders": []}',
     "CLAIM_AGE_UNREADABLE"),
    (b'{"readable": true, "as_of_utc": "2026-10-18T15:00:00", "orders": []}',
     "CLAIM_AGE_UNREADABLE"),
    (b'{"readable": true, "as_of_utc": "nonsense", "orders": []}',
     "CLAIM_AGE_UNREADABLE"),
])
def test_a_malformed_claim_document_is_never_ownership_proof(
        tmp_path, body, prefix):
    httpd, url = serve_raw(200, body)
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert_refused_but_halted(out, venue, refusal_prefix=prefix)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_future_stamped_claim_is_refused(tmp_path):
    """A forged or badly-clocked future stamp would never go stale."""
    ahead = (dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(hours=2)).isoformat()
    doc = json.dumps({"readable": True, "as_of_utc": ahead,
                      "orders": [{"venue_order_id": VID, "market": MARKET}]})
    httpd, url = serve_raw(200, doc)
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert_refused_but_halted(out, venue,
                                  refusal_prefix="CLAIM_IN_THE_FUTURE")
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("status", [204, 500, 503, 404])
def test_a_non_200_registry_response_is_never_ownership_proof(tmp_path,
                                                              status):
    """Whatever the code, a response that is not the document proves nothing.

    204 is the one that does not raise in urllib, so it would otherwise
    return {} with NO refusal recorded -- indistinguishable from owning
    nothing, which is the exact confusion `claim_refused` exists to prevent.
    """
    doc = json.dumps({"readable": True,
                      "as_of_utc": dt.datetime.now(
                          dt.timezone.utc).isoformat(),
                      "orders": [{"venue_order_id": VID, "market": MARKET}]})
    httpd, url = serve_raw(status, doc)
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == [], "a non-200 body was believed"
        assert out["claimed_ids"] == 0
        assert out["claim_refused"], \
            "a non-200 must say WHY it yielded nothing"
        assert out["claim_refused"].startswith(
            ("REGISTRY_HTTP:", "REGISTRY_UNREACHABLE")), out["claim_refused"]
        assert out["halted"] is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


# ------------------------------- the registry is fine, the identity is not

def test_a_fresh_claim_for_a_different_id_never_cancels_the_resting_one(
        tmp_path):
    """The shared-account branch: not in the registry, never cancelled."""
    httpd, url = serve_registry(Ledger(
        {"c-9": {"venue_order_id": "D4SOMEONEELSE", "market": MARKET,
                 "t": OLD}}))
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == []
        assert out["claimed_ids"] == 1
        assert out["cancelled"] == []
        assert [r["why"] for r in out["not_ours"]] == \
            ["NOT_IN_OWNERSHIP_REGISTRY"]
        assert out["halted"] is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_claimed_id_the_venue_calls_manual_is_never_cancelled(tmp_path):
    """Our record is one witness. MANUAL from the venue ends it regardless."""
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue(indicator="MANUAL_ORDER_INDICATOR_MANUAL")
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == []
        assert [r["why"] for r in out["not_ours"]] == ["VENUE_SAYS_MANUAL"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_claimed_id_on_a_different_market_is_never_cancelled(tmp_path):
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue(market="cpcxrp-xrp-updown-15m-2026-10-18-1600z")
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == []
        assert [r["why"] for r in out["not_ours"]] == ["MARKET_MISMATCH"]
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------- positive control

def test_proven_ownership_does_cancel_so_the_refusals_are_a_bound(tmp_path):
    """Without this, every test above would pass on a dead code path.

    Fresh real claim, venue corroborates AUTOMATIC and the same market: the
    one POST is built, the id is RE-READ rather than trusting the ack, and it
    is reported cancelled only on the venue's own terminal state.
    """
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue()
        out = watchdog(tmp_path, url, venue).sweep()
        assert out["claimed_ids"] == 1
        assert venue.posts == [VID], "proven ownership must cancel"
        assert venue.gets.count(VID) == 2, "pre-check AND re-read"
        assert [r["venue_order_id"] for r in out["cancelled"]] == [VID]
        assert out["still_open"] == []
        assert out["not_ours"] == []
        assert out["halted"] is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_ack_is_not_a_cancellation_until_the_venue_agrees(tmp_path):
    """A venue that acks and keeps the order open reports still_open."""
    httpd, url = serve_registry(owned_ledger())
    try:
        venue = Venue(terminal_after_cancel=False)
        out = watchdog(tmp_path, url, venue).sweep()
        assert venue.posts == [VID]
        assert out["cancelled"] == []
        assert [r["venue_order_id"] for r in out["still_open"]] == [VID]
    finally:
        httpd.shutdown()
        httpd.server_close()
