"""ONE post-only order, left to rest, cancelled by the WATCHDOG, re-read.

WHAT THIS IS FOR, AND WHAT IT IS NOT. It answers exactly one question that
no test and no simulation can: does the independent watchdog actually
withdraw a real resting order from PMUS, and does the VENUE then report that
order terminal? Everything else about it is deliberately uninteresting -- one
order, the venue's minimum size, a price that cannot cross, and no second
order under any outcome. It is not an L5 action, not an L5 strategy trade,
and not evidence about L5 behaviour, expectancy or profitability.

WHY IT LIVES IN THE EXECUTOR. The watchdog's ownership oracle reads ONE
registry: `l5-pmus-live`'s own `/l5/live_orders.json`, served off the volume
this process writes its ACKs to. An order placed by any other service is
absent from that document and would be refused NOT_IN_OWNERSHIP_REGISTRY --
correctly, because that is the branch protecting the owner's manual orders on
a shared account. So the only order the watchdog can ever cancel is one this
executor placed, and the mechanism has to be here.

THE ORDER IS DELIBERATELY LEFT UNMANAGED, which is the one genuinely
uncomfortable thing here and is stated plainly: the whole point is to create
the exact condition -- a resting order past the executor's own deadline --
that the watchdog exists to catch. Consequences, all intended:

  * the watchdog WILL latch `UNMANAGED_RESTING_ORDER` before it cancels (the
    sweep asserts first, cancels second), so this test stops the executor.
    Clearing that latch is an owner action through `scripts/l5_halt_reset.py`
    with its confirm phrase, digest and retyped reason. Nothing here clears
    a halt, and this module imports no asserter.
  * it runs as a MODE, not alongside the strategy. `L5_CANCEL_MECHANISM=YES`
    replaces the trading tick entirely, so the normal lifecycle cannot
    cancel the order first and the measurement is of the watchdog, not of a
    race between two cancellers.

THE RISK, STATED IN FULL. One BUY of `quantity_increment` contracts at a
non-marketable limit. A BUY settles worthless at worst, so the premium paid
IS the maximum loss; there is no leverage on this venue. `post_only` cannot
cross, so no taker fee can arise -- and the bound below charges the TAKER fee
anyway, because a worst case computed with a fee we hope not to pay is not a
worst case. At 0.01 contracts that total is cents.

WHAT MUST BE TRUE BEFORE THE ORDER IS LEFT ALONE. A resting order the
registry does not publish is not watchdog-cancellable; it is just an orphan.
So after the ACK this module does not merely RECORD the venue id -- it reads
the ownership answer back out of the durable ledger AND renders the registry
document to confirm the id actually appears in it, and if either fails it
cancels the order itself at once rather than leaving it out there. Ownership
evidence precedes reliance on the watchdog, never follows it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

#: the mode switch. Only the literal YES, like every other lock here.
MODE_ENV = "L5_CANCEL_MECHANISM"
#: optional overrides, for the owner to pin the exact order before it is sent
MARKET_ENV = "L5_MECH_MARKET"
SIDE_ENV = "L5_MECH_SIDE"
PRICE_ENV = "L5_MECH_PRICE"

#: THE TOTAL-RISK CEILING, and fees are INSIDE it. The owner's authorization
#: is $5 "including worst-case fees", so the bound is checked against
#: notional + the TAKER fee, never notional alone.
MAX_TOTAL_RISK_USD = D("5")

#: how far below the bid the resting price is placed. One tick is enough to
#: be non-marketable; the check that matters is `price < ask`, asserted
#: against a fresh two-sided authenticated book, not this margin.
NONMARKETABLE_TICKS = 2

#: THE WATCHDOG'S OWN BOUND. It cancels an order older than
#: L5_WATCHDOG_MAX_ORDER_AGE_S (300s in production). The order must therefore
#: be allowed to rest past that, plus slack for one 20s sweep interval and
#: the terminal re-read, and ALL of that has to fit inside the window --
#: otherwise the venue expires the order at close and we have measured
#: nothing.
WATCHDOG_AGE_S = 300.0
SWEEP_SLACK_S = 90.0
#: after this, the mechanism withdraws the order ITSELF rather than strand
#: it. Reported as a FALLBACK and never as proof: a self-cancel answers the
#: question "can we cancel", which was never in doubt.
SELF_CANCEL_AFTER_S = WATCHDOG_AGE_S + SWEEP_SLACK_S
#: the window must have at least this much left when the order is sent
MIN_WINDOW_REMAINING_S = SELF_CANCEL_AFTER_S + 60.0

#: how often the venue is re-read while the order rests
REREAD_EVERY_S = 10.0

#: CHECKS WHOSE FAILURE IS TRANSIENT, so the mechanism WAITS and retries
#: instead of refusing for good.
#:
#: THE BUG THIS FIXES, from the 1800z attempt. Every precondition failure was
#: terminal, so `transport_armed: false` ended the run -- and that flag is
#: false for the first seconds of EVERY window by construction: the transport
#: arms only after preflight passes, and preflight retries on a 3/6/12/24s
#: backoff. At 18:03:28Z preflight was on attempt 2, having hit the venue's
#: intermittent `book_readable` 404, and the mechanism gave up on a condition
#: that clears itself. `maybe_preflight` in the runner already states the
#: distinction -- "ARMING IS RETRIED; PREFLIGHT IS NOT ... try_arm can refuse
#: for reasons that clear on their own a few seconds later" -- and this file
#: simply failed to honour it.
#:
#: WHY WAITING IS SAFE HERE, and this is the part that matters: waiting never
#: relaxes anything. Every gate is re-evaluated from scratch on the retry
#: that submits, so an order is placed only against a book, a price, an
#: authorization and a window that are all good AT THAT MOMENT. The book and
#: price entries belong here for the same reason as arming: a momentarily
#: one-sided or crossed book is a "not yet", and the price is RECOMPUTED from
#: the fresh book each pass rather than carried forward.
#:
#: AND THE WAIT IS BOUNDED WITHOUT A TIMER: as the window drains,
#: `window_has_room_for_the_watchdog` fails, and that is TERMINAL. So a
#: mechanism that never gets armed stops itself at the 450s mark instead of
#: holding a lifted lock open indefinitely.
WAIT_CHECKS = frozenset({
    "transport_armed",
    "book_two_sided_authenticated",
    "price_readable", "price_positive", "price_below_one",
    "price_is_nonmarketable",
})

#: the durable one-shot marker. "no automatic repeat after a completed or
#: crashed probe" is a standing instruction, and a process that crashed
#: mid-test must not come back and place a second order.
MARKER_KEY = "mechanism_run"

TERMINAL_HINTS = ("FILLED", "CANCEL", "REJECT", "EXPIR", "DONE")


def enabled() -> bool:
    return str(os.getenv(MODE_ENV, "")).strip().upper() == "YES"


def _d(v: Any) -> Optional[D]:
    try:
        x = D(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_terminal(row: Mapping[str, Any]) -> bool:
    """Venue-terminal, by the venue's own row. Identical reading to the
    watchdog's, so the two cannot disagree about what terminal means."""
    leaves = row.get("leavesQuantity")
    if leaves is not None:
        lv = _d(leaves)
        if lv is not None:
            return lv <= 0
    return any(h in str(row.get("state") or "").upper()
               for h in TERMINAL_HINTS)


def worst_case_total_usd(qty: D, price: D, *, fee_theta: D = D("0.0695")) -> D:
    """Premium plus the TAKER fee, rounded up to the cent.

    The venue bills commission in whole cents, so an exact-Decimal fee can
    sit just under the cent actually taken. Rounding up is the only safe
    direction for a bound.
    """
    notional = qty * price
    raw = fee_theta * qty * price * (D(1) - price)
    if raw > 0:
        cents = (raw * 100).to_integral_value(rounding="ROUND_CEILING")
        fee = cents / D(100)
    else:
        fee = D(0)
    return notional + fee


class MechanismRefused(RuntimeError):
    """Nothing was sent. The reason is the message."""


class Mechanism:
    """The place-then-rest-then-re-read state machine.

    Every dependency is injected so the whole thing is drivable in a test
    with a fake clock and a recording transport: there is no path in here
    that reaches a real venue on its own.
    """

    #: the states, in the only order they can occur
    INIT = "INIT"
    RESTING = "RESTING"
    DONE = "DONE"
    REFUSED = "REFUSED"

    def __init__(self, *, store: Any, transport: Any, book: Any,
                 market: str, clock: Callable[[], float] = time.time,
                 emit: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
                 window_start: Optional[Callable[[str], Optional[int]]] = None,
                 window_s: float = 900.0,
                 self_cancel_after_s: float = SELF_CANCEL_AFTER_S,
                 session_id: Optional[str] = None,
                 halted_fn: Optional[Callable[[], bool]] = None) -> None:
        self.store = store
        self.transport = transport
        self.book = book
        self.market = str(market)
        self.clock = clock
        self._emit = emit
        self._window_start = window_start
        self.window_s = float(window_s)
        self.self_cancel_after_s = float(self_cancel_after_s)
        self.session_id = session_id
        # READ-ONLY, INJECTED. The halt is consulted as a FACT handed in by
        # the runner rather than imported: this module must stay unable to
        # assert or clear a latch, and a predicate can only add a refusal.
        self._halted_fn = halted_fn
        self.waits = 0
        self.last_wait_reason: Optional[List[str]] = None
        self.state = self.INIT
        self.coid: Optional[str] = None
        self.venue_order_id: Optional[str] = None
        self.placed_t: Optional[float] = None
        self.last_reread_t = 0.0
        self.rereads = 0
        self.report: Dict[str, Any] = {}
        self.refusal: Optional[str] = None

    # ------------------------------------------------------------ reporting

    def emit(self, kind: str, doc: Mapping[str, Any]) -> None:
        if self._emit is not None:
            try:
                self._emit(kind, doc)
            except BaseException:                              # noqa: BLE001
                pass

    # ------------------------------------------------------------ the order

    def plan(self) -> Dict[str, Any]:
        """Every field of the one order, decided before anything is sent."""
        snap = self.book.snapshot(self.market) or {}
        bid = _d(snap.get("bid"))
        ask = _d(snap.get("ask"))
        tick = _d(getattr(self.transport, "tick_size", None)) or D("0.01")
        qty = _d(getattr(self.transport, "quantity_increment", None)) or D("0.01")
        side = (os.getenv(SIDE_ENV, "").strip().upper() or "UP")
        forced = _d(os.getenv(PRICE_ENV, "").strip() or None)
        price = forced
        if price is None and bid is not None:
            price = bid - tick * NONMARKETABLE_TICKS
        total = (None if price is None
                 else worst_case_total_usd(qty, price))
        start = (self._window_start(self.market)
                 if self._window_start is not None else None)
        remaining = (None if start is None
                     else (start + self.window_s) - self.clock())
        return {
            "market": self.market, "side": side, "mode": "MAKER",
            "post_only": True,
            "qty": str(qty), "price": None if price is None else str(price),
            "tick_size": str(tick),
            "book": {"bid": None if bid is None else str(bid),
                     "ask": None if ask is None else str(ask),
                     "stream_age_s": snap.get("stream_age_s"),
                     "market_state": snap.get("market_state"),
                     "source": snap.get("source")},
            "price_forced_by_owner": forced is not None,
            "worst_case_total_usd": None if total is None else str(total),
            "worst_case_basis": ("premium + TAKER fee, ceiled to the cent. A "
                                 "BUY settles worthless at worst, so the "
                                 "premium IS the loss; post_only cannot "
                                 "cross, so the taker fee is charged here "
                                 "only because a worst case must not assume "
                                 "a fee we hope to avoid"),
            "max_total_risk_usd": str(MAX_TOTAL_RISK_USD),
            "window_start": start,
            "window_remaining_s": (None if remaining is None
                                   else round(remaining, 1)),
            "self_cancel_after_s": self.self_cancel_after_s,
            "watchdog_age_s": WATCHDOG_AGE_S,
            "labels": ("PMUS CANCELLATION MECHANISM TEST. One post-only "
                       "order at the venue minimum, left to rest so the "
                       "INDEPENDENT watchdog cancels it. Not an L5 action "
                       "and not evidence about L5 economics."),
        }

    def checks(self, plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Every precondition, each evaluated and each reported.

        DELIBERATELY NOT SHORT-CIRCUITED: a gate that returns on its first
        failure tells the operator one thing to fix and hides the rest, and
        the next run then fails on the second one.
        """
        out: List[Dict[str, Any]] = []

        def check(name: str, ok: bool, detail: Any = None) -> None:
            out.append({"check": name, "ok": bool(ok), "detail": detail})

        check("mode_enabled", enabled(), MODE_ENV)
        check("one_shot_not_already_used", not self.already_ran(),
              self.marker())
        price = _d(plan.get("price"))
        qty = _d(plan.get("qty"))
        bid = _d((plan.get("book") or {}).get("bid"))
        ask = _d((plan.get("book") or {}).get("ask"))
        check("book_two_sided_authenticated",
              bid is not None and ask is not None,
              {"bid": plan.get("book", {}).get("bid"),
               "ask": plan.get("book", {}).get("ask"),
               "source": plan.get("book", {}).get("source")})
        check("price_readable", price is not None, plan.get("price"))
        check("price_positive", price is not None and price > 0, plan.get("price"))
        check("price_below_one", price is not None and price < D(1),
              plan.get("price"))
        # THE ONE THAT MATTERS: it must not be able to cross. A BUY crosses
        # by reaching the ASK, so the bound is the ask, not the bid.
        check("price_is_nonmarketable",
              price is not None and ask is not None and price < ask,
              {"price": plan.get("price"), "ask": plan.get("book", {}).get("ask")})
        check("post_only", plan.get("post_only") is True
              and str(plan.get("mode")).upper() == "MAKER", plan.get("mode"))
        check("qty_is_venue_minimum",
              qty is not None and qty > 0
              and qty == (_d(getattr(self.transport, "quantity_increment",
                                     None)) or D("0.01")),
              plan.get("qty"))
        total = _d(plan.get("worst_case_total_usd"))
        # TERMINAL ONLY WHEN THE TOTAL IS KNOWN AND TOO BIG.
        #
        # An UNKNOWN total is not a breach, it is an absent price -- and a
        # momentarily dead book is exactly that. Failing terminally here
        # would re-create the 1800z bug one layer down: found by
        # test_every_gate_is_re_evaluated_on_the_submitting_pass, where the
        # book died mid-wait and this check, not the price checks, ended the
        # run.
        #
        # NOTHING CAN SUBMIT ON AN UNKNOWN TOTAL: price_readable,
        # price_positive, price_below_one and price_is_nonmarketable all fail
        # as WAITS whenever the price is absent, so the order is held back by
        # those instead -- and they are re-evaluated on every pass.
        check("worst_case_total_within_authorization",
              total is None or total <= MAX_TOTAL_RISK_USD,
              {"worst_case_total_usd": plan.get("worst_case_total_usd"),
               "max": str(MAX_TOTAL_RISK_USD),
               "note": ("unknown is a WAIT via the price checks, never a "
                        "pass: an absent price cannot reach the wire")})
        rem = plan.get("window_remaining_s")
        check("window_has_room_for_the_watchdog",
              rem is not None and float(rem) >= MIN_WINDOW_REMAINING_S,
              {"window_remaining_s": rem, "needed_s": MIN_WINDOW_REMAINING_S})
        check("transport_armed", bool(getattr(self.transport, "armed", False)),
              "the owner's locks are unchanged; this mode does not lift any. "
              "A WAIT: false here means preflight/arming is still pending, "
              "and every gate is re-evaluated before the submit")
        check("book_is_flat_before_the_test", self.flat(), self.exposure())
        check("no_other_live_order", self.live_count() == 0, self.live_count())
        # TERMINAL, BOTH OF THEM, and both checked HERE rather than trusted of
        # the caller. Waiting for arming means waiting with a lifted lock, so
        # the two conditions that must stop the wait dead are checked on every
        # single pass, not once at the start.
        auth = self.authorization_status()
        check("authorization_window_open",
              str(auth.get("state")) not in ("EXPIRED", "UNREADABLE"), auth)
        check("halt_not_engaged", not self.halted(),
              {"halted": self.halted(),
               "note": ("read-only fact handed in by the runner; this module "
                        "cannot assert or clear a latch")})
        return out

    def authorization_status(self) -> Dict[str, Any]:
        """The owner's deadline as a STATUS. Unreadable counts as closed."""
        try:
            from src import l5_live_authorization as AUTH
            return dict(AUTH.not_after_status())
        except BaseException as exc:                           # noqa: BLE001
            return {"state": "UNREADABLE", "error": type(exc).__name__}

    def halted(self) -> bool:
        """Is the latch engaged? UNREADABLE counts as halted."""
        if self._halted_fn is None:
            return False          # not wired: the runner's own gates still bind
        try:
            return bool(self._halted_fn())
        except BaseException:                                  # noqa: BLE001
            return True           # cannot tell means stop

    # --------------------------------------------------------- store access

    def marker(self) -> Optional[Dict[str, Any]]:
        try:
            return (self.store.state.get(MARKER_KEY) or None)
        except BaseException:                                  # noqa: BLE001
            return None

    def already_ran(self) -> bool:
        """A marker of ANY kind means this has been attempted. Unreadable
        counts as used: the safe direction is never to place a second."""
        try:
            return bool(self.store.state.get(MARKER_KEY))
        except BaseException:                                  # noqa: BLE001
            return True

    def write_marker(self, doc: Mapping[str, Any]) -> None:
        self.store.state[MARKER_KEY] = dict(doc)
        self.store.save()

    def live_count(self) -> int:
        try:
            return int(self.store.live_order_count())
        except BaseException:                                  # noqa: BLE001
            return 1            # uncountable is not zero

    def flat(self) -> bool:
        try:
            return not (self.store.committed() or {})
        except BaseException:                                  # noqa: BLE001
            return False

    def exposure(self) -> Any:
        try:
            return {k: str(v) for k, v in (self.store.committed() or {}).items()}
        except BaseException:                                  # noqa: BLE001
            return "UNREADABLE"

    # ------------------------------------------------------- ownership proof

    def ownership_evidence(self, vid: str) -> Dict[str, Any]:
        """Both witnesses, read back rather than assumed.

        (1) the durable ledger's own answer, which is what the transport's
        cancel oracle consults, and (2) the registry DOCUMENT the watchdog
        actually fetches, rendered here and searched for the id. Recording
        the id is not the same as publishing it, and only the second one is
        what the watchdog will see.
        """
        ev: Dict[str, Any] = {"venue_order_id": vid}
        try:
            ev["ledger_owns_id"] = bool(self.store.owns_venue_order_id(vid))
        except BaseException as exc:                           # noqa: BLE001
            ev["ledger_owns_id"] = False
            ev["ledger_error"] = type(exc).__name__
        try:
            from src import l5_order_registry_server as REG
            doc = REG.registry(self.store)
            ids = [str(r.get("venue_order_id") or "")
                   for r in (doc.get("orders") or [])]
            ev["registry_readable"] = doc.get("readable") is True
            ev["registry_publishes_id"] = vid in ids
            ev["registry_as_of_utc"] = doc.get("as_of_utc")
            ev["registry_order_count"] = len(ids)
        except BaseException as exc:                           # noqa: BLE001
            ev["registry_readable"] = False
            ev["registry_publishes_id"] = False
            ev["registry_error"] = type(exc).__name__
        ev["watchdog_cancellable"] = bool(ev.get("ledger_owns_id")
                                          and ev.get("registry_readable")
                                          and ev.get("registry_publishes_id"))
        return ev

    # ---------------------------------------------------------------- driver

    def step(self, now: Optional[float] = None) -> Dict[str, Any]:
        """One tick. Returns the current report; never raises into the loop."""
        t = self.clock() if now is None else now
        try:
            if self.state == self.INIT:
                return self._place(t)
            if self.state == self.RESTING:
                return self._rest(t)
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.report["error"] = "%s: %s" % (type(exc).__name__,
                                               str(exc)[:200])
            self.emit("L5_MECH_ERROR", dict(self.report))
        return self.report

    def _refuse(self, why: str, plan: Mapping[str, Any],
                checks: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.state = self.REFUSED
        self.refusal = why
        self.report = {"LABEL": "L5_MECH_REFUSED", "refused": why,
                       "plan": dict(plan), "checks": checks,
                       "failed_checks": [c["check"] for c in checks
                                         if not c["ok"]],
                       "submitted": 0, "labels": plan.get("labels")}
        self.emit("L5_MECH_REFUSED", dict(self.report))
        return self.report

    def _wait(self, t: float, failed: List[str], plan: Mapping[str, Any],
              checks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Not yet. State stays INIT so the next tick re-evaluates EVERYTHING.

        Logged on the first wait and whenever the failing set CHANGES, so a
        long wait does not spam while a changing one stays visible.
        """
        self.waits += 1
        self.state = self.INIT
        rep = {"LABEL": "L5_MECH_WAIT", "waiting_on": failed,
               "waits": self.waits, "submitted": 0,
               "window_remaining_s": plan.get("window_remaining_s"),
               "needed_s": MIN_WINDOW_REMAINING_S,
               "labels": ("TRANSIENT. Every gate is re-evaluated on the next "
                          "pass and the order is placed only against a book, "
                          "price, authorization and window that are all good "
                          "at that moment. The wait is bounded by the window: "
                          "window_has_room_for_the_watchdog is TERMINAL.")}
        self.report = rep
        if failed != self.last_wait_reason:
            self.last_wait_reason = list(failed)
            self.emit("L5_MECH_WAIT", dict(rep))
        return rep

    def _place(self, t: float) -> Dict[str, Any]:
        plan = self.plan()
        checks = self.checks(plan)
        failed = [c["check"] for c in checks if not c["ok"]]
        # TERMINAL FIRST. A terminal failure refuses even if transient ones
        # are also outstanding -- an expired authorization is not improved by
        # the book becoming two-sided.
        terminal = [c for c in failed if c not in WAIT_CHECKS]
        if terminal:
            return self._refuse("MECHANISM_PRECONDITIONS_NOT_MET", plan,
                                checks)
        if failed:
            return self._wait(t, failed, plan, checks)

        intent = {"market": plan["market"], "side": plan["side"],
                  "price": D(str(plan["price"])), "qty": D(str(plan["qty"])),
                  "mode": "MAKER"}
        coid = "mech-%s-%d" % (plan["market"], int(t))
        self.coid = coid
        # THE MARKER IS WRITTEN BEFORE THE SUBMIT, not after. A process that
        # dies between the POST and the marker must not come back and place a
        # second order, and an order that reached the venue while our record
        # did not is the one outcome worse than no test.
        self.write_marker({"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "coid": coid, "plan": plan, "stage": "SUBMITTING"})
        self.store.record_submit(coid, intent, session_id=self.session_id,
                                 mode="MAKER")
        # THE SUBMISSION CLAIM, AND IT IS NOT OPTIONAL HERE EITHER.
        #
        # This module is a SECOND route to `transport.submit`, and
        # tests/test_l5_one_submission_claim.py exists to make a second route
        # fail until it is gated like the first. It is right to: the store
        # marker above is a key in a JSON file, while `claim_submission` is an
        # O_CREAT|O_EXCL lock that is fsynced before it returns, bound to
        # (experiment, market), and NEVER reset automatically -- not by a
        # cancellation, not by a restart, not by a refused or unreadable ack.
        # That is the guarantee that one authorization buys exactly one order,
        # and a one-order experiment is precisely where it must hold.
        #
        # BEFORE THE WIRE, deliberately: a process that dies between the claim
        # and the POST has already spent it.
        from src import l5_live_authorization as AUTH
        try:
            claim = AUTH.claim_submission(plan["market"], {
                "purpose": "cancellation_mechanism",
                "coid": coid, "qty": str(plan["qty"]),
                "price": str(plan["price"]), "side": plan["side"],
                "mode": "MAKER", "post_only": True})
        except AUTH.Refused as exc:
            self.store.terminate_order(coid, reason="SUBMISSION_CLAIM")
            self.write_marker({
                "t": dt.datetime.now(dt.timezone.utc).isoformat(),
                "coid": coid, "stage": "CLAIM_REFUSED",
                "refused": str(getattr(exc, "reason", exc))})
            return self._refuse("SUBMISSION_CLAIM:%s"
                                % str(getattr(exc, "reason", exc)),
                                plan, checks)
        self.report["submission_claim"] = {
            "sid": claim.get("sid"), "experiment": claim.get("experiment"),
            "market": claim.get("market"),
            "labels": ("one order per authorization, enforced on the "
                       "filesystem. NEVER reset automatically; clearing it "
                       "is an owner action with the credentials directly.")}
        ack = self.transport.submit(intent) or {}
        vid = str((ack.get("order") or {}).get("id")
                  or ack.get("orderId") or ack.get("id") or "").strip()
        self.venue_order_id = vid or None
        self.placed_t = t
        if vid:
            self.store.attach_venue_id(coid, vid)
        ev = self.ownership_evidence(vid) if vid else {
            "venue_order_id": None, "watchdog_cancellable": False,
            "why": "THE ACK NAMED NO VENUE ORDER ID"}
        self.write_marker({"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "coid": coid, "plan": plan, "stage": "PLACED",
                           "venue_order_id": vid or None,
                           "ownership_evidence": ev})
        self.report = {"LABEL": "L5_MECH_PLACED", "plan": plan,
                       "checks": checks, "submitted": 1,
                       "submission_claim": self.report.get("submission_claim"),
                       "coid": coid, "venue_order_id": vid or None,
                       "ownership_evidence": ev,
                       "placed_t": t, "labels": plan["labels"]}
        self.emit("L5_MECH_PLACED", dict(self.report))

        # OWNERSHIP EVIDENCE PRECEDES RELIANCE ON THE WATCHDOG. Without both
        # witnesses the watchdog would refuse this id -- rightly -- and the
        # order would simply be an orphan we created on purpose. Withdraw it
        # ourselves, now, and report that the test could not be run.
        if not ev.get("watchdog_cancellable"):
            self._self_cancel(t, why="OWNERSHIP_EVIDENCE_INCOMPLETE")
            return self.report
        self.state = self.RESTING
        return self.report

    def _rest(self, t: float) -> Dict[str, Any]:
        """Re-read the venue until it says terminal. Cancel NOTHING here.

        The watchdog is the subject of the experiment, so the one thing this
        branch must not do is withdraw the order before the watchdog gets to.
        """
        if t - self.last_reread_t < REREAD_EVERY_S:
            return self.report
        self.last_reread_t = t
        row: Dict[str, Any] = {}
        err = None
        try:
            row = self.transport.order(str(self.venue_order_id)) or {}
            self.rereads += 1
        except BaseException as exc:                            # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            err = type(exc).__name__
        age = None if self.placed_t is None else round(t - self.placed_t, 1)
        self.report["rest_age_s"] = age
        self.report["rereads"] = self.rereads
        self.report["last_venue_row"] = {
            "state": row.get("state"),
            "leavesQuantity": str(row.get("leavesQuantity")),
            "cumQuantity": str(row.get("cumQuantity")),
            "read_error": err}
        if err is None and row and is_terminal(row):
            # WHO WITHDREW IT. We sent no cancel, so a terminal row here is
            # the watchdog's doing -- unless the venue filled or expired it,
            # which the state says and which is reported as itself.
            state = str(row.get("state") or "").upper()
            filled = "FILLED" in state and "CANCEL" not in state
            self._finish(t, row,
                         cancelled_by=("VENUE_FILL" if filled
                                       else "WATCHDOG_INDEPENDENT"),
                         proof="VENUE_TERMINAL_STATUS_REREAD")
            return self.report
        if (age is not None and age >= self.self_cancel_after_s):
            # THE FALLBACK, AND IT IS NOT PROOF. Stranding a real order to
            # protect the purity of a measurement would be the wrong trade.
            self._self_cancel(t, why="WATCHDOG_DID_NOT_CANCEL_IN_TIME")
        return self.report

    def _self_cancel(self, t: float, *, why: str) -> None:
        vid = self.venue_order_id
        cancel_error = None
        if vid:
            try:
                self.transport.cancel(vid)
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                cancel_error = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        row: Dict[str, Any] = {}
        if vid:
            try:
                row = self.transport.order(vid) or {}
            except BaseException as exc:                        # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                row = {"read_error": type(exc).__name__}
        self.report["self_cancel_why"] = why
        self.report["self_cancel_error"] = cancel_error
        self._finish(t, row, cancelled_by="SELF_FALLBACK",
                     proof=("NOT PROOF of independent cancellation: this "
                            "process withdrew its own order"))

    def _finish(self, t: float, row: Mapping[str, Any], *,
                cancelled_by: str, proof: str) -> None:
        self.state = self.DONE
        terminal = bool(row) and is_terminal(row)
        if terminal and self.coid:
            try:
                self.store.terminate_order(
                    self.coid, reason=str(row.get("state") or "TERMINAL"))
            except BaseException:                              # noqa: BLE001
                pass
        self.report.update({
            "LABEL": "L5_MECH_DONE",
            "cancelled_by": cancelled_by,
            "proof": proof,
            "venue_terminal": terminal,
            "venue_state": row.get("state"),
            "leaves_qty": str(row.get("leavesQuantity")),
            "cum_qty": str(row.get("cumQuantity")),
            "rest_age_s": (None if self.placed_t is None
                           else round(t - self.placed_t, 1)),
            "rereads": self.rereads,
            "independent_cancellation_proven": bool(
                terminal and cancelled_by == "WATCHDOG_INDEPENDENT"),
            "labels": ("independent_cancellation_proven is true ONLY when a "
                       "cancel this process did not send left the order "
                       "terminal in the VENUE's own row. An ack proves "
                       "nothing and a self-cancel proves nothing."),
        })
        self.write_marker({"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "coid": self.coid, "stage": "DONE",
                           "venue_order_id": self.venue_order_id,
                           "cancelled_by": cancelled_by,
                           "independent_cancellation_proven":
                               self.report["independent_cancellation_proven"]})
        self.emit("L5_MECH_DONE", dict(self.report))

    @property
    def finished(self) -> bool:
        return self.state in (self.DONE, self.REFUSED)
