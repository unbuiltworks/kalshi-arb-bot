"""One bounded live L5 probe, on ONE BTC15m market, then gone.

This is the only thing in the repository that can send a real order, and it
exists as its OWN one-shot service so that the 24/7 observer keeps the
guarantee it has today: no path that can POST, and no caller of
`PmusTransport.arm()`. Nothing here is added to that service.

Nothing here is a new engine either. The decision is the frozen 030d8bf
rule, the translation is PMUS_TRANSLATION, the account protection is
SAFETY_OVERLAY, the body is the one shared serializer, the preview/echo gate
is `l5_preview_gate`, the signing is `pmus_auth`, and the inventory and
exposure reservation are `l5_live_execution`. This file supplies exactly two
things those parts do not have: a bound of ONE market, and an authorization
that is spent when used.

    claim the authorization (one market, written before arming)
      -> preflight, every gate, read-only
      -> arm  (needs preflight PASSED *and* the owner token)
      -> until the window closes:
             new L5 action -> frozen decision -> translation -> overlay
             -> reserve exposure -> preview -> exact echo -> submit -> ack
      -> cancel anything still working
      -> reconcile against the venue, which is the authority
      -> consume the authorization
      -> exit

It terminates after that one market whatever happened: fills, no fills,
errors, or an unreadable settlement. It cannot roll into the next window --
the market is fixed when the authorization is claimed, and every order
re-checks it. It cannot restart into another one -- the service sets
restartPolicyType NEVER, and the authorization is spent in the ledger even
if the process dies mid-probe.

REALIZED is not claimed here. Submitted, filled and settled are distinct,
and this reports what the venue said at each step.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src import l5_live_authorization as AUTH
from src import l5_live_book_feed as FEED
from src import l5_live_execution as EX
from src import l5_maker_execution as MK
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM
from src import l5_session_journal as JN
from src import l5_window_actions as WA
from src import pmus_auth as Q1

#: window arithmetic and the L5 action feed come from ONE implementation,
#: the one the continuous recorder has run across hundreds of windows. This
#: file had private copies; the copy of the action builder is what broke
#: Probe 02, so there are no copies here now.
WINDOW_S = WA.WINDOW_S
SLUG_PREFIX = WA.SLUG_PREFIX
current_slug = WA.current_slug
window_start = WA.window_start
new_l5_actions = WA.new_l5_actions

#: the executor refuses an action older than 30s, so the poll must be a small
#: fraction of that rather than a source of the staleness it guards against.
POLL_S = 5.0

#: how far into the window the preflight runs, and how long it may retry.
#: The same bounds the continuous probe uses, for the same reasons: late
#: enough that the venue has published the market, early enough that the
#: window is nowhere near closing.
PREFLIGHT_AT_S = 45.0
PREFLIGHT_RETRY_S = 60.0

#: the account this probe is pinned to, as a key-id fingerprint. Absent, the
#: gate does not run and behaviour is exactly as before -- present, a
#: credential pointing anywhere else fails preflight and nothing arms.
ACCOUNT_ENV = "PMUS_PREFLIGHT_ACCOUNT"
PREFLIGHT_UNTIL_S = 780.0

#: stop opening new orders before the close, so a fill has somewhere to go
#: and the cancel/reconcile tail is not racing the window end.
NO_NEW_ORDERS_AFTER_S = 810.0



def expected_account() -> Optional[str]:
    """The pinned account fingerprint, or None if the owner did not pin one."""
    return os.getenv(ACCOUNT_ENV, "").strip() or None


def gate_result(report: Optional[Mapping[str, Any]],
                name: str) -> Optional[bool]:
    """Whether a named preflight gate passed. None means it did not run.

    None and False are different answers and are kept apart: "the account
    was not checked" must never read as "the account matched".
    """
    for c in (report or {}).get("checks") or []:
        if c.get("check") == name:
            return bool(c.get("passed"))
    return None


def preflight_fingerprint(report: Optional[Mapping[str, Any]]
                          ) -> Optional[str]:
    """The key-id fingerprint the venue answered under, never the key."""
    return ((report or {}).get("identity_probe") or {}).get(
        "api_key_fingerprint")


class Probe:
    """One market, start to finish, and then nothing."""

    def __init__(self, *, market: str, store: EX.InventoryStore,
                 journal: JN.Journal, auth: AUTH.Authorization,
                 transport, book_feed, clock=time.time, sleep=time.sleep,
                 activity=None) -> None:
        self.market = market
        self.feed = book_feed
        self.store = store
        self.journal = journal
        self.auth = auth
        self.tp = transport
        self.clock = clock
        self.sleep = sleep
        self.activity = activity
        self.limits = PM.probe_limits()
        #: where the probe got to, so an exception can name its stage
        #: instead of disappearing into the volume.
        self.stage = "constructed"
        self.l5_health = WA.FeedHealth()
        self.records: List[dict] = []
        self.preflight: Optional[Dict[str, Any]] = None
        self.armed = False
        self.seen: Set[str] = set()

    # ------------------------------------------------------------- arming

    def _utc(self) -> dt.datetime:
        """This probe's clock, as the executor's staleness bound sees it.

        Passed through to the frozen path so the 30s stale-action bound is
        measured against the same clock the probe polls on, rather than
        against a wall clock the caller cannot control under test.
        """
        return dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)

    def run_preflight(self) -> Dict[str, Any]:
        self.stage = "preflight"
        try:
            # the pin activates PmusTransport's own
            # account_matches_expected gate; without it that gate does
            # not run at all, which is how it came to be inert here.
            self.preflight = self.tp.preflight(
                expect_account=expected_account())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.preflight = json.loads(str(exc))
            except Exception:                                  # noqa: BLE001
                self.preflight = {"preflight": "FAILED",
                                  "fatal": "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:200])}
        self.journal.write("preflight", {"market": self.market,
                                         "report": self.preflight})
        return self.preflight

    def arm(self) -> Dict[str, Any]:
        """Both conditions, or nothing that can POST is ever constructed."""
        if (self.preflight or {}).get("preflight") != "PASSED":
            raise AUTH.Refused("PREFLIGHT_NOT_PASSED",
                               {"preflight": (self.preflight or {})
                                .get("preflight", "NOT_RUN")})
        self.stage = "arming"
        out = self.tp.arm()          # itself re-checks preflight AND the token
        self.armed = True
        self.journal.write("armed", {"market": self.market, "detail": out})
        return out

    # -------------------------------------------------------------- trading

    def refresh_live_orders(self) -> List[dict]:
        """Ask the venue what our outstanding orders are actually doing.

        Probe 04's ACK said filled_qty 0; the order filled two milliseconds
        later and nothing ever asked again, so the books carried it as
        resting for the rest of the window. 29 later actions were refused
        MAX_LIVE_ORDERS against an order that no longer existed, and close-out
        then tried to cancel it. The ACK is a receipt, not a final state.
        """
        out: List[dict] = []
        for coid, o in list(self.store.live_orders().items()):
            vid = o.get("venue_order_id")
            if not vid:
                continue
            try:
                row = self.tp.order(str(vid))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # unknowable state stays unknowable; nothing is released
                self.store.mark_uncertain(
                    "order query raised %s for %s" % (type(exc).__name__,
                                                      coid))
                out.append({"coid": coid, "error": type(exc).__name__})
                continue
            prog = EX.order_progress(row)
            res = self.store.apply_order_progress(
                coid, prog, market=self.market, side=str(o.get("side") or ""))
            res["venue_order_id"] = str(vid)
            # the venue's own numbers, carried with the delta rather than
            # discarded: without these the per-order audit cannot state an
            # average fill price or a commission after the fact, which is
            # exactly what Probe 05's journal turned out to be missing.
            for k in ("avg_px", "commission", "leaves", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            out.append(res)
            self.journal.write("order_progress", res)
        return out

    def work_window(self, deadline: float) -> None:
        """Handle actions as they are observed, until the window closes."""
        self.stage = "working_window"
        while self.clock() < deadline:
            now = self.clock()
            if now >= deadline - (WINDOW_S - NO_NEW_ORDERS_AFTER_S):
                break
            # the venue first: a filled order must stop blocking the next
            # decision, and a stale "resting" order must stop consuming the
            # envelope. Cheap -- one GET per outstanding order, and there is
            # at most one.
            if self.armed and self.store.live_order_count():
                self.refresh_live_orders()
            actions, feed_ok = new_l5_actions(self.market, self.seen,
                                              self.activity,
                                              health=self.l5_health)
            if not feed_ok:
                self.journal.write("feed_failed", {"market": self.market})
            if actions:
                # every order re-checks the claim: one market, no exceptions
                self.auth.check_market(self.market)
                # the AUTHENTICATED book, same stream the recorder validates
                # every gate against. `attach` matches each action to the
                # book at or before it; the frozen rule bounds the age.
                books = self.feed.books()
                out = W.run_window(actions, books, market=self.market,
                                   window=self.market[-5:], store=self.store,
                                   limits=self.limits, transport=self.tp,
                                   lam=PM.LAMBDA, now_fn=self._utc)
                for rec in out["records"]:
                    self.records.append(rec)
                    self.journal.write("action", rec)
            self.sleep(POLL_S)

    # ------------------------------------------------------------- closing

    def close_out(self) -> Dict[str, Any]:
        """Cancel, then let the VENUE say what is true. Always runs."""
        self.stage = "close_out"
        cancelled, cancel_errors = [], []
        # Ask before acting. Probe 04 cancelled an order that had been FILLED
        # for eleven minutes, because nothing had asked the venue since the
        # ACK. Cancelling a terminal order is a guaranteed error and tells us
        # nothing; reading its state tells us everything.
        progress = self.refresh_live_orders() if self.armed else []
        if self.armed:
            for coid, o in list(self.store.live_orders().items()):
                vid = o.get("venue_order_id")
                if not vid:
                    continue
                try:
                    self.tp.cancel(str(vid))
                    cancelled.append(vid)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    cancel_errors.append({"order": vid,
                                          "error": type(exc).__name__})

        # Reconciliation is the only thing allowed to release exposure. A
        # cancel we believe succeeded is not evidence that it did.
        recon: Dict[str, Any]
        try:
            recon = self.store.reconcile_from_venue(self.tp.positions(),
                                                    self.tp.open_orders())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.store.mark_uncertain("reconciliation failed: %s"
                                      % type(exc).__name__)
            recon = {"reconciled": False, "authority": "VENUE",
                     "note": "exposure NOT released; this probe ends "
                             "uncertain and the state says so"}
            recon.update(shape_evidence(exc))
        out = {"cancelled": cancelled, "cancel_errors": cancel_errors,
               "order_progress": progress,
               "reconciliation": recon,
               "orders_uncertain": self.store.orders_uncertain()}
        self.journal.write("close_out", out)
        return out

    # -------------------------------------------------------------- report

    def submit_failures(self) -> List[dict]:
        """Every submit whose outcome could not be established, with cause.

        One entry per SUBMIT_OUTCOME_UNKNOWN: the exception class, the
        redacted message, the lifecycle stage the transport had reached, and
        whether the preview and the order POST were actually attempted. The
        intent is carried as strings so a Decimal cannot break the
        serializer at the one moment the report matters most.
        """
        out: List[dict] = []
        for r in self.records:
            o = r.get("ORDER") or {}
            if o.get("reason") != "SUBMIT_OUTCOME_UNKNOWN":
                continue
            out.append({
                "client_order_id": o.get("client_order_id"),
                "error": o.get("error"),
                "detail": o.get("detail"),
                "failure_stage": o.get("failure_stage"),
                "preview_attempted": o.get("preview_attempted"),
                "preview_status": o.get("preview_status"),
                "order_post_attempted": o.get("order_post_attempted"),
                "intent": o.get("intent"),
                "action_key": r.get("action_key")})
        return out

    def session_coids(self) -> List[str]:
        """The client order ids THIS run submitted. Immutable run provenance.

        `filled_qty` and `fees` live in the inventory store, which is a file
        on a volume that outlives the process. Probe 05's report therefore
        carried Probe 04's fill -- coid l5f-e2e57f2b..., 2.25 shares from the
        1300z window -- as if this run had produced it, which would have
        inflated any accounting taken from the report. A probe reports what
        IT submitted; the store keeps the rest.
        """
        out: List[str] = []
        for r in self.records:
            o = r.get("ORDER") or {}
            coid = o.get("client_order_id")
            if o.get("submitted") is True and coid and coid not in out:
                out.append(str(coid))
        return out

    def scoped(self, key: str) -> Dict[str, str]:
        """One store map, narrowed to this run's orders."""
        mine = set(self.session_coids())
        book = self.store.state.get(key) or {}
        return {k: str(v) for k, v in book.items() if k in mine}

    def report(self, close: Mapping[str, Any]) -> Dict[str, Any]:
        submitted = [r for r in self.records
                     if (r.get("ORDER") or {}).get("submitted") is True]
        filled = [r for r in self.records
                  if (r.get("FILL") or {}).get("filled") is True]
        refused: Dict[str, int] = {}
        for r in self.records:
            why = r.get("refused_reason")
            if why:
                refused[why] = refused.get(why, 0) + 1
        return {
            "LABEL": "LIVE_BOUNDED_PROBE",
            "market": self.market,
            "authorization": self.auth.as_dict(),
            "preflight": (self.preflight or {}).get("preflight", "NOT_RUN"),
            # the account this actually ran against. Previously the whole
            # preflight collapsed to one word here and the fingerprint went
            # only to the journal, on a volume no one can read once a
            # one-shot exits -- so the account could be neither gated nor
            # checked afterwards.
            "expected_account": expected_account(),
            "api_key_fingerprint": preflight_fingerprint(self.preflight),
            "account_matches_expected": gate_result(
                self.preflight, "account_matches_expected"),
            "armed": self.armed,
            "l5_actions": len(self.records),
            "submitted": len(submitted),
            "filled": len(filled),
            "refused_reason": refused,
            # Probe 03 reported SUBMIT_OUTCOME_UNKNOWN as a bare count and
            # sent the cause to a volume no one could read. The cause now
            # comes out with the report, redacted through the transport's own
            # scrubber, and says WHICH call was in flight.
            "submit_failures": self.submit_failures(),
            # scoped to THIS run's submissions -- see session_coids()
            "fills_from_venue": self.scoped("filled_qty"),
            "fees_from_venue": self.scoped("fees"),
            "fills_excluded_prior_sessions": sorted(
                set(self.store.state.get("filled_qty") or {})
                - set(self.session_coids())),
            # an ACK that says filled_qty 0 is not evidence of no fill, which
            # is the whole lesson of Probe 04. Both counts are reported so
            # they can never be confused again.
            "filled_at_ack": len([r for r in self.records
                                  if (r.get("FILL") or {}).get("filled")
                                  is True]),
            "filled_at_venue": len([v for v in self.scoped("filled_qty")
                                    .values() if D(str(v)) > 0]),
            "inventory": {
                "up": str(self.store.position(self.market, "UP")),
                "dn": str(self.store.position(self.market, "DOWN")),
                "residual": str(self.store.residual(self.market)),
                "committed_usd": str(
                    self.store.committed().get(self.market, D(0))),
                "outstanding_usd": str(
                    self.store.outstanding_by_market().get(self.market, D(0))),
                "live_orders": self.store.live_order_count()},
            "book_feed": self.feed.health(),
            "l5_feed": self.l5_health.as_dict(),
            "stage_reached": self.stage,
            "close_out": dict(close),
            "live_limits": self.limits.as_dict(),
            "worst_case": PM.worst_case(),
            "settlement": "NOT_OBSERVED_IN_THIS_PROCESS",
            "realized": "NOT_REALIZED",
            "labels": ("submitted, filled and settled are distinct. Nothing "
                       "here is realized: realization requires a settlement "
                       "value from the venue after the market resolves."),
        }


def run(*, clock=time.time, sleep=time.sleep, transport_factory=None,
        activity=None, state_path=None, journal_path=None,
        ledger_path=None, book_feed=None) -> Dict[str, Any]:
    """The whole probe. Returns its report; never runs a second market."""
    now = clock()
    market = (os.getenv(AUTH.MARKET_ENV, "").strip()
              or current_slug(now))
    store = EX.InventoryStore(
        state_path or os.getenv("L5_PROBE_STATE",
                                "/state/pmus-ws/l5_probe_inventory.json"))
    journal = JN.Journal(
        journal_path or os.getenv("L5_SESSION_JOURNAL",
                                  "/state/pmus-ws/l5_session_journal.jsonl"))
    auth = AUTH.Authorization(ledger_path)

    # 1. authorization FIRST. Without it nothing that can POST is built.
    try:
        claim = auth.claim(market)
    except AUTH.Refused as exc:
        out = {"LABEL": "LIVE_BOUNDED_PROBE", "market": market,
               "refused": exc.reason, "detail": exc.detail,
               "armed": False, "submitted": 0,
               "note": "no transport capable of POST was constructed"}
        journal.write("authorization_refused", out)
        return out
    journal.write("authorization_claimed", claim)

    if transport_factory is None:
        def transport_factory(slug):                           # noqa: E306
            from src import l5_pmus_transport as TP
            return TP.PmusTransport(market_slug=slug)
    feed = book_feed or FEED.AuthenticatedBookFeed(market, clock=clock)
    probe = Probe(market=market, store=store, journal=journal, auth=auth,
                  transport=transport_factory(market), book_feed=feed,
                  clock=clock, sleep=sleep, activity=activity)

    start = window_start(market)
    if start is None:
        out = {"LABEL": "LIVE_BOUNDED_PROBE", "market": market,
               "refused": "WINDOW_NOT_ESTABLISHED",
               "detail": {"slug": market,
                          "note": "the window this slug names cannot be "
                                  "read; nothing is decided against a "
                                  "window we cannot place in time"},
               "armed": False, "submitted": 0}
        journal.write("window_not_established", out)
        auth.consume({"armed": False, "submitted": 0,
                      "refused": "WINDOW_NOT_ESTABLISHED"})
        return out
    deadline = start + WINDOW_S
    close: Dict[str, Any] = {}
    try:
        # 2. preflight, read-only, retried within its own bounds
        while clock() < start + PREFLIGHT_UNTIL_S:
            if clock() >= start + PREFLIGHT_AT_S:
                if probe.run_preflight().get("preflight") == "PASSED":
                    break
                sleep(PREFLIGHT_RETRY_S)
            else:
                sleep(POLL_S)
        # 3. the authenticated book, before anything can be decided against
        #    it. Started only after preflight passed, so a probe that will
        #    not trade opens no socket.
        if book_feed is None:
            feed.start()
            if not feed.wait_ready(sleep=sleep):
                raise AUTH.Refused(
                    "AUTH_BOOK_NEVER_STARTED",
                    {"market": market, "health": feed.health(),
                     "note": "no authenticated book arrived; nothing is "
                             "decided against a book we do not have"})
        journal.write("book_feed", feed.health())
        # 4. arm, needing preflight AND the owner token
        probe.arm()
        # 5. work exactly this window
        probe.work_window(deadline)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # STDOUT as well as the journal. Probe 02's reason went only to the
        # volume, which cannot be read once the one-shot container exits,
        # so the failure was undiagnosable from outside.
        from src.l5_pmus_transport import redact
        err = {"LABEL": "LIVE_PROBE_ERROR", "market": market,
               "stage": probe.stage,
               "error": type(exc).__name__,
               "detail": redact(str(exc)[:300]),
               "l5_feed": probe.l5_health.as_dict(),
               "book_feed": feed.health(),
               "note": ("redacted through the transport's own scrubber; no "
                        "credential, header or signature appears here")}
        journal.write("probe_error", err)
        print("L5_PROBE_ERROR " + json.dumps(err, default=str), flush=True)
    finally:
        # 6. close out and spend the authorization, WHATEVER happened above.
        try:
            close = probe.close_out()
        except BaseException:                                  # noqa: BLE001
            close = {"close_out": "FAILED"}
        # spend it BEFORE reporting, so the report cannot say "consumed"
        # about an authorization that is in fact still claimable.
        auth.consume({"armed": probe.armed,
                      "submitted": sum(
                          1 for r in probe.records
                          if (r.get("ORDER") or {}).get("submitted") is True),
                      "orders_uncertain": store.orders_uncertain()})
        rep = probe.report(close)
        journal.write("probe_report", rep)
        if book_feed is None:
            feed.stop()
    return rep


# --------------------------------------------------------- observe only
#
# Probes 01 and 02 both died in the PRE-EXECUTION path -- the book feed and
# the action feed -- and both spent a money authorization to find out. This
# mode exercises that same path, in production, against a live window, with
# no capability to place an order at all.
#
# The impossibility is structural, not a flag. It never calls auth.claim(),
# never constructs PmusTransport, and hands the executor
# `l5_live_execution.NullTransport` -- the production DISARMED transport,
# which holds no credential, has no HTTP client of any kind, and raises
# NotArmed from submit() and cancel(). There is no object in this mode that
# could send an order if the decision path asked it to.

# ------------------------------------------------------- recovery only
#
# Probe 04 filled and our books never learned. The local file still says one
# order is live at $0.72 outstanding while the venue says CQ5NPBADMYC3 was
# ORDER_STATE_FILLED at 13:01:46.847706680Z, the account holds nothing, and
# the market expired at settlementPx 0.0000.
#
# Hand-editing that file is not an option: the whole point of the design is
# that only an authoritative answer moves state. So this runs the SAME
# production lifecycle and reconciliation code the armed session runs --
# order_progress, apply_order_progress, reconcile_from_venue -- against the
# venue, over a transport that cannot mutate.
#
# It differs from observe-only deliberately. Observe-only never constructs
# PmusTransport at all; this constructs one INJECTED with
# pmus_auth.ReadOnlyTransport, which raises PermissionError on any method
# but GET. That is what lets it use the production read paths (order(),
# positions(), open_orders()) rather than a parallel copy of them -- and a
# parallel copy is what produced every defect this probe has found. Two
# independent barriers stand in front of a mutation: the http object refuses
# it, and the transport is never armed, so submit() raises NotArmed first.

def shape_evidence(exc: BaseException) -> Dict[str, Any]:
    """What the venue actually sent, when we could not read it.

    Probe 05 ended with `reconciliation failed: KeyError` and nothing else,
    so the shape that caused it had to be reasoned about after the fact --
    and by then the account was flat and the market expired, so it could not
    be fetched at all. An unreadable row now reports its OWN key list.
    """
    ev: Dict[str, Any] = {"error": type(exc).__name__}
    what = getattr(exc, "what", None)
    if what is not None:
        ev["what"] = str(what)
    rows = getattr(exc, "rows", None)
    if rows is not None:
        ev["row_keys"] = rows
    vals = getattr(exc, "values", None)
    if vals is not None:
        ev["row_values"] = vals
    return ev


def journal_rows(path: str) -> List[dict]:
    """Every journal record, oldest first. Unparseable lines are kept."""
    out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"unparsed": line[:400]})
    except OSError as exc:
        return [{"unreadable": type(exc).__name__}]
    return out


#: the per-order columns the lifecycle audit needs, in order.
ORDER_COLUMNS = ("coid", "venue_order_id", "side", "qty_desired",
                 "qty_executable", "quantization_delta", "limit_px",
                 "avg_fill_px", "cum_qty", "leaves_qty", "commission",
                 "inventory_after", "venue_state", "released", "submitted")


def order_table(records: Sequence[Mapping[str, Any]],
                market: str) -> List[Dict[str, Any]]:
    """One row per submitted order, folded from the journal's own records.

    Two record kinds carry it. An `action` record holds the layered stages --
    PMUS_TRANSLATION for desired vs executable quantity and the quantization
    delta, ORDER for the side, limit price and venue id, INVENTORY for the
    ledger immediately after a fill. An `order_progress` record holds what
    the venue said on refresh. Nothing is recomputed: a number recomputed
    after the fact is not evidence of what the run did, and a column the
    journal does not carry stays None rather than being filled in.
    """
    rows: Dict[str, Dict[str, Any]] = {}

    def row(coid: str) -> Dict[str, Any]:
        r = rows.get(coid)
        if r is None:
            r = {c: None for c in ORDER_COLUMNS}
            r["coid"] = coid
            rows[coid] = r
        return r

    def put(r: Dict[str, Any], col: str, v: Any) -> None:
        if v is not None:
            r[col] = v if isinstance(v, (dict, list)) else str(v)

    for rec in records:
        kind = rec.get("kind")
        if kind == "order_progress":
            coid = rec.get("coid")
            if not coid:
                continue
            r = row(str(coid))
            put(r, "venue_order_id", rec.get("venue_order_id"))
            put(r, "cum_qty", rec.get("cum") or rec.get("cum_qty"))
            put(r, "avg_fill_px", rec.get("avg_px"))
            put(r, "commission", rec.get("commission"))
            put(r, "leaves_qty", rec.get("leaves"))
            put(r, "venue_state", rec.get("state"))
            put(r, "released", rec.get("released"))
            continue
        if market and rec.get("market") not in (None, market):
            continue
        o = rec.get("ORDER") or {}
        coid = o.get("client_order_id")
        if not coid:
            continue
        r = row(str(coid))
        tr = rec.get("PMUS_TRANSLATION") or {}
        put(r, "side", o.get("side") or tr.get("side"))
        put(r, "limit_px", o.get("price") if o.get("price") is not None
            else tr.get("price"))
        put(r, "venue_order_id", o.get("venue_order_id"))
        put(r, "qty_desired", tr.get("qty_desired"))
        put(r, "qty_executable", tr.get("qty"))
        put(r, "quantization_delta", tr.get("quantization_delta"))
        put(r, "submitted", o.get("submitted"))
        inv = rec.get("INVENTORY")
        if isinstance(inv, Mapping):
            r["inventory_after"] = dict(inv)
    return [rows[k] for k in sorted(rows)]


#: one comparison row per L5 action: what he did, what we did about it.
ACTION_COLUMNS = ("action_key", "l5_side", "l5_price", "l5_size",
                  "l5_relation", "our_mode", "our_price", "our_qty_desired",
                  "our_qty_executable", "submitted", "stage_reached",
                  "refused_by_layer", "refused_reason", "book_source",
                  "snapshot_age_s")


def action_table(records: Sequence[Mapping[str, Any]],
                 market: str) -> List[Dict[str, Any]]:
    """L5's action beside our decision on it, one row each.

    This is the comparison the validation standard asks for -- side, price,
    size, aggressiveness and outcome at the same market state -- and it is
    the honest version of it: our engine is DRIVEN by his actions, so this
    says what we did about each thing he did. It does not claim to know what
    he would have done at a state we reached on our own.
    """
    out: List[Dict[str, Any]] = []
    for rec in records:
        if rec.get("kind") != "action":
            continue
        if market and rec.get("market") not in (None, market):
            continue
        l5 = rec.get("L5_NATIVE") or {}
        tr = rec.get("PMUS_TRANSLATION") or {}
        o = rec.get("ORDER") or {}
        out.append({
            "action_key": rec.get("action_key"),
            "l5_side": l5.get("side"),
            "l5_price": str(l5.get("price")) if l5.get("price") is not None
            else None,
            "l5_size": str(l5.get("l5_size")) if l5.get("l5_size") is not None
            else None,
            "l5_relation": l5.get("relation"),
            "our_mode": tr.get("mode"),
            "our_price": tr.get("price"),
            "our_qty_desired": tr.get("qty_desired"),
            "our_qty_executable": tr.get("qty"),
            "submitted": o.get("submitted"),
            "stage_reached": rec.get("stage_reached"),
            "refused_by_layer": rec.get("refused_by_layer"),
            "refused_reason": rec.get("refused_reason"),
            "book_source": rec.get("book_source"),
            "snapshot_age_s": rec.get("pmus_snapshot_age_s"),
        })
    return out


def action_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How the window went, by L5 side and by what stopped us."""
    by_side: Dict[str, Dict[str, int]] = {}
    by_reason: Dict[str, int] = {}
    for r in rows:
        side = str(r.get("l5_side") or "?")
        d = by_side.setdefault(side, {"actions": 0, "submitted": 0,
                                      "refused": 0})
        d["actions"] += 1
        if r.get("submitted") is True:
            d["submitted"] += 1
        why = r.get("refused_reason")
        if why:
            d["refused"] += 1
            by_reason[str(why)] = by_reason.get(str(why), 0) + 1
    return {"actions": len(rows), "by_l5_side": by_side,
            "by_refusal": by_reason,
            "followed": sum(1 for r in rows if r.get("submitted") is True)}


RECOVER_ENV = "L5_RECOVER_ONLY"


def recover_only_enabled() -> bool:
    return os.getenv(RECOVER_ENV, "").strip().upper() in ("1", "YES", "TRUE")


def recover(*, http=None, state_path=None, journal_path=None,
            market: Optional[str] = None) -> Dict[str, Any]:
    """Reconcile stale local order state against the venue. GET only."""
    from src import l5_pmus_transport as TP
    market = (market or os.getenv(AUTH.MARKET_ENV, "").strip()
              or os.getenv("L5_RECOVER_MARKET", "").strip())
    store = EX.InventoryStore(
        state_path or os.getenv("L5_PROBE_STATE",
                                "/state/pmus-ws/l5_probe_inventory.json"))
    journal = JN.Journal(
        journal_path or os.getenv("L5_SESSION_JOURNAL",
                                  "/state/pmus-ws/l5_session_journal.jsonl"))
    ro = http or Q1.ReadOnlyTransport()
    tp = TP.PmusTransport(market_slug=market, http=ro)

    # prove the incapability rather than assert it
    try:
        ro.request("POST", "/v1/orders", {}, Q1.API)
        post_capable = True
    except PermissionError:
        post_capable = False
    except BaseException:                                      # noqa: BLE001
        post_capable = True

    before = {
        "live_orders": {k: dict(v) for k, v in store.live_orders().items()},
        "outstanding_usd": {k: str(v) for k, v
                            in store.outstanding_by_market().items()},
        "positions": dict(store.state.get("positions") or {}),
        "orders_uncertain": store.orders_uncertain(),
    }

    progress: List[dict] = []
    for coid, o in list(store.live_orders().items()):
        vid = o.get("venue_order_id")
        if not vid:
            progress.append({"coid": coid, "skipped": "NO_VENUE_ORDER_ID"})
            continue
        try:
            row = tp.order(str(vid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            store.mark_uncertain("recovery order query raised %s for %s"
                                 % (type(exc).__name__, coid))
            progress.append({"coid": coid, "error": type(exc).__name__})
            continue
        prog = EX.order_progress(row)
        res = store.apply_order_progress(
            coid, prog, market=str(o.get("market") or market),
            side=str(o.get("side") or ""))
        res.update({"venue_order_id": str(vid),
                    "venue_state": prog.get("state"),
                    "avg_px": str(prog.get("avg_px")),
                    "commission": str(prog.get("commission"))})
        progress.append(res)

    recon: Dict[str, Any]
    try:
        recon = store.reconcile_from_venue(tp.positions(), tp.open_orders())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        store.mark_uncertain("recovery reconciliation failed: %s"
                             % type(exc).__name__)
        recon = {"reconciled": False, "authority": "VENUE",
                 "note": "nothing released; the state stays uncertain"}
        recon.update(shape_evidence(exc))

    market_state: Dict[str, Any] = {}
    if market:
        try:
            md = (ro.request("GET", "/v1/markets/%s/bbo" % market, None,
                             Q1.API) or {}).get("marketData") or {}
            market_state = {"state": md.get("state"),
                            "settlement_px": (md.get("settlementPx") or {})
                            .get("value")}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            market_state = {"error": type(exc).__name__}

    rep = {
        "LABEL": "LIVE_RECOVERY",
        "market": market,
        "post_capable": post_capable,
        "armed": bool(getattr(tp, "armed", False)),
        "transport": "PmusTransport+ReadOnlyTransport",
        "before": before,
        "order_progress": progress,
        "reconciliation": recon,
        "market_state": market_state,
        "after": {
            "live_orders": {k: dict(v) for k, v
                            in store.live_orders().items()},
            "outstanding_usd": {k: str(v) for k, v
                                in store.outstanding_by_market().items()},
            "positions": dict(store.state.get("positions") or {}),
            "orders_uncertain": store.orders_uncertain(),
        },
        "labels": ("GET only. The http object raises PermissionError on any "
                   "method but GET and the transport is never armed, so "
                   "submit() would raise NotArmed first. No order was "
                   "created, cancelled or modified. Local state moved only "
                   "where the venue said so."),
    }
    recs = journal_rows(journal.path if isinstance(journal.path, str)
                        else str(journal.path))
    rep["journal"] = {
        "path": str(journal.path),
        "records": len(recs),
        "kinds": sorted({str(r.get("kind")) for r in recs if r.get("kind")}),
        "sessions": sorted({str(r.get("session_id")) for r in recs
                            if r.get("session_id")}),
    }
    rep["order_table"] = order_table(recs, market)
    rep["action_table"] = action_table(recs, market)
    rep["action_summary"] = action_summary(rep["action_table"])
    journal.write("recovery", rep)
    return rep


OBSERVE_ENV = "L5_OBSERVE_ONLY"

#: if less than this remains of the window that is open at start, wait for
#: the next one instead -- a 30-second sample proves nothing about sustained
#: operation. Still exactly one window, then the process exits.
OBSERVE_MIN_S = 300.0


def l5_native_was_readable(rec: Mapping[str, Any]) -> bool:
    """Did the frozen rule get a decision out of this row?

    The codebase's convention for a refusal reason is CODE[:detail] --
    summarize() splits on ":" for exactly this, because reasons such as
    "STALE_ACTION:12.4s" carry their measurement. Comparing the whole string
    worked only because UNREADABLE_ROW happens to be bare today; the day it
    carries detail, every unreadable row would count as a readable one and
    the report would overstate the thing it exists to measure.
    """
    return (str(rec.get("refused_reason") or "").split(":")[0]
            != "UNREADABLE_ROW")


def observe_only_enabled() -> bool:
    return os.getenv(OBSERVE_ENV, "").strip().upper() in ("1", "YES", "TRUE")


def observe(*, clock=time.time, sleep=time.sleep, book_feed=None,
            activity=None, state_path=None, journal_path=None,
            transport=None) -> Dict[str, Any]:
    """Drive the real pre-execution path for one window. Sends nothing."""
    now = clock()
    market = current_slug(now)
    start = window_start(market)
    if start is not None and (start + WINDOW_S) - now < OBSERVE_MIN_S:
        market = current_slug(start + WINDOW_S + 1.0)
        start = window_start(market)
    if start is None:
        return {"LABEL": "LIVE_OBSERVE_ONLY", "market": market,
                "refused": "WINDOW_NOT_ESTABLISHED", "armed": False,
                "submitted_live": 0}
    deadline = start + WINDOW_S

    journal = JN.Journal(
        journal_path or os.getenv("L5_SESSION_JOURNAL",
                                  "/state/pmus-ws/l5_observe_journal.jsonl"))
    store = EX.InventoryStore(
        state_path or "/state/pmus-ws/l5_observe_inventory.json")
    feed = book_feed or FEED.AuthenticatedBookFeed(market, clock=clock)
    # the production DISARMED transport. No credential, no HTTP, no POST.
    tp = transport if transport is not None else EX.NullTransport()
    limits = PM.probe_limits()
    health = WA.FeedHealth()
    seen: Set[str] = set()
    records: List[dict] = []
    stage = "starting"

    try:
        stage = "waiting_for_window"
        while clock() < start:
            sleep(POLL_S)
        stage = "book_feed"
        if book_feed is None:
            feed.start()
            feed.wait_ready(sleep=sleep)
        journal.write("observe_book_feed", feed.health())
        stage = "observing"
        while clock() < deadline:
            actions, ok = new_l5_actions(market, seen, activity, health=health)
            if actions:
                out = W.run_window(
                    actions, feed.books(), market=market,
                    window=market[-5:], store=store, limits=limits,
                    transport=tp, lam=PM.LAMBDA,
                    now_fn=lambda: dt.datetime.fromtimestamp(
                        clock(), dt.timezone.utc))
                for rec in out["records"]:
                    records.append(rec)
                    journal.write("observe_action", rec)
            sleep(POLL_S)
        stage = "done"
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        from src.l5_pmus_transport import redact
        err = {"LABEL": "LIVE_PROBE_ERROR", "mode": "OBSERVE_ONLY",
               "market": market, "stage": stage,
               "error": type(exc).__name__, "detail": redact(str(exc)[:300]),
               "l5_feed": health.as_dict(), "book_feed": feed.health()}
        journal.write("observe_error", err)
        print("L5_PROBE_ERROR " + json.dumps(err, default=str), flush=True)
        stage = "error"
    finally:
        if book_feed is None:
            feed.stop()

    summary = W.summarize(records)
    # THE SECOND TREATMENT, in shadow. Every action the frozen rule called
    # MAKER and then refused STRICT_NOT_EVALUABLE becomes a genuine resting
    # order INTENT here -- price, size and post-only flag -- so a window can
    # show that passive L5 actions produce placeable orders rather than
    # nothing. L5_NATIVE is untouched; this reads its output and adds
    # nothing to its decision.
    maker_intents = []
    for r in records:
        mi = MK.maker_intent(r, lam=PM.LAMBDA,
                             quantity_increment=D("0.01"),
                             floor_fn=EX.floor_to_increment)
        if mi is not None:
            maker_intents.append(mi)
            journal.write("maker_intent", mi)
    readable = sum(1 for r in records if l5_native_was_readable(r))
    translated = sum(1 for r in records
                     if (r.get("PMUS_TRANSLATION") or {}).get("ok") is True)
    overlay_refusals = sum(1 for r in records
                           if r.get("refused_by_layer") == "SAFETY_OVERLAY")
    rep = {
        "LABEL": "LIVE_OBSERVE_ONLY",
        "market": market, "stage_reached": stage,
        "authorization_claimed": False,
        "post_capable": False,
        "transport": getattr(tp, "name", "?"),
        "l5_feed": health.as_dict(),
        "book_feed": feed.health(),
        "actions_handled": len(records),
        "l5_native_decisions": readable,
        "pmus_translations_ok": translated,
        "overlay_refusals": overlay_refusals,
        # summarize() already returns a COUNT. Taking len() of it raised
        # TypeError for every non-zero value, and `or []` hid that at zero --
        # so the report could only be written when there was nothing to
        # report. 00:30:06Z on 2026-09-25, after a full window's work.
        "would_submit": summary.get("would_submit", 0),
        # what the maker treatment WOULD rest, and what it is worth knowing
        # before any of it is real: an intent is not an order and not a fill
        "maker_treatment": {
            "intents": len(maker_intents),
            "placeable": sum(1 for m in maker_intents if m["placeable"]),
            "unplaceable_after_flooring":
                sum(1 for m in maker_intents if not m["placeable"]),
            "by_side": {side: sum(1 for m in maker_intents
                                  if m["side"] == side)
                        for side in ("UP", "DOWN")},
            "notional_usd": str(sum(
                (D(m["price"]) * D(m["qty"]) for m in maker_intents), D(0))),
            "sample": maker_intents[:5],
            "labels": ("INTENTS ONLY. Nothing was placed, nothing rested and "
                       "nothing filled. These are the actions the frozen "
                       "rule refused STRICT_NOT_EVALUABLE, which is a "
                       "statement about replay evidence, not about whether "
                       "the venue would accept the order."),
        },
        "refused_by_layer": summary.get("refused_by_layer"),
        "refused_reason": summary.get("refused_reason"),
        "armed": False,
        "submitted_live": summary.get("submitted_live", 0),
        "live_limits": limits.as_dict(),
        "labels": ("OBSERVE ONLY. No authorization was claimed, no "
                   "PmusTransport was constructed, and the executor held "
                   "NullTransport, which has no HTTP client. A would-submit "
                   "is an intent the transport refused, never a fill."),
    }
    journal.write("observe_report", rep)
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    if recover_only_enabled():
        rep = recover()
        table = rep.pop("order_table", [])
        actions = rep.pop("action_table", [])
        print("L5_RECOVERY " + json.dumps(rep, default=str), flush=True)
        # one line per order. A single line carrying eighteen orders is a
        # line that gets truncated exactly when it matters.
        for r in table:
            print("L5_ORDER_ROW " + json.dumps(r, default=str), flush=True)
        print("L5_ORDER_ROWS %d" % len(table), flush=True)
        for a in actions:
            print("L5_ACTION_ROW " + json.dumps(a, default=str), flush=True)
        print("L5_ACTION_ROWS %d" % len(actions), flush=True)
        return 0
    if observe_only_enabled():
        rep = observe()
        print("L5_OBSERVE_ONLY " + json.dumps(rep, default=str), flush=True)
        return 0
    rep = run()
    print("L5_LIVE_PROBE " + json.dumps(rep, default=str), flush=True)
    # exits either way: one market, then the process is done.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
