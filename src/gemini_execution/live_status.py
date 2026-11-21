"""GEMINI_LIVE_STATUS_V1 -- durable per-cycle status, written as it happens.

WHY THIS EXISTS. Every cohort so far could only be read AFTER the job ended.
COHORT-1 ran for 57 minutes and the first thing anyone could say about window 4
was said at 03:02Z, when the artifacts were committed. When the owner asked
mid-window why Gemini showed no resting orders, the honest answer was that the
window's own record did not exist yet. That is not an acceptable way to run a
20-window cohort.

OBSERVABILITY ONLY, AND THE CONTRACT IS ABSOLUTE:

  * it is fed from `DecisionObserver`, which already wraps `decide`, `place`
    and `cancel` and is explicitly documented as leaving `executable_sha`
    unmoved. Nothing here is a bundle member and nothing here touches the
    driver;
  * it is called AFTER the decision has been made and AFTER the mutation has
    returned, so it cannot sit between a decision and its order;
  * every write is wrapped. A status writer that can raise is a status writer
    that can stop a live window, which is strictly worse than no status at
    all. A failed write is recorded in the row and never propagated;
  * it reads nothing from the venue. There is no extra request, no extra
    latency out of maker resting time, and no new failure mode on the wire.

THE FILE IS REPLACED ATOMICALLY (tmp + fsync + os.replace), so a reader always
sees a whole JSON document, never a half-written one -- and a HISTORY line is
appended to a JSONL beside it, so the sequence survives even though the
snapshot does not.

LABELS ARE NOT RELAXED HERE. `window_settlement_implied_net_usd` is populated
only once the venue has published the contract's resolution, and it says
IMPLIED in its own name. `realized_net_usd` stays null: this file has no
access to an attributable cashflow and never invents one.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

LABEL = "GEMINI_LIVE_STATUS_V1"

#: the per-market ordinary strategy budget. Cleanup/safety cancellation is a
#: SEPARATE budget and is deliberately not counted here.
ACTIONS_PER_MARKET = 12


def _g(m: Any, *path: str, default: Any = None) -> Any:
    cur = m
    for k in path:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _quote(side: Mapping[str, Any]) -> Dict[str, Any]:
    return {"bid": _g(side, "bid"), "ask": _g(side, "ask"),
            "spread": _g(side, "spread")}


def cycle_row(cycle: Mapping[str, Any]) -> Dict[str, Any]:
    """Flatten ONE observer cycle into the fields a human watches.

    Everything below is copied out of the row the observer already built. No
    value is derived from a second source, so this can differ from the window
    report only by being shorter.
    """
    pres = _g(cycle, "book_state", "presented", default={}) or {}
    inv = cycle.get("inventory_before_decision") or {}
    plan = cycle.get("plan") or {}
    readable = bool(pres.get("readable"))
    # A REFUSED BOOK IS NOT AN EMPTY ONE. The prices the venue published are
    # kept under `refused_prices` precisely so the refusal can be audited;
    # they are shown as refused and no spread is derived from them.
    src = (pres if readable else (pres.get("refused_prices") or {}))
    return {
        "cycle": cycle.get("cycle"),
        "utc": cycle.get("utc"),
        "tte_s": cycle.get("tte_s"),
        "state": cycle.get("state"),
        "window_phase": cycle.get("window_phase"),
        "book_readable": readable,
        "book_why": (None if readable
                     else (pres.get("why") or pres.get("state_verdict_why"))),
        "yes": _quote(src.get("yes") or {}),
        "no": _quote(src.get("no") or {}),
        "passive_pair_cost": pres.get("passive_pair_cost"),
        # THE DESIRED SURFACE, which is what the controller wanted -- not what
        # it has. `resting` below is what the venue is actually holding.
        "desired": [{"outcome": _g(r, "outcome"), "price": _g(r, "price"),
                     "qty": _g(r, "qty") or _g(r, "quantity")}
                    for r in (plan.get("place") or [])],
        "actions": cycle.get("classification") or [],
        "held_by": cycle.get("held_by") or [],
        "unreadable_hold": _g(cycle, "unreadable_hold", "verdict"),
        "resting": [{"outcome": _g(o, "outcome"), "price": _g(o, "price"),
                     "qty": _g(o, "qty") or _g(o, "quantity")}
                    for o in (cycle.get("working_orders_before") or [])],
        "n_resting": len(cycle.get("working_orders_before") or []),
        "inventory": {
            "yes_qty": inv.get("yes_qty"), "no_qty": inv.get("no_qty"),
            "n_fills": inv.get("n_fills"),
            "matched_qty": inv.get("matched_qty"),
            "effective_matched_pair_cost":
                inv.get("effective_matched_pair_cost"),
            "directional_residual": inv.get("directional_residual"),
            "inventory_imbalance": inv.get("inventory_imbalance"),
            "same_side_run": inv.get("same_side_run"),
            "gross_fill_spend_usd": inv.get("total_spend_usd"),
            "labels": ("gross_fill_spend_usd is cash OUT from confirmed "
                       "fills. It is TURNOVER, never a loss."),
        },
        "actions_used": _g(cycle, "budget_after_decision", "consumed",
                           default=_g(cycle, "budget_before_decision",
                                      "consumed")),
        "actions_of": ACTIONS_PER_MARKET,
    }


class StatusWriter:
    """One live snapshot file per cohort, plus an append-only history.

    `path` is replaced atomically on every update; `path + '.jsonl'` gains one
    line per update. Both are best-effort: `errors` counts what could not be
    written and nothing is ever raised at the caller.
    """

    def __init__(self, path: str, *, cohort: Optional[Mapping[str, Any]] = None,
                 history: bool = True) -> None:
        self.path = path
        self.history_path = (path + ".jsonl") if history else None
        self.cohort: Dict[str, Any] = dict(cohort or {})
        self.window: Dict[str, Any] = {}
        self.last: Dict[str, Any] = {}
        self.updates = 0
        self.errors: List[str] = []

    # ------------------------------------------------------------- mutators

    def set_cohort(self, **kw: Any) -> None:
        self.cohort.update({k: v for k, v in kw.items()})
        self._write()

    def begin_window(self, *, window_index: int, symbol: str,
                     cohort_id: Optional[str] = None, **kw: Any) -> None:
        self.window = {"window_index": window_index, "symbol": symbol,
                       "cohort_id": cohort_id,
                       "started_utc": datetime.now(timezone.utc).isoformat(),
                       "cycles_seen": 0, "mutations_seen": 0,
                       "stop_reason": None,
                       "settlement_implied_net_usd": None, **kw}
        self.last = {}
        self._write()

    def end_window(self, *, stop_reason: Optional[str] = None,
                   settlement_implied_net_usd: Any = None,
                   economics: Optional[Mapping[str, Any]] = None,
                   **kw: Any) -> None:
        self.window.update({
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "stop_reason": stop_reason,
            # IMPLIED, and null until the venue has published a resolution.
            # This writer has no cashflow and never claims one.
            "settlement_implied_net_usd": settlement_implied_net_usd,
            "economics": dict(economics or {}), **kw})
        self._write()

    def observe_cycle(self, cycle: Mapping[str, Any]) -> None:
        try:
            self.last = cycle_row(cycle)
        except Exception as exc:                              # noqa: BLE001
            self.errors.append("cycle_row: %s: %s" % (type(exc).__name__, exc))
            self.last = {"cycle": cycle.get("cycle"),
                         "row_error": str(exc)}
        self.window["cycles_seen"] = int(
            self.window.get("cycles_seen") or 0) + 1
        self._write()

    def observe_mutation(self, mutation: Mapping[str, Any]) -> None:
        self.window["mutations_seen"] = int(
            self.window.get("mutations_seen") or 0) + 1
        self.window["last_mutation"] = {
            "seq": mutation.get("mutation_seq"), "kind": mutation.get("kind"),
            "cycle": mutation.get("cycle"), "utc": mutation.get("utc"),
            "error": mutation.get("error")}
        self._write()

    # -------------------------------------------------------------- readers

    def snapshot(self) -> Dict[str, Any]:
        return {
            "label": LABEL,
            "utc": datetime.now(timezone.utc).isoformat(),
            "updates": self.updates,
            "cohort": dict(self.cohort),
            "window": dict(self.window),
            "cycle": dict(self.last),
            "write_errors": list(self.errors[-5:]),
            "labels": (
                "OBSERVABILITY ONLY. Written after each decision and after "
                "each mutation returns; it cannot sit between a decision and "
                "its order, makes no venue read of its own, and changes no "
                "timing. settlement_implied_net_usd is IMPLIED and null "
                "until the venue publishes a resolution; nothing here is "
                "REALIZED, which requires an attributable cash movement."),
        }

    # -------------------------------------------------------------- writing

    def _write(self) -> None:
        self.updates += 1
        snap = self.snapshot()
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snap, fh, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception as exc:                              # noqa: BLE001
            self.errors.append("snapshot: %s: %s" % (type(exc).__name__, exc))
        if not self.history_path:
            return
        try:
            with open(self.history_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(snap, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as exc:                              # noqa: BLE001
            self.errors.append("history: %s: %s" % (type(exc).__name__, exc))


def sink_for(writer: Optional[StatusWriter]) -> Optional[Callable]:
    """The callback `DecisionObserver` calls. None in, None out.

    It dispatches on the record kind rather than taking two callbacks, so the
    observer holds exactly one optional attribute and a run with no writer
    pays nothing.
    """
    if writer is None:
        return None

    def sink(kind: str, row: Mapping[str, Any]) -> None:
        if kind == "cycle":
            writer.observe_cycle(row)
        elif kind == "mutation":
            writer.observe_mutation(row)

    return sink


def render(snapshot: Mapping[str, Any]) -> str:
    """ONE line a human can read in a log tail. Never used for a decision."""
    w, c = snapshot.get("window") or {}, snapshot.get("cycle") or {}
    inv = c.get("inventory") or {}
    return ("w%s %s tte %s cyc %s | YES %s/%s NO %s/%s | rest %s | "
            "inv y%s n%s m%s r%s pair %s | fills %s | acts %s/%s | %s"
            % (w.get("window_index"), (w.get("symbol") or "-")[-13:],
               c.get("tte_s"), c.get("cycle"),
               _g(c, "yes", "bid"), _g(c, "yes", "ask"),
               _g(c, "no", "bid"), _g(c, "no", "ask"),
               c.get("n_resting"),
               inv.get("yes_qty"), inv.get("no_qty"), inv.get("matched_qty"),
               inv.get("directional_residual"),
               inv.get("effective_matched_pair_cost"), inv.get("n_fills"),
               c.get("actions_used"), c.get("actions_of"),
               w.get("stop_reason") or (c.get("held_by") or c.get("actions")
                                        or "-")))
