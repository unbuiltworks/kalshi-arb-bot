"""The place-then-rest-then-re-read leg, driven end to end with fakes.

The question this leg exists to answer is whether the INDEPENDENT watchdog
withdraws a real resting order. These tests pin everything around that: that
exactly one order is ever sent, that it cannot cross, that it is the venue's
minimum, that the $5 authorization is checked INCLUDING worst-case fees, that
ownership evidence is established and VERIFIED before the watchdog is relied
on, that a terminal status comes from a venue re-read rather than an ack, and
that a self-cancel is never reported as proof.

Nothing here reaches a venue: the transport is a recorder.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_cancel_mechanism as M

MARKET = "cpc-btc-updown-15m-2026-10-18-1800z"
WS = 1792368000          # the 1800z window start
VID = "D4MECH0000ZZ1"


class Book:
    def __init__(self, bid="0.40", ask="0.41", two_sided=True):
        self._bid, self._ask = bid, ask
        self._two = two_sided

    def snapshot(self, slug):
        if not self._two:
            return None
        return {"bid": D(self._bid), "ask": D(self._ask),
                "stream_age_s": 0.1, "market_state": "MARKET_STATE_OPEN",
                "source": "authenticated_ws"}


class Transport:
    """Records every call. Never reaches a venue."""

    tick_size = D("0.01")
    quantity_increment = D("0.01")
    market_slug = MARKET

    def __init__(self, *, armed=True, vid=VID, rows=None, submit_raises=None,
                 cancel_raises=None, last_cancel_error=None):
        self.armed = armed
        self._vid = vid
        self._cancel_raises = cancel_raises
        self.last_cancel_error = last_cancel_error
        self.submits = []
        self.cancels = []
        self.cancel_args = []
        self.reads = []
        self._rows = list(rows or [])
        self._submit_raises = submit_raises

    def submit(self, intent):
        self.submits.append(dict(intent))
        if self._submit_raises:
            raise self._submit_raises
        return {"order": {"id": self._vid}} if self._vid else {"order": {}}

    def cancel(self, vid, market_slug=None):
        self.cancels.append(vid)
        #: the SLUG as well as the id. The venue refuses a cancel whose body
        #: does not name the market (HTTP 400, 2026-10-18, D68KZ0SG10JR), so
        #: a mechanism that cancels without one cannot withdraw its order.
        self.cancel_args.append((vid, market_slug))
        if self._cancel_raises:
            raise self._cancel_raises
        return {"ok": True}

    def order(self, vid):
        self.reads.append(vid)
        if self._rows:
            return self._rows.pop(0)
        return {"id": vid, "state": "ORDER_STATE_OPEN",
                "leavesQuantity": "0.01", "cumQuantity": "0"}


class Store:
    """Enough InventoryStore for the mechanism, with a real save counter."""

    def __init__(self, *, committed=None, live=None):
        self.state = {"committed": dict(committed or {}),
                      "live_orders": dict(live or {}), "submitted": {}}
        self.saves = 0

    def save(self):
        self.saves += 1

    def committed(self):
        return {k: D(str(v)) for k, v in self.state["committed"].items()}

    def live_order_count(self):
        return len(self.state["live_orders"])

    def live_orders(self):
        # what l5_order_registry_server.registry() actually calls. The
        # mechanism renders the REAL document, so the fake has to serve the
        # real interface -- which is how the first run of these tests caught
        # that `registry_readable` was being reported False.
        return {k: dict(v) for k, v in self.state["live_orders"].items()}

    def record_submit(self, coid, intent, *, session_id=None, mode=None):
        rec = {"t": "2026-10-18T18:01:00+00:00", "market": intent["market"],
               "side": intent["side"], "price": str(intent["price"]),
               "qty": str(intent["qty"]), "session_id": session_id,
               "mode": mode}
        self.state["submitted"][coid] = rec
        self.state["live_orders"][coid] = dict(rec, remaining=str(intent["qty"]),
                                               state="LIVE")
        self.save()

    def attach_venue_id(self, coid, vid):
        if coid in self.state["live_orders"] and vid:
            self.state["live_orders"][coid]["venue_order_id"] = str(vid)
            self.save()

    def owns_venue_order_id(self, vid):
        v = str(vid or "").strip()
        if not v:
            return False
        for bucket in ("live_orders", "pending_terminal", "quarantine",
                       "submitted"):
            for row in (self.state.get(bucket) or {}).values():
                if str(row.get("venue_order_id") or "") == v:
                    return True
        return False

    def terminate_order(self, coid, *, reason):
        self.state["live_orders"].pop(coid, None)
        self.save()


def venue_bounds(ws=WS, span=900):
    """The shape `l5_pmus_realtime_observer.venue_window_bounds` returns.

    Supplied to every mechanism here because production supplies it: the
    placement band is measured from the venue's OWN confirmed open and an
    absent confirmation is a WAIT, so a harness without this would be
    modelling a mechanism that can never place.
    """
    import datetime as _dt
    start = _dt.datetime.fromtimestamp(ws, _dt.timezone.utc)
    return {"source": "VENUE", "start": start.isoformat(),
            "end": (start + _dt.timedelta(seconds=span)).isoformat(),
            "confirmed_by": "verify_alignment"}


class Clock:
    #: T+330, i.e. INSIDE the [T+300, T+450] placement band. It was T+60
    #: before the band existed, which is now correctly a WAIT.
    def __init__(self, t=float(WS + 330)):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


def mech(monkeypatch, *, store=None, transport=None, book=None, clock=None,
         enabled=True, window_bounds=venue_bounds):
    monkeypatch.setenv(M.MODE_ENV, "YES" if enabled else "NO")
    monkeypatch.delenv(M.PRICE_ENV, raising=False)
    monkeypatch.delenv(M.SIDE_ENV, raising=False)
    ck = clock or Clock()
    return M.Mechanism(
        store=store or Store(), transport=transport or Transport(),
        book=book or Book(), market=MARKET, clock=ck,
        window_start=lambda slug: WS, window_s=900.0, session_id="s-mech",
        window_bounds=window_bounds)


# ------------------------------------------------------------ the mode switch

@pytest.mark.parametrize("value,on", [
    ("YES", True), ("yes", True), ("YES ", True),   # house convention:
    ("Y", False), ("1", False), ("TRUE", False),    # strip().upper()=="YES",
    ("", False), ("NO", False), ("no", False)])     # as L5_WATCHDOG_CANCEL
def test_only_the_literal_yes_enables_the_mode(monkeypatch, value, on):
    monkeypatch.setenv(M.MODE_ENV, value)
    assert M.enabled() is on, value


def test_the_mode_is_off_when_unset(monkeypatch):
    monkeypatch.delenv(M.MODE_ENV, raising=False)
    assert M.enabled() is False


# -------------------------------------------------------- the worst-case bound

def test_the_worst_case_total_includes_the_taker_fee():
    """Fees are INSIDE the authorization, so the bound must carry them."""
    qty, px = D("0.01"), D("0.40")
    total = M.worst_case_total_usd(qty, px)
    assert total > qty * px, "a bound without fees is not a worst case"
    assert total == qty * px + D("0.01"), total   # ceiled to the cent


def test_the_fee_is_ceiled_to_the_cent_never_floored():
    """The venue bills whole cents, so a sub-cent fee still costs one."""
    total = M.worst_case_total_usd(D("0.01"), D("0.50"))
    assert total == D("0.01") * D("0.50") + D("0.01")


def test_a_zero_price_has_no_negative_fee():
    assert M.worst_case_total_usd(D("0.01"), D("0")) == D("0")


def test_the_bound_is_five_dollars():
    assert M.MAX_TOTAL_RISK_USD == D("5")


def test_an_order_over_the_authorization_is_refused(monkeypatch):
    """The gate is real: forced to a size that breaches it, it refuses."""
    m = mech(monkeypatch)
    monkeypatch.setattr(M, "MAX_TOTAL_RISK_USD", D("0.001"))
    rep = m.step()
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert "worst_case_total_within_authorization" in rep["failed_checks"]
    assert m.transport.submits == []


# ------------------------------------------------------------- the order shape

def test_the_order_is_post_only_minimum_size_and_cannot_cross(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert rep["submitted"] == 1
    assert len(t.submits) == 1
    sent = t.submits[0]
    assert sent["mode"] == "MAKER", "MAKER is what sets participateDontInitiate"
    assert sent["qty"] == t.quantity_increment == D("0.01")
    assert sent["price"] < D("0.41"), "it must not be able to cross the ask"
    assert sent["market"] == MARKET


def test_the_price_sits_below_the_bid_by_whole_ticks(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t)
    m.step()
    assert t.submits[0]["price"] == D("0.40") - D("0.01") * M.NONMARKETABLE_TICKS


def test_a_marketable_forced_price_never_reaches_the_wire(monkeypatch):
    """An owner-pinned price is still checked against the ASK, not trusted.

    RETARGETED: this is now a WAIT rather than a terminal refusal, because a
    crossed book is a "not yet" and the price is recomputed from a fresh book
    on every pass. What must never change -- and is what this test is really
    for -- is that no marketable order reaches the venue.
    """
    monkeypatch.setenv(M.MODE_ENV, "YES")
    monkeypatch.setenv(M.PRICE_ENV, "0.99")
    t = Transport()
    m = M.Mechanism(store=Store(), transport=t, book=Book(), market=MARKET,
                    clock=Clock(), window_start=lambda s: WS, window_s=900.0)
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert "price_is_nonmarketable" in rep["waiting_on"]
    assert t.submits == [], "a marketable order reached the venue"
    assert m.state == M.Mechanism.INIT, "it must re-evaluate, not give up"


def test_a_one_sided_book_waits_and_never_submits(monkeypatch):
    """RETARGETED with the wait/terminal split: a one-sided book is a "not
    yet", re-read on the next pass. Nothing may reach the wire on it."""
    t = Transport()
    m = mech(monkeypatch, transport=t, book=Book(two_sided=False))
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert "book_two_sided_authenticated" in rep["waiting_on"]
    assert t.submits == []
    assert m.state == M.Mechanism.INIT


def test_an_unarmed_transport_waits_and_never_submits(monkeypatch):
    """RETARGETED, and this is the 1800z bug itself.

    `transport_armed` is false for the first seconds of every window by
    construction -- the transport arms only after preflight passes, and
    preflight retries on a backoff. Treating it as terminal ended the
    authorized run on a condition that clears itself. It is now a WAIT.

    The invariant that must NOT move: nothing is submitted while unarmed.
    """
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert rep["waiting_on"] == ["transport_armed"]
    assert t.submits == [], "it submitted while unarmed"
    assert m.state == M.Mechanism.INIT
    assert m.finished is False


def test_existing_exposure_refuses(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t, store=Store(committed={MARKET: "1.00"}))
    rep = m.step()
    assert "book_is_flat_before_the_test" in rep["failed_checks"]
    assert t.submits == []


def test_another_live_order_refuses(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t,
             store=Store(live={"other": {"state": "LIVE"}}))
    rep = m.step()
    assert "no_other_live_order" in rep["failed_checks"]
    assert t.submits == []


# ------------------------------------------------------------- window timing

def test_too_little_window_left_refuses(monkeypatch):
    """The watchdog needs 300s plus slack INSIDE the window, or the venue
    expires the order at close and nothing has been measured."""
    t = Transport()
    m = mech(monkeypatch, transport=t,
             clock=Clock(WS + 900 - (M.MIN_WINDOW_REMAINING_S - 30)))
    rep = m.step()
    assert "window_has_room_for_the_watchdog" in rep["failed_checks"]
    assert t.submits == []


def test_enough_window_left_is_accepted(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t,
             clock=Clock(WS + 900 - (M.MIN_WINDOW_REMAINING_S + 30)))
    assert m.step()["submitted"] == 1


def test_the_self_cancel_backstop_sits_past_the_watchdog_bound():
    assert M.SELF_CANCEL_AFTER_S > M.WATCHDOG_AGE_S
    assert M.MIN_WINDOW_REMAINING_S > M.SELF_CANCEL_AFTER_S


# --------------------------------------------------------- the one-shot latch

def test_exactly_one_order_is_sent_however_many_times_it_is_stepped(
        monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t)
    ck = m.clock
    for _ in range(50):
        ck.advance(11.0)
        m.step()
    assert len(t.submits) == 1, t.submits


def test_a_durable_marker_blocks_a_second_run_after_a_restart(monkeypatch):
    """A process that crashed mid-test must not come back and place another."""
    store = Store()
    first = mech(monkeypatch, store=store, transport=Transport())
    first.step()
    # read through the accessor, not the storage shape: the slot now holds
    # {"v": 2, "runs": {"<experiment>|<market>": doc}} so markers are scoped
    # to the same identity claim_submission binds its O_EXCL lock to.
    assert first.marker()["stage"] in ("PLACED", "DONE")
    t2 = Transport()
    second = mech(monkeypatch, store=store, transport=t2)
    rep = second.step()
    assert "one_shot_not_already_used" in rep["failed_checks"]
    assert t2.submits == [], "a restart placed a second order"


def test_the_marker_is_written_before_the_submit(monkeypatch):
    """An order that reached the venue while our record did not is the one
    outcome worse than no test, so the marker precedes the POST."""
    store = Store()
    boom = RuntimeError("venue refused")
    m = mech(monkeypatch, store=store, transport=Transport(submit_raises=boom))
    m.step()
    assert m.marker()["stage"] == "SUBMITTING"
    t2 = Transport()
    again = mech(monkeypatch, store=store, transport=t2)
    assert "one_shot_not_already_used" in again.step()["failed_checks"]
    assert t2.submits == []


def test_an_unreadable_marker_counts_as_used(monkeypatch):
    class Hostile(Store):
        @property
        def state(self):
            raise RuntimeError("volume gone")

        @state.setter
        def state(self, v):
            pass

    m = mech(monkeypatch, store=Hostile(), transport=Transport())
    assert m.already_ran() is True


# ------------------------------------------------- ownership evidence first

def test_ownership_evidence_is_verified_not_assumed(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    ev = rep["ownership_evidence"]
    assert ev["venue_order_id"] == VID
    assert ev["ledger_owns_id"] is True, "the ledger must be read BACK"
    assert ev["registry_readable"] is True
    assert ev["registry_publishes_id"] is True, \
        "the watchdog reads the DOCUMENT, so the document must carry the id"
    assert ev["watchdog_cancellable"] is True
    assert m.state == M.Mechanism.RESTING


def test_the_evidence_reads_the_real_registry_renderer(monkeypatch):
    """Not a local reimplementation: the actual document the watchdog GETs."""
    t = Transport()
    m = mech(monkeypatch, transport=t)
    m.step()
    ev = m.report["ownership_evidence"]
    assert ev["registry_order_count"] == 1
    assert ev["registry_as_of_utc"], "the claim's own age must be published"


def test_an_ack_with_no_venue_id_is_cancelled_immediately(monkeypatch):
    """No id means nothing nameable to cancel later; do not leave it out."""
    t = Transport(vid=None)
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert rep["ownership_evidence"]["watchdog_cancellable"] is False
    assert rep["cancelled_by"] == "SELF_FALLBACK"
    assert rep["independent_cancellation_proven"] is False


def test_an_unpublished_id_is_withdrawn_rather_than_left_resting(monkeypatch):
    """If the registry would not show it, the watchdog would refuse it --
    correctly -- and it would just be an orphan we created on purpose."""
    store = Store()

    def no_attach(coid, vid):
        return None            # the id is never recorded, so never published

    store.attach_venue_id = no_attach
    t = Transport()
    m = mech(monkeypatch, store=store, transport=t)
    rep = m.step()
    assert rep["ownership_evidence"]["registry_publishes_id"] is False
    assert rep["ownership_evidence"]["watchdog_cancellable"] is False
    assert t.cancels == [VID], "an unverifiable order must be withdrawn NOW"
    assert rep["self_cancel_why"] == "OWNERSHIP_EVIDENCE_INCOMPLETE"
    assert rep["independent_cancellation_proven"] is False


# ------------------------------------------- resting, and who cancels it

def test_while_resting_the_mechanism_cancels_nothing(monkeypatch):
    """The watchdog is the subject; withdrawing early measures ourselves."""
    t = Transport()
    m = mech(monkeypatch, transport=t)
    m.step()
    ck = m.clock
    for _ in range(20):
        ck.advance(11.0)
        m.step()
    assert m.state == M.Mechanism.RESTING
    assert t.cancels == [], "it withdrew its own order while measuring"
    assert t.reads, "it must be re-reading the venue"


def test_a_terminal_reread_with_no_cancel_of_ours_is_the_watchdog(monkeypatch):
    t = Transport(rows=[
        {"state": "ORDER_STATE_OPEN", "leavesQuantity": "0.01"},
        {"state": "ORDER_STATE_CANCELED", "leavesQuantity": "0",
         "cumQuantity": "0"}])
    m = mech(monkeypatch, transport=t)
    m.step()
    ck = m.clock
    ck.advance(11.0); m.step()
    ck.advance(11.0); m.step()
    assert t.cancels == []
    assert m.report["cancelled_by"] == "WATCHDOG_INDEPENDENT"
    assert m.report["proof"] == "VENUE_TERMINAL_STATUS_REREAD"
    assert m.report["venue_terminal"] is True
    assert m.report["independent_cancellation_proven"] is True
    assert m.state == M.Mechanism.DONE


def test_proof_requires_the_venue_row_not_the_ack(monkeypatch):
    """A venue that keeps reporting the order open never yields proof."""
    t = Transport()               # always OPEN
    m = mech(monkeypatch, transport=t)
    m.step()
    ck = m.clock
    for _ in range(10):
        ck.advance(11.0)
        m.step()
    assert m.report.get("independent_cancellation_proven") in (None, False)


def test_the_self_cancel_fallback_is_never_proof(monkeypatch):
    t = Transport()               # the watchdog never acts
    m = mech(monkeypatch, transport=t)
    m.step()
    ck = m.clock
    ck.advance(M.SELF_CANCEL_AFTER_S + 1.0)
    m.step()
    assert t.cancels == [VID], "it must not strand a real order"
    assert m.report["cancelled_by"] == "SELF_FALLBACK"
    assert m.report["independent_cancellation_proven"] is False
    assert "NOT PROOF" in m.report["proof"]
    assert m.report["self_cancel_why"] == "WATCHDOG_DID_NOT_CANCEL_IN_TIME"
    assert t.cancel_args == [(VID, MARKET)], (
        "the fallback must name the mechanism's own market: a cancel with an "
        "empty body is refused HTTP 400 and the order stays resting")
    assert m.report["self_cancel_error"] is None
    assert m.report["self_cancel_detail"] is None


def test_a_refused_self_cancel_reports_the_sanitized_venue_detail(monkeypatch):
    """`str(HTTPError)` is only "HTTP Error 400: Bad Request", so the error
    line alone could not distinguish a schema rejection from anything else.
    That is how the 2026-10-18 cancel 400 survived a whole authorized window
    recorded as nothing but "HTTPError"."""
    detail = {"error": "HTTPError", "status": 400,
              "body": '{"message":"marketSlug is required"}',
              "venue_order_id": VID, "market_slug": MARKET,
              "endpoint": "/v1/order/{id}/cancel", "method": "POST"}
    t = Transport(cancel_raises=RuntimeError("HTTP Error 400: Bad Request"),
                  last_cancel_error=detail)
    m = mech(monkeypatch, transport=t)
    m.step()
    m.clock.advance(M.SELF_CANCEL_AFTER_S + 1.0)
    m.step()                                   # must not raise
    assert "RuntimeError" in m.report["self_cancel_error"]
    assert m.report["self_cancel_detail"] == detail
    assert m.report["independent_cancellation_proven"] is False


def test_a_fill_is_reported_as_a_fill_not_as_a_cancellation(monkeypatch):
    """A post-only order cannot cross, but a MAKER fill while resting can
    happen and must never be dressed up as a successful cancellation."""
    t = Transport(rows=[{"state": "ORDER_STATE_FILLED", "leavesQuantity": "0",
                         "cumQuantity": "0.01"}])
    m = mech(monkeypatch, transport=t)
    m.step()
    m.clock.advance(11.0)
    m.step()
    assert m.report["cancelled_by"] == "VENUE_FILL"
    assert m.report["independent_cancellation_proven"] is False


def test_a_reread_error_does_not_end_the_test_or_raise(monkeypatch):
    class Flaky(Transport):
        def order(self, vid):
            raise OSError("transient")

    t = Flaky()
    m = mech(monkeypatch, transport=t)
    m.step()
    m.clock.advance(11.0)
    rep = m.step()
    assert m.state == M.Mechanism.RESTING
    assert rep["last_venue_row"]["read_error"] == "OSError"


def test_terminal_detection_matches_the_watchdogs_reading():
    """Two processes must not disagree about what terminal means."""
    from src import l5_watchdog as W
    rows = [{"leavesQuantity": "0"}, {"leavesQuantity": "0.01"},
            {"state": "ORDER_STATE_CANCELED"}, {"state": "ORDER_STATE_FILLED"},
            {"state": "ORDER_STATE_OPEN"}, {"state": "ORDER_STATE_EXPIRED"},
            {}]
    for r in rows:
        assert M.is_terminal(r) == W.is_terminal(r), r


# ------------------------------------------------------- what it cannot do

def test_the_module_holds_no_halt_clearing_capability():
    """It must not be able to lift the latch its own order will trigger.

    Checked on the parsed module rather than on substrings: an earlier
    version of this test grepped for "clear" and tripped on the word
    "clearing" in a comment, which is exactly the kind of false signal that
    teaches people to delete tests.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(M))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name)
    for forbidden in ("l5_halt_gate", "l5_halt_reset", "l5_halt_server",
                      "l5_watchdog"):
        assert not any(forbidden in m for m in imported), forbidden
    # and no call whose name could lift a latch
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for forbidden in ("clear", "clear_halt", "heartbeat_no_halt",
                      "assert_halt", "disarm", "arm"):
        assert forbidden not in called, forbidden


def test_the_module_has_no_account_wide_cancel():
    import inspect
    src = inspect.getsource(M)
    for forbidden in ("orders/open/cancel", "cancel_all", "slugs"):
        assert forbidden not in src, forbidden


def test_the_terminal_store_update_only_happens_on_a_terminal_row(monkeypatch):
    store = Store()
    t = Transport()
    m = mech(monkeypatch, store=store, transport=t)
    m.step()
    assert store.live_order_count() == 1
    m.clock.advance(11.0)
    m.step()
    assert store.live_order_count() == 1, "still resting, still live"


# ------------------------------------------------------- the submission claim

def test_the_claim_is_spent_and_reported(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    claim = rep["submission_claim"]
    assert claim["market"] == MARKET
    assert claim["sid"], "the claim must name the single-use id it consumed"
    assert claim["experiment"], "a claim not bound to an authorization is none"
    assert "NEVER reset automatically" in claim["labels"]


def test_a_spent_claim_refuses_even_when_the_store_marker_is_gone(monkeypatch):
    """The CLAIM is the real backstop, not the store marker.

    The marker is a key in a JSON file on a volume; the claim is an
    O_CREAT|O_EXCL lock fsynced before it returns. If the ledger file were
    restored from a backup, or the marker lost, the claim must still stop a
    second order under the same authorization -- which is the whole guarantee
    that one authorization buys exactly one order.
    """
    store = Store()
    first = mech(monkeypatch, store=store, transport=Transport())
    assert first.step()["submitted"] == 1
    # Lose the marker AND the live-order row -- i.e. the ledger state is
    # gone as if restored from an older copy -- while keeping the SAME
    # experiment id and ledger file. Every in-store precondition now passes,
    # so the claim is the only gate left standing, which is the point.
    store.state.pop(M.MARKER_KEY, None)
    store.state["live_orders"].clear()
    t2 = Transport()
    second = mech(monkeypatch, store=store, transport=t2)
    # THE RACE THE ATOMIC CLAIM EXISTS FOR, forced deliberately. Preflight
    # now also probes the lock (`submission_claim_not_already_spent`) and
    # would refuse this earlier by name; pinning the probe to UNUSED keeps
    # this test pointed at the gate it is about -- the O_EXCL claim AT THE
    # WIRE, which is the only thing that can decide a lock taken between
    # preflight and the POST, by a peer or after a restart.
    second.submission_claim_state = lambda: {
        "state": "UNUSED", "why": "PINNED_BY_THE_TEST_TO_FORCE_THE_RACE"}
    rep = second.step()
    assert str(rep["refused"]).startswith("SUBMISSION_CLAIM:")
    assert "AUTHORIZATION_ALREADY_USED" in rep["refused"]
    assert t2.submits == [], "a second order was sent on a spent claim"


def test_an_unnamed_experiment_refuses_rather_than_defaults(monkeypatch):
    """Unset is never a default: a claim must bind to an authorization."""
    monkeypatch.delenv("L5_EXPERIMENT_ID", raising=False)
    from src import l5_live_authorization as AUTH
    monkeypatch.setattr(AUTH, "experiment_id", lambda: "")
    t = Transport()
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert "SUBMISSION_CLAIM" in str(rep["refused"])
    assert t.submits == []


def test_a_claim_refusal_releases_the_order_reservation(monkeypatch):
    """Nothing was transmitted, so the reservation must not keep the slot."""
    store = Store()
    first = mech(monkeypatch, store=store, transport=Transport())
    first.step()
    live_after_first = store.live_order_count()
    store.state.pop(M.MARKER_KEY, None)
    store.state["live_orders"].clear()
    second = mech(monkeypatch, store=store, transport=Transport())
    # as above: the reservation is only created on the way to the wire, so
    # the preflight probe is pinned UNUSED to reach the path being tested.
    second.submission_claim_state = lambda: {
        "state": "UNUSED", "why": "PINNED_BY_THE_TEST_TO_FORCE_THE_RACE"}
    rep = second.step()
    assert "SUBMISSION_CLAIM" in str(rep["refused"])
    assert store.live_order_count() == 0, \
        "the refused attempt left a phantom live order behind"


# ================================================================
# THE WAIT/TERMINAL SPLIT -- the 1800z repair
# ================================================================

def armed_later(t, after):
    """A transport that arms on the Nth call, like preflight passing."""
    calls = {"n": 0}

    class T(type(t)):
        pass

    t.armed = False
    orig = t.submit

    def tick():
        calls["n"] += 1
        if calls["n"] >= after:
            t.armed = True
    t._tick = tick
    return t


def test_the_wait_set_is_exactly_the_transient_conditions():
    """Pinned explicitly: a future edit that moves a SAFETY gate into the
    wait set has to change this line and explain itself."""
    assert M.WAIT_CHECKS == frozenset({
        "transport_armed",
        "book_two_sided_authenticated",
        "price_readable", "price_positive", "price_below_one",
        "price_is_nonmarketable",
        # ADDED WITH THE PLACEMENT BAND, and here is the explanation this
        # test demands. Being EARLY is transient by construction -- it is
        # cured by the clock advancing -- so refusing on it would kill a run
        # that is about to be perfectly valid, which is what a boot at
        # T+1:00 is. It is not a safety gate waived: the order still cannot
        # be sent while it fails, and the LATE edge
        # (`placement_band_not_expired`) is terminal and NOT in this set.
        "placement_band_reached",
    })


def test_no_safety_gate_is_in_the_wait_set():
    """These must NEVER be waited out -- each one stops the run dead."""
    for terminal in ("one_shot_not_already_used", "mode_enabled",
                     "window_has_room_for_the_watchdog",
                     "placement_band_not_expired",
                     "authorization_window_open", "halt_not_engaged",
                     "book_is_flat_before_the_test", "no_other_live_order",
                     "qty_is_venue_minimum", "post_only",
                     "worst_case_total_within_authorization"):
        assert terminal not in M.WAIT_CHECKS, terminal


def test_it_submits_on_a_later_pass_once_arming_completes(monkeypatch):
    """The whole point of the repair: the run survives to the armed pass."""
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    ck = m.clock

    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert t.submits == []

    for _ in range(3):                      # still arming
        ck.advance(3.0)
        m.step()
    assert t.submits == []
    assert m.state == M.Mechanism.INIT

    t.armed = True                          # preflight passed
    ck.advance(3.0)
    rep = m.step()
    assert rep["submitted"] == 1, "it never recovered after arming"
    assert t.submits and t.submits[0]["qty"] == D("0.01")
    assert m.state == M.Mechanism.RESTING


def test_every_gate_is_re_evaluated_on_the_submitting_pass(monkeypatch):
    """Waiting must not carry a stale verdict forward.

    The book goes BAD while waiting; the pass that would otherwise submit
    must see the new book and keep waiting instead.
    """
    t = Transport(armed=False)
    book = Book()
    m = mech(monkeypatch, transport=t, book=book)
    m.step()
    t.armed = True
    book._two = False                       # book dies before the submit
    m.clock.advance(3.0)
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    assert "book_two_sided_authenticated" in rep["waiting_on"]
    assert t.submits == []


def test_the_price_is_recomputed_from_the_fresh_book_not_carried_forward(
        monkeypatch):
    t = Transport(armed=False)
    book = Book(bid="0.40", ask="0.41")
    m = mech(monkeypatch, transport=t, book=book)
    m.step()
    book._bid, book._ask = "0.70", "0.71"   # the book moved while waiting
    t.armed = True
    m.clock.advance(3.0)
    m.step()
    assert t.submits[0]["price"] == D("0.70") - D("0.01") * M.NONMARKETABLE_TICKS


# ------------------------------------------------- the wait is bounded

def test_the_window_draining_below_the_bound_ends_the_wait(monkeypatch):
    """No timer needed: the window check is TERMINAL, so an unarmed run
    stops itself rather than holding a lifted lock open."""
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    # jump to inside the 450s margin
    m.clock.t = float(WS + 900 - (M.MIN_WINDOW_REMAINING_S - 20))
    rep = m.step()
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert "window_has_room_for_the_watchdog" in rep["failed_checks"]
    assert m.finished is True
    assert t.submits == []


def test_a_terminal_failure_refuses_even_while_a_wait_is_outstanding(
        monkeypatch):
    """An expired authorization is not improved by the book recovering."""
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER", "2020-01-01T00:00:00+00:00")
    rep = m.step()
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert "authorization_window_open" in rep["failed_checks"]
    assert t.submits == []


# ------------------------------------------------- the two new terminal gates

@pytest.mark.parametrize("value,ok", [
    ("2099-01-01T00:00:00+00:00", True),
    ("2020-01-01T00:00:00+00:00", False),
    ("garbage", False),
])
def test_the_authorization_window_gates_the_submit(monkeypatch, value, ok):
    monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER", value)
    t = Transport()
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert (rep.get("submitted") == 1) is ok, value
    assert (t.submits != []) is ok


def test_a_halt_engaged_mid_wait_stops_the_run(monkeypatch):
    """Waiting happens with the shadow lock LIFTED, so a halt arriving
    during the wait has to end it."""
    halt = {"on": False}
    t = Transport(armed=False)
    m = M.Mechanism(store=Store(), transport=t, book=Book(), market=MARKET,
                    clock=Clock(), window_start=lambda s: WS, window_s=900.0,
                    halted_fn=lambda: halt["on"])
    monkeypatch.setenv(M.MODE_ENV, "YES")
    assert m.step()["LABEL"] == "L5_MECH_WAIT"
    halt["on"] = True
    rep = m.step()
    assert "halt_not_engaged" in rep["failed_checks"]
    assert t.submits == []
    assert m.finished is True


def test_an_unreadable_halt_counts_as_halted(monkeypatch):
    def boom():
        raise RuntimeError("halt gate unreachable")

    t = Transport()
    m = M.Mechanism(store=Store(), transport=t, book=Book(), market=MARKET,
                    clock=Clock(), window_start=lambda s: WS, window_s=900.0,
                    halted_fn=boom)
    monkeypatch.setenv(M.MODE_ENV, "YES")
    rep = m.step()
    assert "halt_not_engaged" in rep["failed_checks"]
    assert t.submits == []


def test_the_module_still_cannot_touch_the_latch_it_now_reads():
    """The halt arrives as an injected predicate, NOT an import."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(M))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                imported.add(a.name)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in ("l5_halt_gate", "l5_halt_reset", "l5_halt_server"):
        assert not any(forbidden in m for m in imported), forbidden


# ------------------------------------------------------ wait does not spam

def test_the_wait_is_logged_on_change_not_on_every_pass(monkeypatch):
    seen = []
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    m._emit = lambda k, d: seen.append(k)
    for _ in range(12):
        m.clock.advance(3.0)
        m.step()
    assert seen.count("L5_MECH_WAIT") == 1, seen


def test_the_one_shot_is_not_consumed_by_waiting(monkeypatch):
    """A wait must leave the marker and the claim untouched, or a retry in
    the next window would find the experiment already spent."""
    store = Store()
    t = Transport(armed=False)
    m = mech(monkeypatch, store=store, transport=t)
    for _ in range(5):
        m.clock.advance(3.0)
        m.step()
    assert M.MARKER_KEY not in store.state
    t.armed = True
    m.clock.advance(3.0)
    assert m.step()["submitted"] == 1


def test_an_unknown_worst_case_total_waits_and_cannot_submit(monkeypatch):
    """A dead book makes the total UNKNOWN. That is an absent price, not a
    breach -- but it must still be impossible to submit on it."""
    t = Transport(armed=True)
    m = mech(monkeypatch, transport=t, book=Book(two_sided=False))
    rep = m.step()
    assert rep["LABEL"] == "L5_MECH_WAIT"
    for name in ("price_readable", "book_two_sided_authenticated"):
        assert name in rep["waiting_on"], name
    assert "worst_case_total_within_authorization" not in rep["waiting_on"]
    assert t.submits == [], "it submitted with no readable price"


def test_a_known_total_over_the_bound_is_still_terminal(monkeypatch):
    """The relaxation above must not weaken the real bound."""
    m = mech(monkeypatch)
    monkeypatch.setattr(M, "MAX_TOTAL_RISK_USD", D("0.001"))
    rep = m.step()
    assert rep["refused"] == "MECHANISM_PRECONDITIONS_NOT_MET"
    assert "worst_case_total_within_authorization" in rep["failed_checks"]
    assert m.finished is True
    assert m.transport.submits == []
