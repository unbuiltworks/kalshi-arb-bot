"""The watchdog must be able to ACT, not merely log.

Two properties are under test, and they are the two the owner asked for:
a halt that outlives the executor process, and no path by which a wedged
executor leaves an order resting unmanaged.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_watchdog as W

NOW = dt.datetime(2026, 10, 12, 22, 0, 0, tzinfo=dt.timezone.utc)
MARKET = "cpc-btc-updown-15m-2026-10-12-2200z"


def open_row(oid="D1W00001", *, created="2026-10-12T21:40:00.000000000Z",
             leaves="1.00", state="ORDER_STATE_OPEN",
             indicator=None):
    """A venue row. `manualOrderIndicator` is the venue's own ownership
    signal and the watchdog now requires it to read AUTOMATIC before any
    cancel, so the double carries it."""
    return {"id": oid, "marketSlug": MARKET, "state": state,
            "leavesQuantity": leaves, "cumQuantity": "0",
            "createTime": created, "insertTime": created,
            "manualOrderIndicator": indicator or W.AUTOMATIC}


class Venue:
    def __init__(self, open_rows=(), reads=None, *, open_raises=False,
                 cancel_raises=(), read_raises=()):
        self._open = list(open_rows)
        self._reads = dict(reads or {})
        self.open_raises = open_raises
        self.cancel_raises = set(cancel_raises)
        self.read_raises = set(read_raises)
        self.cancelled = []

    def open_orders(self):
        if self.open_raises:
            raise OSError("network")
        return list(self._open)

    def cancel(self, oid, market_slug=None):
        self.cancelled.append(oid)
        if oid in self.cancel_raises:
            raise RuntimeError("venue said no")
        return {"ok": True}

    def order(self, oid):
        if oid in self.read_raises:
            raise TimeoutError("slow")
        row = dict(self._reads[oid])
        # The real venue always returns these two, and the watchdog now
        # requires both before it will POST a cancel. Defaulting them here
        # keeps each test on its own subject; ownership itself is tested in
        # tests/test_l5_watchdog_ownership.py, including the MANUAL case.
        row.setdefault("manualOrderIndicator", W.AUTOMATIC)
        row.setdefault("marketSlug", MARKET)
        return row


class ClaimsOpenOrders:
    """An ownership oracle claiming every id the venue shows resting.

    These tests predate the ownership requirement and are about the latch,
    the cancel mechanics and the failure modes -- not about ownership, which
    tests/test_l5_watchdog_ownership.py covers on its own. Supplying a
    permissive oracle keeps each test on its own subject.
    """

    def __init__(self, venue):
        self.venue = venue

    def claimed(self):
        try:
            rows = self.venue.open_orders()
        except BaseException:
            return {}
        return {str(r.get("id")): {"venue_order_id": str(r.get("id")),
                                   "market": str(r.get("marketSlug") or "")}
                for r in rows}


def wd(tmp_path, venue, **kw):
    store = HG.FileStore(str(tmp_path))
    kw.setdefault("oracle", ClaimsOpenOrders(venue))
    return W.Watchdog(venue, HG.HaltAsserter(store, who="test"), **kw), store


def gate(tmp_path):
    return HG.HaltGate(HG.FileStore(str(tmp_path)))


# ------------------------------------------------- the transport's own limits

def test_the_transport_can_only_get_and_cancel_one_named_order():
    sent = []
    t = W.CancelOnlyTransport(
        opener=lambda m, p, b, base: sent.append((m, p)) or {})
    t.request("GET", "/v1/orders/open")
    t.request("POST", "/v1/order/D1ABC/cancel", {})
    for method, path in (("POST", "/v1/orders"),
                         ("POST", "/v1/order/preview"),
                         ("POST", "/v1/orders/open/cancel"),
                         ("DELETE", "/v1/order/D1ABC"),
                         ("PUT", "/v1/order/D1ABC/cancel")):
        with pytest.raises(W.MutationRefused):
            t.request(method, path, {})
    assert sent == [("GET", "/v1/orders/open"),
                    ("POST", "/v1/order/D1ABC/cancel")]


def test_the_account_wide_sweep_is_not_merely_disabled_it_is_absent():
    """The shared account makes a market-scoped cancel unacceptable.

    Checked over CODE lines only: the docstring names the route in order to
    explain why it is refused, and a test that cannot tell prose from a
    request is a test that forbids documentation.
    """
    import ast
    src = open("src/l5_watchdog.py").read()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            d = ast.get_docstring(node)
            if d:
                docstrings.add(d)
    code = "\n".join(
        ln for ln in src.splitlines()
        if not any(ln.strip() and ln.strip() in d for d in docstrings)
        and not ln.strip().startswith("#"))
    for forbidden in ("/v1/orders/open/cancel", '"slugs"', "/v1/orders/cancel",
                      "/v1/order/preview"):
        assert forbidden not in code, forbidden
    assert 'request("POST", "/v1/orders"' not in code


def test_a_cancel_without_an_id_is_refused_before_any_request():
    t = W.CancelOnlyTransport(opener=lambda *a: pytest.fail("sent"))
    with pytest.raises(W.MutationRefused):
        t.cancel("", "cpc-btc-updown-15m-X")


def test_a_cancel_without_a_market_slug_is_refused_before_any_request():
    """An empty cancel body is what the venue refused with HTTP 400 on
    2026-10-18, so an absent slug must refuse rather than post `{}`."""
    t = W.CancelOnlyTransport(opener=lambda *a: pytest.fail("sent"))
    with pytest.raises(W.MutationRefused):
        t.cancel("D1", "")


# --------------------------------------------------------------- the latch

def test_an_overdue_order_latches_the_halt_and_the_executor_then_refuses(
        tmp_path):
    v = Venue([open_row()])
    w, store = wd(tmp_path, v, max_order_age_s=300)
    out = w.sweep(now=NOW)
    assert [o["venue_order_id"] for o in out["overdue"]] == ["D1W00001"]
    assert out["halted"] is not None
    # the executor's own gate, reading the same record, now refuses
    assert gate(tmp_path).submissions_permitted() is False


def test_the_halt_outlives_the_process_that_asserted_it(tmp_path):
    v = Venue([open_row()])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    w.sweep(now=NOW)
    del w, v                                  # the watchdog is gone
    assert gate(tmp_path).submissions_permitted() is False, (
        "a latch that dies with its asserter is not a latch")


def test_the_watchdog_cannot_clear_what_it_asserted(tmp_path):
    v = Venue([open_row()])
    w, store = wd(tmp_path, v, max_order_age_s=300)
    w.sweep(now=NOW)
    a = HG.HaltAsserter(store, who="test")
    assert not any(hasattr(a, n) for n in ("clear", "delete", "release",
                                           "unhalt", "resume"))
    assert not hasattr(store, "delete")


def test_a_young_order_is_left_alone_and_the_executor_stays_permitted(
        tmp_path):
    """A clean sweep must ATTEST, not just stay quiet.

    `HaltGate`'s only path to True is a fresh explicit "no halt" and
    absence reads as a halt, so a watchdog that said nothing on a clean
    sweep would stop the executor for good. This test is why `sweep`
    heartbeats.
    """
    v = Venue([open_row(created="2026-10-12T21:59:30.000000000Z")])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    out = w.sweep(now=NOW)
    assert out["overdue"] == [] and out["halted"] is None
    assert v.cancelled == []
    assert out["heartbeat"]["halt"] is False
    assert gate(tmp_path).submissions_permitted() is True


def test_the_heartbeat_cannot_undo_a_latched_halt(tmp_path):
    v = Venue([open_row()])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    w.sweep(now=NOW)                                   # latches
    v._open = []                                       # the order is gone
    out = w.sweep(now=NOW)
    assert out["heartbeat"] is None
    assert "refusing to overwrite a latched HALT" in out["heartbeat_refused"]
    assert gate(tmp_path).submissions_permitted() is False, (
        "clearing is an owner action, not an automated one")


def test_a_terminal_order_in_the_open_list_is_not_overdue(tmp_path):
    v = Venue([open_row(leaves="0", state="ORDER_STATE_FILLED")])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    assert w.sweep(now=NOW)["overdue"] == []


def test_an_order_with_an_unreadable_creation_time_counts_as_overdue(
        tmp_path):
    v = Venue([open_row(created="")])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    out = w.sweep(now=NOW)
    assert out["overdue"][0]["age_known"] is False
    assert out["halted"] is not None


def test_a_blind_watchdog_stops_the_executor_by_omission_not_by_latching(
        tmp_path):
    """A read failure must neither latch nor attest.

    Latching on every network blip would be unusable; attesting would be a
    lie. Writing nothing is the third option, and it is the safe one: the
    last record simply ages out, and `HaltGate` refuses anything staler
    than max_age_s.
    """
    v = Venue(open_raises=True)
    w, store = wd(tmp_path, v, max_order_age_s=300)
    out = w.sweep(now=NOW)
    assert out["read_error"] == "OSError"
    assert out["halted"] is None and out["overdue"] == []
    assert "heartbeat" not in out, "a blind watchdog attests to nothing"
    # nothing written at all, so the gate's default (refuse) stands
    assert gate(tmp_path).submissions_permitted() is False

    # and with a fresh attestation already on disk, one blip is survivable
    HG.HaltAsserter(store, who="test").heartbeat_no_halt()
    assert gate(tmp_path).submissions_permitted() is True
    w.sweep(now=NOW)
    assert gate(tmp_path).submissions_permitted() is True
    # but a stale one is not
    stale = HG.HaltGate(store, max_age_s=0.0)
    assert stale.submissions_permitted() is False
    assert stale.refuse_reason() == "halt_record_stale"


# ----------------------------------------------------- the wedged executor

def test_a_wedged_executor_cannot_leave_an_unmanaged_resting_order(tmp_path):
    """The whole point: halt AND cancel, verified at the venue."""
    v = Venue([open_row()],
              {"D1W00001": {"id": "D1W00001",
                            "state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert v.cancelled == ["D1W00001"]
    assert [c["venue_order_id"] for c in out["cancelled"]] == ["D1W00001"]
    assert out["still_open"] == []
    assert gate(tmp_path).submissions_permitted() is False


def test_the_halt_is_asserted_before_the_cancel_is_attempted(tmp_path):
    """If the cancel loop dies, the executor must already be stopped."""
    order_of_events = []

    class Noisy(Venue):
        def cancel(self, oid, market_slug=None):
            order_of_events.append("cancel")
            raise RuntimeError("died here")

    class Watcher(HG.HaltAsserter):
        def assert_halt(self, **kw):
            order_of_events.append("halt")
            return super().assert_halt(**kw)

    v = Noisy([open_row()], {"D1W00001": open_row()})
    w = W.Watchdog(v, Watcher(HG.FileStore(str(tmp_path)), who="t"),
                   max_order_age_s=300, cancel_enabled=True,
                   oracle=ClaimsOpenOrders(v))
    w.sweep(now=NOW)
    assert order_of_events[:2] == ["halt", "cancel"]
    assert gate(tmp_path).submissions_permitted() is False


def test_a_cancel_ack_is_never_proof(tmp_path):
    """200 on the cancel, still live on the re-read: not a cancellation."""
    v = Venue([open_row()], {"D1W00001": open_row()})      # still leaves 1.00
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert out["cancelled"] == []
    assert [o["venue_order_id"] for o in out["still_open"]] == ["D1W00001"]


def test_an_ambiguous_cancel_is_resolved_by_the_reread_not_retried(tmp_path):
    v = Venue([open_row()],
              {"D1W00001": {"id": "D1W00001", "state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}},
              cancel_raises=["D1W00001"])
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert v.cancelled == ["D1W00001"], "once, never retried"
    assert len(out["cancelled"]) == 1
    assert out["cancelled"][0]["cancel_error"] == "RuntimeError"


def test_an_unverifiable_cancel_is_never_reported_as_cancelled(tmp_path):
    v = Venue([open_row()], {}, read_raises=["D1W00001"])
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert out["cancelled"] == []
    assert out["unverified"][0]["verify_error"] == "TimeoutError"


def test_a_partial_fill_is_reported_as_residual_and_never_traded_out(
        tmp_path):
    v = Venue([open_row()],
              {"D1W00001": {"id": "D1W00001", "state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0.40"}})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert out["cancelled"][0]["cum_qty"] == "0.40"
    # one cancel, and nothing resembling a flattening order
    assert v.cancelled == ["D1W00001"]


def test_an_assert_only_watchdog_says_the_order_is_still_resting(tmp_path):
    v = Venue([open_row()])
    w, _ = wd(tmp_path, v, max_order_age_s=300)      # cancel_enabled False
    out = w.sweep(now=NOW)
    assert v.cancelled == []
    assert "STILL RESTING" in out["cancel_skipped"]
    assert gate(tmp_path).submissions_permitted() is False


def test_cancel_needs_an_explicit_yes_AND_an_ownership_registry(monkeypatch,
                                                                tmp_path):
    monkeypatch.setenv("L5_WATCHDOG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://registry:8081")
    for val in ("", "1", "true", "yes ", "Y"):
        monkeypatch.setenv("L5_WATCHDOG_CANCEL", val)
        assert W.from_env(transport=Venue()).cancel_enabled is (
            val.strip().upper() == "YES")
    monkeypatch.setenv("L5_WATCHDOG_CANCEL", "YES")
    assert W.from_env(transport=Venue()).cancel_enabled is True

    # and YES alone is not enough: with no registry the power is unavailable
    monkeypatch.delenv("L5_ORDER_REGISTRY_URL")
    w = W.from_env(transport=Venue())
    assert w.cancel_requested is True and w.cancel_enabled is False


def test_the_venue_fraction_format_parses(tmp_path):
    """Nine-digit fractions are what the venue actually sends."""
    assert W.order_age_s({"createTime": "2026-10-12T21:40:00.000000000Z"},
                         now=NOW) == pytest.approx(1200.0)
    assert W.order_age_s({"createTime": "2026-10-12T21:40:00Z"},
                         now=NOW) == pytest.approx(1200.0)
    assert W.order_age_s({"createTime": "nonsense"}, now=NOW) is None


# ------------------------------------------------------------- the server

def test_the_server_is_get_only_and_a_missing_latch_is_a_404(tmp_path):
    """And the executor reading over HTTP sees exactly what the gate needs."""
    import urllib.error
    import urllib.request
    from src import l5_halt_server as SRV

    store = HG.FileStore(str(tmp_path))
    httpd = SRV.serve(store, port=0)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        # nothing asserted yet: 404, and the gate reads that as NOT permitted
        remote = HG.HaltGate(HG.HttpHaltStore(base))
        assert remote.submissions_permitted() is False
        assert remote.refuse_reason() == "halt_state_unavailable"

        # a positive attestation makes it permitted, over the wire
        HG.HaltAsserter(store, who="test").heartbeat_no_halt()
        assert remote.submissions_permitted() is True

        # a latch stops it, over the wire
        HG.HaltAsserter(store, who="test").assert_halt(
            reason="UNMANAGED_RESTING_ORDER", evidence={"orders": ["D1X"]})
        assert remote.submissions_permitted() is False
        assert remote.refuse_reason() == "halt_asserted"

        # and the server offers no way to write or clear one
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            req = urllib.request.Request(
                "%s/%s" % (base, HG.HALT_KEY), data=b"{}", method=method)
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            assert e.value.code in (405, 501)
        assert HG.HaltGate(HG.HttpHaltStore(base)
                           ).submissions_permitted() is False
    finally:
        httpd.shutdown()


def test_the_watchdog_entrypoint_holds_no_submit_capability():
    """It imports nothing that can place an order, and reads no arming key.

    The last clause matters in its own right: refusing to clean up because
    an arming flag is unset is the dangerous direction, so this process
    must not consult one.
    """
    src = open("scripts/l5_halt_watchdog.py").read()
    assert "l5_pmus_transport" not in src, "that module can POST an order"
    assert "InventoryStore" not in src and "FaithfulExecutor" not in src
    for forbidden in ("PMUS_Q1_ARM", "PMUS_Q1_LIVE", "A.armed(",
                      "owner_arm_token", "L5_SHADOW_ONLY"):
        assert forbidden not in src, forbidden


# --------------------------------------- executor failure, staleness, restart

def test_a_restarted_watchdog_finds_its_own_latch_and_cannot_lift_it(tmp_path):
    """The latch lives on the volume, not in the process that wrote it."""
    v = Venue([open_row()])
    w, _ = wd(tmp_path, v, max_order_age_s=300)
    w.sweep(now=NOW)
    del w

    # a fresh process, a fresh Watchdog, and the venue now looks clean
    v2 = Venue([])
    w2, _ = wd(tmp_path, v2, max_order_age_s=300)
    out = w2.sweep(now=NOW)
    assert out["overdue"] == []
    assert out["heartbeat"] is None, "a restart is not an all-clear"
    assert "refusing to overwrite a latched HALT" in out["heartbeat_refused"]
    assert gate(tmp_path).submissions_permitted() is False


def test_a_dead_executor_is_indistinguishable_from_a_hung_one_and_both_stop(
        tmp_path):
    """The signal is the ORDER, not the process, so every failure presents
    the same way: crashed container, wedged loop, lost cancel, killed task."""
    v = Venue([open_row()],
              {"D1W00001": {"id": "D1W00001",
                            "state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert len(out["cancelled"]) == 1
    assert gate(tmp_path).submissions_permitted() is False
    # and the halt names what it saw, so the owner can tell why
    rec = json.loads(HG.FileStore(str(tmp_path)).get(HG.HALT_KEY))
    assert rec["halt"] is True
    assert rec["reason"] == "UNMANAGED_RESTING_ORDER"
    assert rec["evidence"]["orders"][0]["venue_order_id"] == "D1W00001"
    assert rec["who"] == "test"


def test_a_stale_heartbeat_stops_the_executor_without_any_halt_being_written(
        tmp_path):
    """Staleness is its own refusal: no latch, no record, still no trading."""
    store = HG.FileStore(str(tmp_path))
    HG.HaltAsserter(store, who="test").heartbeat_no_halt()
    fresh = HG.HaltGate(store, max_age_s=900)
    assert fresh.submissions_permitted() is True
    stale = HG.HaltGate(store, max_age_s=0.0)
    assert stale.submissions_permitted() is False
    assert stale.refuse_reason() == "halt_record_stale"
    # the record still says halt:false -- nothing was latched
    assert json.loads(store.get(HG.HALT_KEY))["halt"] is False


def test_a_watchdog_that_stops_sweeping_stops_the_executor_by_itself(
        tmp_path):
    """No heartbeat, no trading. The executor needs a LIVE attestation."""
    v = Venue([])
    w, store = wd(tmp_path, v, max_order_age_s=300)
    w.sweep(now=NOW)
    assert HG.HaltGate(store, max_age_s=900).submissions_permitted() is True
    # the watchdog dies here and never sweeps again
    assert HG.HaltGate(store, max_age_s=0.0).submissions_permitted() is False


def test_an_ambiguous_order_state_is_never_read_permissively(tmp_path):
    """leavesQuantity decides; the state STRING decides only when it does not.

    PARTIALLY_FILLED contains "FILLED", and a substring match would retire an
    order that is still resting -- which is how an unmanaged order would slip
    past a watchdog that trusted the words.
    """
    partial = {"id": "D1P00001", "state": "ORDER_STATE_PARTIALLY_FILLED",
               "leavesQuantity": "0.60", "cumQuantity": "0.40",
               "createTime": "2026-10-12T21:40:00.000000000Z",
               "marketSlug": MARKET}
    assert W.is_terminal(partial) is False
    v = Venue([partial], {"D1P00001": partial})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)
    assert [o["venue_order_id"] for o in out["overdue"]] == ["D1P00001"]
    assert out["cancelled"] == [], "still working after the cancel"
    assert out["still_open"][0]["cum_qty"] == "0.40"
    assert gate(tmp_path).submissions_permitted() is False


def test_a_state_with_no_leaves_field_falls_back_to_the_words(tmp_path):
    assert W.is_terminal({"state": "ORDER_STATE_CANCELED"}) is True
    assert W.is_terminal({"state": "ORDER_STATE_OPEN"}) is False
    assert W.is_terminal({"state": ""}) is False, "unknown is not terminal"
    assert W.is_terminal({"state": "ORDER_STATE_OPEN",
                          "leavesQuantity": "garbage"}) is False


def test_exactly_one_named_id_is_cancelled_and_nothing_else(tmp_path):
    """Exact-order-only.

    This test used to say the ids "come from the venue's own open list",
    which was the design the owner rejected: on a SHARED account that list
    includes other people's orders. Ownership now comes from the executor's
    durable record cross-checked against the venue, and
    tests/test_l5_watchdog_ownership.py owns that question. What remains
    here is the narrower claim: one cancel per overdue id, by venue id, and
    no broader route.
    """
    v = Venue([open_row("D1SEEN001")],
              {"D1SEEN001": {"id": "D1SEEN001",
                             "state": "ORDER_STATE_CANCELED",
                             "leavesQuantity": "0", "cumQuantity": "0",
                             "marketSlug": MARKET,
                             "manualOrderIndicator": W.AUTOMATIC}})
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    w.sweep(now=NOW)
    assert v.cancelled == ["D1SEEN001"]
