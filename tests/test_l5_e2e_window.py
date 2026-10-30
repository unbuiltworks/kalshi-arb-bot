"""ONE scripted 15-minute BTC15m window, runner-to-executor, end to end.

WHY THIS FILE EXISTS. Every blocker this programme has hit was an
INTEGRATION gap, not a unit bug: the empty book the planner was handed, the
deadline derived from a window start instead of an end, the plan re-sized on
every tick, the recorder pointed at a snapshot instead of the mirror. Each
one passed its own unit tests and each one cost a deployment to find. So this
drives the REAL `FaithfulExecutor.handle` and the REAL unbound
`Runner.maybe_*` lifecycle methods against ONE shared clock, store and
transport, under the PRODUCTION configuration, for a whole window.

WHAT IT IS NOT. Not a venue simulator and not a profitability model. It
answers one question -- does the money path hold together across a window? --
and it answers it deterministically: no network, no real sockets, no threads,
no live orders, a clock that only moves when a test moves it.

PRODUCTION CONFIGURATION, NOT A CONVENIENT ONE. `L5_TS_CANCEL_MARGIN_S=300`,
`L5_TS_DEPTH_TICKS=2`, `max_live_orders=1`, `max_cash_deployed_usd=4.65`,
`max_usd_per_market=5`, `max_total_exposure_usd=5`,
`daily_realized_loss_halt_usd=5`, `NO_RECYCLING`. A harness that relaxes any
of these is testing a system we do not run.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_faithful_translation as FT
from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates

SLUG = "cpc-btc-updown-15m-2026-10-20-0400z"
WS = 1_792_468_800.0                      # the window START, 04:00:00Z
WE = WS + TS.WINDOW_S                     # ... and its END, 04:15:00Z

#: the production margin, so the terminal is WS+600 and not WS+720
MARGIN_S = 300.0
TERMINAL = WE - MARGIN_S                  # WS + 600


class Clock:
    """A clock that moves only when a test moves it."""

    def __init__(self, t: float = WS) -> None:
        self.t = float(t)

    def at(self, offset_s: float) -> "Clock":
        self.t = WS + float(offset_s)
        return self

    def utc(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.t, tz=dt.timezone.utc)


class Book:
    """The authenticated book mirror, scripted. `snapshot` is what the
    executor's resolver calls, and None is a DISCONNECT -- the real AuthBook
    returns None unless it is connected AND two-sided."""

    def __init__(self, bid="0.42", ask="0.44", bid_sh="900", ask_sh="900",
                 age="0.05") -> None:
        self.set(bid, ask, bid_sh, ask_sh, age)
        self.connected = True
        self.reads = 0

    def set(self, bid, ask, bid_sh="900", ask_sh="900", age="0.05") -> "Book":
        self._b = {"bid": bid, "ask": ask, "bid_shares": bid_sh,
                   "ask_shares": ask_sh, "stream_age_s": age}
        return self

    def disconnect(self) -> "Book":
        self.connected = False
        return self

    def reconnect(self) -> "Book":
        self.connected = True
        return self

    def snapshot(self, market=None):
        self.reads += 1
        return dict(self._b) if self.connected else None


class Venue:
    """The transport. Fills are SCRIPTED per client order id, so a test says
    what the venue did rather than a flag deciding it globally."""

    name = "E2E"
    quantity_increment = D("0.01")
    tick_size = D("0.01")

    def __init__(self, armed=True) -> None:
        self.armed = armed
        self.market_slug = SLUG
        self.sent, self.cancelled = [], []
        self.retired = set()
        self.acked = {}
        self.fill_plan = {}          # coid -> qty string filled on the ACK
        self.by_oid = {}
        self.next_fill = None        # what the NEXT submit fills, then clears

    def fills(self, coid: str, qty) -> "Venue":
        self.fill_plan[coid] = str(qty)
        return self

    def fill_next(self, qty="*") -> "Venue":
        """The next order to arrive fills this much on its ACK. A test names
        the fill before it knows the client order id, which is derived. "*"
        means the WHOLE intent -- a venue can never fill more than it was
        asked for, and a harness that does is not modelling anything."""
        self.next_fill = str(qty)
        return self

    def submit(self, intent):
        coid = intent["client_order_id"]
        if any(s["client_order_id"] == coid for s in self.sent):
            raise RuntimeError("DUPLICATE_CLIENT_ORDER_ID")
        self.sent.append(dict(intent))
        oid = "v%d" % len(self.sent)
        self.by_oid[oid] = dict(intent)
        got = self.fill_plan.get(coid)
        if got is None:
            got, self.next_fill = (self.next_fill or "0"), None
        if got == "*":
            got = str(intent["qty"])
        if D(got) > D(str(intent["qty"])):
            raise AssertionError(
                "the harness tried to fill %s of a %s order" % (
                    got, intent["qty"]))
        self.acked[oid] = got
        return {"order_id": oid, "filled_qty": got,
                "fill_price": str(intent["price"])}

    def cancel(self, order_id, market_slug=None):
        self.cancelled.append(order_id)
        self.retired.add(order_id)
        return {"cancelled": order_id}

    def order(self, order_id):
        """What `/v1/order` says. A venue answers for a LIVE order too, which
        is the whole point of re-reading: the ACK is a receipt, not a final
        state. A double that only answers for retired ids makes every poll of
        a resting order read as an error and marks the book uncertain."""
        if order_id not in self.by_oid:
            raise KeyError(order_id)
        cum = self.acked.get(order_id, "0")
        if order_id not in self.retired:
            qty = D(str(self.by_oid[order_id]["qty"]))
            leaves = qty - D(cum)
            return {"id": order_id,
                    "state": ("ORDER_STATE_PARTIALLY_FILLED" if D(cum) > 0
                              else "ORDER_STATE_NEW"),
                    "quantity": str(qty),
                    "cumQuantity": str(cum), "leavesQuantity": str(leaves),
                    "avgPx": {"value": "0", "currency": "USD"},
                    "commissionNotionalTotalCollected": {
                        "value": "0", "currency": "USD"}}
        return {"id": order_id, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": str(cum),
                # a CANCELLED order has nothing left to work: the remainder
                # was withdrawn, not left resting. leaves > 0 here would be
                # the venue reporting it still live, which a cancel is not.
                "leavesQuantity": "0",
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}

    def open_orders(self):
        return []

    def positions(self):
        return []


class RunnerStub:
    """Only the attributes the real `Runner.maybe_*` methods touch, so the
    REAL method bodies run against the SAME store and transport the executor
    used. This is the established pattern in this suite -- a Runner cannot be
    constructed without a venue client -- and it is the integration seam that
    matters: the lifecycle methods and the executor must agree about one
    order registry."""

    def __init__(self, store, transport, clock) -> None:
        self.store = store
        self.transport = transport
        self.clock = clock
        self.stats = collections.Counter()
        self.win = collections.Counter()
        self.shadow_ladder = None
        self.last_order_poll = {}
        self.last_progress_t = 0.0

    @property
    def now_t(self):
        return self.clock.t

    # the deadline paths disarm on an unreadable durable record
    def disarm(self, why=None):
        self.stats["disarmed:%s" % (why or "?")] += 1

    # THE REAL ONE. `maybe_acc_deadline` polls before it cancels, so that a
    # leg which FILLED is booked into inventory instead of being cancelled.
    # Stubbing it out would remove exactly the seam that cost us Probe 05.
    def poll_orders(self):
        return R.Runner.poll_orders(self)

    def lifecycle(self, events):
        for e in events:
            self.stats["LIFECYCLE:%s" % e.get("event", "ERROR")] += 1


def _row(seq, side="UP", l5px="0.65", size=10.0, bid="0.42", ask="0.44",
         clock=None):
    """One L5 action. The receipt is the clock, so the staleness guard sees a
    fresh action at whatever offset the test is standing at."""
    utc = clock.utc().isoformat() if clock else "2026-10-20T04:00:00+00:00"
    return {
        "action_key": "e2e-%d" % seq, "kind": "l5_action", "backlog": False,
        "l5_event_utc": utc, "receipt_utc": utc,
        "window_start_utc": "2026-10-20T04:00:00+00:00",
        "pmus_snapshot_age_s": 0.5,
        "l5": {"pmus_side": side, "l5_price": l5px, "l5_size": size},
        "pmus": {"bbo": {"bid": bid, "ask": ask,
                         "bid_shares": "900", "ask_shares": "900"}},
        "copyability": {"l5_price": l5px},
    }


def _px(plan):
    """Leg prices as Decimals. The string scale follows the input book's, so
    "0.40" and "0.4000" are the same price and a string compare is a trap."""
    return {l["side"]: D(l["price"]) for l in plan["legs"]}


class Window:
    """The harness: one window, one store, one transport, one clock."""

    def __init__(self, tmp_path, *, armed=True, book=None, gates=None,
                 limits=None):
        self.clock = Clock()
        self.book = book or Book()
        self.venue = Venue(armed=armed)
        self.store = E.InventoryStore(str(tmp_path / "inv.json"),
                                      now_fn=self.clock.utc)
        self.ex = E.FaithfulExecutor(
            self.store,
            limits or E.SafetyLimits(
                max_usd_per_market=D("5"), max_abs_residual=D("10"),
                max_total_exposure_usd=D("5"), max_concurrent_markets=1,
                max_live_orders=1, daily_realized_loss_halt_usd=D("5")),
            transport=self.venue, now_fn=self.clock.utc)
        self.ex.validation_gates = gates if gates is not None else (
            ValidationGates(max_cash_deployed_usd=D("4.65"),
                            max_unmatched_contracts=D("5"),
                            max_cumulative_loss_usd=D("20"),
                            cash_policy="NO_RECYCLING"))
        self.ex.ts_book = self.book          # the MIRROR, not a snapshot
        self.runner = RunnerStub(self.store, self.venue, self.clock)
        self._seq = 0
        self.records = []

    def tick(self, offset_s, **row_kw):
        """One decision tick at WS+offset, then the lifecycle sweeps that
        the runner performs on every tick, in the production order."""
        self.clock.at(offset_s)
        self._seq += 1
        rec = self.ex.handle(_row(self._seq, clock=self.clock, **row_kw),
                             {}, market=SLUG, window=str(WS))
        self.records.append(rec)
        # the runner's per-tick lifecycle, REAL method bodies
        R.Runner.maybe_acc_deadline(self.runner, self.clock.t)
        R.Runner.maybe_ts_deadline(self.runner, self.clock.t)
        return rec

    def reason(self, rec):
        return rec.get("refused_reason") or (
            (rec.get("ORDER") or {}).get("reason")) or "SUBMITTED"

    def restart(self, tmp_path):
        """A new process against the SAME durable state."""
        fresh = Window.__new__(Window)
        fresh.clock = self.clock
        fresh.book = self.book
        fresh.venue = self.venue
        fresh.store = E.InventoryStore(str(tmp_path / "inv.json"),
                                       now_fn=self.clock.utc)
        fresh.ex = E.FaithfulExecutor(
            fresh.store, self.ex.limits, transport=self.venue,
            now_fn=self.clock.utc)
        fresh.ex.validation_gates = self.ex.validation_gates
        fresh.ex.ts_book = self.book
        fresh.runner = RunnerStub(fresh.store, self.venue, self.clock)
        fresh._seq = self._seq
        fresh.records = []
        return fresh


@pytest.fixture(autouse=True)
def production_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "E2E-WINDOW")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "E2E-WINDOW")
    monkeypatch.setenv(TS.CANCEL_MARGIN_ENV, "300")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: WS if slug == SLUG else None)
    yield


@pytest.fixture()
def win(tmp_path):
    return Window(tmp_path)


# =================================================== the configuration itself

def test_the_production_margin_moves_the_terminal_to_T_plus_600():
    """ARITHMETIC THAT DECIDES A LIVE WINDOW. With the deployed margin of
    300s the terminal is WS+600, which is EARLIER than the 720s completion
    cutoff -- so `COMPLETE_CUTOFF_S` is unreachable in production and
    `PAST_TERMINAL` is what actually stops a completion. Entry stops 60s
    before that, at WS+540, on TOO_LATE_TO_WITHDRAW rather than on the 600s
    entry cutoff. If this test fails, every offset below means something
    different."""
    assert TS.cancel_margin_s() == 300.0
    assert TS.cancel_cutoff(WE) == WS + 600.0
    assert TS.complete_cutoff_s() == 720.0          # configured, unreachable
    assert TS.may_enter(WS + 539.0, WS, WE) is None
    assert TS.may_enter(WS + 541.0, WS, WE) == "TOO_LATE_TO_WITHDRAW"
    assert TS.may_complete(WS + 599.0, WS, WE) is None
    assert TS.may_complete(WS + 601.0, WS, WE) == "PAST_TERMINAL"


# ============================================ book freshness and disconnects

def test_a_fresh_book_reaches_the_planner_and_prices_the_pair(win):
    rec = win.tick(30.0)
    plan = rec.get("TWO_SIDED_PROBE")
    assert plan and plan.get("ok"), win.reason(rec)
    # 2 ticks behind each touch: UP 0.42-0.02, DOWN (1-0.44)-0.02
    assert _px(plan) == {"UP": D("0.40"), "DOWN": D("0.54")}
    assert D(plan["pair_cost"]) == D("0.94")
    assert win.book.reads >= 1              # the MIRROR was read, per call


def test_a_disconnected_book_fails_closed_and_nothing_is_sent(tmp_path):
    w = Window(tmp_path, book=Book().disconnect())
    rec = w.tick(30.0)
    assert w.reason(rec) == "BOOK_STALE"
    assert w.venue.sent == []


def test_a_stale_book_fails_closed_even_while_connected(tmp_path):
    w = Window(tmp_path, book=Book(age="9.0"))
    assert w.reason(w.tick(30.0)) == "BOOK_STALE"
    assert w.venue.sent == []


def test_a_reconnect_takes_a_FRESH_snapshot_and_never_the_old_one(tmp_path):
    w = Window(tmp_path, book=Book().disconnect())
    assert w.reason(w.tick(30.0)) == "BOOK_STALE"
    w.book.reconnect().set("0.30", "0.32")
    plan = w.tick(60.0)["TWO_SIDED_PROBE"]
    # priced off the NEW touch, so no snapshot was cached across the outage
    assert _px(plan) == {"UP": D("0.28"), "DOWN": D("0.66")}


# ================================================ eligible and ineligible

def test_the_pair_cost_CAP_no_longer_depends_on_one_environment_variable(
        monkeypatch):
    """C3, CLOSED. This test previously pinned the gap: `MIN_GROSS_EDGE` is
    0.01, so a 0.99 pair passed, and `fee_floor` called it profitable only
    because it judges POST-ONLY execution -- `net_if_taker` is negative there
    and `both_positive_if_taker` is False, and neither was gated on. The only
    thing standing between us and such a pair was `L5_TS_DEPTH_TICKS=2`.

    `L5_OPERATING_MODEL` puts the spring target at <= 0.96 and keeps near-$1
    pair tolerance LOCKED, so that is now a gate.
    """
    for dticks in (0, 1):
        o = TS.build_orders(market=SLUG, bid="0.50", ask="0.51",
                            bid_depth="900", ask_depth="900", tick="0.01",
                            min_qty="0.01", depth_ticks=dticks,
                            cash_envelope="4.65", max_worst_case_loss="5",
                            book_age_s="0.05")
        assert o["ok"] is False
        assert o["reason"] == "PAIR_COST_ABOVE_CAP"
    o2 = TS.build_orders(market=SLUG, bid="0.50", ask="0.51",
                         bid_depth="900", ask_depth="900", tick="0.01",
                         min_qty="0.01", depth_ticks=2, cash_envelope="4.65",
                         max_worst_case_loss="5", book_age_s="0.05")
    assert D(o2["pair_cost"]) == D("0.95")
    assert o2["both_positive_if_taker"] is True


def test_an_eligible_book_at_the_deployed_setting_clears_the_fee_floor(win):
    plan = win.tick(30.0)["TWO_SIDED_PROBE"]
    assert plan["fee_floor"]["ok"] is True
    assert plan["both_positive_if_taker"] is True
    assert D(plan["pair_cost"]) <= D("0.96")


# ============================================ entry, fills and the complement

def test_the_cheaper_leg_goes_first_and_ONE_live_order_blocks_the_other(win):
    """PRODUCTION SEQUENCING, which the module's own tests do not exercise
    because they raise `max_live_orders` to 2. With the deployed cap of ONE,
    the complement CANNOT rest beside the first leg: it is refused
    MAX_LIVE_ORDERS until the first leg leaves the book. So production runs a
    SEQUENTIAL strategy -- rest, fill, then complete -- and the simultaneous
    two-sided quote the module was written for never happens."""
    r1 = win.tick(30.0)
    assert win.reason(r1) == "SUBMITTED", win.reason(r1)
    assert win.venue.sent[0]["side"] == "UP"          # the cheaper leg
    assert win.venue.sent[0]["mode"] == FT.MAKER

    r2 = win.tick(45.0)
    assert win.reason(r2) == "MAX_LIVE_ORDERS"
    assert len(win.venue.sent) == 1


def test_a_first_leg_fill_books_inventory_and_then_the_complement_rests(
        tmp_path):
    w = Window(tmp_path)
    w.venue.fill_next()                 # the whole first leg
    r1 = w.tick(30.0)
    assert w.reason(r1) == "SUBMITTED", w.reason(r1)
    first = w.venue.sent[0]
    assert w.store.position(SLUG, first["side"]) == D(first["qty"])
    # a COMPLETING fill pops the row, so the one slot is free again
    assert w.store.still_fillable_order_count() == 0

    r2 = w.tick(60.0)
    assert w.reason(r2) == "SUBMITTED", w.reason(r2)
    assert len(w.venue.sent) == 2
    sides = [s["side"] for s in w.venue.sent]
    assert sides == ["UP", "DOWN"]            # the complement, not a re-entry


def test_a_PARTIAL_fill_keeps_the_order_live_and_the_slot_taken(tmp_path):
    w = Window(tmp_path)
    w.venue.fill_next("1.00")                 # part of a 3.32 leg
    w.tick(30.0)
    first = w.venue.sent[0]
    assert w.store.position(SLUG, first["side"]) == D("1.00")
    # THE PARTIAL-FILL BOUND. The row stays in `live_orders` with a positive
    # remainder and the state string "PARTIALLY_FILLED", which CONTAINS
    # "FILLED" -- the substring that used to retire it from this count and
    # void the one-live-order bound the whole bounded validation rests on.
    assert D(w.store.live_orders()[first["client_order_id"]]["remaining"]) > 0
    assert w.store.still_fillable_order_count() == 1
    assert w.reason(w.tick(45.0)) == "MAX_LIVE_ORDERS"


# ================================== cancellation, replacement and restart

def test_an_unfilled_leg_is_cancelled_at_its_OWN_ttl_by_exact_venue_id(
        tmp_path):
    w = Window(tmp_path)
    w.tick(30.0)
    vid = "v1"
    assert w.venue.cancelled == []
    w.tick(100.0)                             # 70s old: inside the 120s TTL
    assert w.venue.cancelled == []
    w.tick(155.0)                             # 125s old: past it
    assert w.venue.cancelled == [vid]
    assert w.store.live_order_count() == 0


def test_a_leg_cancelled_with_ZERO_fill_may_be_REPLACED_once_the_slot_frees(
        tmp_path):
    w = Window(tmp_path)
    w.tick(30.0)
    w.tick(155.0)                             # TTL cancel, zero filled
    assert w.venue.cancelled == ["v1"]
    r = w.tick(170.0)
    assert w.reason(r) == "SUBMITTED", w.reason(r)
    assert len(w.venue.sent) == 2
    # the SAME leg replaced, not the complement, and a DISTINCT order id
    assert w.venue.sent[1]["side"] == w.venue.sent[0]["side"]
    assert (w.venue.sent[1]["client_order_id"]
            != w.venue.sent[0]["client_order_id"])


def test_a_FILLED_leg_is_never_replaced(tmp_path):
    w = Window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    r = w.tick(60.0)
    assert w.reason(r) == "SUBMITTED"
    assert w.venue.sent[1]["side"] != w.venue.sent[0]["side"]   # complement


def test_a_restart_resumes_the_round_and_cannot_double_the_position(tmp_path):
    w = Window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    held = w.store.position(SLUG, w.venue.sent[0]["side"])

    w2 = w.restart(tmp_path)                  # new process, same files
    assert w2.store.position(SLUG, w.venue.sent[0]["side"]) == held
    r = w2.tick(90.0)
    assert w2.reason(r) == "SUBMITTED", w2.reason(r)
    # it COMPLETED rather than re-entering: still exactly one leg per side
    sides = [s["side"] for s in w.venue.sent]
    assert sorted(sides) == ["DOWN", "UP"]
    assert w2.store.position(SLUG, w.venue.sent[0]["side"]) == held


# ============================================ exposure and capital accounting

def test_a_MATCHED_pair_does_not_release_capital_before_settlement(tmp_path):
    """NO_RECYCLING, asserted where it matters. UP and DOWN being matched is
    not cash back: the venue pays at SETTLEMENT. So after a completed pair the
    cash gate still charges the full acquisition cost, and a second round must
    be refused rather than funded out of a pair that has not paid yet."""
    w = Window(tmp_path)
    w.venue.fill_next()                       # leg one fills in full
    w.tick(30.0)
    first = w.venue.sent[0]
    qty = D(first["qty"])
    w.venue.fill_next()                       # the complement fills in full
    w.tick(60.0)
    assert len(w.venue.sent) == 2

    # matched on both sides, and the cash is STILL deployed
    assert w.store.position(SLUG, "UP") == qty
    assert w.store.position(SLUG, "DOWN") == qty
    cash = w.store.cash_deployed_usd(SLUG)
    assert D(cash.get("gross_out") or cash.get("cash_deployed_usd") or 0) > 0

    # so round two cannot be funded: the gate refuses on cash, not on rounds
    r = w.tick(120.0)
    assert w.reason(r) != "SUBMITTED"
    assert "CASH" in w.reason(r) or "EXPOSURE" in w.reason(r), w.reason(r)
    assert len(w.venue.sent) == 2


def test_the_pair_is_sized_to_fit_the_cash_gate_and_not_one_cent_more(win):
    """THE ENVELOPE BINDS THE SIZE, not the book. The DOWN leg is a
    BUY_SHORT, which the gate charges a dollar a share, so the obligation is
    `qty * (up_price + 1.00)` -- and that is what the $4.65 gate caps."""
    plan = win.tick(30.0)["TWO_SIDED_PROBE"]
    qty = D(plan["qty_per_leg"])
    up = D({l["side"]: l["price"] for l in plan["legs"]}["UP"])
    assert D(plan["cash_gate_obligation_usd"]) == qty * (up + D("1.00"))
    assert D(plan["cash_gate_obligation_usd"]) <= D("4.65")
    # THE ENVELOPE IS NO LONGER THE ONLY BINDING TERM. Since 2026-10-24 an
    # opening leg is also capped at the approved 3-contract residual ceiling,
    # because the executor's residual cap exempts a flat book and a $5
    # envelope therefore opened 4.09 contracts live. Whichever term is
    # smaller binds, and `sized_by` names it.
    assert plan["sized_by"] in ("CASH_ENVELOPE", "OPENING_LEG_CONTRACT_CAP",
                                "LOSS_CEILING", "CAMPAIGN_BUDGET")
    if plan["sized_by"] == "CASH_ENVELOPE":
        # one more increment would not fit, so the size is the largest that
        # does
        assert (qty + D("0.01")) * (up + D("1.00")) > D("4.65")
    else:
        assert qty <= D(plan["opening_leg_max_contracts"])


# ======================================= the terminal, settlement, realized

def test_nothing_may_rest_past_the_terminal_and_it_cancels_by_exact_id(
        tmp_path):
    w = Window(tmp_path)
    w.tick(500.0)                             # inside the entry window
    assert len(w.venue.sent) == 1
    assert w.venue.cancelled == []
    w.tick(TS.WINDOW_S - MARGIN_S + 1.0)      # one second past the terminal
    assert w.venue.cancelled == ["v1"]
    assert w.store.still_fillable_order_count() == 0


def test_no_NEW_leg_is_opened_once_it_could_not_be_withdrawn(tmp_path):
    w = Window(tmp_path)
    assert w.reason(w.tick(539.0)) == "SUBMITTED"
    w2 = Window(tmp_path / "x")
    (tmp_path / "x").mkdir(exist_ok=True)
    assert w2.reason(w2.tick(541.0)) == "TOO_LATE_TO_WITHDRAW"
    assert w2.venue.sent == []


def test_realized_net_comes_only_from_SETTLEMENT_and_keeps_fees_apart():
    """The window's final number. Matched pairs redeem exactly $1.00 a share;
    an unfilled order contributes nothing; fees and rebates are separate
    confirmed lines and are never folded into the trading result."""
    recs = [
        {"L5_NATIVE": {"side": "UP"},
         "FILL": {"filled": True, "filled_qty": "3.32", "fill_price": "0.40",
                  "fee_usd": "0.01", "maker_rebate_usd": "0"}},
        {"L5_NATIVE": {"side": "DOWN"},
         "FILL": {"filled": True, "filled_qty": "3.32", "fill_price": "0.54",
                  "fee_usd": "0.01", "maker_rebate_usd": "0"}},
        {"L5_NATIVE": {"side": "UP"}, "FILL": {"filled": False}},
    ]
    out = E.settle(recs, market=SLUG, yes_value=D("1"))
    assert D(out["matched"]) == D("3.32")
    assert D(out["residual"]) == D("0")
    # pair cost 0.94 on 3.32 matched -> +0.1992 before fees
    assert D(out["matched_pair_pnl_usd"]) == D("0.1992")
    assert D(out["taker_fees_usd"]) == D("0.02")
    assert D(out["maker_rebate_usd"]) == D("0")
    assert D(out["realized_net_usd"]) == D("0.1792")


def test_a_STRANDED_single_leg_settles_as_a_directional_loss(tmp_path):
    """The case the pair exists to avoid, priced. One leg filled, the
    complement never did, and the market settles against us: the loss is the
    whole acquisition cost and the pair's +$0.1992 never existed."""
    recs = [{"L5_NATIVE": {"side": "UP"},
             "FILL": {"filled": True, "filled_qty": "3.32",
                      "fill_price": "0.40", "fee_usd": "0",
                      "maker_rebate_usd": "0"}}]
    lost = E.settle(recs, market=SLUG, yes_value=D("0"))
    assert D(lost["matched"]) == D("0")
    assert D(lost["realized_net_usd"]) == D("-1.328")
    won = E.settle(recs, market=SLUG, yes_value=D("1"))
    assert D(won["realized_net_usd"]) == D("1.992")


# ================================================= the whole window, in order

def test_one_complete_window_entry_to_realized_net(tmp_path):
    """THE DELIVERABLE. Entry, first-leg fill, complement, complement fill,
    terminal sweep, settlement, realized net -- one clock, one store, one
    transport, production configuration, no network and no live venue."""
    w = Window(tmp_path)

    w.venue.fill_next()
    assert w.reason(w.tick(30.0)) == "SUBMITTED"
    leg1 = w.venue.sent[0]
    qty = D(leg1["qty"])

    w.venue.fill_next()
    assert w.reason(w.tick(90.0)) == "SUBMITTED"
    leg2 = w.venue.sent[1]
    assert {leg1["side"], leg2["side"]} == {"UP", "DOWN"}
    assert D(leg2["qty"]) == qty                      # a MATCHED pair
    for s in (leg1, leg2):
        assert s["mode"] == FT.MAKER                  # post-only throughout

    # the terminal finds nothing resting, because both legs filled
    w.tick(TS.WINDOW_S - MARGIN_S + 1.0)
    assert w.venue.cancelled == []
    assert w.store.still_fillable_order_count() == 0

    # settlement, on either outcome, from the prices actually filled
    recs = [{"L5_NATIVE": {"side": s["side"]},
             "FILL": {"filled": True, "filled_qty": str(qty),
                      "fill_price": str(s["price"]), "fee_usd": "0",
                      "maker_rebate_usd": "0"}}
            for s in (leg1, leg2)]
    pair_cost = D(leg1["price"]) + D(leg2["price"])
    for yes in (D("0"), D("1")):
        out = E.settle(recs, market=SLUG, yes_value=yes)
        assert D(out["matched"]) == qty
        assert D(out["residual"]) == D("0")
        # the SAME net on both outcomes: that is the whole shape
        assert D(out["realized_net_usd"]) == (D("1") - pair_cost) * qty
    assert pair_cost <= D("0.96")


# ========================================== the gap a full window exposed

def test_the_COMPLEMENT_is_derived_from_the_CURRENT_book_after_a_fill(
        tmp_path):
    """THE 0330z GAP, CLOSED. This test previously pinned the defect.

    The plan stays frozen per round -- re-planning each tick produced legs of
    DIFFERENT sizes, which is not a matched pair. But with `max_live_orders=1`
    the complement can never rest beside the first leg, so it is ALWAYS placed
    later against a book that has moved. Carrying the planning-time price is
    what left the 0330z complement 45 cents through the touch, where post-only
    is rejected for crossing and the filled leg is left unhedged.

    Here the first leg fills and the market then runs 20 cents. The complement
    now goes out at a price derived from THAT book, sized to the quantity
    actually filled, and judged against the cost actually paid.
    """
    w = Window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    leg1 = w.venue.sent[0]
    planned = {l["side"]: D(l["price"])
               for l in w.records[0]["TWO_SIDED_PROBE"]["legs"]}

    w.book.set("0.62", "0.64")                # the market has moved 20c
    rec = w.tick(90.0)
    leg2 = w.venue.sent[1]
    assert leg2["side"] != leg1["side"]

    # priced off the CURRENT touch: DOWN own = (1 - 0.64) - 0.02
    fresh = (D("0.62") - D("0.02") if leg2["side"] == "UP"
             else D(1) - D("0.64") - D("0.02"))
    assert D(leg2["price"]) == fresh
    assert D(leg2["price"]) != planned[leg2["side"]]

    # sized to what actually filled, and judged on what we actually paid
    comp = rec["TWO_SIDED_COMPLEMENT"]
    assert comp["ok"] is True
    assert comp["provenance"] == "DERIVED_FROM_CURRENT_BOOK"
    assert D(leg2["qty"]) == D(leg1["qty"])
    assert D(comp["first_leg_cost"]) == D(leg1["price"])
    assert D(comp["pair_cost"]) == D(leg1["price"]) + fresh
    assert D(comp["frozen_price_superseded"]) == planned[leg2["side"]]
    # and it still rests post-only, on the correct side of the book
    assert leg2["mode"] == FT.MAKER
    assert TS.leg_crosses(leg2["side"], leg2["price"],
                          bid="0.62", ask="0.64") is False


def test_a_complement_that_no_longer_PAYS_is_refused_and_nothing_is_sent(
        tmp_path):
    """The other half of the same decision. The first leg filled at 0.40 and
    the book has run the WRONG way, so the only complement available would
    put the pair at or above a dollar. It is refused rather than repriced
    into a certain loss, and the residual is carried under the existing
    limits -- an economic decision, not a deadline."""
    w = Window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    assert len(w.venue.sent) == 1
    w.book.set("0.30", "0.32")                # DOWN's fair price is now 0.66
    rec = w.tick(90.0)
    assert w.reason(rec) in ("COMPLEMENT_BELOW_FEE_FLOOR",
                             "COMPLEMENT_NOT_POSITIVE_ON_BOTH_OUTCOMES",
                             "COMPLEMENT_PAIR_ABOVE_CAP")
    assert len(w.venue.sent) == 1             # nothing sent
    assert D(rec["TWO_SIDED_COMPLEMENT"]["pair_cost"]) >= D("0.96")


def test_a_round_the_market_has_LEFT_is_retired_and_re_planned(tmp_path,
                                                               monkeypatch):
    """B4. An unfilled plan the book has walked away from is abandoned, and
    the next round is planned at the CURRENT touch -- instead of the same
    unreachable pair being proposed for the rest of the window, which is
    what 0330z did 32 times."""
    # THE FIRST-LEG PRICE FLOOR IS NEUTRALISED HERE, DELIBERATELY. This test
    # is about RETIREMENT and re-planning, and at its 0.80/0.82 book the
    # cheaper leg is DOWN at own 0.16 -- which the floor added on 2026-10-23
    # declines on purpose. Raising the book instead would mean rewriting the
    # 0.16 assertion below and changing what is under test; the floor has its
    # own tests in tests/test_l5_first_leg_price_floor.py.
    monkeypatch.setenv("L5_TS_MIN_FIRST_LEG_PRICE", "0")
    w = Window(tmp_path)
    w.tick(30.0)                              # round 0 planned and leg sent
    first = D(w.venue.sent[0]["price"])
    assert w.ex.probe_round(SLUG) == 0

    w.tick(155.0)                             # TTL cancel: nothing live, none filled
    assert w.venue.cancelled == ["v1"]

    w.book.set("0.80", "0.82")                # and the market has gone
    rec = w.tick(170.0)
    assert rec["TWO_SIDED_ROUND"]["retired"] is True
    assert rec["TWO_SIDED_ROUND"]["plan_unexecutable"]
    assert rec["TWO_SIDED_ROUND"]["retirement_blocked_by"] is None
    assert w.ex.probe_round_retired(SLUG, 0) is True
    # re-planned at the new touch, not the old one -- and still the CHEAPER
    # leg first, which at a 0.80/0.82 book is DOWN at (1 - 0.82) - 0.02
    assert w.reason(rec) == "SUBMITTED", w.reason(rec)
    sent = w.venue.sent[-1]
    assert D(sent["price"]) != first
    fresh = {s: TS.quote_own_price(s, bid="0.80", ask="0.82", tick="0.01",
                                   depth_ticks=2) for s in ("UP", "DOWN")}
    assert sent["side"] == min(fresh, key=lambda s: fresh[s])
    assert D(sent["price"]) == fresh[sent["side"]] == D("0.16")


def test_a_round_is_NEVER_retired_while_an_order_is_live(tmp_path):
    w = Window(tmp_path)
    w.tick(30.0)                              # leg resting, nothing filled
    w.book.set("0.80", "0.82")
    rec = w.tick(60.0)
    assert rec["TWO_SIDED_ROUND"]["retired"] is False
    assert rec["TWO_SIDED_ROUND"]["retirement_blocked_by"] in (
        "ORDER_STILL_LIVE", "ORDER_STILL_FILLABLE")
    assert w.ex.probe_round_retired(SLUG, 0) is False


def test_a_round_is_NEVER_retired_while_a_leg_HOLDS_inventory(tmp_path):
    """Including a PARTIAL. Walking past a round that holds stock would
    strand it: the complement is tracked per round, so the round is the only
    thing that would have hedged it."""
    w = Window(tmp_path)
    w.venue.fill_next("1.00")                 # partial fill, order ends
    w.tick(30.0)
    w.tick(155.0)                             # TTL cancels the remainder
    w.book.set("0.80", "0.82")
    rec = w.tick(170.0)
    blocked = rec.get("TWO_SIDED_ROUND", {}).get("retirement_blocked_by")
    assert blocked is not None and blocked.startswith("LEG_HOLDS_UNMATCHED")
    assert w.ex.probe_round_retired(SLUG, 0) is False


# ============================================= cancellation UNCERTAINTY

def test_an_UNVERIFIABLE_cancellation_stops_the_engine_rather_than_guessing(
        tmp_path):
    """THE CASE THAT MATTERS MOST. The cancel ack came back, but the re-read
    that would PROVE the order terminal failed -- so the venue may still hold
    it. An unverified cancellation is not a cancellation: the store goes
    UNCERTAIN, no further order may be placed, and no round may be retired.

    A cancel ack is a receipt, not proof. `deadman` stopped trusting its own
    ack for exactly this reason.
    """
    w = Window(tmp_path)
    w.tick(30.0)
    assert len(w.venue.sent) == 1

    # the venue accepts the cancel and then goes dark on the re-read
    def blind(order_id):
        raise OSError("venue unreachable")
    w.venue.order = blind

    w.tick(155.0)                                   # the TTL fires
    assert w.venue.cancelled == ["v1"]              # cancelled by exact id
    assert w.store.orders_uncertain()               # but NOT proved terminal

    # nothing further is placed while the book is uncertain
    rec = w.tick(170.0)
    why = w.reason(rec)
    # the refusal NAMES the order and why it could not be verified, which is
    # stronger than a bare ORDER_STATE_UNCERTAIN
    # `PAIR_<SIDE>:` prefix when the pair reservation is what refused -- the
    # same refusal, raised before the leg is placed and attributed to it.
    assert "BOOK_UNCERTAIN:" in why, why
    assert "v1" in why and "OSError" in why, why
    assert len(w.venue.sent) == 1

    # and the round cannot be retired on an unknown, however far the book moves
    w.book.set("0.80", "0.82")
    assert w.ex.probe_round_is_safe_to_retire(SLUG, 0) is not None
    assert w.ex.probe_round_retired(SLUG, 0) is False


def test_a_cancel_the_venue_says_is_STILL_OPEN_is_not_treated_as_done(
        tmp_path):
    """The venue answers the re-read and says the order is still working.
    That is a refusal to confirm, not a confirmation."""
    w = Window(tmp_path)
    w.tick(30.0)
    intent = w.venue.sent[0]

    def still_open(order_id):
        return {"id": order_id, "state": "ORDER_STATE_NEW",
                "quantity": str(intent["qty"]),
                "cumQuantity": "0", "leavesQuantity": str(intent["qty"]),
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}
    w.venue.order = still_open

    w.tick(155.0)
    assert w.venue.cancelled == ["v1"]
    # the slot is NOT released: the order may still take money
    assert w.store.still_fillable_order_count() >= 1
    why = w.reason(w.tick(170.0))
    # The pair reservation now re-checks a stored plan before its SECOND leg,
    # so this same uncertainty is caught there -- earlier, and attributed to
    # the leg -- hence the optional `PAIR_<SIDE>:` prefix. What must not
    # change, and does not, is that it REFUSES and nothing further is sent.
    assert ("BOOK_UNCERTAIN:" in why) or why == "MAX_LIVE_ORDERS", why
    assert "still reports it live" in why or why == "MAX_LIVE_ORDERS", why
    assert len(w.venue.sent) == 1


# ================================ submitted is NOT hedged; only fills count

def test_a_SUBMITTED_complement_is_not_a_hedge_until_the_exchange_fills_it(
        tmp_path):
    """THE INVARIANT. A resting complement is an intention. Until the venue
    confirms a fill the book is still ONE-SIDED, the residual is undiminished
    and the worst case is unchanged. Nothing in the engine may count a
    submission as cover."""
    w = Window(tmp_path)
    w.venue.fill_next()                        # leg one fills
    w.tick(30.0)
    leg1 = w.venue.sent[0]
    qty = D(leg1["qty"])
    other = "DOWN" if leg1["side"] == "UP" else "UP"

    w.tick(90.0)                               # the complement is SUBMITTED
    assert len(w.venue.sent) == 2
    assert w.venue.sent[1]["side"] == other

    # ... and nothing about the position has changed
    assert w.store.position(SLUG, leg1["side"]) == qty
    assert w.store.position(SLUG, other) == D(0)
    assert w.store.residual(SLUG) in (qty, -qty)
    # the resting complement counts as OUTSTANDING, never as matched
    assert w.store.still_fillable_order_count() == 1
    assert w.store.outstanding_residual(SLUG) != D(0)

    # settlement from the confirmed fills alone: ONE leg, a directional bet
    recs = [{"L5_NATIVE": {"side": leg1["side"]},
             "FILL": {"filled": True, "filled_qty": str(qty),
                      "fill_price": str(leg1["price"]), "fee_usd": "0",
                      "maker_rebate_usd": "0"}},
            {"L5_NATIVE": {"side": other}, "FILL": {"filled": False}}]
    out = E.settle(recs, market=SLUG, yes_value=D("0"))
    assert D(out["matched"]) == D("0")         # NOT a completed pair
    assert D(out["realized_net_usd"]) < 0


# ========================================== a post-only order the venue REJECTS

def test_a_post_only_order_the_venue_REJECTS_leaves_no_phantom_position(
        tmp_path):
    """A crossing post-only order is rejected rather than filled. The engine
    must not carry it as live, must not book a position, and must not treat
    the attempt as a hedge -- while the claim stays spent, because the order
    did reach the venue."""
    w = Window(tmp_path)

    def reject(intent):
        w.venue.sent.append(dict(intent))
        raise RuntimeError("POST_ONLY_WOULD_CROSS")
    w.venue.submit = reject

    rec = w.tick(30.0)
    assert len(w.venue.sent) == 1              # it was attempted
    assert w.reason(rec) != "SUBMITTED"
    # no position, and no phantom resting order
    assert w.store.position(SLUG, "UP") == D(0)
    assert w.store.position(SLUG, "DOWN") == D(0)
    # the registry reserved it BEFORE the wire, so it is not silently dropped:
    # either it is live-and-unresolved or the book is uncertain, never clean
    assert (w.store.still_fillable_order_count() >= 1
            or w.store.orders_uncertain())


# ================================================= the cancellation race

def test_a_fill_that_lands_WHILE_the_cancel_is_in_flight_is_booked_not_lost(
        tmp_path):
    """THE RACE. We decide to cancel at the TTL; between that decision and
    the venue's answer the order fills. `maybe_acc_deadline` polls BEFORE it
    cancels for exactly this reason: the fill must be booked into inventory
    rather than discarded as a cancellation.

    Modelled by a venue that reports the fill on the re-read, which is what a
    venue racing us actually does.
    """
    w = Window(tmp_path)
    w.tick(30.0)                               # resting, nothing filled yet
    intent = w.venue.sent[0]
    qty = D(intent["qty"])

    # the venue filled it in full just as the deadline fired
    w.venue.acked["v1"] = str(qty)

    w.tick(155.0)                              # TTL: poll, then cancel
    # the FILL won: inventory carries it
    assert w.store.position(SLUG, intent["side"]) == qty
    # and no phantom remains fillable
    assert w.store.still_fillable_order_count() == 0


def test_a_PARTIAL_fill_racing_the_cancel_books_the_part_and_withdraws_rest(
        tmp_path):
    w = Window(tmp_path)
    w.tick(30.0)
    intent = w.venue.sent[0]
    w.venue.acked["v1"] = "1.00"               # partial, mid-cancel

    w.tick(155.0)
    assert w.store.position(SLUG, intent["side"]) == D("1.00")
    assert w.venue.cancelled == ["v1"]
    assert w.store.still_fillable_order_count() == 0
    # and the round now holds UNMATCHED inventory, so it may not be retired
    blocked = w.ex.probe_round_is_safe_to_retire(SLUG, 0)
    assert blocked is not None and blocked.startswith("LEG_HOLDS_UNMATCHED")


# ============================== repricing the complement after a PARTIAL fill

def test_the_complement_after_a_PARTIAL_fill_matches_only_what_filled(
        tmp_path):
    """Sized to exchange-confirmed quantity, priced at the current book. A
    1.00 fill of a 3.32 leg is hedged by 1.00, not by 3.32 -- over-hedging
    would open a directional position on the other side."""
    w = Window(tmp_path)
    w.venue.fill_next("1.00")                  # partial
    w.tick(30.0)
    leg1 = w.venue.sent[0]
    assert D(leg1["qty"]) > D("1.00")
    w.tick(155.0)                              # TTL withdraws the remainder
    assert w.store.position(SLUG, leg1["side"]) == D("1.00")

    w.book.set("0.50", "0.52")                 # and the market has moved
    rec = w.tick(170.0)
    comp = rec.get("TWO_SIDED_COMPLEMENT")
    assert comp is not None and comp["ok"] is True, w.reason(rec)
    assert D(comp["unmatched"]) == D("1.00")
    assert D(comp["leg"]["qty"]) == D("1.00")  # matches the FILL, not the plan
    # priced off the CURRENT book
    side = comp["leg"]["side"]
    assert D(comp["leg"]["price"]) == TS.quote_own_price(
        side, bid="0.50", ask="0.52", tick="0.01", depth_ticks=2)
    assert D(comp["first_leg_cost"]) == D(leg1["price"])


def test_the_net_pair_cost_ceiling_holds_on_the_repriced_complement(tmp_path):
    """C3 end to end. Whatever the book does, a complement that would put the
    effective pair above 0.96 against the REALISED first-leg cost is refused
    and nothing is sent."""
    w = Window(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    first = D(w.venue.sent[0]["price"])

    w.book.set("0.30", "0.32")                 # complement would cost 0.66
    rec = w.tick(90.0)
    comp = rec["TWO_SIDED_COMPLEMENT"]
    assert comp["ok"] is False
    assert D(comp["pair_cost"]) > D("0.96")
    assert D(comp["first_leg_cost"]) == first
    assert len(w.venue.sent) == 1              # nothing sent


# ============================== the REAL 1215z sequence, reproduced exactly

def test_the_1215z_sequence_reproduced_DOWN_then_a_repriced_UP_complement(
        tmp_path, monkeypatch):
    """THE WINDOW THAT WORKED, from its own production records.

    `cpc-btc-updown-15m-2026-10-20-1215z`, round 3 (rounds 0-2 retired):

        plan at bid 0.91 / ask 0.92 -> UP 0.8900, DOWN 0.0600, pair 0.9500
        DOWN fills 2.46 at own 0.06            (wire 0.94, D78BWYNG10JH)
        book moves to bid 0.92 / ask 0.93
        UP complement DERIVED_FROM_CURRENT_BOOK -> 0.9000, superseding 0.8900
        first_leg_cost 0.0600, pair_cost 0.9600  (D78C4Q7G10JR)
        settled: matched 2.46, cost $2.3616, redeemed $2.46, +$0.0984

    The pair landed on 0.96 EXACTLY because `complement_order` bounds it
    against the realised first-leg cost -- the cap binding, not a
    coincidence. Two separate per-leg accumulation claims, both legitimate.
    """
    # AND THIS WINDOW IS THE COUNTER-EXAMPLE TO THE FIRST-LEG PRICE FLOOR.
    # The floor added on 2026-10-23 refuses an opening below own 0.20 on the
    # evidence of 2230z (own 0.12) and 2245z (own 0.06), both of which failed
    # to complete. 1215z opened at own 0.06 and DID complete, at a pair of
    # exactly 0.96, for a venue-attested +$0.0984. So cheap openings are 1
    # win and 2 losses, not a clean split, and the floor is neutralised here
    # so this real history keeps its own test.
    monkeypatch.setenv("L5_TS_MIN_FIRST_LEG_PRICE", "0")
    w = Window(tmp_path, book=Book("0.91", "0.92"))

    w.venue.fill_next()
    r1 = w.tick(30.0)
    plan = r1["TWO_SIDED_PROBE"]
    assert _px(plan) == {"UP": D("0.89"), "DOWN": D("0.06")}
    assert D(plan["pair_cost"]) == D("0.95")
    first = w.venue.sent[0]
    assert first["side"] == "DOWN"                 # the CHEAPER leg first
    assert D(first["price"]) == D("0.06")
    qty = D(first["qty"])
    assert w.store.position(SLUG, "DOWN") == qty

    w.book.set("0.92", "0.93")                     # the book moves
    r2 = w.tick(90.0)
    comp = r2["TWO_SIDED_COMPLEMENT"]
    assert comp["ok"] is True
    assert comp["provenance"] == "DERIVED_FROM_CURRENT_BOOK"
    assert comp["leg"]["side"] == "UP"
    assert D(comp["leg"]["price"]) == D("0.90")    # 0.92 - 2 ticks
    assert D(comp["frozen_price_superseded"]) == D("0.89")
    assert D(comp["first_leg_cost"]) == D("0.06")
    assert D(comp["pair_cost"]) == D("0.96") == TS.MAX_PAIR_COST
    assert comp["matching_side"] == "DOWN"
    assert D(comp["unmatched"]) == qty

    second = w.venue.sent[1]
    assert second["side"] == "UP" and D(second["price"]) == D("0.90")
    assert second["mode"] == FT.MAKER

    # and the settlement, on either outcome, from the prices actually filled
    recs = [{"L5_NATIVE": {"side": s["side"]},
             "FILL": {"filled": True, "filled_qty": str(qty),
                      "fill_price": str(s["price"]), "fee_usd": "0",
                      "maker_rebate_usd": "0"}}
            for s in (first, second)]
    for yes in (D("0"), D("1")):
        out = E.settle(recs, market=SLUG, yes_value=yes)
        assert D(out["matched"]) == qty
        assert D(out["residual"]) == D("0")
        assert D(out["realized_net_usd"]) == D("0.0984")
    # the production numbers, exactly
    assert qty == D("2.46")
    assert qty * D("0.06") + qty * D("0.90") == D("2.3616")


def test_the_1215z_pair_is_NOT_blocked_by_the_pair_cost_ceiling(tmp_path):
    """0.96 is AT the ceiling, not above it. A ceiling that refused this
    would refuse the only profitable trade we have."""
    w = Window(tmp_path, book=Book("0.91", "0.92"))
    w.venue.fill_next()
    w.tick(30.0)
    w.book.set("0.92", "0.93")
    assert w.ex.pair_cost_refusal(SLUG, {"side": "UP"},
                                  {"price": "0.90", "qty": "2.46"}) is None
