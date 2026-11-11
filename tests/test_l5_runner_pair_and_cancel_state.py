"""The two 2026-11-10 0200z defects, driven THROUGH THE RUNNER.

WHY THIS FILE EXISTS. `test_l5_window_owner_binding.py` showed two maker
orders resting by calling `FaithfulExecutor.handle` twice. Production then ran
one window and the two legs never rested together -- because the lockout that
stopped them is in `Runner.maybe_controller_open`, one layer above the call
that test made. A test that enters below the defect cannot see it.

WHAT IS REAL HERE. A `Runner` built by its own constructor, on its own
`InventoryStore`, with a `FaithfulExecutor` wired exactly as `Runner.roll`
wires it (maker_treatment, maker_only, `R.validation_gates()` from the
environment) and production's own behaviour switches, read from the service on
2026-11-10. Every decision below is made by a real Runner method:

    Runner.handle                  the L5-action path
    Runner.maybe_book_completion   the inventory-aware complement
    Runner.maybe_controller_open   the timer opening        <- defect 1
    Runner.maybe_completion_requote  the cancel half of a requote
    Runner.poll_orders             the venue's word on each order  <- defect 2

called in the order `Runner.tick` calls them, which a test below pins against
the source.

WHAT IS NOT. The venue and the book are scripted doubles, and `Runner.roll`
and arming are not exercised: the transport is attached armed. Arming has its
own suites; nothing here is evidence about it.

THE WINDOW REPLAYED, from the service's own log lines:

    02:00:06.5  L5 action -> DOWN post-only 0.6 @ own 0.44        (leg one)
    02:00:06.8  timer: ORDER_ALREADY_RESTING                      <- defect 1
    02:00:09.4  timer: ORDER_ALREADY_RESTING
    02:00:12.9  timer: ORDER_ALREADY_RESTING
    02:00:13    DOWN FILLED alone
    02:00:18    UP post-only 0.6 @ 0.50
    02:00:35.9  requote 0.50 -> 0.52 cancels UP; three re-reads still NEW
                "deadman cancelled DMG2WQZG344P but the venue still reports
                 it live"                                         <- defect 2
    02:00:37.3  poll: UP CANCELED -- and the mark stays
    02:00:38-48 every submission refused BOOK_UNCERTAIN
    02:00:54    replacement UP @ 0.55, pair 0.99
"""
import collections
import datetime as dt
import inspect
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS

WS = 1794276000                                   # 2026-11-10T02:00:00Z
SLUG = "cpc-btc-updown-15m-2026-11-10-0200z"
L5_SLUG = "btc-updown-15m-%d" % WS

#: production's behaviour switches on 2026-11-10, read from the service.
#: Limits and switches only; no credential and no authorization document.
PRODUCTION = {
    "L5_WINDOW_STRATEGY": "TWO_SIDED", "L5_TS_MARKET": SLUG,
    "L5_TS_EXPERIMENT_ID": "owner-20261110-0200z-544c7f82",
    "L5_EXPERIMENT_ID": "l5-btc15m-prog25-c1-d823f1d0",
    "L5_TS_TWO_ORDER_PAIR": "1", "L5_DUAL_QUOTE": "ON",
    "L5_DUAL_REPLENISH": "OFF", "L5_MAX_LIVE_ORDERS": "2",
    "L5_CONTROLLER_OPEN": "YES", "L5_BOOK_COMPLETION": "1",
    "L5_COMPLETION_REQUOTE": "1", "L5_COMPLETION_DEPTH_TICKS": "0",
    "L5_TS_DEPTH_TICKS": "2", "L5_SHADOW_ONLY": "0",
    "L5_TS_COMPLETION_CROSS": "NO", "L5_TAKER_COMPLETION_POLICY": "NO",
    "L5_DEFENSIVE_COMPLETION": "0", "L5_ACC_MAX_ROUNDS": "8",
    "L5_MAX_TOTAL_EXPOSURE_USD": "1.00", "L5_MAX_CASH_DEPLOYED_USD": "0.90",
    "L5_MAX_CUMULATIVE_LOSS_USD": "18.6325",
    "L5_MAX_UNMATCHED_CONTRACTS": "5",
    "L5_MAX_WORST_CASE_DEFICIT_USD": "1.00",
    "L5_DAILY_REALIZED_LOSS_HALT_USD": "5.00",
    "L5_MAX_COMPLETION_EV_GIVEUP_USD": "0.05",
    "L5_TS_CANCEL_MARGIN_S": "300", "L5_TS_COMPLETE_CUTOFF_S": "600",
    "L5_TS_ENTRY_CUTOFF_S": "480", "L5_TS_MAX_PAIR_COST": "0.96",
    "L5_TS_MIN_FIRST_LEG_PRICE": "0.30",
    "L5_TS_OPENING_LEG_MAX_CONTRACTS": "1",
    "L5_TS_OPENING_NOTIONAL_BUDGET_USD": "3.00",
    "L5_TS_ROUND_ALLOCATIONS": "1", "L5_VALIDATION_GATES": "1",
}
ABSENT = ("L5_LIVE_HALT", "L5_ORDER_REGISTRY_PORT", "L5_FIDELITY_MODE",
          "L5_TS_ADVERSE_SKEW", "L5_PROVISIONAL_FORCED_SIDE",
          "L5_LIVE_PROBE_MARKET", "L5_STRATEGY_LOSS_LIMIT_USD")


# ------------------------------------------------------------------ doubles
class Book:
    """The authenticated mirror, scripted. Numeric age, as AuthBook gives."""

    def __init__(self, bid, ask):
        self.set(bid, ask)

    def set(self, bid, ask):
        self._b = {"bid": str(bid), "ask": str(ask), "bid_shares": "900",
                   "ask_shares": "900", "stream_age_s": 0.05,
                   "source": "authenticated_ws"}
        return self

    def snapshot(self, market=None):
        return dict(self._b)


class Ladder:
    """Queue evidence for the requote, at the depths the service measured:
    3638.94 ahead at 0.50 and 2643.34 ahead at 0.52."""

    def ahead_of_us(self, slug, resting_side, wire):
        return D("3638.94") - (D(str(wire)) - D("0.50")) * D("49780")


class Venue:
    """`/v1/order` as the venue answers it, scripted per order.

    `lag[oid] = n` makes the next n reads after a cancel still say the order
    is working -- the venue's own cancel latency, which is what the deadman's
    three re-reads lost the race to at 02:00:36.
    """

    name = "RUNNER-E2E"
    quantity_increment = D("0.01")
    tick_size = D("0.01")

    def __init__(self):
        self.armed = True
        self.market_slug = SLUG
        self.sent, self.cancelled = [], []
        self.by_oid, self.cum = {}, {}
        self.retired, self.full = set(), set()
        self.lag = collections.Counter()
        self.reads = collections.Counter()
        self.read_error = {}

    def submit(self, intent):
        coid = intent["client_order_id"]
        if any(s["client_order_id"] == coid for s in self.sent):
            raise RuntimeError("DUPLICATE_CLIENT_ORDER_ID")
        self.sent.append(dict(intent))
        oid = "v%d" % len(self.sent)
        self.by_oid[oid] = dict(intent)
        self.cum[oid] = "0"
        return {"order_id": oid, "filled_qty": "0",
                "fill_price": str(intent["price"])}

    def cancel(self, order_id, market_slug=None):
        self.cancelled.append(order_id)
        self.retired.add(order_id)
        return {"cancelled": order_id}

    def fill(self, oid, qty=None):
        """The venue fills this order; a full fill is terminal."""
        want = D(str(self.by_oid[oid]["qty"]))
        got = want if qty is None else D(str(qty))
        assert got <= want, "a venue cannot fill more than it was asked"
        self.cum[oid] = str(got)
        if got == want:
            self.full.add(oid)

    def oid(self, side, nth=-1):
        return [o for o, i in self.by_oid.items()
                if str(i["side"]) == side][nth]

    def _wire(self, oid):
        i = self.by_oid[oid]
        px = D(str(i["price"]))
        return px if str(i["side"]) == "UP" else D(1) - px

    def order(self, oid):
        self.reads[oid] += 1
        if self.read_error.get(oid, 0) > 0:
            self.read_error[oid] -= 1
            raise ConnectionError("venue read failed")
        qty = D(str(self.by_oid[oid]["qty"]))
        cum = D(self.cum[oid])
        money = {"value": "0", "currency": "USD"}
        if oid in self.full:
            state, leaves = "ORDER_STATE_FILLED", D(0)
        elif oid in self.retired and self.lag[oid] <= 0:
            state, leaves = "ORDER_STATE_CANCELED", D(0)
        else:
            if oid in self.retired:
                self.lag[oid] -= 1
            state = ("ORDER_STATE_PARTIALLY_FILLED" if cum > 0
                     else "ORDER_STATE_NEW")
            leaves = qty - cum
        return {"id": oid, "state": state, "quantity": str(qty),
                "cumQuantity": str(cum), "leavesQuantity": str(leaves),
                "avgPx": {"value": str(self._wire(oid)), "currency": "USD"},
                "commissionNotionalTotalCollected": money}

    def open_orders(self):
        """The venue's open list. A cancelled order leaves it AT ONCE, even
        while its own row still lags -- which is what 1300z's reconciliation
        saw four seconds before the row said CANCELED."""
        return [{"id": oid, "clientOrderId": i["client_order_id"],
                 "marketSlug": SLUG, "qty": str(i["qty"]),
                 "remainingQuantity": str(D(str(i["qty"])) - D(self.cum[oid]))}
                for oid, i in self.by_oid.items()
                if oid not in self.full and oid not in self.retired]

    def positions(self):
        """ONE signed row per market, as PMUS reports it: no side field."""
        up = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                 if str(i["side"]) == "UP")
        dn = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                 if str(i["side"]) == "DOWN")
        if up == 0 and dn == 0:
            return []
        return [{"marketSlug": SLUG, "qtyBought": str(up), "qtySold": str(dn),
                 "netPositionDecimal": str(up - dn)}]


# ------------------------------------------------------------------ harness
#: the per-tick stages this file drives, in `Runner.tick` order.
STAGES = ("maybe_acc_deadline", "maybe_ts_completion", "maybe_book_completion",
          "maybe_controller_open", "maybe_completion_requote",
          "maybe_ts_deadline")


class Harness:
    """One real Runner, one scripted venue, one clock."""

    def __init__(self, tmp_path, monkeypatch, *, bid="0.52", ask="0.54",
                 root="live"):
        self.emitted = []
        monkeypatch.setattr(
            R, "emit", lambda label, rec=None, *a, **k:
            self.emitted.append((label, rec)))
        self.t = float(WS) + 6.0
        self.book = Book(bid, ask)
        self.venue = Venue()
        self.r = R.Runner(root=str(tmp_path / root), book=self.book,
                          feed=lambda: [])
        self.attach()
        self._seq = 0

    def attach(self):
        """What `Runner.roll` does after it builds the transport."""
        r = self.r
        now_fn = lambda: dt.datetime.fromtimestamp(r.now_t, dt.timezone.utc)
        r.clock = lambda: self.t
        r.now_t = self.t
        r.ws = WS
        r.transport = self.venue
        r.shadow_ladder = Ladder()
        r.store._now = now_fn
        # `R.PROBE_LIMITS` is frozen when the module is first imported, which
        # in a test session is long before this environment exists. These are
        # the limits the service's own heartbeat reported in force at 02:00Z.
        r.limits = E.SafetyLimits(
            max_usd_per_market=D("20"), max_abs_residual=D("10"),
            max_total_exposure_usd=D("1.00"), max_concurrent_markets=1,
            max_live_orders=2, daily_realized_loss_halt_usd=D("5.00"))
        r.executor = E.FaithfulExecutor(
            r.store, r.limits, transport=self.venue, lam=r.lam,
            now_fn=now_fn, maker_treatment=True, maker_only=True,
            suppress_imbalance_increase=False,
            residual_cap_k=R.residual_cap_k())
        r.executor.ts_book = self.book
        r.executor.validation_gates = R.validation_gates()

    # -- time and stages
    def at(self, offset_s):
        self.t = float(WS) + float(offset_s)
        self.r.now_t = self.t
        return self

    def l5_action(self, side="Up", price="0.48", size=30.0):
        """One L5 fill arriving on the feed, through `Runner.handle`."""
        self._seq += 1
        self.r.handle({"slug": L5_SLUG, "transactionHash": "0x%d" % self._seq,
                       "asset": "a", "side": "BUY", "outcome": side,
                       "price": price, "size": size,
                       "timestamp": self.t - 2.5}, self.t)

    def tick(self, offset_s, poll=True):
        """One `Runner.tick`, minus roll, arming, settlement and the feed.

        `poll=False` is a tick whose order poll was not yet due: the runner
        polls an order every `poll_interval_s`, not every tick, and at
        02:00:35.9 the next read of the cancelled order was 1.4 s away.
        """
        self.at(offset_s)
        out = {}
        for name in STAGES:
            out[name] = getattr(self.r, name)(self.t)
        out["poll_orders"] = (self.r.poll_orders()
                              if poll and self.r.store.live_order_count()
                              else [])
        # and the two reads that follow it in `Runner.tick`, in its order
        out["resolve_pending_terminal"] = (
            self.r.resolve_pending_terminal(self.t)
            if self.r.store.pending_terminal() else [])
        self.r.reconcile_if_needed(self.t)
        return out

    # -- reads
    def live(self):
        return {str(v.get("side")): D(str(v.get("price")))
                for v in self.r.store.live_orders().values()}

    def sent(self):
        return [(str(s["side"]), D(str(s["price"])), D(str(s["qty"])))
                for s in self.venue.sent]

    def pos(self, side):
        return D(str(self.r.store.position(SLUG, side)))

    def skips(self, reason="ORDER_ALREADY_RESTING"):
        return [rec for label, rec in self.emitted
                if label == "L5_OPENING_DECISION"
                and (rec or {}).get("reason") == reason]


@pytest.fixture()
def h(tmp_path, monkeypatch):
    for k, v in PRODUCTION.items():
        monkeypatch.setenv(k, v)
    for k in ABSENT:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    return Harness(tmp_path, monkeypatch)


def opened(h):
    """02:00:06.5 -- the L5 action places leg one, as it did."""
    h.at(6.5).l5_action()
    assert h.sent() == [("DOWN", D("0.44"), D("0.6"))], h.venue.sent
    return h


def paired_resting(h):
    """Leg one from the L5 action, the complement from the next timer tick."""
    opened(h).tick(6.8)
    assert sorted(h.live()) == ["DOWN", "UP"], h.live()
    return h


def cancelled_by_requote(h, lag=3):
    """02:00:13 DOWN fills alone; 02:00:36 the requote cancels UP and the
    venue is slow to say so."""
    paired_resting(h)
    h.venue.fill(h.venue.oid("DOWN"))
    h.tick(13.0)
    assert h.pos("DOWN") == D("0.6") and h.pos("UP") == D(0)
    h.book.set("0.56", "0.57")
    up = h.venue.oid("UP")
    h.venue.lag[up] = lag
    # the service had reconciled at 02:00:31, so the 15 s retry throttle
    # kept the reconciliation out of the requote's own tick
    h.r.reconciled, h.r.last_reconcile_t = True, float(WS) + 31.0
    out = h.tick(35.9, poll=False)
    rq = out["maybe_completion_requote"]
    assert rq and rq.get("act") is True, (rq, dict(h.r.stats))
    assert h.venue.cancelled == [up]
    return h, up, rq


# ======================================================================
# 0. THE HARNESS IS THE RUNNER'S OWN ORDER, NOT MINE
# ======================================================================
def test_the_stages_are_called_in_the_order_runner_tick_calls_them():
    src = inspect.getsource(R.Runner.tick)
    at = [src.index("self.%s(" % s) for s in STAGES]
    assert at == sorted(at), dict(zip(STAGES, at))
    # and the venue is polled AFTER the decision stages, then parked orders
    # are resolved, then the account is reconciled -- as it is here
    tail = [src.index(x) for x in ("self.poll_orders()",
                                   "self.resolve_pending_terminal(now)",
                                   "self.reconcile_if_needed(now)")]
    assert at[-1] < tail[0] and tail == sorted(tail), tail


def test_the_harness_runs_production_wiring(h):
    ex = h.r.executor
    assert isinstance(h.r, R.Runner) and isinstance(ex, E.FaithfulExecutor)
    assert ex.maker_only is True
    assert ex.validation_gates.max_cash_deployed_usd == D("0.90")
    assert h.r.limits.max_total_exposure_usd == D("1.00")
    assert h.r.limits.max_live_orders == 2
    assert h.r.controller_open_enabled() is True


# ======================================================================
# 1. DEFECT ONE -- THE COMPLEMENT WAS LOCKED OUT BY ITS OWN FIRST LEG
# ======================================================================
class TestBothLegsRestTogether:

    def test_0200z_the_next_timer_tick_now_posts_the_complement(self, h):
        opened(h)
        out = h.tick(6.8)
        rec = out["maybe_controller_open"]
        assert rec is not None and rec.get("refused_reason") is None, rec
        assert h.sent() == [("DOWN", D("0.44"), D("0.6")),
                            ("UP", D("0.50"), D("0.6"))]
        assert h.r.stats["controller_open:PAIR_COMPLEMENT_SLOT"] == 1
        assert h.r.stats["controller_open:ORDER_ALREADY_RESTING"] == 0

    def test_both_orders_are_resting_at_the_venue_at_the_same_time(self, h):
        paired_resting(h)
        assert h.r.store.still_fillable_order_count() == 2
        for side in ("UP", "DOWN"):
            got = h.venue.order(h.venue.oid(side))
            assert got["state"] == "ORDER_STATE_NEW", got
            assert D(got["cumQuantity"]) == 0
        assert h.pos("UP") == 0 and h.pos("DOWN") == 0

    def test_they_are_one_priced_pair_inside_every_limit(self, h):
        paired_resting(h)
        live = h.live()
        assert live["UP"] + live["DOWN"] <= TS.max_pair_cost()
        assert {str(s["mode"]) for s in h.venue.sent} == {"MAKER"}
        # ONE ROUND: both legs are claimed in round 0, so this is the pair
        # the precheck priced and not two unrelated quotes.
        assert h.r.executor.probe_legs_claimed(SLUG, 0) == {
            "UP": True, "DOWN": True}
        cash = sum(px * q for _s, px, q in h.sent())
        assert cash <= D("0.90"), cash
        # worst case if BOTH fill is the pair's own cost, and if only one
        # fills it is that leg's: either way inside the $1.00 exposure cap.
        assert max(cash, *(px * q for _s, px, q in h.sent())) <= D("1.00")

    def test_a_third_order_is_never_placed(self, h):
        paired_resting(h)
        for off in (7.8, 8.8, 9.8, 11.8):
            h.tick(off)
        assert len(h.venue.sent) == 2, h.sent()
        assert h.r.pair_complement_slot_refusal(SLUG, 2) == \
            "NOT_EXACTLY_ONE_ORDER_RESTING"

    def test_both_fill_and_the_runner_books_one_matched_pair(self, h):
        paired_resting(h)
        h.venue.fill(h.venue.oid("DOWN"))
        h.tick(13.0)
        assert h.pos("DOWN") == D("0.6") and h.pos("UP") == 0
        assert list(h.live()) == ["UP"], "the hedge is still working"
        h.venue.fill(h.venue.oid("UP"))
        h.tick(19.5)
        assert h.pos("UP") == h.pos("DOWN") == D("0.6")
        assert h.live() == {}
        assert len(h.venue.sent) == 2 and h.venue.cancelled == []
        legs = h.r.store.leg_cost(SLUG)
        assert D(str(legs["UP"])) + D(str(legs["DOWN"])) == \
            D("0.6") * (D("0.50") + D("0.44"))

    def test_a_partial_first_leg_still_gets_its_complement(self, h):
        opened(h)
        h.venue.fill(h.venue.oid("DOWN"), "0.3")
        h.tick(6.8)
        assert sorted(h.live()) == ["DOWN", "UP"], h.live()


class TestTheLockoutThatMustRemain:
    """Each case is the 0200z state with ONE condition removed."""

    def test_without_the_owners_second_slot_it_is_the_old_lockout(
            self, h, monkeypatch):
        monkeypatch.delenv("L5_TS_TWO_ORDER_PAIR")
        opened(h)
        out = h.tick(6.8)
        assert out["maybe_controller_open"] is None
        assert len(h.venue.sent) == 1
        assert h.r.stats["controller_open:ORDER_ALREADY_RESTING"] == 1
        assert h.skips()[-1]["pair_complement_slot"] == \
            "TWO_ORDER_PAIR_DISABLED"

    def test_with_one_authorized_live_order_it_refuses(self, h, monkeypatch):
        monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "1")
        opened(h)
        h.tick(6.8)
        assert len(h.venue.sent) == 1
        assert h.skips()[-1]["pair_complement_slot"] == \
            "NO_AUTHORIZED_SECOND_SLOT"

    def test_a_resting_order_in_another_market_is_not_a_first_leg(self, h):
        opened(h)
        rows = h.r.store.state["live_orders"]
        for row in rows.values():
            row["market"] = "cpc-btc-updown-15m-2026-11-10-0145z"
        assert h.r.pair_complement_slot_refusal(SLUG, 1) == \
            "RESTING_ORDER_IS_ANOTHER_MARKET"

    def test_a_resting_order_on_the_unplaced_side_is_not_a_first_leg(self, h):
        """A same-side order would be an EXTEND, never this slot's."""
        opened(h)
        for row in h.r.store.state["live_orders"].values():
            row["side"] = "UP"
        assert h.r.pair_complement_slot_refusal(SLUG, 1) == \
            "RESTING_ORDER_IS_THE_SAME_SIDE"

    def test_a_round_with_no_leg_placed_is_an_opening_not_a_complement(
            self, h):
        assert h.r.pair_complement_slot_refusal(SLUG, 1) == \
            "ROUND_IS_NOT_HALF_PLACED"

    def test_a_parked_or_quarantined_order_refuses(self, h):
        opened(h)
        assert h.r.pair_complement_slot_refusal(SLUG, 2) == \
            "NOT_EXACTLY_ONE_ORDER_RESTING"
        assert h.r.pair_complement_slot_refusal(SLUG, 0) == \
            "NOT_EXACTLY_ONE_ORDER_RESTING"

    @pytest.mark.parametrize("broken", ["probe_round", "probe_legs_claimed"])
    def test_unreadable_claim_state_fails_closed(self, h, monkeypatch,
                                                 broken):
        opened(h)

        def boom(*a, **k):
            raise OSError("claim file unreadable")

        monkeypatch.setattr(h.r.executor, broken, boom)
        assert h.r.pair_complement_slot_refusal(SLUG, 1) == \
            "PAIR_SLOT_UNREADABLE:OSError"
        h.tick(6.8)
        assert len(h.venue.sent) == 1

    def test_an_unreadable_ledger_fails_closed(self, h, monkeypatch):
        opened(h)
        monkeypatch.setattr(h.r.store, "live_orders", lambda: None)
        assert h.r.pair_complement_slot_refusal(SLUG, 1) == \
            "LIVE_ORDERS_UNREADABLE"


class TestTheGateOnlyLetsTheTickReachTheExecutor:

    def test_a_complement_outside_the_cash_limit_is_still_refused(
            self, h, monkeypatch):
        """The slot is not headroom: the executor's own cash gate decides."""
        opened(h)
        h.r.executor.validation_gates.max_cash_deployed_usd = D("0.40")
        out = h.tick(6.8)
        rec = out["maybe_controller_open"]
        assert rec is not None and rec.get("refused_reason"), rec
        assert "CASH_COMMITMENT" in str(rec["refused_reason"]), rec
        assert len(h.venue.sent) == 1

    def test_the_executor_repeats_the_slot_test_itself(self, h):
        """Two independent askers of one question, not one trusting the
        other: the runner asks to let the tick through, the executor asks
        before it raises MAX_LIVE_ORDERS."""
        src = inspect.getsource(R.Runner.pair_complement_slot_refusal)
        assert "TSP.second_leg_slot_refusal(" in src
        assert "TS.second_leg_slot_refusal(" in inspect.getsource(
            E.FaithfulExecutor.handle) or "second_leg_slot_refusal" in \
            inspect.getsource(E)

    def test_no_taker_order_and_no_crossing_price(self, h):
        paired_resting(h)
        bid, ask = D("0.52"), D("0.54")
        live = h.live()
        assert live["UP"] < ask, "an UP bid at or above the ask crosses"
        assert (D(1) - live["DOWN"]) > bid, "a DOWN ask at the bid crosses"

    def test_the_complement_is_not_charged_a_replenishment_credit(
            self, h, monkeypatch):
        """With replenishment ON a RE-OPEN needs a fill or a cancel behind
        it. The second leg of a pair is not a re-open."""
        monkeypatch.setenv("L5_DUAL_REPLENISH", "ON")
        paired_resting(h)
        assert not any("REPLENISH_UNPAID" in k for k in h.r.stats)


# ======================================================================
# 2. DEFECT TWO -- A CANCELLED ORDER THE LEDGER STILL CALLED LIVE
# ======================================================================
class TestTheVenuesAnswerEndsTheUncertaintyItCaused:

    def test_0200z_the_requote_is_the_one_the_service_made(self, h):
        _h, _up, rq = cancelled_by_requote(h)
        assert (rq["from_price"], rq["to_price"]) == ("0.50", "0.52")
        assert rq["redemption_cap_binds"] is True
        assert rq["pair_own_cost_at_target"] == "0.9600"

    def test_0200z_the_deadman_marks_the_book_and_names_the_order(self, h):
        _h, up, _rq = cancelled_by_requote(h)
        assert h.venue.reads[up] >= 3, "three re-reads, all still working"
        why = h.r.store.orders_uncertain()
        assert why == ("deadman cancelled %s but the venue still reports "
                       "it live" % up)
        cause = h.r.store.state[E.UNCERTAIN_CAUSE]
        assert cause["kind"] == "CANCEL_STILL_OPEN"
        assert cause["venue_order_id"] == up and cause["why"] == why

    def test_0200z_the_poll_reads_it_cancelled_and_the_mark_clears(self, h):
        """THE DEFECT. This read happened at 02:00:37.3 and changed
        nothing; the mark stood until a reconciliation at 02:00:48."""
        _h, up, _rq = cancelled_by_requote(h, lag=3)
        out = h.tick(37.3)
        states = [e.get("state") for e in out["poll_orders"]]
        assert states == ["ORDER_STATE_CANCELED"], out["poll_orders"]
        assert h.r.store.orders_uncertain() is None
        assert h.r.store.state[E.UNCERTAIN_CAUSE] is None
        audit = h.r.store.state["uncertain_resolved"]
        assert audit[-1]["venue_order_id"] == up
        assert audit[-1]["kind"] == "CANCEL_STILL_OPEN"
        assert audit[-1]["venue_state"] == "ORDER_STATE_CANCELED"

    def test_0200z_the_replacement_is_posted_on_the_next_tick(self, h):
        """Was 02:00:54, eighteen seconds after the cancel. Now the tick
        after the venue answers."""
        cancelled_by_requote(h, lag=3)
        h.tick(37.3)
        assert len(h.venue.sent) == 2, "nothing while the mark stood"
        h.tick(38.3)
        assert len(h.venue.sent) == 3, (h.sent(), dict(h.r.stats))
        side, px, qty = h.sent()[-1]
        assert side == "UP" and qty == D("0.6")
        assert str(h.venue.sent[-1]["mode"]) == "MAKER"
        assert px < D("0.57"), "a bid at the ask would be a taker"
        assert px + D("0.44") <= D("1.00"), "never above redemption"
        assert list(h.live()) == ["UP"]

    def test_nothing_is_submitted_while_the_venue_still_says_live(self, h):
        """A venue row slower than every read keeps the book uncertain, and
        an uncertain book places nothing. This is the protection, intact.

        The reconciliation that runs meanwhile parks the order -- it has left
        the open list -- and the pre-existing rule books a parked order final
        on any readable row. Neither shortcut in this file fires on that: a
        row still reading NEW is the venue's lag, not its answer.
        """
        cancelled_by_requote(h, lag=50)
        for off in (37.3, 38.3, 43.4, 49.5):
            h.tick(off)
            assert h.r.store.orders_uncertain() is not None, off
        assert len(h.venue.sent) == 2
        assert h.r.store.state.get("uncertain_resolved") in (None, [])

    def test_a_fill_that_beat_the_cancel_is_booked_and_not_hedged_twice(
            self, h):
        """The cancel lost the race to a FILL. The terminal read books the
        fill, the pair is matched, and no replacement is posted."""
        _h, up, _rq = cancelled_by_requote(h, lag=3)
        h.venue.fill(up)
        h.tick(37.3)
        assert h.pos("UP") == h.pos("DOWN") == D("0.6")
        assert h.r.store.orders_uncertain() is None
        for off in (38.3, 43.4, 49.5):
            h.tick(off)
        assert len(h.venue.sent) == 2, h.sent()
        assert h.live() == {}

    def test_an_unverified_cancel_is_ended_the_same_way(self, h):
        """Every re-read RAISED: the other 'could not see it finish'."""
        paired_resting(h)
        h.venue.fill(h.venue.oid("DOWN"))
        h.tick(13.0)
        h.book.set("0.56", "0.57")
        up = h.venue.oid("UP")
        h.venue.read_error[up] = 3
        h.r.reconciled, h.r.last_reconcile_t = True, float(WS) + 31.0
        h.tick(35.9, poll=False)
        why = h.r.store.orders_uncertain()
        assert why and "could not verify the cancel of %s" % up in why
        assert h.r.store.state[E.UNCERTAIN_CAUSE]["kind"] == \
            "CANCEL_UNVERIFIED"
        h.tick(42.0)
        assert h.r.store.orders_uncertain() is None


class TestItClearsOnlyWhatItCanProve:

    def test_a_later_uncertainty_is_not_this_ones_to_clear(self, h):
        cancelled_by_requote(h, lag=3)
        h.r.store.mark_uncertain("gross/net divergence ['%s']" % SLUG)
        h.tick(37.3)
        assert h.r.store.orders_uncertain() == \
            "gross/net divergence ['%s']" % SLUG
        assert len(h.venue.sent) == 2

    def test_a_mark_with_no_cause_is_never_cleared_by_a_poll(self, h):
        paired_resting(h)
        h.r.store.mark_uncertain("ambiguous POST for l5f-x")
        assert h.r.store.state[E.UNCERTAIN_CAUSE] is None
        h.venue.fill(h.venue.oid("DOWN"))
        h.tick(13.0)                       # a terminal read of a real order
        assert h.r.store.orders_uncertain() == "ambiguous POST for l5f-x"

    def test_another_orders_terminal_read_does_not_clear_it(self, h):
        """The cause names UP. DOWN finishing says nothing about UP."""
        paired_resting(h)
        up, dn = h.venue.oid("UP"), h.venue.oid("DOWN")
        coid_up = [c for c, v in h.r.store.live_orders().items()
                   if v.get("side") == "UP"][0]
        why = "deadman cancelled %s but the venue still reports it live" % up
        h.r.store.mark_uncertain(why, cause={
            "kind": "CANCEL_STILL_OPEN", "coid": coid_up,
            "venue_order_id": up})
        h.venue.fill(dn)
        h.tick(13.0)
        assert h.pos("DOWN") == D("0.6")
        assert h.r.store.orders_uncertain() == why

    def test_a_cause_naming_the_wrong_venue_id_does_not_clear(self, h):
        _h, up, _rq = cancelled_by_requote(h, lag=3)
        h.r.store.state[E.UNCERTAIN_CAUSE]["venue_order_id"] = "someone-elses"
        h.tick(37.3)
        assert h.r.store.orders_uncertain() is not None

    @pytest.mark.parametrize("bucket", ["pending_terminal", "quarantine"])
    def test_a_parked_or_quarantined_order_keeps_the_book_uncertain(
            self, h, bucket):
        cancelled_by_requote(h, lag=3)
        h.r.store.state.setdefault(bucket, {})["l5f-other"] = {
            "coid": "l5f-other", "reason": "ABSENT_FROM_VENUE_OPEN_ORDERS"}
        h.at(37.3)
        h.r.poll_orders()
        assert h.r.store.orders_uncertain() is not None

    def test_an_unknown_cause_kind_is_not_cleared(self, h):
        _h, _up, _rq = cancelled_by_requote(h, lag=3)
        h.r.store.state[E.UNCERTAIN_CAUSE]["kind"] = "NO_VENUE_ID"
        h.tick(37.3)
        assert h.r.store.orders_uncertain() is not None

    def test_the_cause_survives_a_restart_and_still_resolves(
            self, h, tmp_path, monkeypatch):
        """The mark is durable, so its evidence has to be too: a process
        that restarts between the cancel and the venue's answer must not be
        left uncertain until a reconciliation."""
        _h, up, _rq = cancelled_by_requote(h, lag=3)
        again = Harness.__new__(Harness)
        again.emitted, again.t = [], h.t
        again.book, again.venue, again._seq = h.book, h.venue, h._seq
        again.r = R.Runner(root=str(tmp_path / "live"), book=h.book,
                           feed=lambda: [])
        again.attach()
        assert again.r.store.orders_uncertain() is not None
        assert again.r.store.state[E.UNCERTAIN_CAUSE]["venue_order_id"] == up
        again.tick(37.3)
        assert again.r.store.orders_uncertain() is None

    def test_the_audit_trail_is_bounded(self, h):
        st = h.r.store
        st.state["uncertain_resolved"] = [{"n": i} for i in range(200)]
        cancelled_by_requote(h, lag=3)
        h.tick(37.3)
        assert len(st.state["uncertain_resolved"]) == \
            E.UNCERTAIN_RESOLVED_KEEP
        assert st.state["uncertain_resolved"][-1]["kind"] == \
            "CANCEL_STILL_OPEN"


def test_reconciliation_still_clears_and_still_marks_as_before():
    """The periodic account reconciliation is untouched: it remains the only
    thing that clears a mark without a cause, and it still re-marks."""
    src = inspect.getsource(E.InventoryStore)
    assert src.count('self.state["orders_uncertain"] = None') >= 3
    assert "gross/net divergence %s; incoherent book %s" in src


# ======================================================================
# 3. THE MARK IS NEVER TRADED FOR THE CAUSE
# ======================================================================
class _LegacyStore:
    """A ledger that predates `cause`: `mark_uncertain(why)` and no more."""

    def __init__(self, rows):
        self._rows, self.marks = rows, []

    def live_orders(self):
        return {k: dict(v) for k, v in self._rows.items()}

    def mark_uncertain(self, why):
        self.marks.append(str(why))

    def apply_order_progress(self, coid, prog, *, market, side):
        return {"delta": "0"}


class _StillWorking:
    market_slug = SLUG

    def cancel(self, vid, market_slug=None):
        return {"cancelled": vid}

    def order(self, vid):
        return {"id": vid, "state": "ORDER_STATE_NEW", "cumQuantity": "0",
                "leavesQuantity": "0.6",
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}


def test_a_store_that_cannot_hold_a_cause_is_still_marked_uncertain():
    """Found by the existing requote-wiring suite, whose store double takes
    one argument: passing `cause=` straight through raised inside it and the
    mark was never made. Failing to name the order must never cost the
    refusal that protects against a replacement racing a live one."""
    store = _LegacyStore({"c1": {"venue_order_id": "v9", "market": SLUG,
                                 "side": "UP"}})
    out = E.deadman(store, _StillWorking(), reason="completion_requote",
                    only=["c1"], verify_sleeper=lambda s: None)
    assert [r["venue_order_id"] for r in out["still_open"]] == ["v9"]
    assert store.marks == [
        "deadman cancelled v9 but the venue still reports it live"]


# ======================================================================
# 4. THE SAME DEFECT ONE WRITER OVER -- 2026-11-10 1300z, LIVE
# ======================================================================
#
#   13:02:14.3  order-age deadline cancels DOWN; three re-reads still NEW
#   13:02:14.8  SAME TICK: reconciliation finds it gone from open orders,
#               parks it, and replaces the mark with its own
#               "unbooked terminal orders [...]"
#   13:02:18.3  the venue's order row: CANCELED, nothing filled
#   13:02:29.7  the NEXT reconciliation clears the mark -- 11.4 s later
#
# Section 2's fix could not see it: a reconciliation's mark carried no cause.

@pytest.fixture()
def h95(tmp_path, monkeypatch):
    """A pair that opens at 0.95, as 1300z's did (0.41 + 0.54). The resting
    leg is then one tick inside its redemption limit, so -- as in that
    window -- no requote is worth making and the order simply ages."""
    for k, v in PRODUCTION.items():
        monkeypatch.setenv(k, v)
    for k in ABSENT:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    return Harness(tmp_path, monkeypatch, bid="0.52", ask="0.53")


def cancelled_by_deadline(h, lag=3, fill="0"):
    """Leg one fills and the market runs away from the other, which rests
    past the 120 s order deadline and is cancelled -- the venue's row
    lagging its own open list."""
    h.at(6.5).l5_action()
    h.tick(6.8)
    assert h.live() == {"DOWN": D("0.45"), "UP": D("0.50")}, h.live()
    h.venue.fill(h.venue.oid("DOWN"))
    h.tick(13.0)
    assert h.pos("DOWN") == D("0.6")
    h.book.set("0.87", "0.88")          # UP now costs 0.87 against a 0.51 cap
    up = h.venue.oid("UP")
    h.venue.lag[up] = lag
    if D(fill) > 0:
        h.venue.fill(up, fill)
    for off in (40.0, 70.0, 100.0):
        h.tick(off)
    assert h.venue.cancelled == [], (
        "nothing is cancelled inside its TTL", dict(h.r.stats))
    out = h.tick(134.3)
    return h, up, out


class TestAReconciliationMarkAboutParkedOrdersOnly:

    def test_1300z_the_deadline_cancel_is_re_marked_by_reconciliation(
            self, h95):
        _h, up, out = cancelled_by_deadline(h95)
        assert h95.venue.cancelled == [up]
        assert out["maybe_acc_deadline"] is not None
        why = h95.r.store.orders_uncertain()
        assert why and "unbooked terminal orders ['l5f-" in why, why
        assert "gross/net divergence []" in why and "incoherent book []" in why
        cause = h95.r.store.state[E.UNCERTAIN_CAUSE]
        assert cause["kind"] == E.RECONCILE_PENDING_ONLY
        assert len(cause["pending"]) == 1 and cause["why"] == why
        assert list(h95.r.store.pending_terminal()) == cause["pending"]

    def test_1300z_the_venues_cancelled_row_now_ends_it(self, h95):
        """THE DEFECT. The row said CANCELED at 13:02:18.3 and the mark
        stood another 11.4 s."""
        _h, up, _out = cancelled_by_deadline(h95)
        out = h95.tick(138.3)
        res = out["resolve_pending_terminal"]
        assert [e.get("event") for e in res] == ["PENDING_TERMINAL_RESOLVED"]
        assert res[0].get("state") == "ORDER_STATE_CANCELED", res
        assert h95.r.store.orders_uncertain() is None
        assert h95.r.store.pending_terminal() == {}
        audit = h95.r.store.state["uncertain_resolved"][-1]
        assert audit["kind"] == E.RECONCILE_PENDING_ONLY
        assert len(audit["orders"]) == 1
        assert res[0]["uncertainty_cleared"]["kind"] == \
            E.RECONCILE_PENDING_ONLY

    def test_1300z_it_did_not_need_the_next_reconciliation(self, h95):
        """The clear is from the venue's answer, inside the 15 s retry
        window in which no reconciliation can run."""
        cancelled_by_deadline(h95)
        was = h95.r.last_reconcile_t
        h95.tick(138.3)
        assert h95.r.store.orders_uncertain() is None
        assert h95.r.last_reconcile_t == was, "no second reconciliation ran"
        assert 138.3 - (was - WS) < R.RECONCILE_RETRY_S

    def test_a_fill_that_arrived_with_the_cancel_waits_for_reconciliation(
            self, h95):
        """A fill changes the positions the reconciliation had just
        verified, so the shortcut is forfeited and the next reconciliation
        confirms the book -- as it always did."""
        _h, up, _out = cancelled_by_deadline(h95)
        # 0.2 fills in the instant between the cancel being sent and taking
        # effect: after the reconciliation read the account, before the
        # order row is read.
        h95.venue.cum[up] = "0.2"
        h95.tick(138.3)
        assert h95.r.store.pending_terminal() == {}
        assert h95.pos("UP") == D("0.2"), "the fill is booked from the venue"
        assert h95.r.store.orders_uncertain() is not None
        h95.tick(150.0)                           # >= 15 s after 134.3
        assert h95.r.store.orders_uncertain() is None
        assert h95.pos("UP") == D("0.2") and h95.pos("DOWN") == D("0.6")

    def test_a_row_still_lagging_at_resolution_waits_for_reconciliation(
            self, h95):
        cancelled_by_deadline(h95, lag=4)         # one more read than 1300z
        out = h95.tick(138.3)
        assert out["resolve_pending_terminal"][0].get("state") == \
            "ORDER_STATE_NEW"
        assert h95.r.store.orders_uncertain() is not None
        assert h95.r.store.state.get("uncertain_resolved") in (None, [])
        h95.tick(150.0)
        assert h95.r.store.orders_uncertain() is None

    def test_a_later_mark_is_not_this_ones_to_clear(self, h95):
        cancelled_by_deadline(h95)
        h95.r.store.mark_uncertain("no venue progress on 1 live order(s)")
        h95.r.last_reconcile_t = h95.t              # keep reconciliation out
        h95.r.resolve_pending_terminal(h95.at(138.3).t)
        assert h95.r.store.orders_uncertain() == \
            "no venue progress on 1 live order(s)"


def _store(tmp_path):
    return E.InventoryStore(str(tmp_path / "inv.json"))


def _park(st, coid, vid):
    st.state.setdefault("pending_terminal", {})[coid] = {
        "coid": coid, "venue_order_id": vid, "market": SLUG, "side": "UP",
        "applied": "0", "reason": "ABSENT_FROM_VENUE_OPEN_ORDERS"}


def _prog(state="ORDER_STATE_CANCELED", cum="0", terminal=True):
    return {"state": state, "cum_qty": D(cum), "leaves_qty": D(0),
            "avg_px": D("0"), "commission": D("0"), "terminal": terminal}


class TestWhatReconciliationRecords:

    def test_parked_orders_alone_record_a_cause(self, tmp_path):
        st = _store(tmp_path)
        _park(st, "c1", "v1")
        st.reconcile_from_venue([], [])
        assert st.state[E.UNCERTAIN_CAUSE]["kind"] == E.RECONCILE_PENDING_ONLY
        assert st.state[E.UNCERTAIN_CAUSE]["pending"] == ["c1"]

    def test_a_divergence_records_none_and_only_reconciliation_clears_it(
            self, tmp_path):
        st = _store(tmp_path)
        st.state["positions"] = {"%s|UP" % SLUG: "0.6"}
        _park(st, "c1", "v1")
        st.reconcile_from_venue(
            [{"marketSlug": SLUG, "qtyBought": "0.2", "qtySold": "0",
              "netPositionDecimal": "0.2"}], [])
        assert st.orders_uncertain() and st.state[E.UNCERTAIN_CAUSE] is None
        st.resolve_pending_terminal("c1", _prog())
        assert st.orders_uncertain() is not None

    def test_a_quarantine_records_none(self, tmp_path):
        st = _store(tmp_path)
        _park(st, "c1", "v1")
        st.state["quarantine"] = {"q1": {"reason": "UnknownMarket"}}
        st.reconcile_from_venue([], [])
        assert st.orders_uncertain() and st.state[E.UNCERTAIN_CAUSE] is None

    def test_two_parked_orders_clear_only_when_BOTH_have_answered(
            self, tmp_path):
        st = _store(tmp_path)
        _park(st, "c1", "v1")
        _park(st, "c2", "v2")
        st.reconcile_from_venue([], [])
        st.resolve_pending_terminal("c1", _prog())
        assert st.orders_uncertain() is not None, "one is still unanswered"
        st.resolve_pending_terminal("c2", _prog())
        assert st.orders_uncertain() is None
        assert st.state["uncertain_resolved"][-1]["orders"] == ["c1", "c2"]

    def test_a_clean_reconciliation_leaves_no_cause_behind(self, tmp_path):
        st = _store(tmp_path)
        st.mark_uncertain("x", cause={"kind": "CANCEL_STILL_OPEN",
                                      "coid": "c", "venue_order_id": "v"})
        st.reconcile_from_venue([], [])
        assert st.orders_uncertain() is None
        assert st.state[E.UNCERTAIN_CAUSE] is None
