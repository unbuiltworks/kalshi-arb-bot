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
import pathlib
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
from src import pmus_arm_retry as AR
from src import pmus_completion_shadow as SHADOW
from src import pmus_live_mandate as MANDATE
from src import pmus_lock5_live_adapter as ADAPT
from src import pmus_lock5_translation as LOCK5
from src import pmus_paired_log as PAIRED
from src import pmus_probe_observability as OBS
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



#: this artifact's identity. A mandate naming another service or strategy is
#: somebody else's authorization, however well-formed it is.
RUNG_A_SERVICE = "l5-live-probe-oneshot"
RUNG_A_STRATEGY = "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE"


def rung_a_mandate(now=None):
    """The mandate for THIS probe, or (None, why). VALIDATED, not merely parsed.

    `MANDATE.load_mandate()` falls back to `analysis/l5/authorization/
    live_mandate.json`, which is GIT-TRACKED and currently holds the EXPIRED
    OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT authorization: $5 per market,
    $15 total, 10 markets, service `l5-r1-probe`. It parses without error, because
    expiry and service are checked at `admit()` time rather than at load.

    So "a mandate loaded" is not "authority exists". Without these checks, a probe
    with PMUS_LIVE_MANDATE unset would arm and report $15 of authority it does not
    have, and only fail at the first order -- which is the
    "we could not read today's authorization, so we used yesterday's" failure that
    `pmus_live_mandate` itself names as the whole 2026-09-16 incident.

    The env var is therefore REQUIRED: this path does not accept the file
    fallback. An authorization for a live probe is something the owner sets for
    this run, not something the repository happens to contain.
    """
    raw = (os.getenv(MANDATE.MANDATE_ENV) or "").strip()
    if not raw:
        return None, ("mandate_env_required:%s (the git-tracked file fallback is "
                      "not accepted as authority for this probe)"
                      % MANDATE.MANDATE_ENV)
    m, err = MANDATE.parse_mandate(raw)
    if m is None:
        return None, err
    if m.expired(now):
        return None, "mandate_expired:%s" % (m.expiry,)
    if m.service != RUNG_A_SERVICE:
        return None, "mandate_service_mismatch:%s" % (m.service,)
    if m.strategy != RUNG_A_STRATEGY:
        return None, "mandate_strategy_mismatch:%s" % (m.strategy,)
    return m, None


def mandate_fingerprint() -> Optional[str]:
    """A stable digest of the CURRENT mandate text, or None when there is none.

    Re-read from source on every call rather than cached, so a mandate swapped
    or deleted between arm attempts is detected instead of assumed unchanged.
    """
    import hashlib
    m, _ = rung_a_mandate()
    if m is None:
        return None
    fields = "|".join(str(getattr(m, f)) for f in MANDATE.REQUIRED_FIELDS)
    return hashlib.sha256(fields.encode()).hexdigest()[:16]


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
        #: ONE MONETARY AUTHORITY. No dollars exist until a mandate is loaded;
        #: until then every monetary bound is ZERO rather than a static default.
        #: probe_limits() hardcoded $5 and is deliberately NOT called here -- a
        #: structural test fails if it reappears on this path.
        self.limits = EX.SafetyLimits.no_monetary_authority(
            **PM.operational_controls())
        #: where the probe got to, so an exception can name its stage
        #: instead of disappearing into the volume.
        self.stage = "constructed"
        self.l5_health = WA.FeedHealth()
        self.records: List[dict] = []
        self.preflight: Optional[Dict[str, Any]] = None
        #: the immutable market row pinned before the window, or its refusal.
        #: Reported so "we traded on a row resolved at 21:46, not at 22:15"
        #: is in the record rather than inferred.
        self.metadata_pin: Optional[Dict[str, Any]] = None
        #: the native money path, once the native lane builds one. The R4
        #: window submitted and filled through it and the report said
        #: `submitted: 0`, because `report()` only ever counted
        #: `self.records`, which ONLY the L5-action lane writes.
        self.native = None
        self.armed = False
        self.seen: Set[str] = set()
        #: BLOCKER 1 (2026-09-28). The reviewed LOCK5 composition is built HERE,
        #: on the entrypoint, so the money-moving path actually reaches it. The
        #: previous candidate defined the translation and never imported it: the
        #: 39 unit tests all constructed Lock5Session directly, so a green suite
        #: proved the class worked and not that the probe used it. Nothing about
        #: LOCK5 is reimplemented in this file -- it is composed, and every floor
        #: and threshold decision belongs to the frozen module.
        self.lock5: Optional[LOCK5.Lock5Session] = None
        self.monitor: Optional[OBS.ProbeMonitor] = None
        #: the shadow-vs-live paired record. Composed with LOCK5 so no money path
        #: exists without it; see pmus_paired_log for why the live side of a row
        #: can never be derived from the shadow side.
        self.paired: Optional[PAIRED.PairedLog] = None
        #: the paired/LOCK5 transport decorator, set by run() once the mandate
        #: has been loaded. The ONLY route from venue fill evidence to inventory.
        self.adapter = None

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

    def build_lock5(self, *, book, mandate, state_dir,
                    realized_today=None, window=None) -> "LOCK5.Lock5Session":
        """Compose the reviewed LOCK5 session onto THIS probe's transport.

        The transport passed in is already the mandate-guarded one, so there is
        no second path to the wire; the session adds the mandate admission in
        front of it and the observability journal around it.
        """
        self.monitor = OBS.ProbeMonitor(
            str(state_dir) + "/probe_observability.jsonl", market=self.market,
            clock=self.clock)
        self.lock5 = LOCK5.Lock5Session(
            market=self.market, book=book, transport=self.tp,
            mandate=mandate, state_dir=state_dir, monitor=self.monitor,
            realized_today=(realized_today
                            or (lambda: self.store.realized_today())))
        # THE monetary authority, derived from the mandate and nothing else.
        self.limits = EX.SafetyLimits.from_mandate(
            mandate, **PM.operational_controls())
        self.journal.write("monetary_authority", {
            "source": self.limits.monetary_authority,
            "limits": self.limits.as_dict()})
        self.paired = PAIRED.PairedLog(
            market=self.market, # the BTC15m slug carries the window boundary, so it is the binding
            window=str(window or self.market),
            path=str(state_dir) + "/paired_shadow_live.json")
        self.journal.write("lock5_composed", {
            "market": self.market, "threshold": str(LOCK5.LOCK_USD),
            "floor_impl": LOCK5.settlement_independent_floor.__module__,
            "journal": self.monitor.path,
            "paired_log": self.paired.path,
            "shadow_fill_treatment": self.paired.shadow_treatment})
        return self.lock5

    def pair_intent(self, *, action: str, side: str, shadow_price, shadow_qty,
                    shadow_filled: bool):
        """Open the paired row for an intended action, BEFORE submitting it.

        Opened first on purpose: a row that only appears once a fill arrives
        cannot record a non-fill, and the non-fill is the finding the experiment
        is most likely to produce.
        """
        if self.paired is None:
            raise AUTH.Refused("PAIRED_LOG_NOT_COMPOSED", {"market": self.market})
        floor = locked = None
        if self.lock5 is not None:
            floor, locked = self.lock5.floor_now(), self.lock5.locked
        return self.paired.intend(action=action, side=side,
                                  shadow_price=shadow_price,
                                  shadow_qty=shadow_qty,
                                  shadow_filled=bool(shadow_filled),
                                  shadow_floor=floor,
                                  shadow_locked=bool(locked))

    def lock5_may_open_risk(self, action: str):
        """Ask the reviewed session. Never decide it here."""
        if self.lock5 is None:
            return False, "LOCK5_NOT_COMPOSED"
        self.lock5.evaluate_lock()
        return self.lock5.may_open_risk(action)

    def lock5_complete(self, *, outcome_side, ask, qty, first_cost):
        """Route a completion through the reviewed session, or refuse."""
        if self.lock5 is None:
            raise AUTH.Refused("LOCK5_NOT_COMPOSED", {"market": self.market})
        return self.lock5.submit_taker_completion(
            outcome_side=outcome_side, ask=ask, qty=qty, first_cost=first_cost)

    # ---- BOUNDED PRE-ARM RETRY. The 1545z lesson, and nothing more ---------
    #
    # THIS FILE IS THE ONLY ARMING OWNER (3a), so the loop lives here and only
    # the predicates live in `src/pmus_arm_retry.py`, which contains no arming
    # capability at all. The distinction the design turns on:
    #
    #     retry a TRANSIENT PRE-ARM SAFETY CHECK  -- what this is
    #     reacquire or renew AUTHORIZATION        -- never
    #
    # A retry is a second look at the SAME still-valid authorization for the
    # SAME market before any order exists. It cannot re-claim, extend or
    # refresh anything, and it stops the moment the envelope changes. Success
    # remains one-shot: `arm()` sets `self.armed`, and this returns immediately
    # ever after, so no amount of retrying can produce a second arming.
    def arm_with_bounded_retry(self, gate: "AR.ArmRetryGate",
                               *, sleep=time.sleep) -> Dict[str, Any]:
        if self.armed:
            # already armed: NOT a renewal opportunity
            return {"armed": True, "market": self.market,
                    "note": "already armed; retry is not renewal"}
        gate.preconditions()
        while True:
            gate.note_attempt()
            try:
                out = self.arm()
            except AUTH.Refused as exc:
                refusal = getattr(exc, "reason", None) or str(exc)
                self.journal.write("arm_refused", {
                    "market": self.market, "refusal": str(refusal),
                    "attempt": gate.attempts})
                gate.may_retry(refusal)        # raises ArmAbort when it may not
                sleep(gate.interval_s)
                continue
            self.journal.write("arm_retry_summary", gate.summary())
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
            # AUTHORITATIVE FILL EVIDENCE. `res` came from a venue GET on this
            # order, so a positive delta is a fill and not an ACK's self-report.
            # This is the only place inventory reaches LOCK5 and the paired row.
            if self.adapter is not None:
                try:
                    self.adapter.note_venue_progress(
                        res, side=str(o.get("side") or ""))
                except BaseException as exc:                    # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    # a bookkeeping failure must not silently drop the fill
                    self.store.mark_uncertain(
                        "paired/LOCK5 booking raised %s for %s"
                        % (type(exc).__name__, coid))
                    self.journal.write("paired_booking_failed", {
                        "coid": coid, "error": type(exc).__name__})
        return out

    def submitted_count(self) -> int:
        """Orders this probe has SUBMITTED. Not acked, not filled -- submitted.

        The arm gate uses it to refuse a retry after anything has gone out, so it
        must count intents that reached the wire, whatever the venue then said.
        """
        return sum(1 for r in self.records
                   if (r.get("ORDER") or {}).get("submitted") is True)

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
                                   lam=PM.LAMBDA, now_fn=self._utc,
                                   maker_treatment=maker_treatment_enabled(),
                                   maker_only=maker_only_enabled(),
                                   suppress_imbalance_increase=(
                                       imbalance_suppress_enabled()))
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
        skipped_terminal: List[dict] = []
        not_ours: List[str] = []
        if self.armed:
            sid = os.getenv("PMUS_Q1_SESSION_ID", "").strip()
            # ENUMERATE every remaining resting order, not one. Probe 07 can
            # hold up to eight at once, and the refresh above has already
            # retired whatever the venue says is terminal -- so what is left
            # here is what is genuinely still working.
            mine = (self.store.live_orders_for_session(sid) if sid
                    else self.store.live_orders())
            for coid in self.store.live_orders():
                if coid not in mine:
                    not_ours.append(coid)
            for coid, o in list(mine.items()):
                vid = o.get("venue_order_id")
                if not vid:
                    skipped_terminal.append({"coid": coid,
                                             "why": "NO_VENUE_ORDER_ID"})
                    continue
                if str(o.get("state") or "").upper() in ("FILLED",
                                                         "CANCELLED"):
                    skipped_terminal.append({"coid": coid, "venue_order_id":
                                             str(vid), "why": "TERMINAL"})
                    continue
                try:
                    self.tp.cancel(str(vid))
                    cancelled.append(vid)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    # a cancel whose outcome we cannot establish leaves the
                    # exposure exactly where it was; reconciliation decides.
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
               "cancel_skipped_terminal": skipped_terminal,
               # resting orders belonging to an earlier session are OUR
               # exposure and are counted, but this run did not place them
               # and does not cancel them.
               "live_orders_not_this_session": not_ours,
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

    def record_reconciliation(self, close: Mapping[str, Any]) -> None:
        """Carry the venue reconciliation and terminal state onto every row.

        Settlement and authoritative cash P&L are NOT written here: this process
        ends at window close, before the venue resolves the market. A row whose
        settlement is absent is UNSETTLED, and saying so is the point -- filling
        it in from a mark would be exactly the mislabelling the accounting rules
        forbid.
        """
        if self.paired is None:
            return
        pos = {sd: str(self.store.position(self.market, sd))
               for sd in ("up", "down")}
        for row in self.paired.rows:
            row.reconciled(venue_position_up=pos["up"],
                           venue_position_down=pos["down"],
                           residual=str(self.store.residual(self.market)),
                           orders_uncertain=str(self.store.orders_uncertain()),
                           close_out=str(close.get("close_out") or ""),
                           source="RECONCILIATION")
            if not row.resolved:
                row.nonfill("window_closed_without_authoritative_fill")
        self.paired.flush()

    def native_accounting(self) -> Dict[str, Any]:
        """What the NATIVE lane did, from the object that did it.

        R4 (2026-09-29 0045z) left 10 UP contracts at the venue under a
        report that said `submitted: 0`, `filled: 0`, `committed_usd: 0`.
        None of those were lies about the L5-action lane -- it did nothing --
        and all of them were useless, because the native lane is where the
        money moved and `report()` could not see it.
        """
        mp = self.native
        if mp is None:
            return {"lane": "NATIVE", "present": False, "submitted": 0,
                    "orders": [], "exposure": None}
        orders = []
        for rec in getattr(mp, "submitted", []):
            body = rec.get("body") or {}
            px = body.get("price")
            orders.append({
                "kind": rec.get("kind"), "side": rec.get("side"),
                "order_id": rec.get("oid") or "UNKNOWN",
                "qty": body.get("quantity"),
                "price_wire": (px.get("value") if isinstance(px, Mapping)
                               else px),
                "tif": body.get("tif"),
                "post_only": body.get("participateDontInitiate"),
                "ack": rec.get("ack")})
        try:
            exposure = mp.registry.as_dict()
        except BaseException:                                  # noqa: BLE001
            exposure = None
        return {"lane": "NATIVE", "present": True,
                "submitted": len(orders), "orders": orders,
                "exposure": exposure,
                "locked": getattr(mp.eng, "locked_at", None) is not None,
                "lock_transactions": len(getattr(mp, "lock_transactions", [])),
                "remainders": getattr(mp, "remainders", None)}

    def report(self, close: Mapping[str, Any]) -> Dict[str, Any]:
        submitted = [r for r in self.records
                     if (r.get("ORDER") or {}).get("submitted") is True]
        filled = [r for r in self.records
                  if (r.get("FILL") or {}).get("filled") is True]
        native = self.native_accounting()
        coherent = self.coherence(
            close, market=self.market,
            submitted_total=len(submitted) + int(native["submitted"]))
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
            # WHICH gate, not merely that one did. The 2000z window ended
            # with the word FAILED and three gates consistent with it.
            "preflight_failing_check": (self.preflight
                                        or {}).get("failing_check"),
            "market_metadata_pin": self.metadata_pin,
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
            "maker_treatment": maker_treatment_enabled(),
            "maker_only": maker_only_enabled(),
            "l5_actions": len(self.records),
            # PER LANE, and a total. `submitted` keeps its meaning -- the
            # L5-action lane -- because existing evidence is read against it;
            # `submitted_total` is the number that must never be 0 while the
            # venue holds a position this session acquired.
            "submitted": len(submitted),
            "native_lane": native,
            "submitted_total": len(submitted) + int(native["submitted"]),
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
            "monetary_authority": self.limits.monetary_authority,
            # worst case from THESE limits, never from a static manifest default
            "worst_case_usd": str(self.limits.worst_case_loss_usd()),
            "settlement": "NOT_OBSERVED_IN_THIS_PROCESS",
            "realized": "NOT_REALIZED",
            "paired": (self.paired.summary() if self.paired is not None
                       else "NOT_COMPOSED"),
            "labels": ("submitted, filled and settled are distinct. Nothing "
                       "here is realized: realization requires a settlement "
                       "value from the venue after the market resolves."),
            # THE R4 INVARIANT. A report that says nothing was submitted while
            # the venue holds a position for this market is not a report, it
            # is a contradiction, and it must say so in its own output rather
            # than leave the reader to notice.
            "accounting_coherent": coherent["coherent"],
            "accounting_incoherence": coherent["why"],
        }

    @staticmethod
    def coherence(close: Mapping[str, Any], *, market: str,
                  submitted_total: int) -> Dict[str, Any]:
        """Does the venue's own view agree that this session did nothing?

        Reads the close-out's reconciliation, which is authoritative
        (`authority: VENUE`). Any venue position or drift on THIS market with
        zero submissions across BOTH lanes is incoherent, and the R4 window is
        exactly that shape: venue_net 10.0000, submitted 0.
        """
        recon = (close or {}).get("reconciliation") or {}
        net = recon.get("venue_net") or {}
        drift = recon.get("drift") or {}
        held = None
        for k, v in net.items():
            if market and str(k) != str(market):
                continue
            try:
                if D(str(v)) != 0:
                    held = str(v)
            except BaseException:                              # noqa: BLE001
                held = str(v)
        drifted = [k for k in drift
                   if not market or str(k).startswith(str(market))]
        if submitted_total == 0 and (held is not None or drifted):
            return {"coherent": False,
                    "why": {"reason": "VENUE_POSITION_WITH_ZERO_SUBMISSIONS",
                            "market": market, "venue_net": held,
                            "drift_keys": drifted,
                            "submitted_total": submitted_total,
                            "note": ("the venue holds inventory this report "
                                     "cannot account for; the cost basis is "
                                     "not in this process and must be "
                                     "recovered from the mandate ledger and "
                                     "the journal before anything is called "
                                     "realized")}}
        if not recon.get("uncertainty_cleared", True):
            return {"coherent": False,
                    "why": {"reason": "RECONCILIATION_UNCERTAIN",
                            "orders_uncertain": (close or {}).get(
                                "orders_uncertain"),
                            "submitted_total": submitted_total}}
        return {"coherent": True, "why": None}


#: the probe the last run() built, for tests that must inspect the REAL money
#: path rather than a reconstruction of it. Written once per run and never read
#: by production code; the alternative is a test that builds its own probe, which
#: is precisely the vacuity that let an unwired run() through review.
_LAST_PROBE: Optional["Probe"] = None


def native_work_window(probe, feed, deadline, *, journal, clock=time.time,
                       sleep=time.sleep, venue=None, money_path=None):
    """Work one window with the FROZEN q50 policy, driven by the book feed.

    This is the money lane. It never calls `new_l5_actions` and never reaches
    `pmus_translate`: the decisions come from `pmus_l5_native_engine`, whose
    rules reconcile 25/25 to the frozen q50 PROFIT_LOCK_5 ledger, and every
    order still traverses the SAME certified transport -- paired record, LOCK5
    gate, mandate -- that the comparison lane uses.
    """
    from src import pmus_native_money_path as NMP
    from src import pmus_native_venue as NV

    venue = venue or NV.AuthenticatedVenue(probe.tp, market=probe.market,
                                           journal=journal)
    mp = money_path or NMP.build(market=probe.market, transport=probe.tp,
                                 feed=feed, venue=venue, journal=journal)
    # BEFORE the loop, not after it: R4's crash-free window still reported
    # zero, and a window that dies mid-flight must not lose its orders too.
    probe.native = mp
    journal.write("native_clip", {
        "clip": str(mp.eng.clip), "env": NMP.CLIP_ENV,
        "default_when_unset": str(NMP.E.CLIP),
        "note": "capacity control only: theta, variant, hold_s, "
                "residual_cap_k and profit_lock_usd are fixed"})
    probe.native = mp
    seen_t = None
    ticks = 0
    while clock() < deadline:
        book = feed.latest()
        if book is None or book.get("t") == seen_t:
            sleep(POLL_S)
            continue
        seen_t = book.get("t")
        ticks += 1
        res = mp.on_book_state(book, getattr(feed, "ladder", None))
        journal.write("native_tick", {"t": seen_t, **{
            k: v for k, v in res.items() if k != "submitted"}})
        if mp.eng.locked_at is not None:
            journal.write("native_locked", {"t": str(mp.eng.locked_at),
                                            "via": mp.eng.lock_via,
                                            "floor": str(mp.eng.lock_floor)})
            break
    # END OF WINDOW: cancel every tracked quote, confirm it is gone, release
    # the unfilled budget. A quote left resting outlives the window and can
    # fill later, unmanaged, on a market nothing is watching.
    sweep = mp.sweep_all()
    probe.native = mp
    journal.write("native_window_done", {
        "sweep": sweep,
        "ticks": ticks, "submitted": len(mp.submitted),
        "locked": mp.eng.locked_at is not None,
        "lock_transactions": len(mp.lock_transactions),
        "remainders": mp.remainders})
    return mp


#: the series the probe trades. Used for venue discovery, never to build a
#: slug: the slug always comes back from the venue.
BTC15M_SERIES = "btc-updown-15m"

#: how much of a window must remain for it to be worth entering at all.
MIN_REMAINING_S = 240.0


#: when set, only a window that has NOT YET STARTED is eligible.
#:
#: R4 was activated by timing a redeploy into the last four minutes of a
#: quarter hour so that discovery would land on the next full window. The
#: redeploy then took five minutes instead of the thirty seconds the previous
#: two had taken, and the probe entered a window already 94 seconds old. An
#: eligibility rule that depends on how long a platform takes to boot a
#: container is not an eligibility rule. This one is decided from the venue's
#: own window state at the moment the process is actually running.
FULL_WINDOW_ENV = "L5_REQUIRE_FULL_WINDOW"


def require_full_window() -> bool:
    return os.getenv(FULL_WINDOW_ENV, "").strip().lower() in ("1", "true",
                                                              "yes")


def select_venue_window(*, now: float, series: str = BTC15M_SERIES,
                        windows=None,
                        min_remaining_s: float = MIN_REMAINING_S,
                        require_not_started: Optional[bool] = None
                        ) -> Dict[str, Any]:
    """The EXACT slug the venue serves for the current/upcoming window.

    NOT MANUFACTURED FROM THE CLOCK. `l5_window_actions.current_slug` builds
    a slug arithmetically from the wall clock, and on 2026-09-28 that put a
    live probe on `cpc-btc-updown-15m-2026-09-28-2215z` with no venue
    confirmation of any kind; the authorization was then spent on a slug
    whose metadata the venue would not serve for the next sixteen minutes.
    Selection now returns a slug the venue itself listed, or nothing.

    The venue's `closed` flag cannot be used to pick: every PRE-CREATED
    future window is `closed: true, active: true` and only the window
    trading right now is `closed: false` (639 events measured
    2026-09-28T22:52Z). So the choice is made on the venue's own
    `startTime`: the window in progress if enough of it remains, otherwise
    the next one to start.

    Returns {} when the venue confirmed nothing. A caller must then refuse:
    no venue-confirmed slug is not an invitation to compute one.
    """
    if require_not_started is None:
        require_not_started = require_full_window()
    rows = windows
    if rows is None:
        from src import pmus_book_tape as BT
        rows = BT.series_windows(series)
    if not rows:
        return {}
    cut = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    best = None
    for w in rows:
        start = w["start"]
        end = start + dt.timedelta(seconds=WINDOW_S)
        if end <= cut:
            continue                       # already over
        if start <= cut:
            # IN PROGRESS. Eligibility is decided HERE, from the venue's own
            # start time against the clock in this running process -- never
            # from an expectation about how long the deploy took.
            if require_not_started:
                continue
            if (end - cut).total_seconds() < min_remaining_s:
                continue
        best = w
        break
    if best is None:
        return {}
    remaining = (best["start"] + dt.timedelta(seconds=WINDOW_S)
                 - cut).total_seconds()
    return {"slug": best["slug"],
            "start_epoch": best["start"].timestamp(),
            "start_utc": best["start"].isoformat(),
            "event_slug": best["event_slug"],
            "in_progress": best["start"] <= cut,
            "remaining_s": round(remaining, 1),
            "lead_s": round(-min(0.0, (cut - best["start"]).total_seconds()), 1),
            "require_not_started": bool(require_not_started),
            "decided_at_utc": cut.isoformat(),
            "source": "VENUE_SERIES_LISTING"}


def run(*, clock=time.time, sleep=time.sleep, transport_factory=None,
        activity=None, state_path=None, journal_path=None,
        ledger_path=None, book_feed=None) -> Dict[str, Any]:
    """The whole probe. Returns its report; never runs a second market."""
    now = clock()
    # 0. THE MARKET, from the venue or not at all. An owner pin still wins --
    #    it is an explicit instruction -- but the unpinned path no longer
    #    computes a slug from the clock; it asks the venue which window it is
    #    serving. `selection` is reported so the provenance of the slug is
    #    part of the record rather than an assumption.
    pinned = os.getenv(AUTH.MARKET_ENV, "").strip()
    selection = {} if pinned else select_venue_window(now=now)
    market = pinned or str(selection.get("slug") or "")
    if selection:
        print("L5_WINDOW_SELECTED " + json.dumps(selection, default=str),
              flush=True)
    store = EX.InventoryStore(
        state_path or os.getenv("L5_PROBE_STATE",
                                "/state/pmus-ws/l5_probe_inventory.json"))
    journal = JN.Journal(
        journal_path or os.getenv("L5_SESSION_JOURNAL",
                                  "/state/pmus-ws/l5_session_journal.jsonl"))
    auth = AUTH.Authorization(ledger_path)

    # 0b. no venue-confirmed window, no probe -- and the refusal happens
    #     BEFORE the authorization is claimed, so a discovery failure costs
    #     nothing. Spending a single-use authorization on a slug the venue
    #     never confirmed is exactly what happened on 2026-09-28.
    if not market:
        out = {"LABEL": "LIVE_BOUNDED_PROBE", "market": None,
               "refused": "NO_VENUE_CONFIRMED_WINDOW",
               "detail": {"series": BTC15M_SERIES,
                          "note": "the venue listed no current or upcoming "
                                  "window; a slug is never computed from "
                                  "the clock"},
               "armed": False, "submitted": 0,
               "authorization_claimed": False,
               "note": "no transport capable of POST was constructed"}
        journal.write("no_venue_confirmed_window", out)
        print("L5_NO_WINDOW " + json.dumps(out, default=str), flush=True)
        return out

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

    # 1b. THE MONETARY AUTHORITY. Loaded and validated HERE, in run(), before any
    #     transport capable of POST exists. There is no fallback: no static
    #     manifest envelope, no rung table, no previous mandate. "We could not
    #     read today's authorization, so we used yesterday's" is the entire
    #     2026-09-16 incident in one sentence.
    mandate, mandate_err = rung_a_mandate()
    if mandate is None:
        out = {"LABEL": "LIVE_BOUNDED_PROBE", "market": market,
               "refused": "NO_MONETARY_AUTHORITY", "detail": mandate_err,
               "armed": False, "submitted": 0,
               "monetary_authority": "NONE_NO_MANDATE",
               "effective_limits_usd": "0",
               "note": ("no valid PMUS_LIVE_MANDATE, so the effective monetary "
                        "authority is $0 and the probe fails closed. No "
                        "transport capable of POST was constructed.")}
        journal.write("no_monetary_authority", out)
        print("L5_NO_MANDATE " + json.dumps(out, default=str), flush=True)
        auth.consume({"armed": False, "submitted": 0,
                      "refused": "NO_MONETARY_AUTHORITY"})
        return out
    journal.write("mandate_loaded", {
        "authorization_id": mandate.authorization_id,
        "strategy_version": mandate.strategy_version,
        "max_spend_per_market": str(mandate.max_spend_per_market),
        "max_total_spend": str(mandate.max_total_spend),
        "expiry": str(mandate.expiry)})

    if transport_factory is None:
        def transport_factory(slug):                           # noqa: E306
            from src import l5_pmus_transport as TP
            return TP.PmusTransport(market_slug=slug)
    # the feed reconnects after a venue-side close (RUNG A 1600z took a 1001
    # and never came back), but it must not outlive the window it serves, so
    # recovery is bounded by the window end as well as by MAX_RECONNECTS.
    _w_start = window_start(market)
    feed = book_feed or FEED.AuthenticatedBookFeed(
        market, clock=clock,
        deadline=(None if _w_start is None else _w_start + WINDOW_S))
    state_dir = os.path.dirname(os.path.abspath(store.path)) or "."
    # 1c. the mandate guard goes on the transport BEFORE the probe can hold it,
    #     so no code path exists that reaches the wire without admission.
    guarded = MANDATE.guard(transport_factory(market), mandate, state_dir,
                            realized_today=store.realized_today,
                            # GuardedTransport._emit calls log(kind, **verdict);
                            # Journal.write takes (kind, payload). Without this
                            # adapter every admitted order raised TypeError --
                            # found by executing the real path, not by reading it.
                            log=lambda kind, **kw: journal.write(kind, kw))
    probe = Probe(market=market, store=store, journal=journal, auth=auth,
                  transport=guarded, book_feed=feed,
                  clock=clock, sleep=sleep, activity=activity)
    global _LAST_PROBE
    _LAST_PROBE = probe

    # 1d. a flat book is required. The floor is settlement-independent only if
    #     it is computed over THIS session's inventory; reconstructing an
    #     inherited position's cash would be a guess, and a guessed floor is
    #     worse than no floor. Fresh state paths make this the normal case.
    # CANONICAL SIDES, not the lowercase words. `InventoryStore.position`
    # formats "<market>|<side>" with no canonicalisation, while the SOLE writer
    # of positions (`apply_fill`) canonicalises through `canonical_side` and so
    # only ever creates "<market>|UP" / "<market>|DOWN". Asking for "up"/"down"
    # read two keys that cannot exist, so this guard concluded "flat" over a
    # store holding inventory and could never fire --
    # DEFECT_PREEXISTING_INVENTORY_CASE_001, measured by booking a fill through
    # the real writer and watching residual() report 1 while this test said
    # False. Both legs are checked rather than the net, because a matched pair
    # nets to zero residual and is still inherited inventory.
    pre = {sd: store.position(market,
                              EX.canonical_side(sd, doing="checking the "
                                                "selected market for "
                                                "inherited inventory"))
           for sd in ("up", "down")}
    if any(D(str(v)) != 0 for v in pre.values()):
        out = {"LABEL": "LIVE_BOUNDED_PROBE", "market": market,
               "refused": "PREEXISTING_INVENTORY",
               "detail": {k: str(v) for k, v in pre.items()},
               "armed": False, "submitted": 0,
               "note": ("this market already holds inventory in the probe "
                        "store; a settlement-independent floor cannot be "
                        "computed from a position whose cash basis this "
                        "session did not observe")}
        journal.write("preexisting_inventory", out)
        auth.consume({"armed": False, "submitted": 0,
                      "refused": "PREEXISTING_INVENTORY"})
        return out

    # 1e. THE REVIEWED COMPONENTS, composed onto the executing path. build_lock5
    #     derives the monetary limits from the mandate and opens the paired log.
    probe.build_lock5(book=SHADOW.Book(), mandate=mandate,
                      state_dir=state_dir, window=market)
    # and the paired-record + LOCK5 gate go in FRONT of the guard, so every
    # order the frozen executor sends traverses
    #     pair_intent -> LOCK5 -> mandate.admit -> wire
    # THE INVARIANT on the live path: a measurement envelope can never become
    # live authority. Checked against the POST-capable transport itself.
    EX.refuse_measurement_authority(probe.limits, guarded, where="run")
    paired_tp = ADAPT.PairedLockTransport(guarded, lock5=probe.lock5,
                                          paired=probe.paired, journal=journal,
                                          limits=probe.limits)
    probe.tp = paired_tp
    probe.adapter = paired_tp
    journal.write("money_path_composed", {
        "transport": type(paired_tp).__name__,
        "guarded": MANDATE.is_guarded(guarded),
        "monetary_authority": probe.limits.monetary_authority,
        "limits": probe.limits.as_dict(),
        "lock5_threshold": str(LOCK5.LOCK_USD),
        "paired_log": probe.paired.path,
        "session": os.getenv("PMUS_Q1_SESSION_ID", ""),
        "market_window": market})

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

    # 1e. PIN THE IMMUTABLE METADATA BEFORE THE WINDOW'S FAILURE DOMAIN.
    #     The 2215z row had existed since 10:40:01Z that morning; the only
    #     reason the probe could not read it was that it first asked at
    #     22:15:54Z, inside a transient gateway 404 that lasted about
    #     sixteen minutes. Resolving here -- at process start, before the
    #     wait for the window -- means a later transient failure degrades
    #     instead of killing.
    #
    #     BEST EFFORT, NOT A NEW GATE. If this fails, preflight still tries
    #     and still fails closed: a slug never successfully resolved cannot
    #     trade. Nothing here establishes tradability or price, which stay
    #     with preflight's fresh row and the order-time authenticated book.
    try:
        pin = probe.tp.pin_market_metadata(market)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        pin = {"slug": market, "pinned": False,
               "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
               "note": ("preflight will retry and fail closed if the venue "
                        "still will not serve this exact slug")}
        print("L5_METADATA_PIN_FAILED " + json.dumps(pin, default=str),
              flush=True)
    probe.metadata_pin = pin
    journal.write("market_metadata_pin", pin)
    print("L5_METADATA_PIN " + json.dumps(pin, default=str), flush=True)

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
        # 4. arm, needing preflight AND the owner token. BOUNDED RETRY, which
        #    retries a transient pre-arm safety check and is not authorization
        #    renewal: it stops the moment anything has been submitted.
        #
        #    `halted` is the mandate itself, re-read on every attempt: if the
        #    authorization is removed, replaced or expires mid-retry, arming
        #    aborts. That is a REAL stop for this artifact. L5_LIVE_HALT is not
        #    used, because no file in the reviewed capability graph reads it, and
        #    a control nothing consults is not a control.
        probe.arm_with_bounded_retry(AR.ArmRetryGate(
            market=market,
            authorization=auth,
            halted=lambda: rung_a_mandate()[0] is None,
            mandate_fingerprint=lambda: mandate_fingerprint(),
            submitted_count=lambda: probe.submitted_count(),
            window_target=lambda: (os.getenv(AUTH.MARKET_ENV, "").strip()
                                   or None),
            clock=clock), sleep=sleep)
        # 5. work exactly this window, in the selected lane
        if native_engine_enabled():
            journal.write("money_lane", {
                "lane": "NATIVE_FROZEN_Q50",
                "policy": "theta=0.96 variant=B hold_s=60 "
                          "residual_cap_k=2 profit_lock_usd=5",
                "clip": os.getenv("L5_NATIVE_CLIP", "").strip() or "50",
                "note": "the L5-action translator is not on this path"})
            native_work_window(probe, feed, deadline, journal=journal,
                               clock=clock, sleep=sleep)
        else:
            journal.write("money_lane", {"lane": "L5_ACTION_COMPARISON"})
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
        try:
            probe.record_reconciliation(close)
        except BaseException:                                  # noqa: BLE001
            journal.write("paired_reconciliation_failed", {"market": market})
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
                  "snapshot_age_s", "inventory_class", "residual_before")


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
        ic = rec.get("INVENTORY_CLASS") or {}
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
            "inventory_class": ic.get("inventory_class"),
            "residual_before": ic.get("residual_before"),
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
    by_class: Dict[str, Dict[str, int]] = {}
    for r in rows:
        k = str(r.get("inventory_class") or "UNCLASSIFIED")
        d = by_class.setdefault(k, {"actions": 0, "permitted": 0,
                                    "submitted": 0, "suppressed": 0})
        d["actions"] += 1
        if r.get("submitted") is True:
            d["submitted"] += 1
        why = str(r.get("refused_reason") or "")
        if why == "IMBALANCE_INCREASE_SUPPRESSED":
            d["suppressed"] += 1
        elif not why:
            d["permitted"] += 1
    return {"actions": len(rows), "by_l5_side": by_side,
            "by_refusal": by_reason,
            "by_inventory_class": by_class,
            "imbalance_increase_suppressed": by_reason.get(
                "IMBALANCE_INCREASE_SUPPRESSED", 0),
            "followed": sum(1 for r in rows if r.get("submitted") is True)}


#: Probe 07's two treatment switches. Both default OFF, so every earlier
#: probe's behaviour is exactly what it was.
MAKER_TREATMENT_ENV = "L5_MAKER_TREATMENT"
MAKER_ONLY_ENV = "L5_MAKER_ONLY"
#: Probe 08's single treatment switch, likewise default OFF. With it unset,
#: Probe 07 is reproduced exactly, which is what makes Probe 07 the control.
IMBALANCE_SUPPRESS_ENV = "L5_IMBALANCE_SUPPRESS"
#: THE MONEY LANE. Unset, the probe runs the L5-action comparison lane
#: (new_l5_actions -> pmus_translate), preserved for behavioural research. Set,
#: the money path is the venue-native port of the frozen q50 arm, which is the
#: only policy with a parity result behind it.
NATIVE_ENGINE_ENV = "L5_NATIVE_ENGINE"


def maker_treatment_enabled() -> bool:
    return os.getenv(MAKER_TREATMENT_ENV, "").strip().upper() in (
        "1", "YES", "TRUE")


def maker_only_enabled() -> bool:
    return os.getenv(MAKER_ONLY_ENV, "").strip().upper() in ("1", "YES",
                                                             "TRUE")


def native_engine_enabled() -> bool:
    return os.getenv(NATIVE_ENGINE_ENV, "").strip().upper() in ("1", "YES",
                                                                "TRUE")


def imbalance_suppress_enabled() -> bool:
    return os.getenv(IMBALANCE_SUPPRESS_ENV, "").strip().upper() in (
        "1", "YES", "TRUE")


RECOVER_ENV = "L5_RECOVER_ONLY"


def recover_only_enabled() -> bool:
    return os.getenv(RECOVER_ENV, "").strip().upper() in ("1", "YES", "TRUE")


#: Where recovery READS the journal it is extracting, when that must not be
#: the journal it writes to. Probe 08's journal is the only record of a
#: window whose report died before it printed, and recovery appends its own
#: row to whatever `L5_SESSION_JOURNAL` names -- so extracting Probe 08 by
#: pointing L5_SESSION_JOURNAL at it would have modified the evidence in the
#: act of reading it. Set this to read one file and write another.
RECOVER_JOURNAL_ENV = "L5_RECOVER_JOURNAL"

#: The inventory file recovery must reconcile a COPY of, never in place.
#: reconcile_from_venue rewrites positions and live_orders and saves; run
#: against Probe 08's own inventory that would overwrite the second of the
#: two artifacts the window left behind.
RECOVER_STATE_SOURCE_ENV = "L5_RECOVER_STATE_SOURCE"

#: Extra paths to hash and re-hash around the run, beyond the two sources.
FREEZE_ENV = "L5_FREEZE_ORIGINALS"


def recovery_read_path(journal) -> str:
    """The journal to extract FROM. Defaults to the one we write to."""
    return (os.getenv(RECOVER_JOURNAL_ENV, "").strip()
            or str(journal.path))


AUDIT_ENV = "L5_AUDIT_ONLY"


def audit_only_enabled() -> bool:
    return os.getenv(AUDIT_ENV, "").strip().lower() in ("1", "true", "yes")


def trade_audit(*, http=None, journal_path=None, ledger_dir=None,
                authorization_id=None, market=None) -> Dict[str, Any]:
    """Everything persisted about one authorization's orders. GET ONLY.

    Written because the R4 window (2026-09-29 0045z) left 10 UP contracts at
    the venue and a printed report saying `submitted: 0`. Nothing about a
    native-lane submission was journalled at all: `_post` records into
    `mp.submitted` in memory and the wrapper writes only counts. The durable
    evidence therefore lives in TWO files, and this reads both:

      <ledger_dir>/ledger.jsonl   mandate_commit  -> cash_at_risk, quantity,
                                                     price_wire, side, action
                                  mandate_order   -> the venue order id
      <journal>                   authenticated_fills, order_terminal,
                                  native_tick, native_sweep, ...

    and then asks the VENUE about every order id it found, plus positions,
    open orders and settlement. Nothing is inferred: a field the records do
    not carry is reported as UNKNOWN, and every venue answer is labelled with
    the route it came from.

    Writes nothing. Mutates nothing. Constructs no object able to POST.
    """
    from src import l5_pmus_transport as TP
    from src import pmus_live_mandate as PLM
    jp = (journal_path or os.getenv("L5_AUDIT_JOURNAL", "").strip()
          or os.getenv("L5_SESSION_JOURNAL", "").strip())
    ld = (ledger_dir or os.getenv("L5_AUDIT_LEDGER_DIR", "").strip()
          or (os.path.dirname(os.path.abspath(
              os.getenv("L5_PROBE_STATE", "") or ".")) or "."))
    auth_id = (authorization_id
               or os.getenv("L5_AUDIT_AUTHORIZATION", "").strip())
    market = (market or os.getenv("L5_AUDIT_MARKET", "").strip()
              or os.getenv(AUTH.MARKET_ENV, "").strip())

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

    rows, readable = PLM._read_rows(pathlib.Path(ld))
    commits = [r for r in rows if r.get("kind") == PLM.COMMIT_KIND
               and (not auth_id or r.get("authorization_id") == auth_id)]
    links = [r for r in rows if r.get("kind") == PLM.LINK_KIND
             and (not auth_id or r.get("authorization_id") == auth_id)]
    link_by_commit = {str(r.get("commit_id")): str(r.get("order_id") or "")
                      for r in links}

    jrows = journal_rows(jp) if jp else [{"unreadable": "NO_JOURNAL_NAMED"}]
    by_kind: Dict[str, int] = {}
    for r in jrows:
        by_kind[str(r.get("kind"))] = by_kind.get(str(r.get("kind")), 0) + 1
    interesting = [r for r in jrows
                   if r.get("kind") in ("authenticated_fills", "order_terminal",
                                        "native_sweep", "native_window_done",
                                        "completion_remainder",
                                        "mandate_admission", "market_metadata_pin")]

    trades: List[Dict[str, Any]] = []
    for c in commits:
        cid = str(c.get("commit_id"))
        oid = link_by_commit.get(cid, "")
        row: Dict[str, Any] = {
            "commit_id": cid,
            "authorization_id": c.get("authorization_id"),
            "market": c.get("slug"),
            "side_wire": c.get("side") or "UNKNOWN",
            "action": c.get("action") or "UNKNOWN",
            "intended_quantity": c.get("quantity") or "UNKNOWN",
            "intended_price_wire": c.get("price_wire") or "UNKNOWN",
            "mandate_cash_at_risk": c.get("cash_at_risk") or "UNKNOWN",
            "committed_utc": c.get("t") or "UNKNOWN",
            "order_id": oid or "UNKNOWN_NO_LINK_ROW",
            "linked": bool(oid),
        }
        if oid:
            # THE RAW RECORD, VERBATIM. A rejection reason, if this venue
            # exposes one at all, is a field we have never looked at; a
            # summarised record cannot tell us whether it is there.
            try:
                row["venue_order_raw"] = tp.order(oid) or {}
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                row["venue_order_raw"] = {"error": "%s: %s" % (
                    type(exc).__name__, str(exc)[:200])}
            for probe_path in ("/v1/order/%s/status", "/v1/order/%s/fills",
                               "/v1/order/%s/events"):
                try:
                    row.setdefault("venue_order_probes", {})[probe_path % ""] \
                        = ro.request("GET", probe_path % oid, None, Q1.API)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    row.setdefault("venue_order_probes", {})[
                        probe_path % ""] = "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:80])
            try:
                o = tp.order(oid) or {}
                row["venue_order"] = {
                    "state": o.get("state") or "UNKNOWN",
                    "quantity": o.get("quantity") or "UNKNOWN",
                    "cum_quantity": (o.get("cumQuantity")
                                     if o.get("cumQuantity") is not None
                                     else "UNKNOWN"),
                    "leaves_quantity": (o.get("leavesQuantity")
                                        if o.get("leavesQuantity") is not None
                                        else "UNKNOWN"),
                    "avg_px": ((o.get("avgPx") or {}).get("value")
                               if isinstance(o.get("avgPx"), Mapping)
                               else (o.get("avgPx") or "UNKNOWN")),
                    "commission": (
                        (o.get("commissionNotionalTotalCollected") or {})
                        .get("value")
                        if isinstance(
                            o.get("commissionNotionalTotalCollected"), Mapping)
                        else "UNKNOWN"),
                    "last_transact_time": o.get("lastTransactTime") or "UNKNOWN",
                    "source": "GET /v1/order/%s" % oid}
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                row["venue_order"] = {"error": "%s: %s" % (
                    type(exc).__name__, str(exc)[:160]),
                    "source": "GET /v1/order/%s" % oid}
        else:
            row["venue_order"] = {"note": "no mandate_order row, so no id to "
                                          "ask by", "state": "UNKNOWN"}
        trades.append(row)

    try:
        positions = tp.positions()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        positions = [{"error": type(exc).__name__}]
    try:
        open_orders = tp.open_orders()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        open_orders = [{"error": type(exc).__name__}]
    try:
        settlement = tp.settlement(market) if market else {"note": "NO_MARKET"}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        settlement = {"error": type(exc).__name__}

    consumed: Any
    try:
        consumed = PLM.consumption(pathlib.Path(ld), auth_id) if auth_id \
            else {"note": "NO_AUTHORIZATION_NAMED"}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        consumed = {"error": type(exc).__name__}

    try:
        balances = tp.account()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        balances = {"error": type(exc).__name__}
    try:
        mk = tp.market(market) if market else {}
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        mk = {"error": type(exc).__name__}
    grid = {k: mk.get(k) for k in ("status", "orderPriceMinTickSize",
                                   "minimumTradeQty", "outcomes",
                                   "marketSides", "feeCoefficient")} \
        if isinstance(mk, Mapping) else {}

    return {"LABEL": "R4_TRADE_AUDIT", "post_capable": post_capable,
            "balances_now": balances, "market_grid": grid,
            "authorization_id": auth_id or "UNKNOWN",
            "market": market or "UNKNOWN",
            "ledger": {"dir": str(ld), "readable": readable,
                       "rows_total": len(rows),
                       "commits": len(commits), "links": len(links)},
            "journal": {"path": jp, "records": len(jrows),
                        "kinds": by_kind},
            "trades": trades,
            "journal_evidence": interesting,
            "venue": {"positions": positions, "open_orders": open_orders,
                      "settlement": settlement},
            "mandate_consumption": consumed,
            "labels": ("GET only; nothing written. A field the records do not "
                       "carry is UNKNOWN and is never inferred. REALIZED "
                       "requires settlement evidence, which is reported here "
                       "as the venue gave it.")}


def recover(*, http=None, state_path=None, journal_path=None,
            read_path=None, state_source=None,
            market: Optional[str] = None) -> Dict[str, Any]:
    """Reconcile stale local order state against the venue. GET only.

    Where a source journal or a source inventory is named, the ORIGINALS are
    hashed, the inventory is copied to the working path, and every original
    is re-hashed at the end. An unmutated original is then something the
    report measured, not something this docstring promised.
    """
    from src import l5_pmus_transport as TP
    from src import l5_evidence_freeze as FZ
    market = (market or os.getenv(AUTH.MARKET_ENV, "").strip()
              or os.getenv("L5_RECOVER_MARKET", "").strip())
    work_state = (state_path
                  or os.getenv("L5_PROBE_STATE",
                               "/state/pmus-ws/l5_probe_inventory.json"))
    src_state = (state_source
                 or os.getenv(RECOVER_STATE_SOURCE_ENV, "").strip() or None)
    frozen_paths = [q for q in (
        (read_path or os.getenv(RECOVER_JOURNAL_ENV, "").strip() or None),
        src_state,
        *[x.strip() for x in os.getenv(FREEZE_ENV, "").split(",") if x.strip()],
    ) if q]
    frozen = FZ.freeze_originals(frozen_paths)
    staged = (FZ.stage_copy(src_state, work_state) if src_state
              else {"copied": False, "reason": "NO_SOURCE_NAMED"})
    store = EX.InventoryStore(work_state)
    journal = JN.Journal(
        journal_path or os.getenv("L5_SESSION_JOURNAL",
                                  "/state/pmus-ws/l5_session_journal.jsonl"))
    ro = http or Q1.ReadOnlyTransport()
    tp = TP.PmusTransport(market_slug=market, http=ro)

    # nothing to reconcile is not a reason to run. A recovery flag left set
    # from an earlier run has twice hijacked a window that was meant to
    # observe, and a no-op recovery looks exactly like a successful one.
    if not store.live_orders() and not store.orders_uncertain():
        # Nothing to RECONCILE -- but the journal extraction is a separate
        # job and is often the only reason to run at all, so it still runs.
        # Refusing it too is how this guard cost us Probe 07's order table.
        src_path = read_path or recovery_read_path(journal)
        recs = journal_rows(src_path)
        rep = {"LABEL": "LIVE_RECOVERY", "market": market,
               "post_capable": False, "armed": False,
               "skipped": "NOTHING_TO_RECONCILE",
               "live_orders": {}, "orders_uncertain": None,
               "journal": {"path": src_path, "records": len(recs),
                           "written_to": str(journal.path),
                           "read_only_source": src_path != str(journal.path)},
               "evidence": {"frozen": frozen, "staged_state": staged,
                            "verified": FZ.verify_unchanged(frozen)},
               "order_table": order_table(recs, market),
               "action_table": action_table(recs, market),
               "labels": ("state was already clean, so this run reconciled "
                          "nothing and the journal was extracted anyway. If "
                          "a window was meant to be observed, "
                          "L5_RECOVER_ONLY is still set.")}
        rep["action_summary"] = action_summary(rep["action_table"])
        journal.write("recovery_skipped",
                      {k: v for k, v in rep.items()
                       if k not in ("order_table", "action_table")})
        return rep

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
    src_path = read_path or recovery_read_path(journal)
    recs = journal_rows(src_path)
    rep["evidence"] = {"frozen": frozen, "staged_state": staged}
    rep["journal"] = {
        "path": src_path,
        "written_to": str(journal.path),
        "read_only_source": src_path != str(journal.path),
        "records": len(recs),
        "kinds": sorted({str(r.get("kind")) for r in recs if r.get("kind")}),
        "sessions": sorted({str(r.get("session_id")) for r in recs
                            if r.get("session_id")}),
    }
    rep["order_table"] = order_table(recs, market)
    rep["action_table"] = action_table(recs, market)
    rep["action_summary"] = action_summary(rep["action_table"])
    # re-hash AFTER everything the run could possibly have touched
    rep["evidence"]["verified"] = FZ.verify_unchanged(frozen)
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
    # OBSERVE is a COUNTERFACTUAL measurement path. With a valid mandate its
    # envelope is that mandate's; without one it uses the explicitly labelled
    # measurement envelope, so `would_submit` stays measurable. Zeroing it made
    # the envelope refuse intents before the transport was consulted, which
    # destroyed the diagnostic instead of tightening it.
    _obs_mandate, _ = rung_a_mandate()
    if _obs_mandate is not None:
        limits = EX.SafetyLimits.from_mandate(_obs_mandate,
                                              **PM.operational_controls())
    else:
        limits = EX.SafetyLimits.measurement_only(**PM.measurement_envelope())
    # THE INVARIANT. This path holds NullTransport; if it ever holds one that can
    # POST, a measurement envelope must stop the run rather than authorize it.
    EX.refuse_measurement_authority(limits, tp, where="observe")
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


def selected_mode() -> str:
    """Which branch this process will take, decided once and announced.

    A stale L5_RECOVER_ONLY has now hijacked two runs that were meant to
    observe -- once before the 1615z window and once before 1800z -- and both
    times the only symptom was a report that never appeared. The mode is
    printed before anything else so the wrong one is obvious immediately
    rather than fifteen minutes later.
    """
    if audit_only_enabled():
        return "AUDIT_ONLY"
    if recover_only_enabled():
        return "RECOVER_ONLY"
    if observe_only_enabled():
        return "OBSERVE_ONLY"
    return "LIVE_PROBE"


def main(argv: Optional[Sequence[str]] = None) -> int:
    print("L5_MODE " + json.dumps({
        "mode": selected_mode(),
        "observe_only": os.getenv(OBSERVE_ENV, ""),
        "recover_only": os.getenv(RECOVER_ENV, ""),
        "audit_only": os.getenv(AUDIT_ENV, ""),
        "market_pin": os.getenv(AUTH.MARKET_ENV, ""),
        "recover_market": os.getenv("L5_RECOVER_MARKET", ""),
        "maker_treatment": maker_treatment_enabled(),
        "maker_only": maker_only_enabled(),
        "imbalance_suppress": imbalance_suppress_enabled(),
        "treatment": ("IMBALANCE_INCREASE_SUPPRESSED"
                      if imbalance_suppress_enabled() else "NONE"),
        "session": os.getenv("PMUS_Q1_SESSION_ID", ""),
        "armed": os.getenv("PMUS_Q1_ARM", ""),
        "live": os.getenv("PMUS_Q1_LIVE", ""),
    }), flush=True)
    if audit_only_enabled():
        # READ-ONLY, and it RETURNS. An earlier version of this block landed
        # inside `selected_mode()`, which announces the mode and is called
        # from the L5_MODE line: the audit ran, returned 0 into
        # `{"mode": 0}`, and the process then carried on into run(). It was
        # harmless only because that run was disarmed. A lane that does not
        # return is not a lane.
        rep = trade_audit()
        trades = rep.pop("trades", [])
        ev = rep.pop("journal_evidence", [])
        print("L5_AUDIT " + json.dumps(rep, default=str), flush=True)
        for t in trades:
            print("L5_AUDIT_TRADE " + json.dumps(t, default=str), flush=True)
        print("L5_AUDIT_TRADES %d" % len(trades), flush=True)
        for r in ev:
            print("L5_AUDIT_EVIDENCE " + json.dumps(r, default=str),
                  flush=True)
        print("L5_AUDIT_EVIDENCE_ROWS %d" % len(ev), flush=True)
        return 0
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
