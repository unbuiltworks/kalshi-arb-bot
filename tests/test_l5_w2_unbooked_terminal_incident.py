"""The 1330z incident: one failed progress read cost 6.3 minutes of a window.

LIVE, 2026-11-02, window `cpc-btc-updown-15m-2026-11-02-1330z`, during an
owner-authorized four-window campaign.

    13:32:34.951  DOWN 2.2 fills; the window's pair is matched, residual 0
    13:33:00.326  L5_LIVE_LIFECYCLE {"coid":"l5f-841807f1566e3ada0265df68",
                  "event":"PROGRESS_ERROR","error":"HTTPError",
                  "market_identity":"MISSING"}
    13:33:08      that order ACKs ORDER_STATE_NEW, venue id DFN6HPSG12VX
    13:33 - 13:39 every submission refused:
                  "controller_open:PAIR_UP:BOOK_UNCERTAIN: ... unbooked
                   terminal orders ['l5f-841807f1566e3ada0265d...'" x7
                  "TWO_SIDED_PROBE:PAIR_UP:BOOK_UNCERTAIN: ..."          x2
    13:39:22.608  first L5_CONTROLLER_OPEN_TRIGGER after the block
    13:39:36      TWO_SIDED_PROBE:TOO_LATE_TO_WITHDRAW -- the book is now
                  0.9800/0.9900 and no two-sided pair exists at any price
    13:40:17      PAST_ENTRY_CUTOFF. The window is over.

SIX POINT THREE MINUTES of a fifteen-minute window, and the recurrence in
window 1400z ("TWO_SIDED_PROBE:PAIR_DOWN:BOOK_UNCERTAIN") proves it is not
a one-off.

WHAT IS *NOT* WRONG, because the fix must not touch any of it. The
BOOK_UNCERTAIN block is CORRECT: an order whose terminal state we never
booked may have filled, and trading on a book we cannot describe is how a
cost basis is lost -- `test_l5_uncertainty_recovery` already pins that from
the 2026-09-26 incident and those guarantees stay. ALREADY_MATCHED,
DECIDE_COOLDOWN, the entry cutoff and the per-window allocation were all
measured and cleared: the engine correctly read residual 0 as flat
(`decision_role: "FLAT_OPEN"` throughout) and was trying to open.

WHAT IS WRONG IS THE RECOVERY LATENCY, and it is one line's worth of
omission at `l5_pmus_live_runner.poll_orders`:

    try:
        row = t.order(str(vid))
    except BaseException as exc:
        self.store.mark_uncertain(...)
        out.append({... "event": "PROGRESS_ERROR" ...})
        continue            # <-- the whole defect: no retry of THIS id

A single transient failure latches the book and the loop moves on. The
order is not re-asked until the ordinary per-order poll interval comes
round (`ORDER_POLL_S` = 6.0s, stretched by order count), and if it has
meanwhile left the venue's open list it lands in `pending_terminal`, where
`resolve_pending_terminal` clears at most ONE order per `PENDING_RESOLVE_S`
(3.0s) and only when it carries a venue id. `reconcile` then RE-RAISES
uncertainty on every pass while anything is parked
(`l5_live_execution.py:4659`), so the block renews itself.

These tests reproduce that deterministically from the real identifiers and
measure the latency. They are written to FAIL on the unfixed tree.
"""
from __future__ import annotations

import collections
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

#: the real incident's identifiers, so the reproduction is this incident
COID = "l5f-841807f1566e3ada0265df68"
VENUE_ID = "DFN6HPSG12VX"
MARKET = "cpc-btc-updown-15m-2026-11-02-1330z"
SIDE = "DOWN"

#: 13:33:00.326 -> 13:39:22.608, measured from the production tape
OBSERVED_BLOCK_S = 382.3


class HTTPError(RuntimeError):
    """Named so `type(exc).__name__` matches the production tape."""


class FlakyTransport:
    """Fails `order()` a bounded number of times, then answers truthfully.

    Counts per-id calls so a test can assert whether the failing order was
    re-asked at all, which is the defect.
    """

    def __init__(self, fail_times: int = 1, *, cum_qty: str = "0",
                 state: str = "ORDER_STATE_CANCELED"):
        self.market_slug = MARKET
        self.armed = True
        self.fail_times = int(fail_times)
        self.calls: collections.Counter = collections.Counter()
        self._cum, self._state = cum_qty, state

    def order(self, vid: str):
        self.calls[str(vid)] += 1
        if self.calls[str(vid)] <= self.fail_times:
            raise HTTPError("502")
        return {"orderId": str(vid), "status": self._state,
                "cumQuantity": self._cum, "avgPx": "0",
                "commission": "0", "leavesQuantity": "0"}

    # the runner touches these on some paths
    def positions(self):
        return []

    def open_orders(self):
        return []

    def settlement(self, slug):
        return {"market": slug, "state": "", "settlement_px": None,
                "terminal": False}


def make_runner(tmp, transport):
    r = R.Runner.__new__(R.Runner)
    r.store = LX.InventoryStore(os.path.join(tmp, "inv.json"))
    r.limits = LX.SafetyLimits(**R.PROBE_LIMITS)
    r.transport = transport
    r.reconciled = True
    r.last_reconcile_t = 0.0
    r.last_progress_t = 0.0
    r.last_order_poll = {}
    r.last_pending_resolve = {}
    r.last_pending_resolve_t = 0.0
    r.now_t = 1000.0
    r.stats = collections.Counter()
    r.win = collections.Counter()
    r.emitted = []
    r.lifecycle = lambda out: r.emitted.extend(out)
    return r


def _park_live_order(r):
    """The order as it stood at 13:33:08: ACKed, resting, venue id known."""
    live = r.store.state.setdefault("live_orders", {})
    live[COID] = {"market": MARKET, "side": SIDE, "price": "0.64",
                  "qty": "2.2", "remaining": "2.2",
                  "venue_order_id": VENUE_ID, "client_order_id": COID}
    r.store.save()


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _refused(r):
    """Would a submission be refused right now, and why."""
    return r.limits.refuse(
        price=D("0.40"), qty=D("2"), market=MARKET, committed_by_market={},
        residual_after=D(0), realized_today=D(0), open_markets=[MARKET],
        outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=r.store.live_order_count(), risk_by_market={},
        orders_uncertain=r.store.orders_uncertain())


# ===================================================== the incident itself

def test_one_failed_progress_read_latches_the_book(tmp):
    """13:33:00.326, exactly. This part is CORRECT and must keep working."""
    t = FlakyTransport(fail_times=1)
    r = make_runner(tmp, t)
    _park_live_order(r)

    out = r.poll_orders()

    assert r.store.orders_uncertain() is not None
    assert "HTTPError" in r.store.orders_uncertain()
    assert COID in r.store.orders_uncertain()
    assert any(e.get("event") == "PROGRESS_ERROR" for e in out), out
    # and while latched, nothing may be submitted
    assert _refused(r) == "ORDER_STATE_UNCERTAIN"


def test_the_failing_order_is_not_re_asked_in_the_same_pass(tmp):
    """THE DEFECT. `continue` moves on; the id is not retried.

    One call, one failure, and the loop is done with it -- so recovery
    waits for a later interval instead of for the venue to answer.
    """
    t = FlakyTransport(fail_times=1)
    r = make_runner(tmp, t)
    _park_live_order(r)

    r.poll_orders()

    assert t.calls[VENUE_ID] > 1, (
        "poll_orders asked the venue once, gave up, and latched the book. "
        "A transient failure must be retried against THIS order id before "
        "the session pays for it.")


def test_the_order_is_resolved_within_one_tick_of_a_healthy_venue(tmp):
    """Even when the venue recovers IMMEDIATELY, we do not.

    WHAT RECOVERY MEANS HERE, precisely: the failing ORDER is re-read and
    booked inside the same tick. It does NOT mean the poll clears
    `orders_uncertain` -- only an authoritative reconciliation may do that,
    which `test_a_succeeding_get_alone_never_clears_uncertainty` pins and
    this patch does not touch. The defect is that the ORDER itself waits
    for `ORDER_POLL_S` before anyone asks again, so the reconcile that
    could clear the book has nothing to clear.
    """
    t = FlakyTransport(fail_times=1)
    r = make_runner(tmp, t)
    _park_live_order(r)

    r.poll_orders()                      # one tick, one transient failure

    assert t.calls[VENUE_ID] >= 2, (
        "the venue answered on the retry but was never asked again in "
        "this tick; recovery is floored by ORDER_POLL_S (%.1fs) rather "
        "than by the outage." % R.ORDER_POLL_S)


def test_a_sustained_outage_still_fails_closed(tmp):
    """The fix must NOT trade through a venue that will not answer."""
    t = FlakyTransport(fail_times=10_000)
    r = make_runner(tmp, t)
    _park_live_order(r)
    for _ in range(5):
        r.now_t += R.ORDER_POLL_S + 1
        r.poll_orders()
    assert r.store.orders_uncertain() is not None
    assert _refused(r) == "ORDER_STATE_UNCERTAIN"


def test_retries_are_bounded_not_unlimited(tmp):
    """A retry loop that never gives up is a stall, not a recovery."""
    t = FlakyTransport(fail_times=10_000)
    r = make_runner(tmp, t)
    _park_live_order(r)
    r.poll_orders()
    assert t.calls[VENUE_ID] <= 8, (
        "unbounded retry inside one tick: %d calls" % t.calls[VENUE_ID])


def test_recovery_books_the_real_fill_and_does_not_invent_one(tmp):
    """If the order actually filled while we could not see it, the fill is
    booked from the venue's own row -- not guessed, not dropped."""
    t = FlakyTransport(fail_times=1, cum_qty="2.2",
                       state="ORDER_STATE_FILLED")
    r = make_runner(tmp, t)
    _park_live_order(r)
    r.poll_orders()
    assert r.store.position(MARKET, SIDE) == D("2.2"), (
        "the fill the venue reports must be booked exactly once, from the "
        "venue's own row, inside the tick that saw the failure")
    # and uncertainty is STILL set: only a reconcile may clear it, which is
    # the 2026-09-26 invariant and is deliberately left alone.
    assert r.store.orders_uncertain() is not None


def test_recovery_is_idempotent_across_repeated_reads(tmp):
    """Duplicate events must not double-book."""
    t = FlakyTransport(fail_times=1, cum_qty="2.2",
                       state="ORDER_STATE_FILLED")
    r = make_runner(tmp, t)
    _park_live_order(r)
    for _ in range(12):
        r.poll_orders()
        r.now_t += R.ORDER_POLL_S + 1
    assert r.store.position(MARKET, SIDE) == D("2.2")


def test_recovery_submits_nothing(tmp):
    """The whole patch is a READ path. It must never place an order."""
    t = FlakyTransport(fail_times=2, cum_qty="0")
    r = make_runner(tmp, t)
    _park_live_order(r)
    assert not hasattr(t, "place"), "the fake has no submit path at all"
    for _ in range(4):
        r.poll_orders()
        r.now_t += 1.0
    # the ledger still owns exactly the one order it started with
    assert set(r.store.live_orders()) <= {COID}


def test_the_measured_incident_cost(tmp):
    """Records the economic size of the defect: 382.3s of a 900s window."""
    assert OBSERVED_BLOCK_S / 900.0 > 0.42
