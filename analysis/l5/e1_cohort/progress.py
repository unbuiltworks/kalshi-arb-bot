"""Durable cohort progress. A RESTART RESUMES; IT NEVER RESETS.

THE RISK THIS CLOSES. The cohort bounds -- 12 windows, 20 attempt slots per
window, $1.56 per window, the $5.00 forward-looking invariant -- are only
bounds if they survive a process restart. A tracker that lives in memory
bounds a PROCESS, not a COHORT, and a container that restarts twelve times
would get twelve fresh cohorts while every in-memory counter read zero.

So the counters live on the volume. On boot the cohort REHYDRATES: windows
already consumed stay consumed, placements already made stay counted,
realized loss already taken stays taken, and unresolved residuals stay in
the forward-looking sum. A restart is a resume.

THE LATCH IS STILL THE AUTHORITY ON ORDERS. Named slot files are what
actually bound submissions, and they are already durable. This file is the
authority on COHORT PROGRESS -- which windows are spent, and what the risk
counters stand at -- so the two agree after a restart instead of the
tracker starting over behind an already-spent latch.

APPEND-ONLY, like everything else in this package. Progress is a sequence
of events, and the current state is their fold. There is no overwrite path
and no reset path: nothing here deletes, truncates or rewinds, and a test
asserts that.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort import arming as A
from analysis.l5.e1_cohort.spec import (MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_PLACEMENTS, WINDOWS)

FILENAME = "e1.cohort_progress.jsonl"

EV_WINDOW_OPENED = "WINDOW_OPENED"
EV_PLACEMENT = "PLACEMENT"
EV_RESIDUAL_OPENED = "RESIDUAL_OPENED"
EV_RESIDUAL_RESOLVED = "RESIDUAL_RESOLVED"
EV_CONFIRMED_CASH = "CONFIRMED_CASH"
EV_WINDOW_CLOSED = "WINDOW_CLOSED"
EV_COHORT_STOPPED = "COHORT_STOPPED"


class ProgressUnavailable(RuntimeError):
    """Progress could not be read or written, so nothing may proceed.

    FAILS CLOSED. An unwritable progress file means the cohort cannot
    prove how far it has already gone, and a cohort that cannot prove that
    must not place another order.
    """


class CohortProgress:
    """The durable fold of the cohort's own history."""

    def __init__(self, state_dir: str,
                 session_id: Optional[str] = None) -> None:
        self.state_dir = state_dir
        self.session_id = session_id or A.session_id()
        if not self.session_id:
            raise ProgressUnavailable(
                "%s is not set; cohort progress needs a session to scope "
                "itself to" % A.SESSION_ID_ENV)
        self.path = os.path.join(state_dir, FILENAME)
        self.events: List[Dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------- durable
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("session_id") == self.session_id:
                        self.events.append(row)
        except OSError as exc:
            raise ProgressUnavailable(
                "cohort progress at %s could not be read (%s); refusing to "
                "proceed" % (self.path, type(exc).__name__)) from exc

    def _append(self, event: str, **fields: Any) -> Dict[str, Any]:
        row = {"session_id": self.session_id, "event": event,
               "unix": time.time(),
               "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "pid": os.getpid(),
               "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID")}
        row.update(fields)
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.path, "a", encoding="utf8") as fh:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise ProgressUnavailable(
                "cohort progress could not be written (%s); refusing to "
                "proceed" % type(exc).__name__) from exc
        self.events.append(row)
        return row

    # -------------------------------------------------------------- record
    def record_window_opened(self, *, window_index: int, window_start: int,
                             slug: str, market_id: str, token_up: str,
                             token_down: str,
                             selection_provenance: Any = None,
                             authorization_result: Any = None
                             ) -> Dict[str, Any]:
        """One DISTINCT window consumed. This is what a restart cannot
        undo: a window opened is a window spent, forever."""
        return self._append(EV_WINDOW_OPENED, window_index=window_index,
                            window_start=window_start, slug=slug,
                            market_id=market_id, token_up=token_up,
                            token_down=token_down,
                            selection_provenance=selection_provenance,
                            authorization_result=authorization_result)

    def record_placement(self, *, window_index: int, slot: int,
                         notional_usd: Any, slug: str) -> Dict[str, Any]:
        return self._append(EV_PLACEMENT, window_index=window_index,
                            slot=slot, notional_usd=str(notional_usd),
                            slug=slug)

    def record_residual_opened(self, *, window_index: int, slot: int,
                               filled_qty: Any, fill_price_usd: Any
                               ) -> Dict[str, Any]:
        return self._append(EV_RESIDUAL_OPENED, window_index=window_index,
                            slot=slot, filled_qty=str(filled_qty),
                            fill_price_usd=str(fill_price_usd))

    def record_residual_resolved(self, *, window_index: int, slot: int
                                 ) -> Dict[str, Any]:
        return self._append(EV_RESIDUAL_RESOLVED,
                            window_index=window_index, slot=slot)

    def record_confirmed_cash(self, *, amount_usd: Any,
                              source_identity: Any) -> Dict[str, Any]:
        return self._append(EV_CONFIRMED_CASH, amount_usd=str(amount_usd),
                            source_identity=source_identity)

    def record_window_closed(self, *, window_index: int,
                             reason: Any = None) -> Dict[str, Any]:
        return self._append(EV_WINDOW_CLOSED, window_index=window_index,
                            reason=reason)

    def record_cohort_stopped(self, *, verdict: str,
                              truncation_cause: Any = None
                              ) -> Dict[str, Any]:
        return self._append(EV_COHORT_STOPPED, verdict=verdict,
                            truncation_cause=truncation_cause)

    # ---------------------------------------------------------------- fold
    def windows_opened(self) -> List[Dict[str, Any]]:
        seen: Dict[int, Dict[str, Any]] = {}
        for e in self.events:
            if e["event"] == EV_WINDOW_OPENED:
                seen.setdefault(int(e["window_index"]), e)
        return [seen[k] for k in sorted(seen)]

    def windows_used(self) -> int:
        return len(self.windows_opened())

    def windows_remaining(self) -> int:
        return max(0, WINDOWS - self.windows_used())

    def slugs_used(self) -> List[str]:
        return [w["slug"] for w in self.windows_opened()]

    def window_starts_used(self) -> List[int]:
        return [int(w["window_start"]) for w in self.windows_opened()]

    def next_window_index(self) -> int:
        used = {int(w["window_index"]) for w in self.windows_opened()}
        for i in range(WINDOWS):
            if i not in used:
                return i
        return WINDOWS

    def placements(self) -> int:
        return sum(1 for e in self.events if e["event"] == EV_PLACEMENT)

    def placements_in_window(self, window_index: int) -> int:
        return sum(1 for e in self.events
                   if e["event"] == EV_PLACEMENT
                   and int(e["window_index"]) == int(window_index))

    def exposure_in_window(self, window_index: int) -> D:
        return sum((D(e["notional_usd"]) for e in self.events
                    if e["event"] == EV_PLACEMENT
                    and int(e["window_index"]) == int(window_index)),
                   D("0"))

    def realized_loss_usd(self) -> D:
        loss = D("0")
        for e in self.events:
            if e["event"] != EV_CONFIRMED_CASH:
                continue
            amt = D(e["amount_usd"])
            if amt < 0:
                loss += -amt
            elif amt > 0:
                loss -= amt
                if loss < 0:
                    loss = D("0")
        return loss

    def unresolved_residuals(self) -> Dict[Any, Dict[str, Any]]:
        opened: Dict[Any, Dict[str, Any]] = {}
        for e in self.events:
            key = (e.get("window_index"), e.get("slot"))
            if e["event"] == EV_RESIDUAL_OPENED:
                opened[key] = e
            elif e["event"] == EV_RESIDUAL_RESOLVED:
                opened.pop(key, None)
        return opened

    def rehydrate(self, tracker: Any) -> Dict[str, Any]:
        """Replay durable progress into a fresh tracker, so a restarted
        process carries the cohort's real counters rather than zeros."""
        for w in self.windows_opened():
            pass
        for e in self.events:
            if e["event"] == EV_PLACEMENT:
                tracker.record_placement(
                    window=int(e["window_index"]),
                    notional_usd=e["notional_usd"])
            elif e["event"] == EV_RESIDUAL_OPENED:
                tracker.open_residual(window=int(e["window_index"]),
                                      slot=int(e["slot"]),
                                      filled_qty=e["filled_qty"],
                                      fill_price_usd=e["fill_price_usd"])
            elif e["event"] == EV_RESIDUAL_RESOLVED:
                tracker.resolve_residual(
                    key=(int(e["window_index"]), int(e["slot"])))
            elif e["event"] == EV_CONFIRMED_CASH:
                tracker.record_confirmed_cash(amount_usd=e["amount_usd"])
        return {"rehydrated": True,
                "events_replayed": len(self.events),
                "windows_used": self.windows_used(),
                "windows_remaining": self.windows_remaining(),
                "placements": tracker.placements,
                "realized_loss_usd": str(tracker.realized_loss_usd),
                "unresolved_residual_max_loss_usd":
                    str(tracker.unresolved_residual_max_loss_usd()),
                "A_RESTART_RESUMES_IT_DOES_NOT_RESET": True}

    def report(self) -> Dict[str, Any]:
        return {"path": self.path, "append_only": True,
                "session_id_present": bool(self.session_id),
                "events": len(self.events),
                "windows_used": self.windows_used(),
                "windows_authorized": WINDOWS,
                "windows_remaining": self.windows_remaining(),
                "slugs_used": self.slugs_used(),
                "window_starts_used": self.window_starts_used(),
                "next_window_index": self.next_window_index(),
                "placements": self.placements(),
                "max_placements": MAX_PLACEMENTS,
                "max_attempts_per_window": MAX_ATTEMPTS_PER_WINDOW,
                "realized_loss_usd": str(self.realized_loss_usd()),
                "unresolved_residuals": len(self.unresolved_residuals()),
                "a_restart_is_a_resume": True,
                "a_restart_grants_another_12_windows": False,
                "reset_path_exists": False}
