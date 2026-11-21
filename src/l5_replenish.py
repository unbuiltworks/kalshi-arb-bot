"""Bounded, event-driven replenishment of the opening slot.

WHAT THIS IS FOR. `maybe_controller_open` is slot-gated and has NO cadence of
its own: it runs on every tick and refuses only while
`still_fillable_order_count() >= FID.ceiling("concurrent_opening_orders")`.
So the engine already re-opens once a slot frees -- but it re-opens because a
timer came round again, not because anything happened, and nothing counts how
many times it did. There is no record tying a re-open to the fill or the
cancellation that freed the slot, and no bound on how many re-opens one window
may spend.

WHAT THIS CHANGES. Replenishment becomes a CREDIT that a confirmed fill or a
terminal cancellation issues, and that one opening spends. The window gets one
free opening to start from; after that an opening needs a credit. Credits are
capped per window by `max_rounds()`, which is clamped DOWNWARD against
`MAX_ROUNDS_CEILING` so the environment can only ever tighten it.

SO THIS IS A RESTRICTION, NOT A RELAXATION, and that is deliberate. The
capability requirement is that re-opening be driven by events and bounded;
unbounded per-tick retry is neither. Nothing here can place an order, price
one, size one, choose a side, or widen any limit -- it answers one question
("may an opening be attempted now?") with False more often than the bare slot
gate does, never with True where the slot gate said no.

DEFAULT OFF, AND THE DEFAULT IS WHAT KEEPS PARITY. The runner consults this
only when `l5_dual_quote.replenish_enabled(window_start)` is true, which needs
BOTH `L5_DUAL_QUOTE` and `L5_DUAL_REPLENISH` set to their exact tokens. With
production's `L5_DUAL_REPLENISH=OFF` the ledger is never asked anything and the
opening path behaves exactly as it does today.

WHAT A CREDIT IS NOT. A credit is not a fill, not a pair, and not profit. A
TERMINAL_CANCEL credit is issued precisely because NOTHING filled. Counting
credits measures how often the engine got to ask, never how often it was
right to.
"""
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

#: a window may never spend more replenishment rounds than this, whatever the
#: environment says. Pinned to the same number as the executor's
#: ROUND_ALLOCATIONS_MAX: a fifth round has no submission budget allocated to
#: it, so permitting one here would only manufacture a refusal downstream.
MAX_ROUNDS_CEILING = 4

ROUNDS_ENV = "L5_REPLENISH_MAX_ROUNDS"

#: the two events that free a slot, and the only two that issue a credit.
#: A confirmed fill is a positive booked delta; a terminal cancellation is a
#: venue-terminal order that booked nothing.
FILL = "FILL"
TERMINAL_CANCEL = "TERMINAL_CANCEL"
KINDS: Tuple[str, ...] = (FILL, TERMINAL_CANCEL)

#: NOT TOUCHED HERE, and asserted disjoint from anything this module reads.
#: Replenishment changes WHEN an opening may be attempted. Whether it is
#: allowed remains entirely the business of the gates below.
SAFETY: Tuple[str, ...] = (
    "max_total_exposure_usd", "max_cash_deployed_usd", "max_usd_per_market",
    "max_abs_residual", "ABS_RESIDUAL_CAP_CONTRACTS",
    "max_unmatched_contracts", "max_worst_case_deficit_usd",
    "max_cumulative_loss_usd", "daily_realized_loss_halt_usd",
    "campaign_additional_loss_usd", "max_concurrent_markets",
    "max_live_orders", "completion_pair_ceiling", "max_pair_cost",
    "min_first_leg_own_price", "max_first_leg_own_price",
    "shadow_lock", "owner_arm_token", "halt_latch", "L5_LIVE_HALT",
    "reconciliation", "exact_order_cancellation", "watchdog_deadman",
    "authorization_expiry", "campaign_approval", "submission_class_budgets",
    "entry_cutoff_s", "complete_cutoff_s",
)


def max_rounds() -> int:
    """Replenishment rounds one window may spend. DOWNWARD-ONLY.

    An unreadable or absent value is the ceiling, which is today's effective
    behaviour; a value above the ceiling is the ceiling. A value of zero is
    honoured and means "no replenishment", because refusing to trade is always
    a safe reading of an operator's number.
    """
    raw = (os.getenv(ROUNDS_ENV) or "").strip()
    if not raw:
        return MAX_ROUNDS_CEILING
    try:
        n = int(raw)
    except ValueError:
        return MAX_ROUNDS_CEILING
    if n < 0:
        return 0
    return min(MAX_ROUNDS_CEILING, n)


def _qty(v: Any) -> D:
    try:
        q = D(str(v))
    except (InvalidOperation, ArithmeticError, ValueError, TypeError):
        return D(0)
    if not q.is_finite() or q < 0:
        return D(0)
    return q


class Ledger:
    """Per-window replenishment credits. Pure bookkeeping, no I/O.

    One instance lives on the Runner for the life of the process and rolls
    itself whenever it is asked about a different window, so a restart or a
    window rollover can never inherit another window's credits.
    """

    def __init__(self) -> None:
        self._window: Optional[float] = None
        self._issued: int = 0
        self._spent: int = 0
        self._opened: int = 0
        self._events: List[Dict[str, Any]] = []

    # ---- window identity -------------------------------------------------
    def _key(self, window_start: Any) -> Optional[float]:
        try:
            w = float(window_start)
        except (TypeError, ValueError):
            return None
        return w if w == w else None            # NaN is not a window

    def roll(self, window_start: Any) -> bool:
        """Adopt `window_start`, discarding another window's credits.

        Returns True when the window changed. An unreadable window start does
        NOT roll and does not clear: a reading we cannot trust must not be
        able to hand the current window a fresh allowance.
        """
        w = self._key(window_start)
        if w is None or w == self._window:
            return False
        self._window = w
        self._issued = 0
        self._spent = 0
        self._opened = 0
        self._events = []
        return True

    # ---- credit issuance -------------------------------------------------
    def note(self, kind: str, *, market: Any = None, qty: Any = 0,
             window_start: Any = None, t: Any = None) -> bool:
        """Record a slot-freeing event. Returns True when a credit was issued.

        Only FILL and TERMINAL_CANCEL issue credits, and only up to
        `max_rounds()`. An unknown kind is recorded and issues nothing, so a
        future event type cannot silently widen replenishment.
        """
        if window_start is not None:
            self.roll(window_start)
        k = str(kind or "").upper()
        cap = max_rounds()
        issued = False
        if k in KINDS and self._issued < cap:
            self._issued += 1
            issued = True
        if len(self._events) < 256:
            self._events.append({
                "kind": k, "market": (str(market) if market else None),
                "qty": str(_qty(qty)), "t": (None if t is None else float(t)),
                "credit_issued": bool(issued),
                "reason": ("ISSUED" if issued else
                           ("UNKNOWN_KIND" if k not in KINDS
                            else "ROUND_CEILING")),
            })
        return issued

    def note_progress(self, event: Any, *, window_start: Any = None,
                      t: Any = None) -> bool:
        """Classify one lifecycle row and credit it. Returns True if credited.

        A POSITIVE BOOKED DELTA IS THE FILL, exactly as `lifecycle` already
        reads it for fill evidence -- a zero-delta progress poll saw nothing
        new and is not a fill. A terminal row that booked nothing is the
        cancellation. A terminal row that DID book is credited once, as a
        fill, because one freed slot is one credit however it was freed.
        """
        try:
            row = dict(event or {})
        except (TypeError, ValueError):
            return False
        delta = _qty(row.get("delta"))
        terminal = bool(row.get("terminal"))
        if delta > 0:
            return self.note(FILL, market=row.get("market"), qty=delta,
                             window_start=window_start, t=t)
        if terminal:
            return self.note(TERMINAL_CANCEL, market=row.get("market"),
                             qty=D(0), window_start=window_start, t=t)
        return False

    # ---- spending --------------------------------------------------------
    def may_open(self, window_start: Any = None) -> bool:
        """Is an opening attempt permitted by replenishment accounting?

        The window's FIRST opening needs no credit: there has been no fill and
        no cancellation yet, so requiring one would mean never opening at all.
        Every later opening needs an unspent credit.
        """
        if window_start is not None:
            self.roll(window_start)
        if self._opened <= 0:
            return True
        return self._spent < self._issued

    def spend(self, window_start: Any = None) -> bool:
        """Charge one opening. Returns True when a credit was consumed.

        Called ONLY after an opening was actually attempted, so a refused
        decision that never reached the executor cannot burn an allowance.
        """
        if window_start is not None:
            self.roll(window_start)
        self._opened += 1
        if self._opened <= 1:
            return False                        # the free first opening
        if self._spent < self._issued:
            self._spent += 1
            return True
        return False

    # ---- reporting -------------------------------------------------------
    def refuse_reason(self) -> str:
        if self._issued <= 0:
            return "NO_REPLENISH_EVENT_YET"
        if self._spent >= self._issued:
            return ("REPLENISH_ROUNDS_EXHAUSTED"
                    if self._issued >= max_rounds()
                    else "REPLENISH_CREDITS_SPENT")
        return "PERMITTED"

    def telemetry(self) -> Dict[str, Any]:
        return {
            "window_start": self._window,
            "credits_issued": self._issued,
            "credits_spent": self._spent,
            "openings_attempted": self._opened,
            "max_rounds": max_rounds(),
            "ceiling": MAX_ROUNDS_CEILING,
            "env": ROUNDS_ENV,
            "may_open": self.may_open(),
            "refuse_reason": self.refuse_reason(),
            "events": list(self._events[-16:]),
            "safety_never_touched_here": list(SAFETY),
            "labels": ("A credit is issued by a confirmed fill or a terminal "
                       "cancellation and spent by one opening attempt. A "
                       "credit is NOT a fill, NOT a pair and NOT profit: a "
                       "TERMINAL_CANCEL credit exists precisely because "
                       "nothing filled. This module can only refuse an "
                       "opening the slot gate would have allowed; it can "
                       "never permit one the slot gate refused."),
        }


# SAFETY must name nothing this module reads, or the claim above is false.
assert not (set(SAFETY) & {ROUNDS_ENV, "max_rounds", "MAX_ROUNDS_CEILING"}), \
    "l5_replenish SAFETY overlaps a knob this module reads"
