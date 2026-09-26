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


def emit(kind: str, doc: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(doc, default=str,
                                      separators=(",", ":"))), flush=True)


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
        self.transport = T.PmusTransport(
            market_slug=slug, http=self.http)
        self.executor = LX.FaithfulExecutor(
            self.store, self.limits, transport=self.transport, lam=self.lam,
            now_fn=lambda: dt.datetime.fromtimestamp(self.now_t,
                                                     dt.timezone.utc),
            # the wiring Probes 04-08 actually traded through
            maker_treatment=True, maker_only=True,
            # BASELINE: no Probe 08 treatment, no Probe 09. Native.
            suppress_imbalance_increase=False)
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        self.last_blockers = None
        self.win = collections.Counter()
        self.first_action_t = None

    def maybe_preflight(self, now: float) -> None:
        t = self.transport
        if t is None or self.preflight_status == "PASSED":
            return
        if now - self.last_preflight_t < PREFLIGHT_RETRY_S:
            return
        self.last_preflight_t = now
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
            try:
                rep = t.settlement(market)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["settlement_read_error"] += 1
                continue
            if not rep.get("terminal"):
                self.stats["awaiting_settlement"] += 1
                continue
            out = self.store.retire_settled_market(
                market, yes_value=rep["settlement_px"])
            if out.get("retired"):
                emit("L5_LIVE_SETTLED", dict(out, venue_state=rep["state"]))
            else:
                self.stats["retire_refused:%s"
                           % out.get("reason", "?")] += 1

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
        if self.reconciled and not uncertain and stalled < STALLED_ORDER_S:
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
        for coid, o in list(self.store.live_orders().items()):
            vid = o.get("venue_order_id")
            if not vid:
                continue
            try:
                row = t.order(str(vid))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.mark_uncertain(
                    "order query raised %s for %s" % (type(exc).__name__,
                                                      coid))
                out.append({"coid": coid, "event": "PROGRESS_ERROR",
                            "error": type(exc).__name__})
                continue
            prog = LX.order_progress(row)
            res = self.store.apply_order_progress(
                coid, prog, market=t.market_slug,
                side=str(o.get("side") or ""))
            res["venue_order_id"] = str(vid)
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
            emit("L5_LIVE_LIFECYCLE", dict(e, market=(
                self.transport.market_slug if self.transport else None)))

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
            "limits": self.limits.as_dict(), "lambda": str(self.lam),
            "frozen_rule": "030d8bf"})

    def tick(self, now: float) -> None:
        self.now_t = now
        self.roll(now)
        if self.halted() and self.transport is not None and self.transport.armed:
            emit("L5_LIVE_HALT", LX.deadman(self.store, self.transport,
                                            reason="operator_halt"))
            self.transport.disarm("operator_halt")
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
]


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
        out = store.rebase_session_accounting(
            attributed=spec["attributed"], cash_delta=spec["cash_delta"],
            anchor=spec["anchor"], reason=spec["reason"])
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
