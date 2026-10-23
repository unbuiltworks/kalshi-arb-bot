"""One-window two-sided BTC15m probe. Experiment-gated, inert by default.

THE QUESTION IT EXISTS TO ANSWER. Can we acquire actual filled inventory --
not displayed prices -- whose settlement pays positive net on BOTH outcomes?
A book holding `a` UP and `b` DOWN for executed cost `C` pays `a` if UP
settles and `b` if DOWN settles, so that question is exactly `C < min(a, b)`
after fees. Unequal quantities are allowed: extra contracts on whichever side
is currently the MINIMUM lift `min(a, b)` for less than a dollar a share.

WHY A SEPARATE MODULE AND NOT A CHANGE TO THE ENGINE. `FaithfulExecutor`
mirrors L5: `FT.l5_native(row)` takes side, price and size from the L5 row and
there is no injection point. The ordinary path can HOLD and GATE this shape
but cannot generate it. This module generates two intents and nothing else --
it owns no transport, no ledger, no cancellation and no gate. Everything that
can move money or lose track of an order stays where it already is.

STRUCTURAL FACT THIS MODULE CANNOT CHANGE. Resting post-only on both sides,
the pair cost is forced by the spread: bid_UP + bid_DOWN = 1 - spread. On a
one-tick book that is 0.99, whose 1-cent gross edge is less than the 3.44-cent
worst-case taker fee at p~0.45. So a probe resting AT the touch is negative on
both outcomes by construction, and `MIN_EDGE` refuses it. Only resting behind
the touch clears the bar -- which is a bet on fill probability, and
`min_completion_probability()` below says how big a bet.

NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import os
from decimal import Decimal as D, ROUND_CEILING, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

from src import l5_faithful_translation as FT

EXPERIMENT_ENV = "L5_TS_EXPERIMENT_ID"
MARKET_ENV = "L5_TS_MARKET"

#: worst-case fee coefficient. The maker rebate is a credit we are never
#: entitled to assume, so the probe is priced as if both legs paid taker.
TAKER_THETA = D("0.0695")
#: the rebate, used ONLY to report the upside, never to justify an order
MAKER_THETA = D("-0.0125")

#: both legs post-only. A crossing order is a different experiment.
MODE = FT.MAKER

#: the two sides, named rather than spelled out at each comparison. Same
#: strings the rest of this module and `AUTH.PROBE_LEGS` already use.
UP, DOWN = "UP", "DOWN"

#: the SMALLEST gross edge ever acceptable. A pair at or above $1.00 is
#: never placed whatever the fee schedule says.
MIN_GROSS_EDGE = D("0.01")

#: the DEAREST pair this module will ever quote.
#:
#: WHY A CAP AND NOT JUST AN EDGE FLOOR. `MIN_GROSS_EDGE` admits a pair at
#: 0.99, and `fee_floor` then calls it profitable -- because it judges
#: POST-ONLY execution, where the maker credit carries it. On that pair
#: `net_if_taker` is NEGATIVE and `both_positive_if_taker` is False, so its
#: profitability depends entirely on a rebate, and L5_OPERATING_MODEL is
#: explicit that an uncredited rebate is never permission to place a trade.
#:
#: What stood between us and such a pair was one environment variable:
#: `L5_TS_DEPTH_TICKS=2` happens to make the pair at most `1 - spread - 0.04`.
#: At 1 tick a one-cent book gives 0.97 and at 0 it gives 0.99, both sendable.
#: 0.96 is the spring target in L5_OPERATING_MODEL, so the discipline is now
#: a gate rather than a side effect of a setting.
MAX_PAIR_COST = D("0.96")
MAX_PAIR_COST_ENV = "L5_TS_MAX_PAIR_COST"


def max_pair_cost() -> D:
    """The pair ceiling. Configurable CHEAPER only; unreadable is not wider."""
    raw = (os.getenv(MAX_PAIR_COST_ENV) or "").strip()
    if not raw:
        return MAX_PAIR_COST
    try:
        v = D(raw)
    except (InvalidOperation, ValueError, ArithmeticError):
        return MAX_PAIR_COST
    if v <= 0:
        return MAX_PAIR_COST
    return min(MAX_PAIR_COST, v)
#: retained for callers that still read it; the binding test is now
#: `fee_floor()`, computed from the ACTUAL proposed quote prices.
MIN_EDGE = MIN_GROSS_EDGE


def fee_floor(up_px: D, dn_px: D) -> Dict[str, Any]:
    """Is this pair profitable on both outcomes, and under which execution?

    WHY THIS IS NOT A CONSTANT. The old flat 0.04 floor was the worst-case
    TAKER fee at p ~ 0.45, which is the most expensive point on the curve:
    the fee is Theta * p * (1-p), so at 0.12/0.88 it is less than half what
    it is at 50/50. A single constant therefore refused configurations that
    are comfortably profitable at the extremes, and it refused resting AT
    THE TOUCH everywhere -- the configuration with by far the best measured
    fill rate.

    BOTH LEGS ARE POST-ONLY BY CONSTRUCTION, so the maker coefficient is the
    one that applies: MODE is FT.MAKER, which is what l5_order_body turns
    into participateDontInitiate, and a crossing order is a different
    experiment. `requires_post_only` is reported so no caller can read this
    verdict as applying to a taker fill.

    THE TAKER FIGURE IS STILL COMPUTED AND REPORTED, including when it is
    negative, because it is the honest worst case if the rebate is not
    credited on a given order.
    """
    edge = D(1) - (up_px + dn_px)
    taker_cost = (TAKER_THETA * up_px * (D(1) - up_px)
                  + TAKER_THETA * dn_px * (D(1) - dn_px))
    maker_credit = -(MAKER_THETA * up_px * (D(1) - up_px)
                     + MAKER_THETA * dn_px * (D(1) - dn_px))
    net_maker = edge + maker_credit
    net_taker = edge - taker_cost
    return {"edge": edge,
            "taker_fee_per_contract": taker_cost,
            "maker_credit_per_contract": maker_credit,
            "net_if_maker": net_maker,
            "net_if_taker": net_taker,
            "ok": bool(edge >= MIN_GROSS_EDGE and net_maker > 0),
            "requires_post_only": True,
            "labels": ("the verdict is for POST-ONLY execution on both "
                       "legs, which is how this probe quotes. net_if_taker "
                       "is the worst case should the rebate not be credited "
                       "and is reported even when negative.")}
#: price sanity, same band the queue measurement uses
MIN_PRICE = D("0.05")
MAX_PRICE = D("0.95")

WINDOW_S = 900
#: the hard terminal deadline: nothing rests past this
CANCEL_MARGIN_S = 180.0
#: the margin may be WIDENED (cancel EARLIER), never narrowed, exactly as
#: `l5_live_authorization.close_margin_s` works.
#:
#: WHY IT NEEDS A KNOB AT ALL. The independent watchdog treats an order older
#: than `L5_WATCHDOG_REPORT_MAX_AGE_S` (300s in production) as overdue, and it
#: ASSERTS HALT BEFORE it cancels. With the default margin a leg placed at
#: T+2:00 and held to the T+12:00 deadline rests 600s, so the watchdog would
#: latch UNMANAGED_RESTING_ORDER, stop the executor and kill the complement
#: mid-window. That is safe but it destroys the measurement. Widening the
#: margin cancels EARLIER and is therefore a tightening in both senses.
CANCEL_MARGIN_ENV = "L5_TS_CANCEL_MARGIN_S"


def cancel_margin_s() -> float:
    """The effective terminal margin. Wider only; unreadable is not wider."""
    raw = (os.getenv(CANCEL_MARGIN_ENV) or "").strip()
    try:
        v = float(raw) if raw else CANCEL_MARGIN_S
    except ValueError:
        v = CANCEL_MARGIN_S
    return max(CANCEL_MARGIN_S, v)
#: no leg may be PLACED after this
PLACE_OFFSET_S = 480.0
MIN_REST_S = 60.0
#: the EARLIEST offset into a window at which a leg may be placed. Zero keeps
#: the deployed behaviour byte-for-byte.
#:
#: WHY IT EXISTS. `may_place` allowed placement from T+0, and the terminal
#: deadline is fixed relative to the window CLOSE, so the rest of a leg was
#: bounded only by whoever chose the moment the shadow lock came off. That
#: worked for one hand-timed window and does not survive a MANDATE, where the
#: lock stays open across consecutive windows: a leg placed at T+0:05 and held
#: to a T+10:00 deadline rests 595s against the watchdog's 300s max order age,
#: and the watchdog ASSERTS HALT BEFORE it cancels. A floor makes the bound
#: structural instead of operational.
#:
#: RAISE-ONLY, like `cancel_margin_s` widens only: a later floor is a shorter
#: rest, so only tightening is accepted and an unreadable value is not
#: permission.
PLACE_FLOOR_S = 0.0
PLACE_FLOOR_ENV = "L5_TS_PLACE_FLOOR_S"


def place_floor_s() -> float:
    """The effective placement floor, as an offset from the window start."""
    raw = (os.getenv(PLACE_FLOOR_ENV) or "").strip()
    try:
        v = float(raw) if raw else PLACE_FLOOR_S
    except ValueError:
        v = PLACE_FLOOR_S
    return max(PLACE_FLOOR_S, v)
MAX_BOOK_AGE_S = 2.0


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def experiment_id() -> str:
    return str(os.getenv(EXPERIMENT_ENV, "")).strip()


def named_market() -> str:
    return str(os.getenv(MARKET_ENV, "")).strip()


def market_patterns() -> tuple:
    """The authorized market patterns, in order, from `L5_TS_MARKET`.

    Comma-separated. An entry with no `*` is an EXACT slug and matches only
    itself, exactly as before.
    """
    raw = named_market()
    return tuple(p for p in (x.strip() for x in raw.split(",")) if p)


def market_authorized(market: str) -> bool:
    """Does this slug match an authorized pattern?

    WHY A PATTERN AT ALL. The runner rebuilds its venue client against a
    NEW slug every fifteen minutes, so an exact-slug authorization
    expires the moment its window rolls and the engine goes inert until a
    human edits an environment variable. That is the second half of the
    one-shot restriction: continuous accumulation across successive markets
    is impossible while the authorization names one slug.

    A pattern is still a deliberate, bounded authorization -- it names an
    instrument family (`cpc-btc-updown-15m-*`), not "any market" -- and an
    exact slug remains exact, so nothing already configured widens.
    """
    m = str(market or "").strip()
    if not m:
        return False
    for pat in market_patterns():
        if "*" in pat:
            if fnmatch.fnmatchcase(m, pat):
                return True
        elif m == pat:
            return True
    return False


def active(market: Optional[str] = None) -> bool:
    """False unless BOTH an experiment and a market are named AND it matches.

    INERT IS THE DEFAULT AND THERE IS NO PARTIAL ARMING. A missing experiment
    id, a missing market, or a market that no authorized pattern covers all
    return False, so an incomplete configuration cannot half-enable the
    probe.
    """
    exp = experiment_id()
    if not exp or not market_patterns():
        return False
    if market is None:
        return True
    return market_authorized(market)


# --------------------------------------------------------------- timing

#: THE TWO ECONOMIC CUTOFFS, replacing one arbitrary placement band.
#:
#: WHY TWO. A single `place_cutoff` conflated two decisions with opposite
#: risk. ENTERING a first leg late is the dangerous one: if the window cannot
#: still produce a complement at a price that clears the fee floor, that leg
#: is a directional bet the engine never intended. COMPLETING is the safe one
#: -- it REMOVES exposure -- so it must stay available later than opening.
#: Collapsing them meant the engine stopped reducing risk at the same instant
#: it stopped taking it.
#:
#: Both are offsets from the window START and are RAISE-ONLY in the safe
#: direction: `open` may only move EARLIER (stop entering sooner) and
#: `complete` may only move EARLIER too, so neither knob can extend trading.
ENTRY_CUTOFF_S = 600.0
COMPLETE_CUTOFF_S = 720.0
ENTRY_CUTOFF_ENV = "L5_TS_ENTRY_CUTOFF_S"
COMPLETE_CUTOFF_ENV = "L5_TS_COMPLETE_CUTOFF_S"


def _earlier_only(env: str, default: float) -> float:
    """Configured value, accepted only when it is EARLIER than the default."""
    raw = (os.getenv(env) or "").strip()
    try:
        v = float(raw) if raw else default
    except ValueError:
        return default                      # unreadable is not permission
    return min(default, v)


def entry_cutoff_s() -> float:
    return _earlier_only(ENTRY_CUTOFF_ENV, ENTRY_CUTOFF_S)


def complete_cutoff_s() -> float:
    return _earlier_only(COMPLETE_CUTOFF_ENV, COMPLETE_CUTOFF_S)


#: HOW LONG ONE ORDER MAY REST, from its own submission. This is what makes
#: the watchdog's 300s a PER-ORDER backstop instead of the strategy's clock:
#: with a per-order deadline, an order's life stops being the window's life,
#: so the engine can quote from the window's open and still never hold
#: anything near the watchdog's limit.
#:
#: Measured from SUBMIT rather than from the venue acknowledgement, which is
#: the conservative direction: submit is always at or before the ack, so the
#: deadline fires sooner than an ack-based one would.
#:
#: SHORTER ONLY. A longer value and an unreadable value are both refused.
ORDER_TTL_S = 120.0
ORDER_TTL_ENV = "L5_TS_ORDER_TTL_S"


def order_ttl_s() -> float:
    """The per-order rest bound. Configurable shorter, never longer."""
    raw = (os.getenv(ORDER_TTL_ENV) or "").strip()
    try:
        v = float(raw) if raw else ORDER_TTL_S
    except ValueError:
        return ORDER_TTL_S
    if v <= 0:
        return ORDER_TTL_S
    return min(ORDER_TTL_S, v)


def order_due(now: float, submitted_t: float, t_start: float,
              t_end: float) -> bool:
    """Is this order past its own deadline, or past the terminal?

    `min(submit + ttl, terminal)`, the same shape as the queue
    measurement's `min(T_ack + 120, T_end - 180)`.
    """
    return (float(now) >= float(submitted_t) + order_ttl_s()
            or float(now) >= cancel_cutoff(t_end))


def may_enter(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow a NEW first leg, else the refusal reason.

    Bounded by the open cutoff AND by the terminal deadline, so a leg can
    never be opened that the terminal could not withdraw.
    """
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    floor = float(t_start) + place_floor_s()
    if now < floor:
        return "BEFORE_PLACE_FLOOR"
    if now >= float(t_start) + entry_cutoff_s():
        return "PAST_ENTRY_CUTOFF"
    if now >= cancel_cutoff(t_end) - MIN_REST_S:
        return "TOO_LATE_TO_WITHDRAW"
    return None


#: THE MINIMUM USABLE ENTRY RUNWAY a window must still offer to be worth
#: CLAIMING. A claim is irreversible -- `WindowLedger.reserve` is not
#: refundable and `retire_settled_market` is the only thing that frees a
#: campaign -- so claiming a window that cannot realistically enter spends
#: the authorization for nothing.
#:
#: MEASURED, NOT ASSUMED. On 2026-10-23 the executor armed window 0000z at
#: T+441s. `entry_cutoff_s` is 600, so arithmetic said 159s of runway
#: remained; `may_enter` actually began refusing TOO_LATE_TO_WITHDRAW at
#: T+565s, because `t_end` comes from the VENUE (`bounded_by: VENUE_CLOSE`)
#: and the venue's close can be earlier than start+WINDOW_S implies. The
#: window produced zero submissions and the single-window campaign was spent.
#: That is why `usable_entry_runway_s` asks `may_enter` instead of
#: recomputing the bound: the only trustworthy deadline is the one the gate
#: itself enforces, against the venue's own t_end.
#:
#: RAISE-ONLY. A LARGER runway requirement is strictly more conservative, so
#: only increases are accepted and an unreadable value is not permission.
MIN_ENTRY_RUNWAY_S = 300.0
MIN_ENTRY_RUNWAY_ENV = "L5_TS_MIN_ENTRY_RUNWAY_S"


def min_entry_runway_s() -> float:
    raw = (os.getenv(MIN_ENTRY_RUNWAY_ENV) or "").strip()
    try:
        v = float(raw) if raw else MIN_ENTRY_RUNWAY_S
    except ValueError:
        return MIN_ENTRY_RUNWAY_S           # unreadable is not permission
    return max(MIN_ENTRY_RUNWAY_S, v)


def usable_entry_runway_s(now: float, t_start: float, t_end: float) -> float:
    """Seconds of ENTRY opportunity this window still offers, from `now`.

    Derived by asking `may_enter` where it stops allowing entry, rather than
    re-deriving the bound from constants -- `t_end` is the venue's close and
    only the gate knows the composite deadline. Zero when entry is already
    closed; zero also before the window opens, because a window that has not
    started has no runway to spend yet and `window_eligible` reports that
    case separately.

    Resolution is one second, which is far finer than the 300s threshold it
    feeds and costs at most 900 evaluations of a pure predicate.
    """
    n = float(now); s = float(t_start); e = float(t_end)
    if n < s:
        return 0.0
    if may_enter(n, s, e) is not None:
        # already refused at `now`; BEFORE_PLACE_FLOOR is the one refusal
        # that resolves on its own, so it is not treated as exhausted here.
        if may_enter(n, s, e) != "BEFORE_PLACE_FLOOR":
            return 0.0
    t = n
    limit = s + float(WINDOW_S)
    while t <= limit:
        why = may_enter(t, s, e)
        if why is not None and why != "BEFORE_PLACE_FLOOR":
            break
        t += 1.0
    return max(0.0, t - n)


def window_eligible(now: float, t_start: float, t_end: float
                    ) -> Dict[str, Any]:
    """Whether this window is worth an IRREVERSIBLE claim. Read-only.

    SEPARATED FROM THE CLAIM ON PURPOSE. An eligibility question must be
    answerable without spending anything, so this touches no ledger, writes
    nothing, and may be called on every tick. The caller claims only after
    this says ok.

    `WINDOW_NOT_OPEN` is reported as not-eligible-yet rather than as a
    failure: the correct response is to wait for the next window start, which
    is exactly the behaviour that was missing when a mid-window deployment
    claimed an aging window.
    """
    n = float(now); s = float(t_start); e = float(t_end)
    if n < s:
        return {"ok": False, "reason": "WINDOW_NOT_OPEN_YET",
                "seconds_until_open": s - n,
                "runway_s": 0.0, "required_s": min_entry_runway_s(),
                "labels": ("not a refusal -- wait for this window to open "
                           "and claim it from the start")}
    runway = usable_entry_runway_s(n, s, e)
    need = min_entry_runway_s()
    if runway < need:
        return {"ok": False, "reason": "INSUFFICIENT_ENTRY_RUNWAY",
                "runway_s": runway, "required_s": need,
                "elapsed_s": n - s,
                "labels": ("the claim is irreversible and this window cannot "
                           "use it; wait for the next window rather than "
                           "spending the campaign on a stub")}
    return {"ok": True, "reason": None, "runway_s": runway,
            "required_s": need, "elapsed_s": n - s}


def may_complete(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow a COMPLEMENT against existing inventory.

    Deliberately later than `may_enter`: completing removes risk, so the
    engine keeps doing it after it has stopped opening. Still bounded by the
    terminal, which nothing may rest past.
    """
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    if now >= float(t_start) + complete_cutoff_s():
        return "PAST_COMPLETE_CUTOFF"
    if now >= cancel_cutoff(t_end):
        return "PAST_TERMINAL"
    return None


def cancel_cutoff(t_end: float) -> float:
    """The hard terminal deadline. Nothing may rest past it."""
    return float(t_end) - cancel_margin_s()


def window_end(t_start: float) -> float:
    """Window END from its START. BTC15m slugs name the start."""
    return float(t_start) + WINDOW_S


def cancel_cutoff_from_start(t_start: float) -> float:
    """The terminal deadline, taken from the WINDOW START.

    WHY THIS EXISTS RATHER THAN CALLERS DOING THE ARITHMETIC. `cancel_cutoff`
    takes the window END, but every slug and every book row names the window
    START, so a caller who passes what it has gets `start - 180` -- a
    deadline 180 seconds BEFORE the window opens, i.e. already expired on the
    first tick, which cancels the probe's legs the moment they rest. That is
    exactly the bug an independent review found in Runner.maybe_ts_deadline.
    Callers that hold a start must use this and never do the addition
    themselves.
    """
    return cancel_cutoff(window_end(t_start))


def place_cutoff(t_start: float, t_end: float) -> float:
    return min(float(t_start) + PLACE_OFFSET_S,
               cancel_cutoff(t_end) - MIN_REST_S)


def may_place(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow, else the refusal reason."""
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    floor = float(t_start) + place_floor_s()
    cutoff = place_cutoff(t_start, t_end)
    if floor >= cutoff:
        # a floor past the cutoff leaves no placement band at all. Say so
        # rather than silently never placing, which reads like a quiet market.
        return "PLACE_FLOOR_LEAVES_NO_BAND"
    if now < floor:
        return "BEFORE_PLACE_FLOOR"
    if now >= cutoff:
        return "PAST_PLACE_CUTOFF"
    return None


# ----------------------------------------------------------------- fees

def fee(theta: D, qty: D, price: D) -> D:
    """Theta * C * p * (1-p), rounded AWAY from zero to the cent.

    The venue bills commission in whole cents -- 107 of our own orders show
    not one sub-cent value -- so a cost is rounded UP and a credit is rounded
    DOWN in magnitude. Both are the conservative direction.
    """
    q = _d(qty) or D(0)
    p = _d(price) or D(0)
    raw = theta * q * p * (D(1) - p)
    if raw > 0:
        return raw.quantize(D("0.01"), rounding=ROUND_CEILING)
    if raw < 0:                      # a credit: keep the SMALLER magnitude
        return -((-raw).quantize(D("0.01")))
    return D(0)


def worst_fee(qty: D, price: D) -> D:
    return fee(TAKER_THETA, qty, price)


# ------------------------------------------------------- the economics

def net_settlement(*, up_qty: Any, dn_qty: Any,
                   up_price: Any, dn_price: Any,
                   theta: D = TAKER_THETA) -> Dict[str, Any]:
    """Net P&L on BOTH outcomes for an arbitrary, possibly ASYMMETRIC book.

    payout is `up_qty` if UP settles and `dn_qty` if DOWN settles, against one
    total executed cost. `both_positive` is the probe's whole objective.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    pu = _d(up_price) or D(0); pd = _d(dn_price) or D(0)
    cost = a * pu + b * pd + fee(theta, a, pu) + fee(theta, b, pd)
    up_pnl = a - cost
    dn_pnl = b - cost
    return {"cost": cost, "up_pnl": up_pnl, "dn_pnl": dn_pnl,
            "min_payout": min(a, b),
            "worst_outcome": min(up_pnl, dn_pnl),
            "both_positive": bool(up_pnl > 0 and dn_pnl > 0),
            "basis": ("payout is up_qty if UP settles and dn_qty if DOWN "
                      "settles; both_positive iff cost < min(up_qty, dn_qty)")}


def expected_value(*, matched_edge: Any, residual_alpha: Any,
                   p_first: Any, p_complete: Any) -> Dict[str, Any]:
    """EV per intended contract, over the four fill outcomes.

    `residual_alpha` is the measured LOSS per unmatched contract and is passed
    as a POSITIVE number. It is not variance: in L5's own BTC15m record the
    unmatched side won 39.5% of windows, so an unmatched leg is adversely
    selected, and a probe resting BEHIND the touch is selected harder still --
    it fills precisely when the market moves against it.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    pa = _d(p_first) or D(0)
    pb = _d(p_complete) or D(0)
    both = pa * pb
    one = pa * (D(1) - pb) + (D(1) - pa) * pb
    ev = both * e - one * al
    return {"ev": ev, "p_both": both, "p_one_leg": one,
            "from_matched": both * e, "from_residual": -(one * al)}


def min_completion_probability(*, matched_edge: Any,
                               residual_alpha: Any) -> Optional[D]:
    """The symmetric fill probability at which the probe breaks even.

    With p on both legs, EV = p^2 * e - 2p(1-p) * alpha, so break-even is
    p = 2*alpha / (e + 2*alpha). None when no probability suffices.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    if al <= 0:
        return D(0)
    denom = e + D(2) * al
    if denom <= 0:
        return None
    p = (D(2) * al) / denom
    return p if p <= 1 else None


# ------------------------------------------------- the two intents

def build_orders(*, market: str, bid: Any, ask: Any,
                 bid_depth: Any, ask_depth: Any,
                 tick: Any, min_qty: Any,
                 depth_ticks: int,
                 cash_envelope: Any,
                 max_worst_case_loss: Any,
                 budget_headroom: Any = None,
                 book_age_s: Any = None,
                 gap_suspected: bool = False) -> Dict[str, Any]:
    """Exactly two post-only intents, or a terminal refusal.

    NO FALLBACK. Every failure returns ok False and nothing is submitted. The
    module never degrades into a single leg, a crossing order, or the
    ordinary lambda-sized L5 decision -- a fallback would turn a measurement
    into a trade under an authorization granted for a measurement.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if not market or not active(market):
        return no("NOT_THE_AUTHORIZED_MARKET")
    if gap_suspected:
        return no("GAP_SUSPECTED")
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE")
    b = _d(bid); a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return no("BOOK_NOT_TWO_SIDED")
    if a <= b:
        return no("BOOK_CROSSED_OR_LOCKED")
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    if depth_ticks is None or int(depth_ticks) < 0:
        return no("DEPTH_TICKS_UNKNOWN")
    env = _d(cash_envelope); cap = _d(max_worst_case_loss)
    if env is None or env <= 0:
        return no("CASH_ENVELOPE_UNKNOWN")
    if cap is None or cap <= 0:
        return no("LOSS_CEILING_UNKNOWN")
    # THE CAMPAIGN BUDGET IS A THIRD BINDING CONSTRAINT, and it has to be
    # applied HERE for the same reason the cash basis already is -- see the
    # `oblig_per_contract` comment below, which records this module learning
    # the identical lesson one gate earlier. Sizing against the cash gate
    # alone built a 3.36-contract plan whose $4.6368 obligation fit the
    # $4.65 cash envelope and then had to be refused by the $11.10 campaign
    # budget at 13.5356 > 11.10, because $8.8988 of account-wide realized
    # loss already stood against it. The planner must not emit a plan its
    # own gates must reject; a refusal that the planner could have avoided
    # spends a window reservation for nothing.
    #
    # None means NOT SUPPLIED and is passed through unconstrained, so a
    # caller that does not run a campaign is unaffected. A supplied value of
    # zero or less is EXHAUSTED and refuses outright rather than sizing to
    # nothing -- "no budget" and "a tiny budget" are different answers.
    head = _d(budget_headroom)
    if budget_headroom is not None and (head is None or head <= 0):
        return no("CAMPAIGN_BUDGET_EXHAUSTED",
                  budget_headroom_usd=(None if head is None
                                       else str(head)))

    dsteps = D(int(depth_ticks)) * tk
    up_px = b - dsteps                     # rest BEHIND the UP bid
    dn_px = (D(1) - a) - dsteps            # and behind the DOWN bid
    for px in (up_px, dn_px):
        if px < MIN_PRICE or px > MAX_PRICE:
            return no("PRICE_OUT_OF_BAND", up_price=str(up_px),
                      dn_price=str(dn_px))
        if (px / tk) != (px / tk).to_integral_value():
            return no("PRICE_NOT_ON_TICK", price=str(px))

    pair = up_px + dn_px
    cap_px = max_pair_cost()
    if pair > cap_px:
        return no("PAIR_COST_ABOVE_CAP", pair_cost=str(pair),
                  max_pair_cost=str(cap_px))
    floor = fee_floor(up_px, dn_px)
    edge = floor["edge"]
    if not floor["ok"]:
        return no("EDGE_BELOW_FEE_FLOOR", pair_cost=str(pair),
                  edge=str(edge),
                  net_if_maker=str(floor["net_if_maker"]),
                  net_if_taker=str(floor["net_if_taker"]),
                  required="gross edge >= %s and net_if_maker > 0"
                           % MIN_GROSS_EDGE)

    # size: the WORST single-leg fill must stay inside the loss ceiling, and
    # the gross cash must stay inside the envelope. The dearer leg binds.
    dear = max(up_px, dn_px)
    by_loss = cap / dear
    # SIZE AGAINST THE GATE'S OWN CASH BASIS, not the pair cost. The cash
    # gate prices a resting DOWN leg (a BUY_SHORT) at a DOLLAR A SHARE, not
    # at its limit price, because a short owes $1 if YES settles 1. Sizing on
    # `pair` would therefore build a plan the gate then refuses -- which is
    # exactly what happened the first time this was wired. The conservative
    # accounting is preserved and respected, not worked around.
    oblig_per_contract = D(1) + up_px
    by_cash = env / oblig_per_contract
    qty = min(by_loss, by_cash)
    # and the campaign budget, on that SAME obligation basis, because
    # `ValidationGates` gate 3b charges the order exactly as the cash gate
    # above does -- a resting BUY_SHORT at a dollar a share.
    by_budget = None
    if head is not None:
        by_budget = head / oblig_per_contract
        qty = min(qty, by_budget)
    # quantise DOWN to the venue grid, never up
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    # THEN SHRINK UNTIL THE FEE FITS TOO. Sizing on price alone overshoots,
    # because the worst single-leg fill costs qty*price PLUS its fee, and a
    # loss ceiling that the fee can step over is not a ceiling. One grid
    # step per iteration, bounded, and the refusal stands if nothing fits.
    for _ in range(64):
        if qty < mq:
            break
        if (qty * dear + worst_fee(qty, dear) <= cap
                and qty * oblig_per_contract <= env
                and (head is None or qty * oblig_per_contract <= head)):
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_QUANTITY", by_loss=str(by_loss),
                  by_cash=str(by_cash),
                  by_budget=(None if by_budget is None else str(by_budget)),
                  budget_headroom_usd=(None if head is None else str(head)),
                  min_qty=str(mq),
                  obligation_per_contract=str(oblig_per_contract),
                  binding=("CAMPAIGN_BUDGET"
                           if by_budget is not None
                           and by_budget <= min(by_loss, by_cash)
                           else "CASH_ENVELOPE"
                           if by_cash <= by_loss else "LOSS_CEILING"),
                  labels=("the venue minimum is one grid step; a budget "
                          "that cannot fund even that is reported as the "
                          "binding constraint rather than as a book "
                          "problem"))
    if qty * dear + worst_fee(qty, dear) > cap:
        return no("LOSS_CEILING_UNREACHABLE", qty=str(qty))
    bd = _d(bid_depth); ad = _d(ask_depth)
    if bd is None or ad is None:
        return no("NO_DISPLAYED_DEPTH")

    # ACCEPTANCE IS ON THE EXECUTION THIS PROBE ACTUALLY USES. Both legs are
    # post-only, so the maker coefficient applies here exactly as it does in
    # fee_floor; evaluating acceptance under taker fees while the floor used
    # maker fees would have contradicted itself, and did.
    econ = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                          dn_price=dn_px, theta=MAKER_THETA)
    # the worst case is still COMPUTED and reported, never hidden
    econ_taker = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                                dn_price=dn_px, theta=TAKER_THETA)
    if not econ["both_positive"]:
        return no("NOT_POSITIVE_ON_BOTH_OUTCOMES",
                  up_pnl=str(econ["up_pnl"]), dn_pnl=str(econ["dn_pnl"]))

    def leg(side: str, px: D) -> Dict[str, Any]:
        # DEPTH IS RECORDED ON THE LEG, not left to be re-derived later. The
        # depth comparison read 7,310 order records off the production tape
        # and attributed NOT ONE of them, because nothing wrote down how far
        # behind the touch each order rested and the touch at placement was
        # not recoverable from the record. A measurement whose input is not
        # recorded cannot be taken.
        return {"side": side, "action": "BUY", "price": str(px),
                "qty": str(qty), "mode": MODE, "post_only": True,
                "depth_ticks": int(depth_ticks),
                "touch_at_placement": str(b if side == "UP" else D(1) - a),
                "worst_case_fee_usd": str(worst_fee(qty, px)),
                "alone_cost_usd": str(qty * px + worst_fee(qty, px))}

    return {"ok": True, "market": market,
            "depth_ticks": int(depth_ticks),
            "legs": [leg("UP", up_px), leg("DOWN", dn_px)],
            "pair_cost": str(pair), "edge_per_contract": str(edge),
            "fee_floor": {k: (str(v) if isinstance(v, D) else v)
                          for k, v in floor.items()},
            "qty_per_leg": str(qty),
            "gross_cash_usd": str(econ["cost"]),
            "cash_gate_obligation_usd": str(qty * oblig_per_contract),
            "cash_basis": ("a resting DOWN leg is a BUY_SHORT and the cash "
                           "gate charges it a dollar a share; sizing uses "
                           "that basis so the plan the gate sees is the "
                           "plan this built"),
            "budget_headroom_usd": (None if head is None else str(head)),
            "sized_by": ("CAMPAIGN_BUDGET" if by_budget is not None
                         and by_budget <= min(by_loss, by_cash)
                         else "CASH_ENVELOPE" if by_cash <= by_loss
                         else "LOSS_CEILING"),
            "budget_basis": ("the campaign budget is charged on the same "
                             "dollar-a-share obligation as the cash gate, "
                             "so the smaller of the two binds and the plan "
                             "this emits is one BOTH gates accept"),
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "up_pnl_if_taker": str(econ_taker["up_pnl"]),
            "dn_pnl_if_taker": str(econ_taker["dn_pnl"]),
            "both_positive_if_taker": bool(econ_taker["both_positive"]),
            "worst_single_leg_loss_usd": str(
                -(qty * dear + worst_fee(qty, dear))),
            "rebate_upside_usd": str(
                -(fee(MAKER_THETA, qty, up_px)
                  + fee(MAKER_THETA, qty, dn_px))),
            "labels": ("displayed prices are NOT fills. Both legs may rest "
                       "unfilled, one may fill alone, and either may fill "
                       "partially; the single-leg loss above is the bound "
                       "that matters.")}


def top_up(*, up_qty: Any, dn_qty: Any, up_price: Any, dn_price: Any,
           extra_side: str, extra_qty: Any, extra_price: Any
           ) -> Dict[str, Any]:
    """Would extra contracts on one side improve the WORST outcome?

    Allowed only when it does. Extra on the side that is already the maximum
    adds cost without lifting `min(a, b)`, which is strictly worse -- so this
    is the asymmetric case stated as a predicate rather than a hope.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    before = net_settlement(up_qty=a, dn_qty=b,
                            up_price=up_price, dn_price=dn_price)
    side = str(extra_side).upper()
    if side not in ("UP", "DOWN"):
        return {"ok": False, "reason": "EXTRA_SIDE_UNKNOWN"}
    eq = _d(extra_qty) or D(0)
    ep = _d(extra_price)
    if eq <= 0 or ep is None or ep <= 0 or ep >= D(1):
        return {"ok": False, "reason": "EXTRA_UNREADABLE"}
    # blended cost on the topped-up side
    if side == "UP":
        na = a + eq
        nc_u = (a * (_d(up_price) or D(0)) + eq * ep) / na if na else D(0)
        after = net_settlement(up_qty=na, dn_qty=b,
                               up_price=nc_u, dn_price=dn_price)
    else:
        nb = b + eq
        nc_d = (b * (_d(dn_price) or D(0)) + eq * ep) / nb if nb else D(0)
        after = net_settlement(up_qty=a, dn_qty=nb,
                               up_price=up_price, dn_price=nc_d)
    better = after["worst_outcome"] > before["worst_outcome"]
    return {"ok": bool(better),
            "reason": None if better else "TOP_UP_WORSENS_WORST_OUTCOME",
            "worst_before": before["worst_outcome"],
            "worst_after": after["worst_outcome"],
            "up_pnl_after": after["up_pnl"], "dn_pnl_after": after["dn_pnl"],
            "both_positive_after": after["both_positive"]}


# ------------------------------------------- patient opposite-side completion

#: how close to the terminal deadline completion may be CONSIDERED. Before
#: this the maker leg is still resting and still the better price, so there is
#: nothing to decide; crossing early throws away the edge the maker quote was
#: placed for.
COMPLETION_WINDOW_S = 120.0


def completion_phase(now: float, t_start: float) -> str:
    """MAKER while there is time, COMPLETION near the deadline, else LATE.

    Three named phases rather than a boolean, because "near the deadline" and
    "past the deadline" need different answers: at the cutoff nothing may
    rest, so a completion that has not been decided by then is not decided at
    all.
    """
    cut = cancel_cutoff_from_start(float(t_start))
    if float(now) >= cut:
        return "LATE"
    if float(now) >= cut - COMPLETION_WINDOW_S:
        return "COMPLETION"
    return "MAKER"


def completion_decision(*, up_qty: Any, dn_qty: Any,
                        up_cost_px: Any, dn_cost_px: Any,
                        taker_price: Any, available_depth: Any,
                        tick: Any, min_qty: Any,
                        cash_headroom: Any, worst_case_ceiling: Any,
                        book_age_s: Any = None,
                        gap_suspected: bool = False) -> Dict[str, Any]:
    """Should we CROSS to complete the deficient side? Economics only.

    THE DEADLINE IS NOT A REASON. This never returns ok True because time is
    short. It returns ok True only when crossing at the price actually
    executable, paying the actual taker fee, STRICTLY IMPROVES the worst of
    the two settlement outcomes, and fits inside the cash headroom and the
    worst-case ceiling we are already authorized for. Every other case is a
    refusal with its reason, and the caller's answer to a refusal is to
    cancel the unfilled order and carry the residual under the existing
    controls -- not to cross anyway.

    WHY IT CAN BEAT HOLDING. A one-sided book bought at `c` loses `c` on the
    wrong outcome. Completing at `q` makes the pair cost `c + q`, which pays
    `min(a, b)` on BOTH outcomes; whenever `c + q + fee < min(a, b)` after the
    completion that is a smaller worst case than holding, even though `q` is
    a crossing price and strictly worse than the maker quote we wanted.

    PARTIAL COMPLETION IS ALLOWED AND IS THE NORMAL CASE. Displayed depth
    rarely covers the whole deficit. A partial fill of the deficient side
    still lifts `min(a, b)`, so the size is clipped to the depth, the cash and
    the ceiling, and judged on what that clipped size actually does.

    PRICES ARE OWN-OUTCOME. `taker_price` is what a share of the DEFICIENT
    side costs to buy right now -- for DOWN that is `1 - up_bid`, not the UP
    ask -- and the caller owns that arithmetic because only the caller has the
    book.

    NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason, "cross": False}, **extra)

    a = _d(up_qty); b = _d(dn_qty)
    if a is None or b is None or a < 0 or b < 0:
        return no("INVENTORY_UNREADABLE")
    if a == 0 and b == 0:
        return no("NO_INVENTORY_TO_COMPLETE")
    if a == b:
        return no("ALREADY_MATCHED", matched=str(a))
    side = "DOWN" if a > b else "UP"
    deficit = abs(a - b)

    if gap_suspected:
        return no("GAP_SUSPECTED", side=side, deficit=str(deficit))
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        # A STALE BOOK IS NOT AN EXECUTABLE PRICE. Crossing on a price we
        # cannot date is how a "completion" becomes a worse one-sided book.
        return no("BOOK_STALE", side=side, book_age_s=None if age is None
                  else str(age))
    q = _d(taker_price)
    if q is None or q <= 0 or q >= D(1):
        return no("COMPLETION_PRICE_UNREADABLE", side=side)
    if q < MIN_PRICE or q > MAX_PRICE:
        return no("COMPLETION_PRICE_OUT_OF_BAND", side=side, price=str(q))
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if (q / tk) != (q / tk).to_integral_value():
        return no("COMPLETION_PRICE_NOT_ON_TICK", side=side, price=str(q))
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    depth = _d(available_depth)
    if depth is None:
        # NO DISPLAYED DEPTH IS NOT INFINITE DEPTH.
        return no("NO_DISPLAYED_DEPTH", side=side)
    cash = _d(cash_headroom)
    cap = _d(worst_case_ceiling)
    if cash is None or cash < 0:
        return no("CASH_HEADROOM_UNKNOWN", side=side)
    if cap is None or cap <= 0:
        return no("WORST_CASE_CEILING_UNKNOWN", side=side)

    # SIZE: the deficit, clipped by displayed depth, by the cash this costs,
    # and then shrunk on the venue grid until the fee fits too.
    qty = min(deficit, depth, cash / q if q > 0 else D(0))
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    for _ in range(64):
        if qty < mq:
            break
        if qty * q + worst_fee(qty, q) <= cash:
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_COMPLETION_QUANTITY", side=side,
                  deficit=str(deficit), depth=str(depth),
                  cash_headroom=str(cash))

    # THE DECISION, through the EXISTING predicate. top_up already answers
    # "does extra on this side improve the worst outcome", at taker fees, so
    # the completion is judged by the same arithmetic as any other top-up
    # rather than by a second opinion written for the deadline.
    tu = top_up(up_qty=a, dn_qty=b, up_price=up_cost_px, dn_price=dn_cost_px,
                extra_side=side, extra_qty=qty, extra_price=q)
    if not tu.get("ok"):
        return no(str(tu.get("reason") or "TOP_UP_WORSENS_WORST_OUTCOME"),
                  side=side, qty=str(qty), price=str(q),
                  worst_before=str(tu.get("worst_before")),
                  worst_after=str(tu.get("worst_after")),
                  labels=("crossing would NOT improve the worst settlement "
                          "outcome, so the deadline is not a reason to do "
                          "it. Cancel the unfilled order and carry the "
                          "residual under the existing controls."))

    # AND THE CROSS MUST BUY MORE THAN IT COSTS.
    #
    # WHY THIS SECOND BAR IS NEEDED, measured rather than assumed. "Improves
    # the worst outcome" alone is very nearly VACUOUS once a book is
    # one-sided: holding loses the leg's whole cost on the wrong outcome, so
    # every completion priced below a dollar reduces that. On a 5-share UP
    # leg at 0.59 the predicate passes at 0.35, 0.45, 0.60, 0.80, 0.90 AND
    # 0.95 -- so a rule written only on worst-case improvement would cross at
    # essentially any price the book showed, which is not an economic
    # decision.
    #
    # WHAT COMPLETION ACTUALLY COSTS. At the market's own price the cross is
    # fair value, so in expectation it costs exactly the TAKER FEE and buys
    # the removal of all settlement variance. This bar says that purchase
    # must be worth its price: the worst case must improve by MORE than the
    # fee paid for the improvement.
    taker_fee = worst_fee(qty, q)
    improvement = tu["worst_after"] - tu["worst_before"]
    if improvement <= taker_fee:
        return no("IMPROVEMENT_BELOW_ITS_OWN_FEE", side=side, qty=str(qty),
                  price=str(q), improvement_usd=str(improvement),
                  taker_fee_usd=str(taker_fee),
                  labels=("the cross would cost more in fee than it buys in "
                          "worst-case improvement. In expectation a "
                          "completion at the market's own price costs "
                          "exactly the taker fee and buys the removal of "
                          "settlement variance; this one does not pay for "
                          "itself."))

    # AND IT MUST STILL FIT THE CEILING WE ARE AUTHORIZED FOR. An improvement
    # that breaches the worst-case bound is not permission to breach it.
    worst_after = tu["worst_after"]
    if worst_after < -cap:
        return no("COMPLETION_EXCEEDS_WORST_CASE_CEILING", side=side,
                  qty=str(qty), price=str(q), worst_after=str(worst_after),
                  ceiling=str(cap))

    partial = bool(qty < deficit)
    return {"ok": True, "cross": True, "side": side, "action": "BUY",
            "qty": str(qty), "price": str(q), "mode": FT.TAKER,
            "post_only": False,
            "deficit": str(deficit), "partial": partial,
            "displayed_depth": str(depth),
            "taker_fee_usd": str(taker_fee),
            "cash_cost_usd": str(qty * q + worst_fee(qty, q)),
            "worst_before": str(tu["worst_before"]),
            "worst_after": str(worst_after),
            "worst_improvement_usd": str(worst_after - tu["worst_before"]),
            "up_pnl_after": str(tu["up_pnl_after"]),
            "dn_pnl_after": str(tu["dn_pnl_after"]),
            "both_positive_after": bool(tu["both_positive_after"]),
            "labels": ("a CROSSING order, priced at the displayed executable "
                       "price and charged the TAKER fee. It is proposed only "
                       "because it improves the worst settlement outcome, "
                       "never because the deadline arrived. Displayed depth "
                       "is not a fill: the size may fill partially or not at "
                       "all, and a refusal to fill leaves the residual the "
                       "existing controls already bound.")}


# ===================================================== the execution lifecycle
#
# WHAT THE 0330z WINDOW SHOWED. The plan is frozen per round -- rightly:
# re-planning on every tick produced legs of DIFFERENT sizes (1.89 against
# 0.98 in test), which is not a matched pair at all. But a frozen plan was
# never RETIRED either, so when the book left it behind the engine proposed
# the same unreachable pair 32 times in one window, and the complement's price
# came from planning time however far the market had moved.
#
# These two functions are the whole remedy and they are PURE: one says whether
# a frozen plan is still executable against the book in front of us, the other
# prices a complement against that book and the cost we ACTUALLY paid. Neither
# reads a file, a clock or an environment variable beyond the tick knobs, and
# neither can submit, cancel or claim anything.

#: how far the book may move away from a frozen leg before its round is
#: retired, in ticks. Measured per leg, against where that leg would be
#: priced right now.
#:
#: WHY A DRIFT TEST AND NOT ONLY A CROSSING TEST. A crossing leg is rejected
#: by the venue outright, so it is unambiguously dead. A leg that has merely
#: been left behind is still a legal resting order -- it simply cannot fill,
#: because the price has to travel back to it, and `cross_price_depth` is
#: cleared by WITHDRAWAL, which trade volume cannot measure. Holding it is not
#: unsafe; it just spends the window. Six ticks is wider than the 2-tick
#: placement offset and well inside the 51-cent move that exposed this.
STALE_DRIFT_TICKS = 6
STALE_DRIFT_ENV = "L5_TS_STALE_DRIFT_TICKS"


def stale_drift_ticks() -> int:
    """The drift tolerance. Configurable TIGHTER only -- a wider tolerance
    keeps unreachable legs alive longer, which is the unsafe direction for
    the measurement even though it risks no money."""
    raw = (os.getenv(STALE_DRIFT_ENV) or "").strip()
    try:
        v = int(raw) if raw else STALE_DRIFT_TICKS
    except ValueError:
        return STALE_DRIFT_TICKS
    if v < 0:
        return STALE_DRIFT_TICKS
    return min(STALE_DRIFT_TICKS, v)


def wire_price(side: str, own_px: Any) -> Optional[D]:
    """The price the VENUE sees for a leg quoted at `own_px` in own terms.

    THE TRANSFORMATION, IN ONE PLACE. A DOWN buy at own cost `q` IS a sell of
    YES at `1 - q`, and it rests as an ASK; an UP buy at `p` rests as a BID at
    `p`. So the 0330z plan's DOWN leg at 0.5400 and its queue row's wire price
    of 0.4600 are the same order, and `0.54 + 0.46 == 1` is the check that
    says so. Behind-the-touch for DOWN therefore means a LOWER own price and a
    HIGHER wire price: at ask 0.44 and 2 ticks, own 0.54 and wire 0.46, which
    is two ticks ABOVE the ask -- correctly away from the touch, exactly as
    `up_px = bid - 2 ticks` is.
    """
    p = _d(own_px)
    if p is None:
        return None
    return p if side == UP else (D(1) - p)


def resting_side(side: str) -> str:
    """BID for UP, ASK for DOWN -- see `wire_price`."""
    return "BID" if side == UP else "ASK"


def leg_crosses(side: str, own_px: Any, *, bid: Any, ask: Any) -> bool:
    """Would this leg cross the book, so post-only rejects it?

    A BID at or above the ask crosses; an ASK at or below the bid crosses.
    Unknowable prices are NOT called crossing -- the caller fails closed on a
    missing book long before this.
    """
    w = wire_price(side, own_px)
    b = _d(bid)
    a = _d(ask)
    if w is None:
        return False
    if side == UP:
        return a is not None and w >= a
    return b is not None and w <= b


def touch_own_price(side: str, *, bid: Any, ask: Any) -> Optional[D]:
    """Where this side's TOUCH sits in own terms.

    UP's touch is the YES bid. DOWN's is `1 - ask`, because buying DOWN means
    selling YES and the best resting sell is the ask.
    """
    b = _d(bid)
    a = _d(ask)
    if side == UP:
        return b
    return None if a is None else (D(1) - a)


def quote_own_price(side: str, *, bid: Any, ask: Any, tick: Any,
                    depth_ticks: int) -> Optional[D]:
    """Where this side would be quoted RIGHT NOW: `depth_ticks` behind its
    touch, in own terms. The same arithmetic `build_orders` uses, so a
    complement derived here and a leg planned there agree by construction."""
    t = touch_own_price(side, bid=bid, ask=ask)
    tk = _d(tick)
    if t is None or tk is None or tk <= 0:
        return None
    return t - (D(int(depth_ticks)) * tk)


def plan_executable(plan: Mapping[str, Any], *, bid: Any, ask: Any,
                    tick: Any, depth_ticks: int,
                    drift_ticks: Optional[int] = None) -> Optional[str]:
    """None if this frozen plan is still executable, else WHY it is not.

    Says nothing about whether retiring the round is SAFE -- that is the
    caller's business, and it must confirm no order is live and no leg holds a
    fill before acting on this. This function only reads prices.
    """
    tk = _d(tick)
    if tk is None or tk <= 0:
        return "TICK_UNKNOWN"
    b = _d(bid)
    a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return None            # no book to judge against: judge nothing
    if a <= b:
        return None            # crossed or locked: not the plan's fault
    legs = list(plan.get("legs") or ())
    if len(legs) != 2:
        return "PLAN_NOT_TWO_LEGS"
    allow = stale_drift_ticks() if drift_ticks is None else min(
        stale_drift_ticks(), int(drift_ticks))
    priced = []
    for leg in legs:
        side = str(leg.get("side"))
        own = _d(leg.get("price"))
        if own is None:
            return "LEG_PRICE_UNREADABLE"
        priced.append((side, own))
    # CROSSING FIRST, BOTH LEGS, BEFORE ANY DRIFT. A crossing leg is rejected
    # by the venue outright; a leg left behind is merely unable to fill. When
    # both are true -- which a large move usually makes true, since on a
    # two-cent book one side crosses long before the other drifts six ticks
    # -- the harder fact is the one worth reporting.
    for side, own in priced:
        if leg_crosses(side, own, bid=b, ask=a):
            return "LEG_WOULD_CROSS:%s" % side
    # THE DRIFT CASE IS THE SPREAD WIDENING. With a narrow book and a 2-tick
    # offset a leg cannot drift six ticks without the other crossing, so this
    # fires when the spread opens up and the complement's fair price walks
    # away while nothing crosses -- which is exactly when completing stops
    # paying.
    for side, own in priced:
        now_px = quote_own_price(side, bid=b, ask=a, tick=tk,
                                 depth_ticks=depth_ticks)
        if now_px is None:
            return None
        drift = (now_px - own) / tk
        if drift < 0:
            drift = -drift
        if drift > D(allow):
            return "PRICE_LEFT_BEHIND:%s:%s_ticks" % (
                side, drift.to_integral_value(rounding=ROUND_CEILING))
    return None


def complement_order(*, side: str, qty: Any, cost_px: Any, bid: Any, ask: Any,
                     tick: Any, min_qty: Any, depth_ticks: int,
                     book_age_s: Any = None,
                     gap_suspected: bool = False) -> Dict[str, Any]:
    """Price the SECOND leg against the book in front of us and the cost we
    ACTUALLY paid for the first.

    WHY THE FROZEN PRICE CANNOT BE USED. With one live-order slot the
    complement can never rest beside the first leg -- it is refused
    MAX_LIVE_ORDERS until that leg leaves the book -- so it is ALWAYS placed
    later, against a book that has moved. In 0330z the frozen complement's
    wire price ended 45 cents BELOW the bid: post-only, the venue rejects it
    for crossing, so it never rests and the filled leg is left unhedged.

    WHAT IS PRESERVED. The QUANTITY, because matching the filled quantity is
    what makes the pair a pair -- never more than was filled, and quantised
    DOWN to the venue grid. And the economics, judged on the REALISED cost of
    leg one plus this quote: `cost_px` is what we paid, not what we hoped to
    pay, so the floor this clears is the real one. A complement that no longer
    clears it is REFUSED rather than repriced into a loss -- the residual is
    then carried under the existing controls, which is a decision about
    economics and not about the deadline.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if side not in (UP, DOWN):
        return no("COMPLEMENT_SIDE_UNKNOWN", side=str(side))
    if gap_suspected:
        return no("GAP_SUSPECTED")
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE")
    b = _d(bid)
    a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return no("BOOK_NOT_TWO_SIDED")
    if a <= b:
        return no("BOOK_CROSSED_OR_LOCKED")
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    need = _d(qty)
    if need is None or need <= 0:
        return no("NOTHING_TO_MATCH", qty=str(qty))
    paid = _d(cost_px)
    if paid is None or paid <= 0:
        return no("FIRST_LEG_COST_UNKNOWN", cost_px=str(cost_px))

    px = quote_own_price(side, bid=b, ask=a, tick=tk, depth_ticks=depth_ticks)
    if px is None:
        return no("PRICE_UNDERIVABLE")
    if px < MIN_PRICE or px > MAX_PRICE:
        return no("PRICE_OUT_OF_BAND", price=str(px))
    if (px / tk) != (px / tk).to_integral_value():
        return no("PRICE_NOT_ON_TICK", price=str(px))
    if leg_crosses(side, px, bid=b, ask=a):
        # cannot happen from `quote_own_price` at depth_ticks >= 0, and is
        # asserted rather than assumed because post-only would reject it
        return no("COMPLEMENT_WOULD_CROSS", price=str(px))

    # match no more than was filled, on the venue's grid, rounded DOWN
    q = (need / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    if q < mq:
        return no("NOTHING_TO_MATCH_ON_GRID", need=str(need), min_qty=str(mq))

    up_px, dn_px = ((paid, px) if side == DOWN else (px, paid))
    floor = fee_floor(up_px, dn_px)
    pair = up_px + dn_px
    cap_px = max_pair_cost()
    if pair > cap_px:
        # the REALISED first leg plus this quote is what must clear the cap,
        # so a leg that filled dearer than planned tightens the complement
        return no("COMPLEMENT_PAIR_ABOVE_CAP", pair_cost=str(pair),
                  max_pair_cost=str(cap_px), first_leg_cost=str(paid),
                  complement_price=str(px))
    if not floor["ok"]:
        return no("COMPLEMENT_BELOW_FEE_FLOOR", pair_cost=str(pair),
                  edge=str(floor["edge"]),
                  net_if_maker=str(floor["net_if_maker"]),
                  net_if_taker=str(floor["net_if_taker"]),
                  first_leg_cost=str(paid), complement_price=str(px))
    econ = net_settlement(up_qty=q, dn_qty=q, up_price=up_px,
                          dn_price=dn_px, theta=MAKER_THETA)
    if not econ["both_positive"]:
        return no("COMPLEMENT_NOT_POSITIVE_ON_BOTH_OUTCOMES",
                  up_pnl=str(econ["up_pnl"]), dn_pnl=str(econ["dn_pnl"]),
                  pair_cost=str(pair))
    econ_taker = net_settlement(up_qty=q, dn_qty=q, up_price=up_px,
                                dn_price=dn_px, theta=TAKER_THETA)
    return {"ok": True, "provenance": "DERIVED_FROM_CURRENT_BOOK",
            "leg": {"side": side, "action": "BUY", "price": str(px),
                    "qty": str(q), "mode": MODE, "post_only": True,
                    "depth_ticks": int(depth_ticks),
                    "touch_at_placement": str(
                        touch_own_price(side, bid=b, ask=a)),
                    "wire_price": str(wire_price(side, px)),
                    "resting_side": resting_side(side)},
            "first_leg_cost": str(paid), "pair_cost": str(pair),
            "edge_per_contract": str(floor["edge"]),
            "fee_floor": floor,
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "up_pnl_if_taker": str(econ_taker["up_pnl"]),
            "dn_pnl_if_taker": str(econ_taker["dn_pnl"]),
            "both_positive_if_taker": bool(econ_taker["both_positive"]),
            "labels": ("priced against the CURRENT book and the REALISED cost "
                       "of the first leg. A displayed price is not a fill: "
                       "this leg may rest unfilled or fill partially, and the "
                       "residual is then carried under the existing limits.")}


# ============================================ B5: the completion-price ceiling
#
# WHAT THIS IS FOR. `completion_decision` already answers "should we cross
# right now, at the one price the book is showing?". It does not answer the
# question an authorization needs answered first: "how dear a completion is
# permissible AT ALL, given what we actually paid, the fee we would actually
# owe, the inventory we actually hold and the residual risk the owner has
# actually approved?" That ceiling is a POLICY, and a policy has to be stated
# as a number before it can be authorized. So this computes it and reports
# every binding constraint separately. It enables nothing.
#
# FOUR CEILINGS, AND THEY ARE DIFFERENT QUESTIONS.
#
#   positive_both  the completed book pays on BOTH outcomes. The strongest
#                  ceiling, and the only one that makes a completion a
#                  profit rather than a smaller loss.
#   improves       completing beats HOLDING the one-sided book. This can be
#                  satisfied where `positive_both` is not: a stranded leg
#                  bought at 0.79 is already a $3.21 worst case, and a dear
#                  completion that leaves a $0.40 worst case is better,
#                  though still a loss.
#   within_budget  the worst outcome after completing stays inside the
#                  residual-risk budget the owner approved.
#   pair_cap       the effective cost per MATCHED share stays under the
#                  spring target. This is the maker-pair discipline applied
#                  to a crossing completion; it may be the binding one.
#
# The dearest permissible price is the MINIMUM of the ceilings in force, and
# which ceilings are in force is the owner's decision, not this function's.
#
# PRICES ARE OWN-OUTCOME, on the venue tick grid, and the fee is charged at
# the TAKER coefficient because crossing is what this prices. Costs already
# paid are taken as given and NOT re-charged a fee: they are sunk, and the
# fees on them were charged when they filled.
#
# NOTHING HERE SUBMITS, CANCELS OR AUTHORIZES ANYTHING.

def max_completion_price(*, held_side: str, held_qty: Any, held_cost_usd: Any,
                         deficient_qty: Any = 0,
                         deficient_cost_usd: Any = 0,
                         complete_qty: Any,
                         residual_budget_usd: Any,
                         tick: Any = "0.01",
                         pair_cap: Any = None,
                         theta: Any = None) -> Dict[str, Any]:
    """The dearest price at which completing is permissible, and why.

    `held_*` is the side that FILLED -- exchange-confirmed quantity and the
    cash actually paid for it, fees included. `deficient_*` is whatever the
    other side already holds, which is usually nothing. `complete_qty` is how
    much of the deficit the caller intends to buy, already clipped to the
    displayed depth by the caller, because only the caller has the book.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if held_side not in (UP, DOWN):
        return no("HELD_SIDE_UNKNOWN", held_side=str(held_side))
    h = _d(held_qty)
    ch = _d(held_cost_usd)
    d0 = _d(deficient_qty)
    cd0 = _d(deficient_cost_usd)
    d = _d(complete_qty)
    budget = _d(residual_budget_usd)
    tk = _d(tick)
    if h is None or h <= 0:
        return no("NO_CONFIRMED_INVENTORY", held_qty=str(held_qty))
    if ch is None or ch < 0:
        return no("HELD_COST_UNKNOWN", held_cost_usd=str(held_cost_usd))
    if d0 is None or d0 < 0 or cd0 is None or cd0 < 0:
        return no("DEFICIENT_SIDE_UNREADABLE")
    if d is None or d <= 0:
        return no("NOTHING_TO_COMPLETE", complete_qty=str(complete_qty))
    if budget is None or budget < 0:
        # a budget we cannot read is NOT an unlimited one
        return no("RESIDUAL_BUDGET_UNKNOWN",
                  residual_budget_usd=str(residual_budget_usd))
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    cap = max_pair_cost() if pair_cap is None else _d(pair_cap)
    if cap is None or cap <= 0:
        return no("PAIR_CAP_UNKNOWN")
    th = TAKER_THETA if theta is None else _d(theta)
    if th is None:
        return no("THETA_UNKNOWN")

    sunk = ch + cd0
    # the worst outcome if we DO NOTHING and carry the one-sided book
    held_worst = min(h, d0) - sunk

    ceilings: Dict[str, Optional[D]] = {"positive_both": None,
                                        "improves": None,
                                        "within_budget": None,
                                        "pair_cap": None}
    rows = []
    q = MAX_PRICE
    # descending the grid: every constraint is monotone in q (a cheaper
    # completion is never worse), so the FIRST q that satisfies one is the
    # dearest price that satisfies it.
    while q >= MIN_PRICE:
        f = fee(th, d, q)
        cost = sunk + q * d + f
        up_after = (h + D(0)) if held_side == UP else (d0 + d)
        dn_after = (d0 + d) if held_side == UP else (h + D(0))
        worst = min(up_after - cost, dn_after - cost)
        matched = min(up_after, dn_after)
        eff_pair = (cost / matched) if matched > 0 else None
        tests = {
            "positive_both": worst > 0,
            "improves": worst > held_worst,
            "within_budget": worst >= -budget,
            "pair_cap": eff_pair is not None and eff_pair <= cap,
        }
        for name, passed in tests.items():
            if passed and ceilings[name] is None:
                ceilings[name] = q
        if all(tests.values()):
            rows.append(q)
        q -= tk

    present = {k: v for k, v in ceilings.items() if v is not None}
    dearest_all = max(rows) if rows else None
    binding = None
    if present:
        binding = min(present, key=lambda k: present[k])

    return {
        "ok": dearest_all is not None,
        "reason": None if dearest_all is not None else "NO_PERMISSIBLE_PRICE",
        "held_side": held_side, "deficient_side": (DOWN if held_side == UP
                                                   else UP),
        "held_qty": str(h), "held_cost_usd": str(sunk),
        "avg_cost_px": str((sunk / h).quantize(D("0.0001"))),
        "complete_qty": str(d),
        "residual_budget_usd": str(budget),
        "pair_cap": str(cap), "theta": str(th),
        "worst_outcome_if_held_usd": str(held_worst),
        "ceilings": {k: (None if v is None else str(v))
                     for k, v in ceilings.items()},
        "max_permissible_price": (None if dearest_all is None
                                  else str(dearest_all)),
        "binding_ceiling": binding,
        "labels": ("own-outcome prices on the venue grid, TAKER fee, costs "
                   "already paid treated as SUNK and not re-charged. A null "
                   "ceiling means NO price on the grid satisfies that "
                   "constraint. This computes a policy and authorizes "
                   "nothing: which ceilings are in force is the owner's "
                   "decision, and `max_permissible_price` is the minimum of "
                   "ALL FOUR, which is the most conservative reading."),
    }
