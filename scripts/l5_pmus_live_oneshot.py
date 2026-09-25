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
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM
from src import l5_session_journal as JN
from src import l5_window_actions as WA

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
PREFLIGHT_UNTIL_S = 780.0

#: stop opening new orders before the close, so a fill has somewhere to go
#: and the cancel/reconcile tail is not racing the window end.
NO_NEW_ORDERS_AFTER_S = 810.0


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
            self.preflight = self.tp.preflight()
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

    def work_window(self, deadline: float) -> None:
        """Handle actions as they are observed, until the window closes."""
        self.stage = "working_window"
        while self.clock() < deadline:
            now = self.clock()
            if now >= deadline - (WINDOW_S - NO_NEW_ORDERS_AFTER_S):
                break
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
        if self.armed:
            for coid, o in self.store.live_orders().items():
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
                     "error": type(exc).__name__,
                     "note": "exposure NOT released; this probe ends "
                             "uncertain and the state says so"}
        out = {"cancelled": cancelled, "cancel_errors": cancel_errors,
               "reconciliation": recon,
               "orders_uncertain": self.store.orders_uncertain()}
        self.journal.write("close_out", out)
        return out

    # -------------------------------------------------------------- report

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
            "armed": self.armed,
            "l5_actions": len(self.records),
            "submitted": len(submitted),
            "filled": len(filled),
            "refused_reason": refused,
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
