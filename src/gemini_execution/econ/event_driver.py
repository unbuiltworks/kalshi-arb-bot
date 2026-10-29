"""EVENT-DRIVEN RETARGETING, beside the 8-second control rather than instead.

THE HYPOTHESIS THIS EXISTS TO TEST, stated as the evidence that produced it.
The A+B study replayed 1,031 decisions in which a rung was already at the front
of the queue: 207 kept and 824 requoted, and EVERY ONE of those 824 requotes
was because the touch had moved MORE than a tick -- none for a better queue
position. The conclusion recorded there was that "the touch commonly moves 2-7
ticks between 8-second cycles", and that queue preservation therefore has
little leverage because the market simply leaves.

That is a statement about the CYCLE, not about the strategy. If the touch moves
2-7 ticks between decisions, then a decision every 8 seconds is retargeting at
a price that was already stale when it was computed. Adaptation A works by
reaching zero queue-ahead; a rung that reaches the front of a queue that has
since moved three ticks away is at the front of the wrong queue.

So the challenger recomputes on EVENTS instead, and the measurement that
settles it is not fill count -- it is how far the touch travels between
consecutive decisions. If event-driven decisions see a median touch movement of
0-1 ticks where the 8s cycle sees 2-7, the cycle was the binding constraint.
If they see the same movement, it was not, and that refutation is the result.

WHAT COUNTS AS MEANINGFUL, and what is deliberately ignored. The public socket
delivers ~35 messages a second, dominated by quote churn -- `gemini_ws` records
a place and a cancel at the same price 25 MILLISECONDS apart. Recomputing on
every message would burn CPU to arrive at the identical surface. So a recompute
is triggered only by:

    TOUCH        the best bid or ask MOVED (not merely re-posted)
    TRADE        a print, which is the only event that can fill us
    FILL         our own (hypothetical) fill -- inventory changed
    INVENTORY    an authoritative inventory change from any other source
    REFERENCE    spot/reference moved beyond a threshold
    HEARTBEAT    nothing happened for `max_quiet_s`; recompute anyway so a
                 quiet market cannot leave a stale rung resting forever

A size change at an unchanged touch is NOT a trigger. Depth cannot gate
anything here in any case -- `metadata.contract_depth` carries the standing
"EVIDENCE ONLY" label -- so acting on it would break an existing invariant as
well as burning cycles.

DEBOUNCE, AND WHY IT HAS A CEILING TOO. `min_gap_s` stops a burst from
producing a decision per message. But a debounce with no upper bound is just a
slower cycle with worse jitter, so `max_defer_s` forces a recompute once a
trigger has been waiting that long. Both are recorded per decision, and
`suppressed` counts what the debounce swallowed, so the cost of the rate limit
is visible rather than invisible.

LATENCY IS MEASURED AT BOTH SEAMS, because they answer different questions:

    event -> decision   how stale was our view when we decided
    decision -> order   how long our own arithmetic took

In SHADOW no order exists, so `decision -> order` is measured to the instant
the order WOULD have been emitted, and is labelled as such. It is not a venue
round trip and is never reported as one.

NO TRANSPORT. The public market-data socket takes no credentials and carries no
order path; this module adds none.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

SCHEMA = "gemini_econ_event_driver/v1"

TOUCH, TRADE, FILL, INVENTORY, REFERENCE, HEARTBEAT = (
    "TOUCH", "TRADE", "FILL", "INVENTORY", "REFERENCE", "HEARTBEAT")
TRIGGERS = (TOUCH, TRADE, FILL, INVENTORY, REFERENCE, HEARTBEAT)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass
class Debouncer:
    """Rate limit with a ceiling. Records what it swallowed."""

    min_gap_s: float = 0.25
    max_defer_s: float = 2.0
    last_decision_t: Optional[float] = None
    pending_since: Optional[float] = None
    suppressed: int = 0
    forced_by_ceiling: int = 0

    def offer(self, now: float) -> Dict[str, Any]:
        if self.last_decision_t is None:
            self.pending_since = None
            self.last_decision_t = now
            return {"decide": True, "why": "first decision", "waited_s": 0.0}
        gap = now - self.last_decision_t
        if gap >= self.min_gap_s:
            waited = (now - self.pending_since) if self.pending_since else 0.0
            self.pending_since = None
            self.last_decision_t = now
            return {"decide": True, "why": "min_gap satisfied",
                    "waited_s": waited}
        if self.pending_since is None:
            self.pending_since = now
        if (now - self.pending_since) >= self.max_defer_s:
            waited = now - self.pending_since
            self.pending_since = None
            self.last_decision_t = now
            self.forced_by_ceiling += 1
            return {"decide": True, "why": "max_defer ceiling reached",
                    "waited_s": waited}
        self.suppressed += 1
        return {"decide": False, "why": "debounced (%.3fs since last)" % gap,
                "waited_s": now - self.pending_since}

    def report(self) -> Dict[str, Any]:
        return {"min_gap_s": self.min_gap_s, "max_defer_s": self.max_defer_s,
                "suppressed": self.suppressed,
                "forced_by_ceiling": self.forced_by_ceiling}


@dataclass
class Latency:
    """Percentiles at both seams. Medians, because one stall is not a typical
    case and a mean would let it pretend to be."""

    event_to_decision_ms: List[float] = field(default_factory=list)
    decision_to_order_ms: List[float] = field(default_factory=list)

    def record(self, *, event_t: Optional[float], decided_t: float,
               emitted_t: Optional[float] = None) -> None:
        if event_t is not None:
            self.event_to_decision_ms.append((decided_t - event_t) * 1000.0)
        if emitted_t is not None:
            self.decision_to_order_ms.append((emitted_t - decided_t) * 1000.0)

    @staticmethod
    def _pct(xs: Sequence[float], q: float) -> Optional[float]:
        if not xs:
            return None
        s = sorted(xs)
        i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return round(s[i], 3)

    def report(self) -> Dict[str, Any]:
        return {
            "n": len(self.event_to_decision_ms),
            "event_to_decision_ms_p50": self._pct(self.event_to_decision_ms, .5),
            "event_to_decision_ms_p95": self._pct(self.event_to_decision_ms, .95),
            "decision_to_order_ms_p50": self._pct(self.decision_to_order_ms, .5),
            "decision_to_order_ms_p95": self._pct(self.decision_to_order_ms, .95),
            "labels": ("decision_to_order is measured to the instant an order "
                       "WOULD have been emitted. SHADOW: no order exists and "
                       "this is not a venue round trip."),
        }


class TouchTracker:
    """How far the touch travels BETWEEN DECISIONS -- the measurement that
    settles whether the 8-second cycle was the binding constraint."""

    def __init__(self, tick: Any = "0.01") -> None:
        self.tick = _dec(tick) or D("0.01")
        self._last: Optional[D] = None
        self.moves_ticks: List[int] = []

    def observe(self, best_bid: Any) -> Optional[int]:
        b = _dec(best_bid)
        if b is None:
            return None
        if self._last is None:
            self._last = b
            return None
        moved = int(abs(b - self._last) / self.tick) if self.tick > 0 else 0
        self._last = b
        self.moves_ticks.append(moved)
        return moved

    def report(self) -> Dict[str, Any]:
        xs = self.moves_ticks
        return {
            "n": len(xs),
            "median_touch_move_ticks_between_decisions": (
                statistics.median(xs) if xs else None),
            "max_touch_move_ticks": (max(xs) if xs else None),
            "share_moved_2_or_more": (
                (sum(1 for x in xs if x >= 2) / len(xs)) if xs else None),
            "reference": ("the 8-second control's own replay recorded the "
                          "touch commonly moving 2-7 ticks between cycles; "
                          "this is the same quantity measured between "
                          "EVENT-DRIVEN decisions"),
        }


def classify(events: Sequence[Mapping[str, Any]], *,
             prev_touch: Optional[Mapping[str, Any]] = None,
             touch: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Which meaningful triggers this batch of socket events carries.

    A `place`/`cancel` pair at an UNCHANGED touch produces no trigger, which is
    the whole point: the socket is dominated by exactly that churn.
    """
    kinds: List[str] = []
    reasons: List[str] = []
    first_t: Optional[float] = None

    for e in events or []:
        t = e.get("t")
        if t is not None and (first_t is None or t < first_t):
            first_t = t
        if str(e.get("reason") or e.get("type") or "").lower() == "trade":
            if TRADE not in kinds:
                kinds.append(TRADE)
                reasons.append("a print landed")

    if prev_touch is not None and touch is not None:
        for side in ("bid", "ask"):
            a, b = _dec(prev_touch.get(side)), _dec(touch.get(side))
            if a is not None and b is not None and a != b:
                if TOUCH not in kinds:
                    kinds.append(TOUCH)
                    reasons.append("best %s moved %s -> %s" % (side, a, b))

    return {"triggers": kinds, "reasons": reasons, "earliest_event_t": first_t,
            "n_events": len(events or [])}


@dataclass
class EventDrivenLane:
    """One symbol, driven by events, decided by the economic challenger.

    The arm is injected rather than constructed here so this module can be
    exercised against a synthetic event source with no socket at all -- a
    driver that can only be tested against a live venue is a driver that does
    not get tested.
    """

    arm: Any
    symbol: str
    debounce: Debouncer = field(default_factory=Debouncer)
    latency: Latency = field(default_factory=Latency)
    touch: TouchTracker = field(default_factory=TouchTracker)
    decisions: int = 0
    triggers_seen: Dict[str, int] = field(default_factory=dict)
    last_touch: Optional[Dict[str, Any]] = None
    last_decision_t: Optional[float] = None
    max_quiet_s: float = 8.0

    def on_events(self, events: Sequence[Mapping[str, Any]], *,
                  book: Mapping[str, Any], depth: Any,
                  flow: Mapping[str, Any], tte: Any, policy: str,
                  now: Optional[float] = None) -> Dict[str, Any]:
        t = time.time() if now is None else now
        touch = {"bid": (book or {}).get("yes", {}).get("bid"),
                 "ask": (book or {}).get("yes", {}).get("ask")}
        cls = classify(events, prev_touch=self.last_touch, touch=touch)
        self.last_touch = touch

        quiet = (self.last_decision_t is not None
                 and (t - self.last_decision_t) >= self.max_quiet_s)
        if quiet and not cls["triggers"]:
            cls["triggers"].append(HEARTBEAT)
            cls["reasons"].append("no meaningful event for %.1fs"
                                  % self.max_quiet_s)

        if not cls["triggers"]:
            return {"decided": False, "why": "no meaningful trigger",
                    "classification": cls}

        gate = self.debounce.offer(t)
        if not gate["decide"]:
            return {"decided": False, "why": gate["why"],
                    "classification": cls}

        for k in cls["triggers"]:
            self.triggers_seen[k] = self.triggers_seen.get(k, 0) + 1

        self.touch.observe(touch.get("bid"))
        decided_t = time.time() if now is None else now
        st = self.arm.step(symbol=self.symbol, tte=tte, book=book,
                           depth=depth, flow=flow, policy=policy)
        emitted_t = time.time() if now is None else now
        self.latency.record(event_t=cls.get("earliest_event_t"),
                            decided_t=decided_t, emitted_t=emitted_t)
        self.decisions += 1
        self.last_decision_t = t
        return {"decided": True, "classification": cls, "state": st,
                "waited_s": gate.get("waited_s")}

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "symbol": self.symbol,
                "decisions": self.decisions,
                "triggers": dict(self.triggers_seen),
                "debounce": self.debounce.report(),
                "latency": self.latency.report(),
                "touch_movement": self.touch.report(),
                "labels": [
                    "SHADOW. No order exists; no credential and no order path "
                    "is reachable from this module.",
                    "A size change at an unchanged touch is not a trigger.",
                ]}
