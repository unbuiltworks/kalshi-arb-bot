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

    def __init__(self, *, armed=True, vid=VID, rows=None, submit_raises=None):
        self.armed = armed
        self._vid = vid
        self.submits = []
        self.cancels = []
        self.reads = []
        self._rows = list(rows or [])
        self._submit_raises = submit_raises

    def submit(self, intent):
        self.submits.append(dict(intent))
        if self._submit_raises:
            raise self._submit_raises
        return {"order": {"id": self._vid}} if self._vid else {"order": {}}

    def cancel(self, vid):
        self.cancels.append(vid)
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


class Clock:
    def __init__(self, t=float(WS + 60)):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


def mech(monkeypatch, *, store=None, transport=None, book=None, clock=None,
         enabled=True):
    monkeypatch.setenv(M.MODE_ENV, "YES" if enabled else "NO")
    monkeypatch.delenv(M.PRICE_ENV, raising=False)
    monkeypatch.delenv(M.SIDE_ENV, raising=False)
    ck = clock or Clock()
    return M.Mechanism(
        store=store or Store(), transport=transport or Transport(),
        book=book or Book(), market=MARKET, clock=ck,
        window_start=lambda slug: WS, window_s=900.0, session_id="s-mech")


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


def test_a_marketable_forced_price_is_refused(monkeypatch):
    """An owner-pinned price is still checked against the ASK, not trusted."""
    monkeypatch.setenv(M.MODE_ENV, "YES")
    monkeypatch.setenv(M.PRICE_ENV, "0.99")
    t = Transport()
    m = M.Mechanism(store=Store(), transport=t, book=Book(), market=MARKET,
                    clock=Clock(), window_start=lambda s: WS, window_s=900.0)
    rep = m.step()
    assert "price_is_nonmarketable" in rep["failed_checks"]
    assert t.submits == []


def test_a_one_sided_book_refuses(monkeypatch):
    t = Transport()
    m = mech(monkeypatch, transport=t, book=Book(two_sided=False))
    rep = m.step()
    assert "book_two_sided_authenticated" in rep["failed_checks"]
    assert t.submits == []


def test_an_unarmed_transport_refuses_and_no_lock_is_lifted(monkeypatch):
    t = Transport(armed=False)
    m = mech(monkeypatch, transport=t)
    rep = m.step()
    assert "transport_armed" in rep["failed_checks"]
    assert t.submits == []


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
    assert store.state[M.MARKER_KEY]["stage"] in ("PLACED", "DONE")
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
    assert store.state[M.MARKER_KEY]["stage"] == "SUBMITTING"
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
    rep = second.step()
    assert "SUBMISSION_CLAIM" in str(rep["refused"])
    assert store.live_order_count() == 0, \
        "the refused attempt left a phantom live order behind"
