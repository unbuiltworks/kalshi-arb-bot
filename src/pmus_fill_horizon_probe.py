"""Measure the maker fill-survival curve PAST the horizon our orders have reached.

THE ONE QUESTION THIS EXISTS TO ANSWER
---------------------------------------
`analysis/l5/pmus_fill_model.json`, calibrated on 965 of our own submitted
orders, stops at **32.192 s** -- the longest we have EVER rested an order. Past
it there is no data, so `CalibratedFillModel.p_fill` returns None and the
inventory engine refuses with FILL_UNESTIMABLE rather than extrapolate.

L5's median complement gap is **47 s**. For a strategy whose premise is RESTING
orders, the most load-bearing execution parameter is therefore outside our
evidence, and no replay can extend it: a replay can only replay fills that
already happened. The shadow has already been shown wrong about fills by ~9x
(it modelled ~0.05 where the measured figure is 0.43), so another shadow is not
an instrument here. Resting real orders longer is the only one.

WHAT IT IS NOT
--------------
NOT a profit attempt, and its success criterion contains no P&L. It takes no
directional view, consults no probability estimate and requires no edge to be
informative: it posts a maker order at the touch, lets it rest for a target
time, and records whether it filled. A probe that needed an edge to be
informative would be a strategy wearing a measurement's clothes.

THE ENVELOPE IS IN THE CODE, NOT IN THE ENVIRONMENT
----------------------------------------------------
Owner-approved 2026-10-25: maker-only, PMUS btc-updown-15m, rest steps
30/45/60/90 s, ONE resting order at a time, 5-contract clips, cancel-only
cleanup, hard stop at 20 windows or on the first unexplained terminal state.
Bounded worst case ~$100.

Those numbers are MODULE CONSTANTS. They are deliberately not environment
variables: an envelope that can be widened by setting a variable is not an
envelope, and the failure mode of this family of services has been a stale
flag left set. `check_envelope()` recomputes the worst case from the constants
and REFUSES to run if it exceeds the approved ceiling, so the bound cannot be
loosened without a code change that is visible in a diff.

WHY MAKER-ONLY MAKES THE WORST CASE COMPUTABLE
-----------------------------------------------
A maker buy either does not fill (cost: nothing) or fills at its own posted
price and is held to settlement, where it pays 1.00 or 0.00. One unpaired
5-contract clip can therefore lose at most 5 x its price <= $5.00, and the
probe never holds more than one order or re-enters a window it has traded. Over
20 windows the ceiling is 20 x $5.00 = $100.00, which is the approved figure.
It never crosses, so it cannot pay a taker fee and cannot slip.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

LABEL = "PMUS_FILL_HORIZON_PROBE_V1"

# ---------------------------------------------------------------- envelope
#: owner-approved envelope, 2026-10-25. Constants, not configuration.
SERIES = "btc-updown-15m"
CLIP_CONTRACTS = D(5)
REST_STEPS_S: Sequence[float] = (30.0, 45.0, 60.0, 90.0)
MAX_WINDOWS = 20
MAX_CONCURRENT_ORDERS = 1
MAX_SUBMISSIONS = MAX_WINDOWS            # one order per window, at most
APPROVED_WORST_CASE_USD = D("100.00")

#: the calibrated horizon this probe exists to see past
KNOWN_HORIZON_S = 32.192
L5_MEDIAN_COMPLEMENT_GAP_S = 47.0

#: a maker order is never posted outside this band: near 0 or 1 the quote is
#: not a real two-sided market and a fill there measures nothing about queueing.
MIN_POST_PRICE = D("0.10")
MAX_POST_PRICE = D("0.90")

# ------------------------------------------------------------------ arming
LIVE_ENV = "PMUS_FILL_HORIZON_LIVE"
AUTH_ENV = "PMUS_FILL_HORIZON_AUTH"
EXPIRY_ENV = "PMUS_FILL_HORIZON_AUTH_EXPIRES"

#: THE AUTHORIZATION THIS BUILD WILL HONOUR, and nothing else.
#:
#: Granted by the owner on 2026-10-25 for the 5-contract variant of the
#: fill-horizon envelope above. It is named here, in the build, so that the
#: control stays two-party: the code says which authorization it accepts, and a
#: human installs the matching flag. Neither half alone can arm it. An
#: identifier for any other probe is refused, and once this one is spent it is
#: MOVED rather than cleared -- a spent authorization never arms again.
EXPECTED_AUTH_ID = "OWNER-2026-10-25-PMUS-FILL-HORIZON-1"

TERMINAL_FILLED = "FILLED"
TERMINAL_CANCELLED = "CANCELLED"
TERMINAL_PARTIAL = "PARTIAL"
TERMINAL_UNKNOWN = "UNKNOWN"
KNOWN_TERMINALS = (TERMINAL_FILLED, TERMINAL_CANCELLED, TERMINAL_PARTIAL)


class NotArmed(Exception):
    """Structural refusal: no mutation is reachable without both keys."""


class EnvelopeViolation(Exception):
    """The probe's own bounds do not hold. It refuses to run at all."""


class HardStop(Exception):
    """An unexplained state. The run ends; it does not retry or assume."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def live_flag() -> bool:
    return _env(LIVE_ENV).upper() == "YES"


def auth_id() -> str:
    return _env(AUTH_ENV)


def expired(now: Optional[float] = None) -> Optional[bool]:
    """None means NO EXPIRY SUPPLIED, which is itself a refusal: an
    authorization without an end is not a one-probe authorization."""
    raw = _env(EXPIRY_ENV)
    if not raw:
        return None
    import datetime as dt
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True                       # unparseable expiry = expired
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (now if now is not None else time.time()) >= when.timestamp()


def armed(now: Optional[float] = None) -> bool:
    return bool(live_flag() and auth_id() == EXPECTED_AUTH_ID
                and expired(now) is False)


def why_not_armed(now: Optional[float] = None) -> Optional[str]:
    if armed(now):
        return None
    if not live_flag():
        return f"{LIVE_ENV} is not YES"
    got = auth_id()
    if not got:
        return f"{AUTH_ENV} is absent"
    if got != EXPECTED_AUTH_ID:
        return "authorization identifier does not match this build"
    if expired(now) is None:
        return (f"{EXPIRY_ENV} is absent; an authorization with no end is not "
                f"a one-probe authorization")
    return "the authorization has expired"


def require_armed(now: Optional[float] = None) -> None:
    if not armed(now):
        raise NotArmed(why_not_armed(now) or "not armed")


def check_envelope() -> Dict[str, Any]:
    """Recompute the worst case from the constants and refuse if it exceeds the
    approved ceiling. Called before anything is placed."""
    worst = CLIP_CONTRACTS * MAX_POST_PRICE * D(MAX_WINDOWS)
    out = {"label": LABEL, "series": SERIES,
           "clip_contracts": str(CLIP_CONTRACTS),
           "rest_steps_s": list(REST_STEPS_S),
           "max_windows": MAX_WINDOWS,
           "max_concurrent_orders": MAX_CONCURRENT_ORDERS,
           "max_submissions": MAX_SUBMISSIONS,
           "maker_only": True, "crosses_ever": False,
           "computed_worst_case_usd": str(worst),
           "approved_worst_case_usd": str(APPROVED_WORST_CASE_USD),
           "known_horizon_s": KNOWN_HORIZON_S,
           "l5_median_complement_gap_s": L5_MEDIAN_COMPLEMENT_GAP_S}
    if worst > APPROVED_WORST_CASE_USD:
        raise EnvelopeViolation(
            f"computed worst case {worst} exceeds approved "
            f"{APPROVED_WORST_CASE_USD}; refusing to run")
    if MAX_CONCURRENT_ORDERS != 1:
        raise EnvelopeViolation("the envelope permits one resting order")
    if not REST_STEPS_S or max(REST_STEPS_S) <= KNOWN_HORIZON_S:
        raise EnvelopeViolation(
            "every rest step is inside the already-measured horizon, so this "
            "run could not answer the question it exists for")
    return out


# ------------------------------------------------------------- decisions
@dataclass(frozen=True)
class Quote:
    """The executable touch, own-cost convention. UP == YES, DOWN == NO."""

    yes_bid: Optional[D]
    yes_bid_qty: Optional[D]
    yes_ask: Optional[D]
    yes_ask_qty: Optional[D]
    readable: bool = True

    @property
    def no_bid(self) -> Optional[D]:
        return (D(1) - self.yes_ask) if self.yes_ask is not None else None


@dataclass
class ProbeOrder:
    window: str
    side: str
    price: D
    qty: D
    rest_target_s: float
    placed_at: float
    venue_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    terminal: Optional[str] = None
    filled_qty: D = D(0)
    rest_actual_s: Optional[float] = None

    def observation(self) -> Optional[Dict[str, Any]]:
        """One Kaplan-Meier row: time-to-event and whether it was an event.

        A cancel is CENSORED at its rest time, not a permanent no-fill -- the
        same convention the existing calibration uses, so the new points can be
        pooled with the old 965 rather than compared against them.
        """
        if self.terminal is None or self.rest_actual_s is None:
            return None
        return {"rest_s": self.rest_actual_s,
                "event": self.terminal in (TERMINAL_FILLED, TERMINAL_PARTIAL),
                "censored": self.terminal == TERMINAL_CANCELLED,
                "rest_target_s": self.rest_target_s,
                "filled_qty": str(self.filled_qty),
                "side": self.side, "price": str(self.price),
                "window": self.window,
                "past_known_horizon": self.rest_actual_s > KNOWN_HORIZON_S}


def rest_target_for(window_index: int) -> float:
    """Rotate the rest steps so the curve is sampled evenly even if the run
    stops early -- a run that halted after six windows still has points at
    every step rather than all of them at 30 s."""
    return REST_STEPS_S[window_index % len(REST_STEPS_S)]


def choose_post(q: Quote, rest_target_s: float, tte_s: float) -> Dict[str, Any]:
    """Where to rest, or why not to.

    No probability input, by design: this measures queueing, not direction. It
    posts at the touch on the side with the DEEPER visible book, because that
    is the side whose queue dynamics we most need to characterise and it avoids
    systematically sampling only thin books.
    """
    if not q.readable or q.yes_bid is None or q.yes_ask is None:
        return {"post": False, "reason": "BOOK_UNREADABLE"}
    if q.yes_bid <= 0 or q.yes_ask >= 1 or q.yes_bid >= q.yes_ask:
        return {"post": False, "reason": "BOOK_NOT_TWO_SIDED"}
    # the order must be able to rest its full target inside the window, with
    # margin to cancel cleanly before close.
    if tte_s < rest_target_s + 30.0:
        return {"post": False, "reason": "NOT_ENOUGH_TIME_TO_REST"}
    up_px, dn_px = q.yes_bid, q.no_bid
    up_depth = q.yes_bid_qty or D(0)
    dn_depth = q.yes_ask_qty or D(0)
    side, price = (("UP", up_px) if up_depth >= dn_depth else ("DOWN", dn_px))
    if price is None:
        return {"post": False, "reason": "NO_PRICE_ON_CHOSEN_SIDE"}
    if price < MIN_POST_PRICE or price > MAX_POST_PRICE:
        return {"post": False, "reason": "PRICE_OUTSIDE_MEASUREMENT_BAND"}
    return {"post": True, "side": side, "price": price, "qty": CLIP_CONTRACTS,
            "rest_target_s": rest_target_s,
            "reason": "POST_AT_TOUCH_DEEPER_SIDE",
            "worst_case_usd": str(price * CLIP_CONTRACTS)}


def classify_terminal(venue_state: Any, filled_qty: Any, requested: D) -> str:
    """Map the venue's answer onto a terminal we are willing to record.

    Anything we cannot explain is UNKNOWN, and UNKNOWN is a hard stop. The
    whole point of the probe is a clean survival curve; a run that guessed at
    ambiguous terminals would produce a dirty one.
    """
    s = str(venue_state or "").upper()
    try:
        fq = D(str(filled_qty if filled_qty is not None else 0))
    except Exception:
        return TERMINAL_UNKNOWN
    if fq >= requested and requested > 0:
        return TERMINAL_FILLED
    if fq > 0:
        return TERMINAL_PARTIAL
    if any(k in s for k in ("CANCEL", "EXPIRED", "CLOSED", "REJECT")):
        return TERMINAL_CANCELLED
    if "FILL" in s:
        return TERMINAL_FILLED
    if any(k in s for k in ("OPEN", "LIVE", "NEW", "ACCEPT")):
        return TERMINAL_UNKNOWN      # still live is not a terminal
    return TERMINAL_UNKNOWN


# ------------------------------------------------------------------ runner
@dataclass
class ProbeState:
    windows_used: int = 0
    submissions: int = 0
    orders: List[ProbeOrder] = field(default_factory=list)
    stopped: Optional[str] = None
    why: Optional[str] = None

    def admits_another_window(self) -> Optional[str]:
        if self.stopped:
            return self.stopped
        if self.windows_used >= MAX_WINDOWS:
            return "MAX_WINDOWS"
        if self.submissions >= MAX_SUBMISSIONS:
            return "MAX_SUBMISSIONS"
        return None

    def worst_case_committed_usd(self) -> D:
        """What the orders actually placed could lose, not what the envelope
        allows. Reported so the bound is observable during the run."""
        return sum((o.price * o.qty for o in self.orders
                    if o.terminal in (None, TERMINAL_FILLED, TERMINAL_PARTIAL)),
                   D(0))

    def survival_rows(self) -> List[Dict[str, Any]]:
        return [r for r in (o.observation() for o in self.orders) if r]

    def report(self) -> Dict[str, Any]:
        rows = self.survival_rows()
        past = [r for r in rows if r["past_known_horizon"]]
        by_step: Dict[str, Dict[str, int]] = {}
        for r in rows:
            b = by_step.setdefault(str(r["rest_target_s"]),
                                   {"n": 0, "filled": 0, "censored": 0})
            b["n"] += 1
            b["filled"] += 1 if r["event"] else 0
            b["censored"] += 1 if r["censored"] else 0
        return {"label": LABEL, "auth_id": EXPECTED_AUTH_ID,
                "envelope": check_envelope(),
                "windows_used": self.windows_used,
                "submissions": self.submissions,
                "orders": len(self.orders),
                "observations": len(rows),
                "observations_past_known_horizon": len(past),
                "by_rest_step": by_step,
                "worst_case_committed_usd": str(self.worst_case_committed_usd()),
                "stopped": self.stopped, "why": self.why,
                "survival_rows": rows,
                "labels": {
                    "event": "the order filled; time-to-event is its rest time",
                    "censored": "cancelled unfilled; CENSORED at its rest time, "
                                "not a permanent no-fill",
                    "purpose": "extend the measured fill-survival curve past "
                               f"{KNOWN_HORIZON_S}s; L5's median complement gap "
                               f"is {L5_MEDIAN_COMPLEMENT_GAP_S}s",
                    "not_a_pnl_run": "success is a clean survival curve, not a "
                                     "profit; no directional view is taken"}}


def run_one_window(*, window: str, state: ProbeState, quote_fn: Callable[[], Quote],
                   place_fn: Callable[..., Mapping[str, Any]],
                   cancel_fn: Callable[[ProbeOrder], Mapping[str, Any]],
                   poll_fn: Callable[[ProbeOrder], Mapping[str, Any]],
                   tte_fn: Callable[[], float],
                   sleep_fn: Callable[[float], None] = time.sleep,
                   now_fn: Callable[[], float] = time.time) -> Dict[str, Any]:
    """One window: post one maker order, let it rest to target, record its fate.

    Every venue interaction is injected, so the whole lifecycle is testable
    without a venue and the test suite exercises the real control flow.
    """
    require_armed(now_fn())
    check_envelope()

    stop = state.admits_another_window()
    if stop:
        return {"placed": False, "reason": stop}

    target = rest_target_for(state.windows_used)
    q = quote_fn()
    plan = choose_post(q, target, tte_fn())
    state.windows_used += 1
    if not plan["post"]:
        return {"placed": False, "reason": plan["reason"], "window": window}

    order = ProbeOrder(window=window, side=plan["side"], price=plan["price"],
                       qty=plan["qty"], rest_target_s=target,
                       placed_at=now_fn())
    ack = place_fn(side=order.side, price=order.price, qty=order.qty,
                   window=window)
    if not ack or not ack.get("ok"):
        state.stopped, state.why = "PLACE_REJECTED", str(
            (ack or {}).get("why") or "the venue did not acknowledge the order")
        raise HardStop(state.why)
    order.venue_order_id = ack.get("venue_order_id")
    order.client_order_id = ack.get("client_order_id")
    if not order.venue_order_id:
        # an order we cannot name is an order we cannot cancel.
        state.stopped, state.why = "ACK_WITHOUT_ORDER_ID", (
            "the venue acknowledged without an order id, so the order could "
            "not be cancelled by id")
        raise HardStop(state.why)
    state.orders.append(order)
    state.submissions += 1

    # ---- rest to target, polling so a fill is timestamped when it happens
    deadline = order.placed_at + target
    while now_fn() < deadline:
        sleep_fn(min(2.0, max(0.1, deadline - now_fn())))
        st = poll_fn(order) or {}
        t = classify_terminal(st.get("state"), st.get("filled_qty"), order.qty)
        if t in (TERMINAL_FILLED, TERMINAL_PARTIAL):
            order.terminal = t
            order.filled_qty = D(str(st.get("filled_qty") or order.qty))
            order.rest_actual_s = now_fn() - order.placed_at
            return {"placed": True, "terminal": t, "window": window,
                    "rest_s": order.rest_actual_s}
        if t == TERMINAL_CANCELLED:
            # cancelled by someone other than us mid-rest: explainable, but it
            # ends this observation as censored at the time it happened.
            order.terminal = TERMINAL_CANCELLED
            order.rest_actual_s = now_fn() - order.placed_at
            return {"placed": True, "terminal": t, "window": window,
                    "rest_s": order.rest_actual_s}

    # ---- target reached unfilled: cancel, and insist on a clean terminal
    res = cancel_fn(order) or {}
    final = poll_fn(order) or {}
    t = classify_terminal(final.get("state"), final.get("filled_qty"), order.qty)
    order.rest_actual_s = now_fn() - order.placed_at
    if t == TERMINAL_UNKNOWN:
        state.stopped, state.why = "UNKNOWN_TERMINAL", (
            f"order {order.venue_order_id} left the book unexplained "
            f"(cancel said {res.get('ok')}, state {final.get('state')!r}); "
            f"the run stops rather than record a guess")
        raise HardStop(state.why)
    order.terminal = t
    if t in (TERMINAL_FILLED, TERMINAL_PARTIAL):
        order.filled_qty = D(str(final.get("filled_qty") or 0))
    return {"placed": True, "terminal": t, "window": window,
            "rest_s": order.rest_actual_s, "cancelled": True}


def preflight(now: Optional[float] = None) -> Dict[str, Any]:
    """Everything checkable before a single order exists. Safe to call unarmed,
    and it is the thing to run first: it says exactly what is missing."""
    out: Dict[str, Any] = {"label": LABEL, "armed": armed(now),
                           "why_not_armed": why_not_armed(now),
                           "expected_auth_id": EXPECTED_AUTH_ID,
                           "live_env": LIVE_ENV, "auth_env": AUTH_ENV,
                           "expiry_env": EXPIRY_ENV}
    try:
        out["envelope"] = check_envelope()
        out["envelope_ok"] = True
    except EnvelopeViolation as e:
        out["envelope_ok"] = False
        out["envelope_why"] = str(e)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Preflight only. This entry point deliberately places nothing: wiring the
    probe to the live transport is a separate, reviewable change on the service
    that holds the credentials, and this session has none."""
    print(json.dumps(preflight(), indent=1, default=str))
    return 0 if armed() else 2


if __name__ == "__main__":
    raise SystemExit(main())
