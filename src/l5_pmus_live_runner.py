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
        self.limits = LX.SafetyLimits(**PROBE_LIMITS)
        self.lam = lam
        self.seen: set = set()
        self.seeded = False
        self.ws: Optional[int] = None
        self.transport: Optional[T.PmusTransport] = None
        self.executor: Optional[LX.FaithfulExecutor] = None
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

    def try_arm(self) -> None:
        t = self.transport
        if t is None or t.armed or self.halted():
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
                out["venue_position"] = str(t.positions().get(slug, "0"))
            except Exception as exc:                           # noqa: BLE001
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
        self.poll(now)


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
