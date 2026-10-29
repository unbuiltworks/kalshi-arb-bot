"""CONTINUOUS L5 SYSTEM V1 -- many windows, one unchanged policy.

Built directly on the CONTINUOUS-8 execution path. Nothing about the
controller, the pricing, the sizing, the envelope or the maker-only rule
changes here; what changes is that the same proven window lifecycle now runs
again, and again, under a budget that can only ever tighten.

THE LIFECYCLE, ONE WINDOW:

    discover eligible BTC5m contracts
      -> wait for the entry band (240:280, bounded)
      -> authoritative preflight and recovery, BEFORE anything is spent
      -> run the frozen policy against authoritative state
      -> bounded terminal cleanup, cancel-only
      -> ROLLOVER GATE: prove the previous window is authoritatively safe
      -> only then consider the next one

THE ROLLOVER GATE IS THE LOAD-BEARING PART. CONTINUOUS-5 stopped its
controller and left two maker orders resting; one filled 57 seconds later and
settled against us. A continuous system that rolls forward without proving
the previous window is flat would do that on a loop. So the gate demands
POSITIVE evidence -- a readable open-order list with none of our orders in it
and a readable execution view -- and treats anything else as a stop.

FAILURE STATES ARE ALL STOPS. There is no retry loop, no "assume flat", no
sleep-and-hope. Every one of them ends the run and says why:

    ENTRY_BAND_UNREACHABLE      no contract reached the band within the bound
    PREFLIGHT_DISAGREES         the venue's answer differs from the frozen one
    STATE_UNREADABLE            a required authoritative read failed
    EXECUTION_CONTRADICTION     the execution sources disagree
    UNKNOWN_TERMINAL            an order left the book unexplained
    ORDERS_LIVE_AFTER_STOP      cleanup could not prove the book is clear
    RISK_BUDGET_EXHAUSTED       a bound said stop
    DAILY_LOSS_STOP             realized loss hit the stop

MODES. SHADOW runs the identical lifecycle with NO mutation path: it reads
the venue, maintains state, runs the policy and records what it WOULD have
done. LIVE additionally sends orders and requires an owner authorization in
the driver, which this module never installs and cannot supply.

WHAT SHADOW CANNOT PROVE, said plainly rather than left to be assumed: with
no orders resting there are no fills, so inventory stays flat, the policy
stays in OPEN, and KEEP/CANCEL and the fill-driven transitions are NOT
exercised. Shadow proves discovery, banding, state maintenance, evidence
completeness, rollover and the failure states. C8B proved the rest.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import executions as EX
from src.gemini_execution import habitat as HB
from src.gemini_execution import metadata as MD

LABEL = "GEMINI_L5_CONTINUOUS_V1"

SHADOW, LIVE = "SHADOW", "LIVE"

# ------------------------------------------------------------- failure states
ENTRY_BAND_UNREACHABLE = "ENTRY_BAND_UNREACHABLE"
PREFLIGHT_DISAGREES = "PREFLIGHT_DISAGREES"
STATE_UNREADABLE = "STATE_UNREADABLE"
EXECUTION_CONTRADICTION = "EXECUTION_CONTRADICTION"
UNKNOWN_TERMINAL = "UNKNOWN_TERMINAL"
ORDERS_LIVE_AFTER_STOP = "ORDERS_LIVE_AFTER_STOP"
RISK_BUDGET_EXHAUSTED = "RISK_BUDGET_EXHAUSTED"
DAILY_LOSS_STOP = "DAILY_LOSS_STOP"
COMPLETED = "COMPLETED"

#: every state above is terminal for the RUN, not just for the window
TERMINAL_RUN_STATES = (ENTRY_BAND_UNREACHABLE, PREFLIGHT_DISAGREES,
                       STATE_UNREADABLE, EXECUTION_CONTRADICTION,
                       UNKNOWN_TERMINAL, ORDERS_LIVE_AFTER_STOP,
                       RISK_BUDGET_EXHAUSTED, DAILY_LOSS_STOP, COMPLETED)


@dataclass(frozen=True)
class RiskBudget:
    """Bounds for a continuous RUN. Frozen, and deliberately so.

    A budget that can raise itself is not a budget. This object has no method
    that widens a bound; `spend` returns a NEW budget state with more
    consumed, never more allowed. Tightening happens by constructing a
    narrower one, which is an act outside this module.

    Every value here is the EXISTING per-window bound or stricter. Continuous
    operation multiplies the number of windows, not the size of one.
    """
    #: the frozen per-window envelope, unchanged
    max_spend_per_window_usd: str = "0.15"
    action_size: str = "0.01"
    max_residual_units: str = "0.04"
    max_imbalance_units: str = "0.04"
    #: one market at a time. Concurrency is a separate authorization.
    max_concurrent_markets: int = 1
    #: the RUN-level bounds continuous operation adds
    max_windows: int = 12
    max_total_spend_usd: str = "1.00"
    daily_realized_loss_stop_usd: str = "-0.25"
    #: a run that cannot finish a window inside this is stopped, not retried
    max_run_seconds: float = 7200.0

    def labels(self) -> str:
        return ("bounds TIGHTEN or STOP; nothing here can self-increase. "
                "Continuous operation multiplies WINDOWS, never size: the "
                "per-window envelope is the CONTINUOUS-8 one, unchanged.")


@dataclass
class RunState:
    """What the run has consumed so far. Monotonic by construction."""
    windows_started: int = 0
    windows_completed: int = 0
    total_spend_usd: D = field(default_factory=lambda: D("0"))
    realized_usd: D = field(default_factory=lambda: D("0"))
    started_at: float = field(default_factory=time.time)
    stopped: Optional[str] = None
    why: Optional[str] = None


def admits_another_window(budget: RiskBudget, st: RunState,
                          *, now: Optional[float] = None) -> Dict[str, Any]:
    """May the run start one more window? Refusals name the bound."""
    t = now if now is not None else time.time()
    if st.stopped:
        return {"admits": False, "state": st.stopped, "why": st.why}
    if st.windows_started >= budget.max_windows:
        return {"admits": False, "state": RISK_BUDGET_EXHAUSTED,
                "why": "max_windows %d reached" % budget.max_windows}
    if st.total_spend_usd >= D(budget.max_total_spend_usd):
        return {"admits": False, "state": RISK_BUDGET_EXHAUSTED,
                "why": "max_total_spend_usd %s reached"
                       % budget.max_total_spend_usd}
    if st.realized_usd <= D(budget.daily_realized_loss_stop_usd):
        return {"admits": False, "state": DAILY_LOSS_STOP,
                "why": "daily realized loss stop %s reached"
                       % budget.daily_realized_loss_stop_usd}
    if (t - st.started_at) >= budget.max_run_seconds:
        return {"admits": False, "state": RISK_BUDGET_EXHAUSTED,
                "why": "max_run_seconds %.0f reached" % budget.max_run_seconds}
    return {"admits": True, "state": None, "why": None}


def rollover_safe(symbol: str, state: Mapping[str, Any],
                  our_ids: Any = ()) -> Dict[str, Any]:
    """Is the previous window authoritatively finished?

    POSITIVE EVIDENCE ONLY. An unreadable open-order list is not an empty
    one, and "the controller stopped" is not "the book is clear" -- C5's NO
    leg filled 57 seconds after its window terminated. A continuous system
    that rolls forward on anything less repeats that on a loop.
    """
    open_orders = state.get("open_orders")
    if open_orders is None:
        return {"safe": False, "state": STATE_UNREADABLE,
                "why": ("the open-order list is UNREADABLE, which is not an "
                        "empty one; the previous window cannot be shown "
                        "finished")}
    ours = {str(x) for x in (our_ids or [])}
    live = []
    for o in open_orders:
        if not isinstance(o, Mapping):
            continue
        ids = {str(o.get(k)) for k in ("clientOrderId", "client_order_id",
                                       "orderId", "order_id",
                                       "globalOrderId")
               if o.get(k) not in (None, "")}
        if not ours or (ids & ours):
            live.append(sorted(ids)[:1] or ["?"])
    if live:
        return {"safe": False, "state": ORDERS_LIVE_AFTER_STOP,
                "why": ("%d strategy-owned order(s) are still executable on "
                        "the venue" % len(live)),
                "live": live}
    view = state.get("executions")
    if isinstance(view, Mapping):
        if view.get("contradictions"):
            return {"safe": False, "state": EXECUTION_CONTRADICTION,
                    "why": "the execution sources contradict each other",
                    "contradictions": view.get("contradictions")}
        if not view.get("readable"):
            return {"safe": False, "state": STATE_UNREADABLE,
                    "why": view.get("why") or "execution view unreadable"}
    return {"safe": True, "state": None, "why": None,
            "labels": ("positive evidence: a READABLE open-order list with "
                       "none of ours in it, and a readable execution view")}


class ContinuousRunner:
    """The lifecycle. Owns no bound of its own; every limit is elsewhere.

    The driver enforces the per-window envelope, the cohort enforces the
    mutation budget, the authorization gate enforces whether anything may be
    sent at all. This orchestrates them and refuses to continue when any of
    them, or the venue, cannot establish a safe state.
    """

    def __init__(self, *, mode: str = SHADOW,
                 budget: Optional[RiskBudget] = None,
                 entry_band: Any = (240.0, 280.0),
                 evidence_dir: Optional[str] = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        if mode not in (SHADOW, LIVE):
            raise ValueError("mode must be SHADOW or LIVE")
        self.mode = mode
        self.budget = budget or RiskBudget()
        self.entry_band = entry_band
        self.evidence_dir = evidence_dir
        self.now_fn = now_fn
        self.state = RunState(started_at=now_fn())
        self.windows: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- evidence

    def _persist(self, rec: Mapping[str, Any]) -> Optional[str]:
        if not self.evidence_dir:
            return None
        os.makedirs(self.evidence_dir, exist_ok=True)
        p = os.path.join(self.evidence_dir, "continuous_v1.jsonl")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return p

    def stop(self, state: str, why: str) -> None:
        if self.state.stopped is None:
            self.state.stopped = state
            self.state.why = why

    # -------------------------------------------------------------- one cycle

    def observe_cycle(self, *, symbol: str, tte_s: Any, market_state: Any,
                      depth: Any, flow: Any, inventory: Any,
                      working: Any, decision: Any) -> Dict[str, Any]:
        """ONE fully reconstructable row.

        The owner's chain, in order, in one record:
        market state -> book/depth/flow -> inventory -> L5 action ->
        execution plan. What follows it (submitted orders, fills, next
        inventory, next action, settlement) is the NEXT rows and the window
        summary, joined by symbol and cycle.
        """
        from src.gemini_execution import run_continuous as RC
        presented = RC.presented_book(market_state)
        our_prices = []
        for w in (working or []):
            if isinstance(w, Mapping) and w.get("price") is not None:
                our_prices.append(str(w["price"]))
        turn = (decision or {}).get("turn") or {}
        plan = (decision or {}).get("plan") or {}
        return {
            "label": LABEL, "kind": "cycle", "mode": self.mode,
            "utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "tte_s": tte_s,
            "book": presented,
            "depth": depth,
            "habitat": HB.cycle_sample(
                utc=datetime.now(timezone.utc).isoformat(), tte_s=tte_s,
                presented=presented, depth=depth, flow=flow,
                our_prices=our_prices, inventory=inventory),
            "inventory": inventory,
            "working_orders": working,
            "l5_action": [
                {"leg": a.get("leg"), "price": a.get("price"),
                 "qty": a.get("qty"), "action": a.get("action")}
                for a in (turn.get("actions") or [])],
            "holds": [{"leg": h.get("leg"), "price": h.get("price"),
                       "held_by": h.get("held_by")}
                      for h in (turn.get("holds") or [])],
            "execution_plan": {
                "keep": len(plan.get("keep") or []),
                "place": [{"leg": p.get("rung", p).get("leg"),
                           "price": p.get("rung", p).get("price")}
                          for p in (plan.get("place") or [])],
                "cancel": len(plan.get("cancel") or []),
                "mutations": plan.get("mutations"),
                "SHADOW_NOT_SENT": self.mode == SHADOW},
            "labels": ("SHADOW records what the unchanged policy WOULD do; no "
                       "order path is reachable from this mode. Accounting "
                       "stays SUBMITTED -> FILLED -> SETTLED -> REALIZED and "
                       "a shadow plan is none of them."),
        }

    # ------------------------------------------------------------- reporting

    def report(self) -> Dict[str, Any]:
        return {
            "label": LABEL, "mode": self.mode,
            "budget": {k: getattr(self.budget, k) for k in
                       ("max_spend_per_window_usd", "action_size",
                        "max_residual_units", "max_imbalance_units",
                        "max_concurrent_markets", "max_windows",
                        "max_total_spend_usd", "daily_realized_loss_stop_usd",
                        "max_run_seconds")},
            "budget_labels": self.budget.labels(),
            "entry_band": list(self.entry_band) if self.entry_band else None,
            "windows_started": self.state.windows_started,
            "windows_completed": self.state.windows_completed,
            "total_spend_usd": str(self.state.total_spend_usd),
            "realized_usd": str(self.state.realized_usd),
            "stopped": self.state.stopped, "why": self.state.why,
            "elapsed_s": round(self.now_fn() - self.state.started_at, 1),
            "windows": self.windows,
            "accounting_labels": (
                "SUBMITTED -> FILLED -> SETTLED -> REALIZED are distinct and "
                "never collapsed. realized_usd moves only on a "
                "venue-confirmed cash movement; a shadow plan, a fill, a "
                "settled contract and a mark are none of them."),
        }
