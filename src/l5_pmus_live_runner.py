"""THE production path for the faithful L5 probe on Polymarket US BTC15m.

    L5 realtime activity -> authenticated PMUS BTC15m book -> L5_NATIVE (frozen)
      -> PMUS_TRANSLATION (frozen) -> SAFETY_OVERLAY -> PmusTransport

This is not another shadow. It is the one process that will place the probe's
orders once the owner authorizes it, running today exactly as it will run
then. The ONLY thing that differs between now and an authorized run is whether
`PmusTransport.arm()` succeeds, and that needs BOTH a preflight that PASSED in
this process for the current market AND the owner's arm token. Until then
every intent reaches the transport and is refused as NOT_ARMED, and the refusal
is recorded on the action's provenance record.

WHAT IT DOES, EVERY WINDOW
  * the authenticated WS book subscribes the window BEFORE it opens and
    reports when its first book and first trade arrived (observability)
  * the authenticated preflight runs against the window's market, retried
    until it passes or the window ends, and its report is logged verbatim
  * each new L5 fill in that window is driven through FaithfulExecutor with
    the authenticated book AT RECEIPT attached -- nothing later, nothing public
  * at window end, a per-window summary: decisions by layer, observability,
    and (if armed) the venue's own position

WHAT IT DOES NOT DO
  * change L5_NATIVE or the frozen translation (030d8bf); both are imported,
    not copied, and the freeze guard hashes them
  * add a band, ceiling, maker-only rule or predictor. The only refusals it
    adds are account protection in SAFETY_OVERLAY, each named.

Logs are single-line JSON documents prefixed L5_LIVE_*, so they can be read
back from the platform's log stream without a volume.

    L5_LIVE_ROOT        state dir (inventory, decisions tape)
    L5_LIVE_POLL_S      L5 activity poll interval (default 3)
    L5_LIVE_LAMBDA      sizing factor (default: the manifest's lambda)
    L5_LIVE_EXPECT_ACCOUNT  optional: refuse unless the venue reports this id
    L5_LIVE_HALT        any non-empty value: deadman now, never arm
    PMUS_Q1_LIVE / PMUS_Q1_SESSION_ID / PMUS_Q1_ARM   the OWNER's arm token
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import signal
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_faithful_translation as FT
from src import l5_live_execution as LX
from src import l5_pmus_auth_book as AB
from src import l5_pmus_realtime_observer as OBS
from src import l5_pmus_transport as T
from src import pmus_q1_executor as Q1

SERVICE_ROLE = "l5-pmus-live"
ROLE_ENV = "PMUS_SERVICE_ROLE"

#: P38 manifest: 1 / median(l5_size) over the preserved corpus
MANIFEST_LAMBDA = D("0.04518752824220514525")

#: P38 manifest limits -- account protection only
PROBE_LIMITS = dict(max_usd_per_market=D("5"), max_abs_residual=D("10"),
                    max_total_exposure_usd=D("5"), max_concurrent_markets=1,
                    max_live_orders=8,
                    daily_realized_loss_halt_usd=D("5"))

#: retried quickly: until it passes, an armed runner cannot act on the
#: window, and L5 opens within seconds (0045z: first action at +7.0s).
#: Five GETs per attempt, only until the first PASS of each window.
PREFLIGHT_RETRY_S = 3.0
#: and how it grows while the venue simply has not listed the next market
#: yet. 2300z on 2026-09-26 spent 126 seconds in that state and spent 80
#: venue requests on it -- 0.63 req/s, disarmed, with no orders. Retrying at
#: a fixed 3s against a market that does not exist yet is not urgency, it is
#: a busy-wait against a rate limit.
PREFLIGHT_BACKOFF_S = (3.0, 6.0, 12.0, 24.0)
#: while the book is uncertain, how often we may re-ask the venue. Not every
#: tick: hammering a venue that is already erroring turns a transient fault
#: into a rate limit.
RECONCILE_RETRY_S = 15.0
#: a locally-live order that produces no venue progress for this long is not
#: trusted to be live. 1700z sat on eight of them: the venue had finished
#: with all eight, we never learned it, and MAX_LIVE_ORDERS refused 276
#: actions against orders that did not exist. Uncertainty was never set, so
#: the uncertainty path could not rescue it either.
STALLED_ORDER_S = 90.0
HB_EVERY_S = 60.0
#: how often ONE order may be re-read. The tick is 3s and the old code
#: polled every live order on every tick: with a full envelope that is 2.7
#: GETs a second from this path alone, which is most of how we rate-limited
#: ourselves into the 2045z defect. A fill is still seen within seconds, and
#: the authenticated websocket book -- not this poll -- is the fast path.
ORDER_POLL_S = 6.0
#: THE AGGREGATE CEILING for order progress, which is what actually matters.
#: A per-order interval alone scales the request rate with the size of the
#: envelope: eight orders at one read per 6s is 1.333 req/s from this path
#: alone, most of the 1.5 the venue tolerates, before preflight or
#: settlement or reconciliation have asked for anything. The per-order
#: interval is therefore a FLOOR and this is the cap: with few orders
#: nothing changes, and a full envelope stretches instead of overflowing.
ORDER_POLL_RATE = 0.5
#: how often ONE market's settlement may be re-read. A market that has not
#: expired will not have expired 3 seconds later either.
SETTLEMENT_POLL_S = 10.0
#: how often a vanished order may be re-asked about. These resolve on the
#: first or second attempt; the interval exists so a genuinely unreadable
#: order cannot become a hot loop against a venue that is already refusing.
#: SERIAL, and globally. Parked orders are the highest-priority read in the
#: system -- each one owes the ledger a fill and the book is uncertain until
#: every one of them is resolved -- but they are also the easiest to
#: multiply: eight parked orders at one read each per 5s is 1.6 req/s from a
#: path that only exists because the venue was already refusing us. One
#: request per interval, oldest first, however many are parked. Serial
#: resolution is slower and is not less safe: the book stays uncertain and
#: every submission stays refused for the whole of it, which is the same
#: state it would be in anyway.
PENDING_RESOLVE_S = 3.0
#: SOAK INSTRUMENTATION, off unless set, and ignored while armed.
#:
#: A disarmed process with no live orders reconciles once at boot and then
#: never again -- nothing sets uncertainty, nothing stalls, so the venue is
#: never re-read. That is correct for production and useless for proving
#: that the reconciliation path works against the real venue rather than
#: against a fake one, which is the whole point of a disarmed soak.
#:
#: When set, and ONLY while disarmed, the runner takes a periodic
#: authoritative read: two GETs, positions and open orders, through the
#: same rate limiter and the same InventoryStore.reconcile_from_venue that
#: an armed process uses. It creates nothing, cancels nothing and submits
#: nothing. Unset (the default) the behaviour is bit-for-bit what it was.
SOAK_RECONCILE_S = float(os.getenv("L5_LIVE_SOAK_RECONCILE_S", "0") or 0)


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(doc, default=str,
                                      separators=(",", ":"))), flush=True)


def residual_cap_k() -> Optional[D]:
    """The k for RESIDUAL_CAP, read from PMUS_RESIDUAL_CAP_K. None is OFF.

    Unset, blank, unparseable and non-positive all mean OFF rather than some
    default k. This selects a policy, and a policy a typo can switch on is
    not selectable. The value is echoed in the heartbeat, so which policy ran
    is read off the record and never inferred from what happened.
    """
    raw = (os.getenv("PMUS_RESIDUAL_CAP_K") or "").strip()
    if not raw:
        return None
    try:
        k = D(raw)
    except Exception:                                          # noqa: BLE001
        return None
    return k if k > 0 else None


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def engine_row(r: Mapping[str, Any], book: Optional[Mapping[str, Any]],
               recv: float) -> Dict[str, Any]:
    """One L5 fill in the shape the frozen rule reads, with the AUTHENTICATED
    book at our receipt instant attached.

    `pmus_snapshot_age_s` is the age of the book KNOWLEDGE: on a connected
    push stream that is the time since the socket last proved itself live.
    A dead or silent stream therefore ages past the frozen rule's own 2.0s
    bound and is refused as STALE; it is never repriced from anything later.
    """
    act = OBS.l5_to_pmus_action(r)
    ev_t = float(r.get("timestamp") or 0)
    bbo = None
    age = None
    if book:
        bbo = {"bid": book.get("bid"), "ask": book.get("ask"),
               "bid_shares": book.get("bid_shares"),
               "ask_shares": book.get("ask_shares")}
        sa = book.get("stream_age_s")
        age = 0.0 if sa is not None and sa <= FT.STALE_MAX_S else sa
    return {
        "action_key": OBS.action_key(r),
        "l5_event_utc": dt.datetime.fromtimestamp(
            ev_t, dt.timezone.utc).isoformat(),
        "receipt_utc": dt.datetime.fromtimestamp(
            recv, dt.timezone.utc).isoformat(),
        "latency_s": round(recv - ev_t, 3),
        "l5": {"slug": r.get("slug"), "tx": r.get("transactionHash"), **act},
        "copyability": {"l5_price": act.get("l5_price")},
        "pmus": {"bbo": bbo, "book_source": (book or {}).get("source"),
                 "stream_age_s": (book or {}).get("stream_age_s"),
                 "book_last_update_t": (book or {}).get("last_update_t")},
        "pmus_snapshot_age_s": age,
    }


class Runner:
    def __init__(self, *, root: str, book: AB.AuthBook,
                 http: Optional[Any] = None, lam: D = MANIFEST_LAMBDA,
                 feed=OBS.l5_activity, clock=time.time) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.book = book
        self.http = http
        self.feed = feed
        self.clock = clock
        self.store = LX.InventoryStore(os.path.join(root, "inventory.json"))
        repair_orphaned_keys(self.store)
        repair_ledger_from_venue(self.store)
        # settlement deltas for markets already retired. Separate from
        # the repair above because it must never re-settle a market.
        apply_ledger_adjustments(self.store)
        rebase_contaminated_accounting(self.store)
        self.limits = LX.SafetyLimits(**PROBE_LIMITS)
        self.lam = lam
        self.seen: set = set()
        self.seeded = False
        self.ws: Optional[int] = None
        self.transport: Optional[T.PmusTransport] = None
        self.executor: Optional[LX.FaithfulExecutor] = None
        self.reconciled = False        # venue has not confirmed our book
        self.last_reconcile_t = 0.0
        self.last_progress_t = 0.0
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        self.last_blockers: Optional[List[str]] = None
        self.win = collections.Counter()
        self.first_action_t: Optional[float] = None
        self.stats = collections.Counter()
        self.tape = os.path.join(root, "decisions.jsonl")
        #: the tick's clock; the executor's staleness is judged against it
        self.now_t = clock()
        #: per-key last-read clocks, so cadence is bounded per THING rather
        #: than per tick. Without these the request rate scales with the
        #: number of live orders and open markets, which is exactly
        #: backwards: the busier we are, the harder we hit the venue.
        self.last_order_poll: Dict[str, float] = {}
        self.last_settlement_poll: Dict[str, float] = {}
        self.last_pending_resolve: Dict[str, float] = {}
        #: the single global slot the parked-order reads take turns in
        self.last_pending_resolve_t = 0.0
        #: preflight's escalating retry index, reset on every window roll
        self.preflight_attempts = 0

    # ------------------------------------------------------------ window
    def halted(self) -> bool:
        return bool(os.getenv("L5_LIVE_HALT", "").strip())

    def roll(self, now: float) -> None:
        ws = AB.window_start(now)
        if ws == self.ws:
            return
        if self.ws is not None:
            self.close_window()
        self.ws = ws
        slug = AB.slug_for(ws)
        # THE SAME LIMITER, explicitly. PmusTransport would reach for the
        # process-wide one anyway; naming it here is the point -- this line
        # is where the old code silently created a fresh token bucket and a
        # fresh 429 cool-off every fifteen minutes.
        self.transport = T.PmusTransport(
            market_slug=slug, http=self.http, limiter=T.shared_limiter())
        # STRUCTURAL DISARM, applied before the executor is given the
        # transport, so there is no window in which it holds a
        # submit-capable object.
        if shadow_only():
            self.transport = ShadowOnlyTransport(self.transport)
            emit("L5_LIVE_SHADOW_LOCK", {
                "locked": True, "env": SHADOW_ONLY_ENV,
                "blocked": list(ShadowOnlyTransport.BLOCKED),
                "cancel_still_permitted": True,
                "labels": ("submit() raises NotArmed regardless of "
                           "PMUS_Q1_LIVE/SESSION_ID/ARM. The owner's "
                           "live authorization is unchanged; this "
                           "lock sits in front of it.")})
        self.executor = LX.FaithfulExecutor(
            self.store, self.limits, transport=self.transport, lam=self.lam,
            now_fn=lambda: dt.datetime.fromtimestamp(self.now_t,
                                                     dt.timezone.utc),
            # the wiring Probes 04-08 actually traded through
            maker_treatment=True, maker_only=True,
            # BASELINE: no Probe 08 treatment, no Probe 09. Native.
            suppress_imbalance_increase=False,
            # RESIDUAL_CAP: OFF unless PMUS_RESIDUAL_CAP_K names a k. Absent
            # or unreadable is OFF, so a typo cannot silently half-enable a
            # policy, and OFF is byte-for-byte the frozen behaviour.
            residual_cap_k=residual_cap_k())
        # DISARMED-SHADOW VALIDATION PROTECTIONS, attached after construction
        # so the executor's own signature is unchanged.
        #
        # The validation gates are ON by default. They are refuse-only, so
        # the worst they can do is decline an action the deployed limits
        # already allowed -- and in shadow nothing is submitted anyway, so
        # turning them on here is how we find out they bind.
        self.executor.validation_gates = validation_gates()
        # The independent halt is INERT unless L5_HALT_URL names the guard's
        # read-only endpoint, so this deployment changes nothing by itself.
        # Once named, it FAILS CLOSED: absent, stale or unreachable all
        # refuse, because a halt that fails open is not a halt.
        self.executor.halt_gate = halt_gate()
        # authoritative cash, when a reading exists. None means the cash gate
        # binds on its configured ceiling alone and require_cash_evidence()
        # refuses to arm -- never that cash is unlimited.
        self.executor.available_cash_usd = None
        emit("L5_LIVE_GATES", {
            "validation_gates": (self.executor.validation_gates.as_dict()
                                 if self.executor.validation_gates else None),
            "halt_gate_configured": self.executor.halt_gate is not None,
            "halt_url_env": HALT_URL_ENV,
            "available_cash_usd": None,
            "labels": ("additive refuse-only gates. They cannot raise any "
                       "pre-existing limit and cannot arm anything.")})
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        self.preflight_attempts = 0
        self.last_blockers = None
        self.win = collections.Counter()
        self.first_action_t = None

    def maybe_preflight(self, now: float) -> None:
        t = self.transport
        if t is None:
            return
        if self.preflight_status == "PASSED":
            # ARMING IS RETRIED; PREFLIGHT IS NOT. A PASSED preflight is a
            # fact about this market and is never re-run, but try_arm can
            # refuse for reasons that clear on their own a few seconds
            # later -- an unconfirmed book, or an order in an UNCERTAIN
            # state. Reaching try_arm only through the not-yet-PASSED branch
            # gave arming exactly ONE attempt per market window.
            #
            # 1545z was lost to precisely that: the one attempt at 15:45:23
            # hit a terminal order that was not yet booked, the uncertainty
            # cleared at 15:45:35, and nothing in the process ever asked
            # again. The whole window ran disarmed with owner_arm_token true
            # and preflight PASSED.
            #
            # Retrying costs nothing: try_arm returns at once when already
            # armed or halted, reconcile_if_needed returns at once on a
            # confirmed book, and transport.arm() is a local gate check that
            # touches no venue. The refusal is still logged only on its
            # first occurrence in the window, so a long refusal does not
            # spam; its duration shows in the window counters.
            self.try_arm()
            return
        # escalating while it keeps failing: 3, 6, 12, then 24 seconds.
        # The common failure is simply that the venue has not listed the
        # next market yet, and asking four times as often does not list it
        # any sooner -- it only spends the budget that the orders and the
        # reconciliation need.
        idx = min(self.preflight_attempts, len(PREFLIGHT_BACKOFF_S) - 1)
        if now - self.last_preflight_t < PREFLIGHT_BACKOFF_S[idx]:
            return
        self.last_preflight_t = now
        self.preflight_attempts += 1
        try:
            rep = t.preflight(
                expect_account=os.getenv("L5_LIVE_EXPECT_ACCOUNT", "").strip()
                or None)
        except T.PreflightFailed as exc:
            rep = json.loads(str(exc))
        except Exception as exc:                               # noqa: BLE001
            rep = {"preflight": "FAILED", "market_slug": t.market_slug,
                   "fatal": T.redact("%s: %s" % (type(exc).__name__, exc))}
        self.preflight_status = rep.get("preflight", "FAILED")
        blockers = rep.get("blocking_failures")
        # log every PASS, and a FAIL only when what blocks it changes
        if self.preflight_status == "PASSED" or blockers != self.last_blockers:
            emit("L5_LIVE_PREFLIGHT", rep)
        self.last_blockers = blockers
        if self.preflight_status == "PASSED":
            # preflight reads tick and quantity increment off THIS market
            # and stores them on the transport; None means "not established"
            # and is never defaulted. The executor asks the transport.
            if t.tick_size is None:
                emit("L5_LIVE_TICK_UNKNOWN", {"market": t.market_slug})
            self.try_arm()

    def sweep_settlements(self) -> None:
        """Retire every market the venue has finished with.

        Without this the executor trades exactly one window. `committed` is
        incremented on every fill and was never retired, so `open_markets`
        kept naming the first market forever and MAX_CONCURRENT_MARKETS
        refused every window after it. The probes never saw it because they
        ran one window and exited.

        Only the venue may declare a market finished, and only a terminal
        state WITH a settlement price counts -- MARKET_STATE_CLOSED shows up
        at the 900s boundary before the price is final, and retiring against
        a price that is not final closes the book at the wrong number. A
        market that has closed but not yet settled therefore keeps holding
        its slot, which is the safe direction to be wrong in: it delays the
        next window rather than letting two markets' exposure overlap.
        """
        t = self.transport
        if t is None:
            return
        for market in self.store.markets_with_exposure():
            if market == t.market_slug:
                continue                       # the one we are working now
            # ONE READ PER MARKET PER SETTLEMENT_POLL_S. This loop used to
            # run on every 3s tick against every market still in the book,
            # and with 2115z stuck there by the retirement defect that was a
            # settlement GET every 3 seconds forever.
            if (self.now_t - self.last_settlement_poll.get(market, 0.0)
                    < SETTLEMENT_POLL_S):
                continue
            self.last_settlement_poll[market] = self.now_t
            try:
                rep = t.settlement(market)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["settlement_read_error"] += 1
                if T.is_rate_limited(exc):
                    self.stats["settlement_read_429"] += 1
                continue
            if not rep.get("terminal"):
                self.stats["awaiting_settlement"] += 1
                continue
            try:
                out = self.store.retire_settled_market(
                    market, yes_value=rep["settlement_px"])
            except LX.IncoherentBook as exc:
                # the book cannot be settled because it is not a book a
                # trade could have produced -- quantity with no cost, or
                # cost with no quantity. Settling it anyway is what posted
                # 2045z at zero. It stays open, uncertain and unretired
                # until reconciliation or a pending order resolves it.
                self.stats["retire_refused:INCOHERENT_BOOK"] += 1
                emit("L5_LIVE_SETTLE_REFUSED",
                     {"market": market, "reason": "INCOHERENT_BOOK",
                      "detail": T.redact("%s" % exc)[:400],
                      "venue_state": rep.get("state"),
                      "submissions_allowed": False,
                      "why": ("a settlement computed against an incoherent "
                              "book posts a wrong realized P&L and moves "
                              "the loss halt with it")})
                continue
            if out.get("retired"):
                emit("L5_LIVE_SETTLED", dict(
                    out, venue_state=rep["state"],
                    cash_deployed=self.store.cash_deployed_usd(market)))
            else:
                self.stats["retire_refused:%s"
                           % out.get("reason", "?")] += 1

    def resolve_pending_terminal(self, now: float) -> List[Dict[str, Any]]:
        """Book the final fill of every order that left the venue's open list.

        THE REPAIR FOR 2045z. An order that disappears is no longer released
        unbooked -- reconcile parks it, and this asks the venue what it
        finally did. `/v1/order/{id}` answers for a filled order long after
        it has left the open list (the read-only recovery service reads a
        day-old one), so this resolves on the first attempt in the ordinary
        case. When it does not, the order stays parked and the book stays
        uncertain, which refuses submissions until it is settled one way or
        the other. That is the correct direction to be stuck in.
        """
        t = self.transport
        out: List[Dict[str, Any]] = []
        if t is None:
            return out
        parked = self.store.pending_terminal()
        # ONE GLOBAL SLOT, oldest attempt first. Eight parked orders used to
        # mean eight reads every PENDING_RESOLVE_S; now they mean one read
        # per interval and eight intervals to clear, with the book uncertain
        # and every submission refused for all of it -- which is exactly the
        # state it was in before the first of them resolved.
        if now - self.last_pending_resolve_t < PENDING_RESOLVE_S:
            return out
        askable = sorted(
            ((c, r) for c, r in parked.items() if r.get("venue_order_id")),
            key=lambda cr: self.last_pending_resolve.get(cr[0], 0.0))
        for coid, row in parked.items():
            if not row.get("venue_order_id"):
                # nothing to ask by. It cannot be resolved automatically and
                # must not be silently dropped: it stays parked and loud.
                self.stats["pending_unresolvable"] += 1
                emit("L5_LIVE_PENDING_UNRESOLVABLE",
                     dict(row, why=("no venue order id, so the venue cannot "
                                    "be asked what this order finally did"),
                          submissions_allowed=False))
        for coid, row in askable[:1]:
            vid = row.get("venue_order_id")
            self.last_pending_resolve[coid] = now
            self.last_pending_resolve_t = now
            try:
                prog = LX.order_progress(t.order(str(vid)))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.note_pending_attempt(coid, type(exc).__name__)
                self.stats["pending_resolve_error"] += 1
                out.append({"coid": coid, "event": "PENDING_RESOLVE_ERROR",
                            "error": type(exc).__name__,
                            "rate_limited": T.is_rate_limited(exc),
                            "market": row.get("market")})
                continue
            res = self.store.resolve_pending_terminal(coid, prog)
            res.setdefault("event", "PENDING_TERMINAL_RESOLVED")
            res["venue_order_id"] = str(vid)
            res.setdefault("market", row.get("market"))
            for k in ("avg_px", "commission", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            out.append(res)
        if out:
            self.lifecycle(out)
        return out

    def reconcile_if_needed(self, now: float) -> None:
        """Reconcile against the venue when our book is unconfirmed OR uncertain.

        ONCE PER PROCESS WAS A LATCH, and it shut the executor down live on
        2026-09-26. Three order-progress GETs returned HTTPError at 16:12:06,
        poll_orders marked the book uncertain -- correctly, an order whose
        state we cannot read might be live and consuming the envelope -- and
        the SAFETY_OVERLAY then refused every action with
        ORDER_STATE_UNCERTAIN. Submissions stopped dead at 43. Uncertainty is
        cleared ONLY by a full authoritative reconciliation, and this method
        would not run a second one, so nothing in the process could ever
        clear it. A transient GET failure cost the whole session.

        So: reconcile when we have never confirmed a book, and AGAIN whenever
        the book goes uncertain. Not on every tick -- at most once every
        RECONCILE_RETRY_S, because hammering the venue while it is already
        returning errors is how a transient fault becomes a rate limit.

        WHAT DOES NOT CHANGE. A GET that merely succeeds clears nothing. Only
        InventoryStore.reconcile_from_venue clears the flag, and it does so
        at the END, after the venue's positions have replaced ours by the
        net = long - short identity, its open orders have been adopted or
        released, and the divergence has been reported. If either read
        raises, or the reconcile itself raises on a venue shape we do not
        recognise, the flag stays set, no order may be submitted, and we try
        again later. Fail-closed is the resting state, not the exception.

        Adoption, not duplication: orders are keyed by our deterministic
        client_order_id, so an order the venue lists that we had lost is
        adopted under the same id rather than re-sent as a second order.

        `positions()`, not `available_positions()`: the venue serves both and
        the settlement auditor treats the latter as explicitly NOT held, so
        merging them would inflate what we think we own.
        """
        t = self.transport
        if t is None:
            return
        uncertain = self.store.orders_uncertain()
        stalled = self.orders_stalled_for_s(now)
        # disarmed soak only: a periodic authoritative read so the real
        # reconciliation path is exercised against the real venue. Never
        # while armed -- there it would adopt and park live orders on a
        # timer nobody asked for.
        soak_due = (SOAK_RECONCILE_S > 0 and not (t is not None and t.armed)
                    and (now - self.last_reconcile_t) >= SOAK_RECONCILE_S)
        if (self.reconciled and not uncertain
                and stalled < STALLED_ORDER_S and not soak_due):
            return
        if stalled >= STALLED_ORDER_S and not uncertain:
            # not an error, so nothing marked it: the book simply stopped
            # agreeing with the venue. Fail closed until the venue answers.
            self.store.mark_uncertain(
                "no venue progress on %d live order(s) for %.0fs"
                % (self.store.live_order_count(), stalled))
            uncertain = self.store.orders_uncertain()
            emit("L5_LIVE_ORDERS_STALLED",
                 {"live_orders": self.store.live_order_count(),
                  "stalled_s": round(stalled, 1),
                  "submissions_allowed": False})
        if now - self.last_reconcile_t < RECONCILE_RETRY_S:
            return
        self.last_reconcile_t = now
        try:
            pos = t.positions()
            oo = t.open_orders()
            out = self.store.reconcile_from_venue(pos, oo)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # still unknowable. Keep the flag, keep refusing, come back.
            self.store.mark_uncertain(
                "reconcile raised %s" % type(exc).__name__)
            self.reconciled = False
            emit("L5_LIVE_RECONCILE_FAILED",
                 {"error": type(exc).__name__,
                  "detail": T.redact("%s" % exc)[:300],
                  "was_uncertain": str(uncertain or ""),
                  "submissions_allowed": False,
                  "retry_in_s": RECONCILE_RETRY_S})
            return
        self.reconciled = True
        self.last_progress_t = now
        emit("L5_LIVE_RECONCILE",
             dict(out, market=t.market_slug,
                  cleared_uncertainty=str(uncertain or ""),
                  trigger=("SOAK_PERIODIC" if soak_due and not uncertain
                           else "UNCERTAIN_OR_UNCONFIRMED"),
                  armed=bool(t.armed),
                  # getattr, because TELEMETRY MUST NOT BE ABLE TO KILL A
                  # RECONCILE. By the time this line runs the venue has
                  # already answered and the book is already saved; an
                  # AttributeError here would throw that away and report it
                  # as a reconcile failure, which is a lie about the venue.
                  venue_requests=(lim.metrics()
                                  if (lim := getattr(t, "limiter", None))
                                  is not None else None),
                  realized_today=str(self.store.realized_today())))

    def try_arm(self) -> None:
        t = self.transport
        if t is None or t.armed or self.halted():
            return
        # never arm on a book the venue has not confirmed
        self.reconcile_if_needed(self.now_t)
        if not self.reconciled:
            self.win["arm_refused_unreconciled"] += 1
            return
        if self.store.orders_uncertain():
            self.win["arm_refused_orders_uncertain"] += 1
            if self.win["arm_refused_orders_uncertain"] == 1:
                emit("L5_LIVE_DISARMED",
                     {"market": t.market_slug,
                      "why": "orders in an UNCERTAIN state; refusing to arm"})
            return
        try:
            res = t.arm()
            emit("L5_LIVE_ARMED", res)
        except LX.NotArmed as exc:
            self.win["arm_refused"] += 1
            if self.win["arm_refused"] == 1:
                emit("L5_LIVE_DISARMED", {"market": t.market_slug,
                                          "why": str(exc)[:300]})

    def close_window(self) -> None:
        t = self.transport
        slug = t.market_slug if t else None
        out: Dict[str, Any] = {
            "market": slug, "decisions": dict(self.win),
            "preflight": self.preflight_status,
            "armed": bool(t and t.armed),
            "observability": self.book.window_report(slug) if slug else None,
        }
        if slug and self.first_action_t is not None:
            obs = out["observability"]
            ws = AB.slug_start(slug)
            out["first_l5_action_offset_s"] = round(self.first_action_t - ws, 1)
            fb = obs.get("first_book_offset_s")
            out["book_before_first_l5_action"] = (
                fb is not None and fb <= out["first_l5_action_offset_s"])
        # A final progress read BEFORE the deadman, so an order that filled
        # between the last poll and window close is recorded as a fill rather
        # than disappearing into a cancel.
        if self.store.live_order_count():
            out["final_progress"] = self.poll_orders()
        if t is not None and t.armed:
            out["deadman"] = LX.deadman(self.store, t, reason="window_end")
            try:
                # positions() returns ROWS; .get() on a list silently raised
                # AttributeError into the except below, so this field was
                # never populated in any window.
                rows = [LX.position_row(p) for p in t.positions()]
                out["venue_position"] = {
                    r["side"]: r["qty"] for r in rows if r["market"] == slug}
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["venue_position_error"] = type(exc).__name__
            t.disarm("window_end")
        emit("L5_LIVE_WINDOW", out)

    # ------------------------------------------------------------ actions
    def poll(self, now: float) -> None:
        rows = self.feed()
        if rows is None:
            self.stats["feed_errors"] += 1
            return
        fresh = []
        for r in rows:
            k = OBS.action_key(r)
            if k in self.seen:
                continue
            self.seen.add(k)
            fresh.append(r)
        if not self.seeded:
            # the first read is history, not a signal: act only on what
            # arrives after this process began watching
            self.seeded = True
            self.stats["seeded"] = len(fresh)
            return
        for r in sorted(fresh, key=lambda x: float(x.get("timestamp") or 0)):
            if str(r.get("slug") or "").startswith("btc-updown-15m-"):
                self.handle(r, now)

    def handle(self, r: Mapping[str, Any], recv: float) -> None:
        ws = OBS.l5_window_start(r.get("slug", ""))
        t, ex = self.transport, self.executor
        slug = OBS.pmus_slug_for(ws) if ws else None
        book = self.book.snapshot(slug) if slug else None
        row = engine_row(r, book, recv)
        act = row["l5"]
        if ws != self.ws or t is None or ex is None:
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": "PMUS_TRANSLATION",
                   "refused_reason": "NOT_CURRENT_WINDOW",
                   "L5_NATIVE": FT.l5_native(row)}
        elif act.get("is_sell"):
            # L5's BTC15m corpus holds ZERO sells; the probe does not
            # translate one. Our choice, so it is filed against SAFETY.
            rec = {"action_key": row["action_key"], "market": slug,
                   "refused_by_layer": "SAFETY_OVERLAY",
                   "refused_reason": "SELL_NOT_IN_PROBE_SCOPE",
                   "L5_NATIVE": FT.l5_native(row)}
        else:
            if self.halted():
                rec = {"action_key": row["action_key"], "market": slug,
                       "refused_by_layer": "SAFETY_OVERLAY",
                       "refused_reason": "OPERATOR_HALT",
                       "L5_NATIVE": FT.l5_native(row)}
            else:
                if self.first_action_t is None:
                    self.first_action_t = float(r.get("timestamp") or recv)
                # NO intra-window cancel-and-replace. The P39 harness ended
                # every resting order that L5's next action no longer matched,
                # which the canonical core has no method for -- deliberately.
                # The lifecycle Probes 04-08 actually traded is: rest at L5's
                # price, and let the venue answer; orders that never fill are
                # cancelled by the deadman at window end, which close_window
                # already calls. Probe 08's three cheapest repairs rested to
                # the end exactly that way, and that is the evidence we have.
                # Re-pricing a resting order toward L5's newer price is
                # chasing under another name, and it has never been tested
                # with money. The 8-live-order cap and the $5 exposure bound
                # are what keep unfilled orders from accumulating.
                rec = ex.handle(row, {}, market=slug, window=str(ws),
                                open_markets=self.open_markets())
        layer = rec.get("refused_by_layer")
        order = rec.get("ORDER") or {}
        key = ("ORDER:%s" % (order.get("reason") or
                             ("SUBMITTED" if order.get("submitted") else "?"))
               if not layer else "%s:%s" % (layer, rec.get("refused_reason")))
        self.win[key] += 1
        self.stats[key] += 1
        doc = {"t": now_utc().isoformat(), "row": row, "record": rec,
               "armed": bool(t and t.armed), "preflight": self.preflight_status}
        with open(self.tape, "a") as fh:
            fh.write(json.dumps(doc, default=str) + "\n")
        emit("L5_LIVE_DECISION", {
            "market": slug, "key": key, "side": act.get("pmus_side"),
            "l5_price": act.get("l5_price"), "l5_size": act.get("l5_size"),
            "latency_s": row["latency_s"],
            "book": row["pmus"]["bbo"], "book_source": row["pmus"]["book_source"],
            "stream_age_s": row["pmus"]["stream_age_s"],
            "translation": rec.get("PMUS_TRANSLATION"),
            "safety": (rec.get("SAFETY_OVERLAY") or {}).get("reason"),
            "order": {k: order.get(k) for k in
                      ("client_order_id", "submitted", "reason", "venue_order_id",
                       "state", "error")} if order else None,
            "intent": order.get("intent"),
            "armed": bool(t and t.armed)})

    def orders_stalled_for_s(self, now: float) -> float:
        """How long our live orders have gone without any venue progress.

        0.0 when nothing is live, or when progress moved. This is the signal
        that says a live-order book has drifted from the venue WITHOUT the
        uncertainty flag being set -- an order we cannot query (no venue id)
        or one the venue quietly finished never raises an error, it just
        stops changing while it holds a slot in MAX_LIVE_ORDERS.
        """
        if not self.store.live_order_count():
            self.last_progress_t = now
            return 0.0
        if self.last_progress_t <= 0:
            self.last_progress_t = now
            return 0.0
        return max(0.0, now - self.last_progress_t)

    def poll_orders(self) -> List[Dict[str, Any]]:
        """Ask the venue what each live order actually did.

        THE ACK IS A RECEIPT, NOT A FINAL STATE. Probe 05 took an ACK for a
        resting order, never asked again, and carried it as live for the rest
        of the window; 29 later actions were refused MAX_LIVE_ORDERS against
        an order that no longer existed. So every live order is re-read from
        the venue, and this is the only thing that moves a fill into
        inventory -- without it residual stays 0 and every exposure number
        downstream is wrong.

        An order whose state cannot be established stays UNCERTAIN and
        nothing is released: unknowable is not the same as flat.
        """
        t = self.transport
        out: List[Dict[str, Any]] = []
        if t is None:
            return out
        live = list(self.store.live_orders().items())
        # the per-order interval is a FLOOR; the aggregate rate is the cap.
        # One or two orders poll every 6s exactly as before; eight stretch
        # to 16s each so this path never exceeds ORDER_POLL_RATE.
        interval = max(ORDER_POLL_S,
                       len(live) / ORDER_POLL_RATE if ORDER_POLL_RATE else 0)
        # oldest-read first, so a stretched envelope still round-robins
        # fairly instead of starving whichever order sorts last.
        live.sort(key=lambda kv: self.last_order_poll.get(kv[0], 0.0))
        for coid, o in live:
            vid = o.get("venue_order_id")
            if not vid:
                continue
            if self.now_t - self.last_order_poll.get(coid, 0.0) < interval:
                continue
            self.last_order_poll[coid] = self.now_t
            try:
                row = t.order(str(vid))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.mark_uncertain(
                    "order query raised %s for %s" % (type(exc).__name__,
                                                      coid))
                out.append({"coid": coid, "event": "PROGRESS_ERROR",
                            "error": type(exc).__name__,
                            "rate_limited": T.is_rate_limited(exc)})
                continue
            prog = LX.order_progress(row)
            # THE ORDER'S OWN MARKET, not the transport's current one. The
            # transport follows the window; an order placed in 2045z that is
            # still live when 2100z opens would have had its fill booked
            # into 2100z -- a whole market's inventory attributed to the
            # wrong book. Visible in the 21:01:21Z lifecycle events, which
            # report two 2045z orders under market 2100z. They had filled
            # nothing, so nothing was misbooked. That was luck.
            res = self.store.apply_order_progress(
                coid, prog, market=str(o.get("market") or t.market_slug),
                side=str(o.get("side") or ""))
            res["venue_order_id"] = str(vid)
            res["market"] = str(o.get("market") or t.market_slug)
            res.setdefault("event", "ORDER_PROGRESS")
            # the venue's own numbers, carried with the delta rather than
            # discarded: without them a per-order audit cannot state an
            # average fill price or a commission after the fact.
            for k in ("avg_px", "commission", "leaves", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            out.append(res)
        if out:
            self.lifecycle(out)
            self.last_progress_t = self.now_t
        return out

    def lifecycle(self, events: List[Dict[str, Any]]) -> None:
        for e in events:
            self.stats["LIFECYCLE:%s" % e.get("event", "ERROR")] += 1
            self.win["LIFECYCLE:%s" % e.get("event", "ERROR")] += 1
            # NO FALLBACK. This used to emit `e.get("market") or
            # transport_slug`, and `"" or slug` is the slug -- so the two
            # orders booked under "" logged as 0115z and the defect was
            # invisible in the one place it should have been obvious.
            # Missing identity stays missing, and is named.
            m = e.get("market")
            emit("L5_LIVE_LIFECYCLE", dict(
                e, market=(m if m else None),
                market_identity=("PRESENT" if m else "MISSING")))

    def order_progress_latency_s(self, now: float) -> Dict[str, Any]:
        """How stale our knowledge of any live order is, right now.

        THE NUMBER PACING CAN HIDE. A limiter makes the transmitted request
        rate look safe by construction -- it is the rate we chose -- while
        the thing that actually degrades is how long an order's true state
        goes unread. This reports it directly: the oldest live order's age
        since its last authoritative read, and how far past its own
        scheduled interval that is.
        """
        live = self.store.live_orders()
        interval = max(ORDER_POLL_S,
                       len(live) / ORDER_POLL_RATE if ORDER_POLL_RATE else 0)
        ages = [now - self.last_order_poll.get(c, now) for c in live]
        oldest = max(ages) if ages else 0.0
        return {"live_orders": len(live),
                "poll_interval_s": round(interval, 2),
                "oldest_unread_s": round(oldest, 2),
                "overdue_s": round(max(0.0, oldest - interval), 2),
                "parked_orders": len(self.store.pending_terminal()),
                "labels": ("oldest_unread_s is how stale our knowledge of "
                           "any live order is. overdue_s above zero means "
                           "the schedule is not keeping up, which a "
                           "transmitted request rate cannot show.")}

    def open_markets(self) -> List[str]:
        return [m for m, v in self.store.committed().items() if v > 0]

    def heartbeat(self) -> None:
        t = self.transport
        emit("L5_LIVE_HB", {
            "utc": now_utc().isoformat(), "role": SERVICE_ROLE,
            "market": t.market_slug if t else None,
            "armed": bool(t and t.armed), "preflight": self.preflight_status,
            "owner_arm_token": Q1.armed(), "halt": self.halted(),
            "auth_book": self.book.status(),
            "book_now": self.book.snapshot(t.market_slug) if t else None,
            "stats": dict(self.stats),
            "committed_usd": {k: str(v) for k, v in self.store.committed().items()},
            # GROSS CASH OUT, beside the gate and never instead of it. The
            # gate is worst-case loss and is unchanged at $5; this is what
            # actually left the account, which on 2100z was $7.4871 under a
            # $1.8171 worst case. Reported so the difference is visible.
            "cash_deployed": [self.store.cash_deployed_usd(m)
                              for m in self.store.markets_with_exposure()],
            "risk_gate": {
                "measures": "worst-case loss over both settlements",
                "max_usd_per_market": str(self.limits.max_usd_per_market),
                "note": ("cash_deployed is reported, NOT gated. Changing "
                         "which of the two the gate reads is an explicit "
                         "decision and has not been made."),
            },
            # SELF-INFLICTED RATE LIMIT TELEMETRY. 2026-09-26 ran >1 req/s
            # sustained and the venue answered 429; the fallback from that
            # is what lost a cost basis. These say whether it is fixed.
            "venue_requests": t.limiter.metrics() if t else None,
            "order_progress": self.order_progress_latency_s(self.now_t),
            "preflight_attempts": self.preflight_attempts,
            "pending_terminal": sorted(self.store.pending_terminal()),
            "quarantined": sorted(self.store.quarantined()),
            "cooloff_remaining_s": round(self.venue_cooling(), 2),
            "retired_markets": sorted(
                self.store.state.get("retired_markets") or {}),
            "realized": self.store.realized_lines(),
            "limits": self.limits.as_dict(), "lambda": str(self.lam),
            # WHICH POLICY RAN. k is echoed from the executor that is
            # actually wired, not from the environment, so a heartbeat can
            # never claim a treatment the decision path is not applying.
            "policy": {
                "name": ("L5_NATIVE" if getattr(self.executor,
                                                "residual_cap_k", None) is None
                         else "RESIDUAL_CAP_k%s" % getattr(
                             self.executor, "residual_cap_k")),
                "residual_cap_k": (None if getattr(
                    self.executor, "residual_cap_k", None) is None
                    else str(self.executor.residual_cap_k)),
                "residual_cap_binds_session": int(self.stats.get(
                    "SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP", 0)),
                "residual_cap_binds_this_market": int(self.win.get(
                    "SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP", 0)),
                "max_clip_filled": {k: str(v) for k, v in
                                    (self.store.state.get("max_clip")
                                     or {}).items()},
                "basis": ("PROSPECTIVE_NET: prospective = realized residual + "
                          "outstanding orders in this market as-if-filled. "
                          "Refuse iff the prospective book is NOT flat AND the "
                          "order increases |prospective| AND |prospective "
                          "after| > k * max(largest filled clip, this order's "
                          "qty). Contracts. A leg that shrinks |prospective| "
                          "is never refused at any depth.")},
            "frozen_rule": "030d8bf"})

    def venue_cooling(self) -> float:
        """Seconds of venue cool-off remaining, or 0.

        THE P1 FIX, and it is in the CALLER rather than the limiter. The
        limiter's contract is unchanged and still proven: shared bucket,
        shared cool-off, raises VenueThrottled without sending. What was
        wrong is that every path kept calling it anyway -- 2,601 refusals
        inside one ~3s cool-off on the first occurrence, 19,420 by the end
        of the run. None reached the venue, but each one is a caller asking
        a question it already knows the answer to.

        So the tick asks ONCE and skips the venue-touching work while the
        answer is no. Nothing here decides an order is safe, terminal or
        flat: skipping a read leaves every piece of state exactly as
        unknown as it was, which is the direction that fails closed.
        """
        t = self.transport
        lim = getattr(t, "limiter", None) if t is not None else None
        if lim is None:
            return 0.0
        m = lim.metrics()
        return float(m["cooloff_remaining_s"]) if m.get("in_cooloff") else 0.0

    def tick(self, now: float) -> None:
        self.now_t = now
        self.roll(now)
        if self.halted() and self.transport is not None and self.transport.armed:
            emit("L5_LIVE_HALT", LX.deadman(self.store, self.transport,
                                            reason="operator_halt"))
            self.transport.disarm("operator_halt")
        # one question per tick instead of one per caller
        cooling = self.venue_cooling()
        if cooling > 0:
            self.stats["ticks_skipped_in_cooloff"] += 1
            self.win["ticks_skipped_in_cooloff"] += 1
            self.poll(now)          # the websocket feed costs the venue nothing
            return
        self.maybe_preflight(now)
        if self.store.live_order_count():
            self.poll_orders()
        # ORDER MATTERS, and it is the opposite of what it looks like.
        #
        # Settle FIRST, reconcile second. Once a market expires the venue
        # reports no position in it at all -- 1600z settled at 16:15Z and
        # /v1/portfolio/positions came back {} -- so the venue cannot tell us
        # what we held. Our own book is the ONLY surviving record of that
        # market's economics. Reconciling first and settling second would
        # compute the settlement against an emptied book and post a P&L of
        # zero, silently erasing a real result and, with it, the realized
        # total the -$5 halt is measured on.
        #
        # Retirement is still gated on the VENUE: it happens only for a
        # market the venue reports terminal WITH a settlement price, and only
        # when no order is live in it. On a restart the first pass may refuse
        # on stale live orders; the reconcile below releases those, and the
        # next tick retires it properly. One tick late is fine. Erased
        # accounting is not.
        # BEFORE settling. A market whose last order vanished still owes
        # the ledger that order's fill, and retire_settled_market now
        # refuses while anything is parked -- so resolving first is what
        # lets an ordinary window settle on the same tick it expires.
        if self.store.pending_terminal():
            self.resolve_pending_terminal(now)
        self.sweep_settlements()
        # a book that has gone uncertain gets another authoritative read,
        # rather than latching the session shut until a redeploy
        self.reconcile_if_needed(now)
        self.poll(now)


#: Known contaminated realized totals and what the authoritative evidence
#: says they should have been. Each applies AT MOST ONCE, keyed by id, and
#: only when the stored total still matches the contaminated figure.
#:
#: Neither is "make realized equal the balance". Each carries an attributed
#: line reconstructed from venue records and an explicit unattributed
#: remainder against a flat-account cash anchor. The remainder is named, not
#: absorbed into the trading line.
ACCOUNTING_REBASES = [
    {
        "id": "wire-priced-down-legs",
        "matches_realized_total": D("4.766200"),
        # 1600z, recomputed in own-outcome terms: Sum(1-p)q = Sum q - Sum pq,
        # so 10.46 - 4.4399 = 6.0201 exactly.
        "attributed": D("3.4442"),
        "cash_delta": D("1.90920"),          # 63.60685 - 61.69765
        "anchor": {
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "at_disarm_utc": "2026-09-26T17:11:40Z",
            "at_disarm_balance": "63.60685",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
        },
        "reason": ("realized accumulated from DOWN legs priced at the "
                   "venue's WIRE number instead of own-outcome cash"),
    },
    {
        "id": "deleted-down-quantity-1815z",
        "matches_realized_total": D("-2.43330000"),
        # 1815z settled on a book whose DOWN quantity had been deleted while
        # its cost survived: the record read up 1.81 / dn 0 / cost 4.3425 and
        # posted -4.3425. The seven venue order rows say the gross book was
        # UP 0.49 at 0.0931 and DOWN 5.73 at 4.2494 -- BOTH COSTS MATCH THE
        # RECORD EXACTLY, so leg_cost was right and only the quantities were
        # lost. Settled at 0.0000 (DOWN won) that book redeems 5.73 against
        # 4.3425, a GAIN of 1.3875. The error is exactly 5.73: the deleted
        # DOWN shares, each paying $1.
        #
        # Cumulative attributed across both arms: 3.4442 + 1.3875 = 4.8317.
        "attributed": D("4.8317"),
        "cash_delta": D("2.63770"),          # 64.33535 - 61.69765
        "anchor": {
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "rearm_utc": "2026-09-26T18:14:18Z", "rearm_balance": "63.60685",
            "at_disarm_utc": "2026-09-26T18:55:54Z",
            "at_disarm_balance": "64.33535",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
            "attributed_detail": ("1600z +3.4442 and 1815z +1.3875, both "
                                  "recomputed in own-outcome terms from "
                                  "venue order rows"),
            "commissions_observed": "0.0000 on every 1815z row",
        },
        "reason": ("1815z settled against a book whose DOWN quantity had "
                   "been deleted by reconcile while its cost survived"),
    },
    {
        "id": "costless-venue-net-2045z-2115z",
        "matches_realized_total": D("0.82060000"),
        # Two windows settled against inventory that had NO cost beside it.
        #
        # 2115z is reconstructed from venue order rows and is a real line.
        # Four adopted orders filled, all DOWN (the venue's ORDER_SIDE_SELL
        # / OUTCOME_SIDE_NO / ORDER_INTENT_BUY_SHORT triple), commission
        # 0.0000 on every one:
        #     0.91 @ wire 0.79 -> own 0.21 = 0.1911
        #     0.53 @ wire 0.80 -> own 0.20 = 0.1060
        #     1.72 @ wire 0.80 -> own 0.20 = 0.3440
        #     0.09 @ wire 0.79 -> own 0.21 = 0.0189
        #     3.25 shares, 0.6600 of cost -- and 3.25 is exactly what the
        #     book held under `...2115z|ORDER_SIDE_SELL`.
        # UP won (yes_value 1.0000), so those DOWN shares redeemed 0 and the
        # window's residual P&L is -0.6600.
        #
        # 2045z is NOT reconstructed. Its 2.25 DOWN shares came from order
        # CR157F2WJYC3, which filled, left the open list, and whose
        # /v1/order read raised before anything was booked; no fill row for
        # it survives in what we hold. The cash anchor says the remainder is
        # -0.43230 (2.25 shares at ~0.192 own terms, consistent with the
        # 0.79-0.80 wire regime of the adjacent window) -- but derived by
        # difference is precisely what `unattributed` means, so that is
        # where it stays. It is NOT called a trading line, a fee or a rebate.
        #
        # attributed = 4.8317 (1600z +3.4442, 1815z +1.3875)
        #              - 1.81710 (2100z, settled from a coherent gross book)
        #              - 0.66000 (2115z, from four venue fill rows)
        #            = 2.35460
        "attributed": D("2.35460"),
        "cash_delta": D("-0.27170"),         # 61.42595 - 61.69765
        "anchor": {
            # pre_arm_* is the anchor the cash_delta is measured FROM, and
            # for this rebase that is the START OF THE PROGRAMME, because
            # the attributed line spans every window since it (1600z, 1815z,
            # 2100z, 2115z). The third arm's own pre-arm read is recorded
            # below as third_arm_*, which is evidence, not the anchor.
            "pre_arm_utc": "2026-09-26T15:49:54Z",
            "pre_arm_balance": "61.69765",
            "third_arm_utc": "2026-09-26T20:49:29Z",
            "third_arm_balance": "64.33535",
            "at_disarm_utc": "2026-09-26T21:35:58Z",
            "at_disarm_balance": "61.42595",
            "flat_at_each_read": "zero positions, zero open orders, zero unsettled",
            "api_key_fingerprint": "d51b44a3fa83",
            "attributed_detail": ("1600z +3.4442, 1815z +1.3875, "
                                  "2100z -1.81710, 2115z -0.66000"),
            "unattributed_detail": ("-2.19400 carried forward, plus -0.43230 "
                                    "for 2045z whose 2.25 DOWN shares have "
                                    "no surviving fill row"),
            "superseded_total": "+0.82060 , overstated by 1.09230",
            "commissions_observed": "0.0000 on every 2115z row",
            "rebates_confirmed": ("none. A zero commission is not a rebate "
                                  "and nothing was credited as one."),
        },
        "reason": ("2045z and 2115z settled against quantity that had no "
                   "cost basis: an order released unbooked after a failed "
                   "query, and an adopted order filed under the venue's "
                   "raw side word"),
        # AND the book those defects left behind. The live volume still
        # holds `...2115z|ORDER_SIDE_SELL`, which the canonical-side rules
        # now treat as a permanent gross/net disagreement -- an uncertain
        # book that never submits again. The 21:35:58Z read says the venue
        # holds nothing, owes nothing and has nothing outstanding, and the
        # attributed/unattributed lines above already account for every
        # window in it, so those markets are CLOSED with no P&L posted
        # rather than settled a second time.
        "flat_account": {
            "utc": "2026-09-26T21:35:58Z", "balance": "61.42595",
            "open_orders": 0, "positions": {}, "unsettled_funds": 0,
            "api_key_fingerprint": "d51b44a3fa83",
            "source": ("read-only recovery service 247304c7, GET-only "
                       "transport, deployment 6d9e19ca"),
        },
    },
]


#: The one orphaned key this deployment must repair, and the authoritative
#: evidence for it. Matched on CONTENTS, so a store that never carried the
#: defect is untouched and one already repaired cannot be repaired twice.
ORPHAN_REPAIRS = [
    {
        "id": "blank-market-key-0115z",
        "key": "",
        "to_market": "cpc-btc-updown-15m-2026-09-27-0115z",
        "side": "UP", "qty": D("0.7"), "cost": D("0.09700"),
        # DOWN won 0115z, so these UP shares redeemed nothing.
        "settlement_pnl": D("-0.09700"),
        "matches_cost": D("0.09700"),
        "evidence": {
            "source": "/v1/order rows read by the GET-only recovery service",
            "orders": ["CR52P8GZ4YAQ", "CR52QM4S6YBY"],
            "detail": ("CR52P8GZ4YAQ 0.10 UP avgPx 0.1300 commission 0.0000; "
                       "CR52QM4S6YBY 0.60 UP avgPx 0.1400 commission 0.0000; "
                       "both marketSlug cpc-btc-updown-15m-2026-09-27-0115z"),
            "cash_anchor": ("59.18945 confirmed 01:03:02Z and 59.69195 "
                            "confirmed 02:01:19Z span this; their delta "
                            "+0.50250 = 0115z booked +0.599500 less 0.097000"),
            "api_key_fingerprint": "d51b44a3fa83",
            "why_not_folded_into_0115z": ("0115z is already retired and a "
                                          "settled market cannot settle "
                                          "again; this is its own line"),
        },
    },
]


#: Legs whose fills are known from authoritative venue order rows but which
#: the automatic path could not book. Applied once, at boot, and only when the
#: ledger still holds the broken state each entry describes.
LEDGER_REPAIRS = [
    {
        "id": "1300z-incoherent-resolver-zero-fill",
        "market": "cpc-btc-updown-15m-2026-09-27-1300z",
        "side": "UP",
        # what the venue's own order rows say was actually bought
        "qty": D("1.80"), "cost": D("0.54000"),
        # what the broken book held instead, and must still hold for this
        # repair to be the right one to apply
        "expect_qty": D("0.9000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order rows read by the GET-only recovery service",
            "orders": ["CREX7ZDP2YC6", "CREWYQ75RYCA"],
            "detail": ("CREX7ZDP2YC6 ORDER_STATE_FILLED quantity 0.9 "
                       "cumQuantity 0.9 avgPx 0.3100 commission 0.0000; "
                       "CREWYQ75RYCA ORDER_STATE_FILLED quantity 0.9 "
                       "cumQuantity 0.9 avgPx 0.2900 commission 0.0000; "
                       "both marketSlug cpc-btc-updown-15m-2026-09-27-1300z, "
                       "OUTCOME_SIDE_YES, ORDER_INTENT_BUY_LONG"),
            "settlement": ("/v1/markets/.../bbo settlementPx 0.0000, state "
                           "MARKET_STATE_EXPIRED: the 1.80 UP redeemed "
                           "nothing, so settling this leg posts -0.54000"),
            "cash_anchor": ("60.59505 confirmed 12:52:03Z and 60.05505 "
                            "confirmed by /v1/account/balances 14:26:31Z; "
                            "their delta -0.54000 IS this leg's cost"),
            "positions_at_repair": "/v1/portfolio/positions empty; openOrders 0",
            "api_key_fingerprint": "d51b44a3fa83",
            "what_went_wrong": ("the parked-order resolver accepted a venue "
                                "row reading state ORDER_STATE_NEW, quantity "
                                "0.9, cumQuantity 0, leavesQuantity 0 and "
                                "booked a zero fill as final. order_progress "
                                "now raises IncoherentOrderRow on a row whose "
                                "cum + leaves != quantity"),
            "original_bad_record": ("positions 1300z|UP = 0.9000 with "
                                    "leg_cost 0 and fill_count 0, which "
                                    "QUANTITY_WITHOUT_COST refused to settle "
                                    "91+ times. It is replaced, not erased: "
                                    "cost_reconstructions keeps the write and "
                                    "this entry keeps what it replaced"),
        },
    },
    {
        "id": "1245z-manual-yes-fill-no-cost-basis",
        "market": "cpc-btc-updown-15m-2026-10-12-1245z",
        "side": "UP",
        "qty": D("2"), "cost": D("1.6800"),
        "expect_qty": D("2.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order read by the GET-only pmus-readonly-recovery",
            "orders": ["D23N485QAZ89"],
            "detail": ("D23N485QAZ89 ORDER_STATE_FILLED quantity 2 "
                       "cumQuantity 2 leavesQuantity 0 avgPx 0.8400 "
                       "commission 0.0000, OUTCOME_SIDE_YES, "
                       "ORDER_INTENT_BUY_LONG, limit_price 0.84, "
                       "resting_s 10.274, "
                       "manualOrderIndicator MANUAL_ORDER_INDICATOR_MANUAL"),
            "settlement": ("/v1/markets/.../bbo state MARKET_STATE_EXPIRED, "
                           "settlementPx 0.0000: the 2 UP redeemed nothing, "
                           "so settling this leg posts -1.6800"),
            "positions_at_repair": ("/v1/portfolio/positions {} with "
                                    "unsettledFunds 0, openOrders 0 and "
                                    "buyingPower 243.15505 -- the venue holds "
                                    "nothing, because it already settled"),
            "what_went_wrong": ("the order was MANUAL, so the executor never "
                                "recorded a fill for it and took the quantity "
                                "from venue reconciliation with no cost. "
                                "QUANTITY_WITHOUT_COST then refused to settle "
                                "it 4,107+ times"),
            "why_not_dropped": ("on settlement price alone this looks like a "
                                "phantom: cost 0, cash_deployed 0, "
                                "fills_booked 0, venue net 0, settlementPx "
                                "0.0000. It is NOT. $1.68 of cash genuinely "
                                "left the account. A settlement price says "
                                "what a share PAID; only the order record "
                                "says what it COST. Dropping this quantity "
                                "would have erased a real loss"),
        },
    },
    {
        "id": "nfl-bal-atl-manual-yes-legs-undercounted",
        "market": "aec-nfl-bal-atl-2026-10-11",
        "side": "UP",
        "qty": D("4"), "cost": D("1.7700"),
        "expect_qty": D("2"), "expect_cost": D("0.8800"),
        "evidence": {
            "source": "/v1/order reads by the GET-only pmus-readonly-recovery",
            "orders": ["D1SA9RRSTZ73", "D1S42Z0APZ73"],
            "detail": ("D1SA9RRSTZ73 FILLED qty 2 cumQuantity 2 avgPx 0.4400 "
                       "commission -0.0100; D1S42Z0APZ73 FILLED qty 2 "
                       "cumQuantity 2 avgPx 0.4450 commission +0.0300; both "
                       "OUTCOME_SIDE_YES ORDER_INTENT_BUY_LONG and MANUAL. "
                       "Own-outcome UP cost = 2*0.4400 + 2*0.4450 = 1.7700"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 1.0000: "
                           "4 UP redeem 4.0000 against cost 1.7700"),
            "what_went_wrong": ("the ledger held only ONE of the two YES "
                                "orders (2 @ 0.4400 = 0.8800); the second was "
                                "never booked because it was MANUAL"),
        },
    },
    {
        "id": "nfl-bal-atl-manual-no-legs-no-cost-basis",
        "market": "aec-nfl-bal-atl-2026-10-11",
        "side": "DOWN",
        "qty": D("4"), "cost": D("2.1700"),
        "expect_qty": D("2.0000"), "expect_cost": D("0"),
        "evidence": {
            "source": "/v1/order reads by the GET-only pmus-readonly-recovery",
            "orders": ["D1SA22CA2Z7Z", "D1S4RM12JZ7T"],
            "detail": ("D1SA22CA2Z7Z FILLED qty 2 cumQuantity 2 avgPx 0.4650 "
                       "commission +0.0300; D1S4RM12JZ7T FILLED qty 2 "
                       "cumQuantity 2 avgPx 0.4500 commission -0.0100; both "
                       "OUTCOME_SIDE_NO ORDER_INTENT_BUY_SHORT and MANUAL. "
                       "The venue quotes ONE number per market, the LONG "
                       "price, so a NO leg's own-outcome cost is 1 - wire: "
                       "2*(1-0.4650) + 2*(1-0.4500) = 1.0700 + 1.1000 "
                       "= 2.1700 for 4 DOWN"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 1.0000: "
                           "4 DOWN redeem 0.0000 against cost 2.1700, so the "
                           "market's own-outcome settlement is "
                           "(4.0000-1.7700) + (0-2.1700) = +0.0600 gross, "
                           "and +0.0200 net of the 0.0400 of commissions "
                           "which this ledger keeps as a separate line"),
            "what_went_wrong": ("both NO legs were MANUAL, so the ledger held "
                                "a DOWN quantity with no cost at all"),
        },
    },
]


#: SETTLEMENT ADJUSTMENTS -- for a market already retired, whose recognised
#: settlement is provably wrong by a known amount.
#:
#: SEPARATE FROM LEDGER_REPAIRS ON PURPOSE. A repair fixes a cost basis
#: BEFORE settlement. This corrects a figure recognised AFTER it. It must
#: never retire the market again, never rewrite the original
#: `retired_markets` entry, and never bypass ALREADY_RETIRED -- that guard
#: exists because 2115z re-emitted its settlement 40+ times on 2026-09-26,
#: and a second pass would post a second P&L and move the loss halt with it.
#:
#: So this adds a DELTA to `realized_attributed` and nothing else, keyed by a
#: unique id in its own applied-list, and it verifies the market's recorded
#: settlement_pnl is exactly the wrong value it was written for. If the
#: ledger says anything else, the adjustment refuses rather than guesses.
LEDGER_ADJUSTMENTS = [
    {
        "id": "0030z-settlement-understated-by-two-cents",
        "market": "cpc-btc-updown-15m-2026-10-12-0030z",
        "expect_recorded_pnl": D("1.6800"),
        "authoritative_pnl": D("1.7000"),
        "delta": D("0.0200"),
        "evidence": {
            "source": "/v1/order and /v1/markets/.../bbo via the GET-only "
                      "pmus-readonly-recovery service",
            "orders": ["D1SCCX4F8Z84"],
            "detail": ("D1SCCX4F8Z84 ORDER_STATE_FILLED quantity 2 "
                       "cumQuantity 2 avgPx 0.8500 commission 0.0000, "
                       "OUTCOME_SIDE_NO ORDER_INTENT_BUY_SHORT, MANUAL. "
                       "Own-outcome DOWN cost = 2*(1-0.8500) = 0.3000"),
            "settlement": ("state MARKET_STATE_EXPIRED settlementPx 0.0000: "
                           "2 DOWN redeem 2.0000 against cost 0.3000, so the "
                           "authoritative settlement is +1.7000. The venue "
                           "independently reported realized 1.7000 for this "
                           "position, which this computation reproduces from "
                           "the order alone"),
            "before": "retired_markets settlement_pnl 1.6800",
            "after": "realized_attributed increased by 0.0200; the "
                     "retired_markets entry is left EXACTLY as written",
            "why_an_adjustment_and_not_a_repair": (
                "the market is already retired. Re-settling it would post a "
                "second P&L, so ALREADY_RETIRED correctly forbids it. The "
                "discrepancy is real but can only be corrected as a labelled "
                "delta, never by replaying the settlement"),
        },
    },
]


def apply_ledger_adjustments(store) -> Optional[Dict[str, Any]]:
    """Add labelled settlement deltas to already-retired markets.

    Idempotent by id in `ledger_adjustments_applied`, which persists in the
    state file, so a restart cannot apply one twice. Refuses unless the
    market is retired AND its recorded settlement_pnl is exactly the value
    the adjustment was written for.
    """
    done = store.state.setdefault("ledger_adjustments_applied", [])
    applied: List[Dict[str, Any]] = []
    for a in LEDGER_ADJUSTMENTS:
        if a["id"] in done:
            continue
        retired = (store.state.get("retired_markets") or {}).get(a["market"])
        if not retired:
            emit("L5_LIVE_ADJUST_REFUSED", {
                "id": a["id"], "market": a["market"],
                "why": ("the market is not retired, so there is no recognised "
                        "settlement to adjust. A cost-basis repair, not an "
                        "adjustment, is what an unsettled market needs")})
            continue
        recorded = D(str(retired.get("settlement_pnl") or 0))
        if recorded != a["expect_recorded_pnl"]:
            emit("L5_LIVE_ADJUST_REFUSED", {
                "id": a["id"], "market": a["market"],
                "recorded_pnl": str(recorded),
                "expected_recorded_pnl": str(a["expect_recorded_pnl"]),
                "why": ("the ledger does not hold the figure this adjustment "
                        "was written for; adjusting blind is manufacturing "
                        "P&L")})
            continue
        before = D(str(store.state.get("realized_attributed") or 0))
        after = before + D(str(a["delta"]))
        store.state["realized_attributed"] = str(after)
        # the legacy scalar is kept moving in step, as settlement does
        store.state["realized_today"] = str(
            D(str(store.state.get("realized_today") or 0))
            + D(str(a["delta"])))
        log = store.state.setdefault("settlement_adjustments", [])
        log.append({
            "id": a["id"], "market": a["market"],
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "recorded_pnl": str(recorded),
            "authoritative_pnl": str(a["authoritative_pnl"]),
            "delta": str(a["delta"]),
            "realized_attributed_before": str(before),
            "realized_attributed_after": str(after),
            "evidence": a["evidence"],
            "retired_entry_untouched": True,
        })
        done.append(a["id"])
        store.save()
        rec = {"id": a["id"], "market": a["market"],
               "delta": str(a["delta"]),
               "realized_attributed_before": str(before),
               "realized_attributed_after": str(after),
               "retired_entry_untouched": True,
               "orders": a["evidence"]["orders"]}
        emit("L5_LIVE_ADJUSTED", rec)
        applied.append(rec)
    return {"applied": applied} if applied else None


#: SHADOW-ONLY LOCK. "0" releases it; anything else (including unset) locks.
SHADOW_ONLY_ENV = "L5_SHADOW_ONLY"


class ShadowOnlyTransport:
    """Everything the real transport does, except it cannot submit an order.

    WHY A PROXY AND NOT A FLAG. `armed()` is three environment variables that
    must agree, and all three are already present on this service. A flag
    checked somewhere in the decision path can be bypassed by any code path
    that forgets to check it; a transport that has no working submit cannot
    be talked into one. The disarmed state becomes a property of the object
    graph rather than an observation about a heartbeat.

    `submit` raises `NotArmed`, which is the SAME exception the disarmed
    transport already raises, so it lands in the executor's existing,
    already-tested handler: the order reservation is undone and the decision
    is recorded as NOT_ARMED. No new code path runs on the money side.

    CANCEL IS DELIBERATELY NOT BLOCKED. Cancelling only ever reduces
    exposure, and a lock that prevented it could strand a resting order.
    Reads pass through untouched so reconciliation, settlement and the
    shadow all keep working.

    This does NOT alter the owner's live authorization. It sits in front of
    it, and the owner releases it explicitly with L5_SHADOW_ONLY=0.
    """

    #: every name that could put an order on the venue
    BLOCKED = ("submit",)

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        return self._inner

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        raise LX.NotArmed(
            "SHADOW_ONLY: this deployment cannot submit orders. The lock is "
            "structural, not a flag, and is released only by setting "
            "%s=0." % SHADOW_ONLY_ENV)

    def __getattr__(self, name: str) -> Any:
        # anything not named above delegates. BLOCKED names are defined
        # explicitly on the class, so they never reach here.
        if name in self.BLOCKED:                       # pragma: no cover
            raise AttributeError(name)
        return getattr(self._inner, name)


def shadow_only() -> bool:
    return str(os.getenv(SHADOW_ONLY_ENV, "1")).strip() != "0"


#: names the guard service's READ-ONLY halt endpoint. Unset = inert.
HALT_URL_ENV = "L5_HALT_URL"
#: "0" disables the validation gates; anything else (including unset) is ON
GATES_ENV = "L5_VALIDATION_GATES"


def validation_gates():
    """The run-scoped additive gates, or None when explicitly disabled."""
    if str(os.getenv(GATES_ENV, "1")).strip() == "0":
        return None
    try:
        from src import l5_live_validation_gates as VG
        return VG.ValidationGates(
            max_cash_deployed_usd=D(str(os.getenv(
                "L5_MAX_CASH_DEPLOYED_USD", "10"))),
            max_unmatched_contracts=D(str(os.getenv(
                "L5_MAX_UNMATCHED_CONTRACTS", "5"))),
            max_cumulative_loss_usd=D(str(os.getenv(
                "L5_MAX_CUMULATIVE_LOSS_USD", "20"))),
            cash_policy=VG.NO_RECYCLING)
    except Exception as exc:                                   # noqa: BLE001
        # a gate we cannot build is announced, never silently skipped
        emit("L5_LIVE_ALARM", {
            "kind": "validation_gates_unavailable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("the additive gates are NOT active. Pre-existing "
                       "limits are unaffected.")})
        return None


def halt_gate():
    """The independent halt, or None when no endpoint is configured."""
    url = str(os.getenv(HALT_URL_ENV, "")).strip()
    if not url:
        return None
    try:
        from src import l5_halt_gate as HG
        return HG.HaltGate(HG.HttpHaltStore(url))
    except Exception as exc:                                   # noqa: BLE001
        emit("L5_LIVE_ALARM", {
            "kind": "halt_gate_unavailable",
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
            "labels": ("L5_HALT_URL is set but the gate could not be built, "
                       "so the independent halt is ABSENT")})
        return None


def repair_ledger_from_venue(store) -> Optional[Dict[str, Any]]:
    """Book a leg the automatic path could not, from named venue evidence.

    Idempotent by id. It refuses unless the ledger still holds exactly the
    broken state the entry describes, because a repair applied to a book that
    has moved on is not a repair, it is a second corruption. It writes
    quantity and cost together through reconstruct_cost_basis, which requires
    a source and records it, and it never touches realized P&L -- the leg is
    left for the ordinary settlement path to retire, so the loss is posted by
    the same code that posts every other one.
    """
    done = store.state.setdefault("ledger_repairs_applied", [])
    out = []
    for r in LEDGER_REPAIRS:
        if r["id"] in done:
            continue
        legs = store.leg_cost(r["market"])
        pos = D(str((store.state.get("positions") or {}).get(
            "%s|%s" % (r["market"], r["side"]), 0)))
        cost = legs.get(r["side"], D(0))
        if pos != r["expect_qty"] or cost != r["expect_cost"]:
            emit("L5_LIVE_REPAIR_REFUSED", {
                "id": r["id"], "market": r["market"], "side": r["side"],
                "ledger_qty": str(pos), "ledger_cost": str(cost),
                "expected_qty": str(r["expect_qty"]),
                "expected_cost": str(r["expect_cost"]),
                "why": ("the ledger does not hold the state this repair was "
                        "written for; repairing blind is manufacturing P&L")})
            continue
        res = store.reconstruct_cost_basis(
            r["market"], r["side"], qty=r["qty"], cost=r["cost"],
            source=json.dumps(r["evidence"], sort_keys=True))
        done.append(r["id"])
        store.save()
        rec = {"id": r["id"], "market": r["market"], "side": r["side"],
               "replaced": {"qty": str(pos), "cost": str(cost)},
               "booked": {"qty": str(r["qty"]), "cost": str(r["cost"])},
               "evidence": r["evidence"], "result": res,
               "note": ("realized is NOT touched here; the ordinary "
                        "settlement sweep retires this market and posts the "
                        "loss through the same path as every other market")}
        emit("L5_LIVE_LEDGER_REPAIRED", rec)
        out.append(rec)
    return {"repairs": out} if out else None


def repair_orphaned_keys(store) -> Optional[Dict[str, Any]]:
    """Retire known corrupt ledger keys against authoritative evidence, once.

    The identity guard stops new corruption; it cannot clean up what is
    already on the volume, and on 2026-09-27 that left every reconcile
    dying on `leg_cost("")` -- correctly refusing, and wedged.
    """
    done = {r.get("id") for r in (store.state.get("orphan_repairs") or [])}
    for spec in ORPHAN_REPAIRS:
        if spec["id"] in done:
            continue
        if spec["key"] not in store.corrupt_market_keys() and not any(
                str(k).rsplit("|", 1)[0] == spec["key"]
                for k in (store.state.get("leg_cost") or {})):
            continue
        held = sum(D(str(v)) for k, v in
                   (store.state.get("leg_cost") or {}).items()
                   if str(k).rsplit("|", 1)[0] == spec["key"])
        if held != spec["matches_cost"]:
            emit("L5_LIVE_ORPHAN_REPAIR_REFUSED",
                 {"id": spec["id"], "expected": str(spec["matches_cost"]),
                  "found": str(held),
                  "why": ("the ledger does not hold what the evidence "
                          "describes; refusing to repair it blind")})
            continue
        out = store.repair_orphaned_market_key(
            key=spec["key"], to_market=spec["to_market"], qty=spec["qty"],
            cost=spec["cost"], side=spec["side"],
            settlement_pnl=spec["settlement_pnl"], evidence=spec["evidence"])
        log = store.state.setdefault("orphan_repairs", [])
        log[-1]["id"] = spec["id"]
        store.save()
        emit("L5_LIVE_ORPHAN_REPAIRED", dict(out, repair_id=spec["id"]))
        return out
    return None


def rebase_contaminated_accounting(store) -> Optional[Dict[str, Any]]:
    """Retire a known-contaminated realized total, once per contamination.

    Matches on the stored total, so a store that never carried the defect is
    untouched and a store already rebased past it cannot be rebased again.
    """
    done = {r.get("id") for r in (store.state.get("accounting_rebase_log")
                                  or [])}
    for spec in ACCOUNTING_REBASES:
        if spec["id"] in done:
            continue
        if store.realized_today() != spec["matches_realized_total"]:
            continue
        # the KEYS first, so a book written under a raw venue side word is
        # readable before anything decides what to do with it
        keys = store.canonicalize_position_keys()
        if keys["moved"] or keys["unmapped"]:
            emit("L5_LIVE_KEYS_CANONICALIZED", keys)
        out = store.rebase_session_accounting(
            attributed=spec["attributed"], cash_delta=spec["cash_delta"],
            anchor=spec["anchor"], reason=spec["reason"])
        flat = spec.get("flat_account")
        if flat:
            emit("L5_LIVE_FLAT_ACCOUNT_ADOPTED",
                 dict(store.adopt_flat_account(anchor=flat,
                                               reason=spec["reason"]),
                      rebase_id=spec["id"]))
        log = store.state.setdefault("accounting_rebase_log", [])
        if log:
            log[-1]["id"] = spec["id"]
            store.save()
        emit("L5_LIVE_ACCOUNTING_REBASED", dict(out, rebase_id=spec["id"]))
        return out
    return None


def assert_role() -> None:
    role = os.getenv(ROLE_ENV, "").strip()
    if role and role != SERVICE_ROLE:
        emit("SERVICE_ROLE_MISMATCH", {"expected": SERVICE_ROLE, "got": role})
        raise SystemExit(3)


def main() -> int:
    assert_role()
    root = os.getenv("L5_LIVE_ROOT", "/state/l5-live")
    poll_s = float(os.getenv("L5_LIVE_POLL_S", "3"))
    lam = D(os.getenv("L5_LIVE_LAMBDA", str(MANIFEST_LAMBDA)))
    book = AB.AuthBook().start()
    runner = Runner(root=root, book=book, lam=lam)
    stop = {"now": False}

    def _sig(*_a):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    emit("L5_LIVE_START", {"utc": now_utc().isoformat(), "root": root,
                           "credentials_present": Q1.has_credentials(),
                           "owner_arm_token": Q1.armed(),
                           "lambda": str(lam), "limits": runner.limits.as_dict(),
                           "worst_case_usd": str(runner.limits.worst_case_loss_usd()),
                           "frozen_rule": "030d8bf"})
    last_hb = 0.0
    while not stop["now"]:
        now = time.time()
        try:
            runner.tick(now)
        except Exception as exc:                               # noqa: BLE001
            runner.stats["tick_errors"] += 1
            emit("L5_LIVE_ERROR", {"error": T.redact("%s: %s" % (
                type(exc).__name__, exc))})
        if now - last_hb >= HB_EVERY_S:
            runner.heartbeat()
            last_hb = now
        time.sleep(poll_s)
    if runner.transport is not None and runner.transport.armed:
        emit("L5_LIVE_SHUTDOWN", LX.deadman(runner.store, runner.transport,
                                            reason="shutdown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
