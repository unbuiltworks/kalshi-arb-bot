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
from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM
from src import l5_session_journal as JN

WINDOW_S = 900.0
SLUG_PREFIX = "cpc-btc-updown-15m-"

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


def current_slug(now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(int(now // WINDOW_S) * WINDOW_S,
                                  dt.timezone.utc)
    return "%s%s-%02d%02dz" % (SLUG_PREFIX, t.strftime("%Y-%m-%d"),
                               t.hour, t.minute)


def window_start(slug: str) -> float:
    """Epoch seconds of the window this slug names."""
    tail = slug[len(SLUG_PREFIX):]
    day, hhmm = tail[:10], tail[11:15]
    t = dt.datetime.strptime("%s %s" % (day, hhmm), "%Y-%m-%d %H%M")
    return t.replace(tzinfo=dt.timezone.utc).timestamp()


def books_from_transport(tp: Any, slug: str, now: float) -> List[dict]:
    """One authenticated book sample, in the shape the wiring expects.

    A GET on the proven read path. This runs in its own service and cannot
    see the recorder's in-process websocket state, so it reads the book
    itself rather than depending on another service's memory.
    """
    try:
        md = tp.book(slug) or {}
    except BaseException:                                      # noqa: BLE001
        return []
    bid = md.get("bestBid") or md.get("bid")
    ask = md.get("bestAsk") or md.get("ask")
    row = {"t": now,
           "bid": None if bid is None else str(bid),
           "ask": None if ask is None else str(ask),
           "bid_qty": md.get("bestBidQuantity") or md.get("bid_qty"),
           "ask_qty": md.get("bestAskQuantity") or md.get("ask_qty")}
    row["status"] = ("OK" if row["bid"] is not None and row["ask"] is not None
                     else "NO_BOOK" if row["bid"] is None and row["ask"] is None
                     else "ONE_SIDED")
    return [row]


def new_l5_actions(slug: str, seen: Set[str],
                   activity=None) -> Tuple[List[dict], bool]:
    """L5's trades in THIS window that we have not handled yet.

    `None` from the feed means the FEED FAILED; an empty list means L5 has
    not traded. They are kept apart, because a broken feed reported as a
    quiet market is evidence of nothing that looks like evidence.
    """
    if activity is None:
        from src import l5_pmus_realtime_observer as O
        activity = O.l5_activity
    from src import l5_authenticated_observation as OBS
    ws = OBS.window_start_epoch(slug)
    rows = activity(limit=100)
    if rows is None:
        return [], False
    out = []
    for r in rows:
        try:
            t = float(r.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if ws is not None and not (ws <= t < ws + WINDOW_S):
            continue
        key = str(r.get("transactionHash") or r.get("id") or t)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r, action_key=key,
                        receipt_utc=dt.datetime.now(
                            dt.timezone.utc).isoformat()))
    return out, True


class Probe:
    """One market, start to finish, and then nothing."""

    def __init__(self, *, market: str, store: EX.InventoryStore,
                 journal: JN.Journal, auth: AUTH.Authorization,
                 transport, clock=time.time, sleep=time.sleep,
                 activity=None) -> None:
        self.market = market
        self.store = store
        self.journal = journal
        self.auth = auth
        self.tp = transport
        self.clock = clock
        self.sleep = sleep
        self.activity = activity
        self.limits = PM.probe_limits()
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
        out = self.tp.arm()          # itself re-checks preflight AND the token
        self.armed = True
        self.journal.write("armed", {"market": self.market, "detail": out})
        return out

    # -------------------------------------------------------------- trading

    def work_window(self, deadline: float) -> None:
        """Handle actions as they are observed, until the window closes."""
        while self.clock() < deadline:
            now = self.clock()
            if now >= deadline - (WINDOW_S - NO_NEW_ORDERS_AFTER_S):
                break
            actions, feed_ok = new_l5_actions(self.market, self.seen,
                                              self.activity)
            if not feed_ok:
                self.journal.write("feed_failed", {"market": self.market})
            if actions:
                # every order re-checks the claim: one market, no exceptions
                self.auth.check_market(self.market)
                books = books_from_transport(self.tp, self.market, now)
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
        ledger_path=None) -> Dict[str, Any]:
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
    probe = Probe(market=market, store=store, journal=journal, auth=auth,
                  transport=transport_factory(market), clock=clock,
                  sleep=sleep, activity=activity)

    start = window_start(market)
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
        # 3. arm, needing preflight AND the owner token
        probe.arm()
        # 4. work exactly this window
        probe.work_window(deadline)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        journal.write("probe_error", {"market": market,
                                      "error": type(exc).__name__,
                                      "detail": str(exc)[:300]})
    finally:
        # 5. close out and spend the authorization, WHATEVER happened above.
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
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    rep = run()
    print("L5_LIVE_PROBE " + json.dumps(rep, default=str), flush=True)
    # exits either way: one market, then the process is done.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
