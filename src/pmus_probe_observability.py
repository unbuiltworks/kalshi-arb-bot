"""3d: the probe's own observability, where failure ABORTS.

THE ONE DESIGN DECISION HERE, stated first because everything follows from it.
`pmus_ws_evidence_recorder`'s handoff deliberately NEVER RAISES -- a monitoring
problem must not cost a read-only tape. This module is the opposite: a
money-moving session that cannot be observed must not continue, so every failure
raises `MonitorUnavailable` and the probe aborts. The two rules are not
inconsistent; they follow from what is at stake in each.

This is NOT a monitoring architecture. It is a required event vocabulary, an
append-only journal, and a health gate. The transport, the journal file format
and the status handoff all already exist.

WHAT MUST BE VISIBLE, per the owner's 3d list. Each is a required `kind`:

    DECISION        the frozen rule's output for this state, and its inputs
    SUBMISSION      the order body we sent, before any venue answer
    ACK             the venue's receipt -- a receipt, never a final state
    FILL            an authoritative fill, from a venue read and not from an ACK
    INVENTORY       positions and basis after the event
    LOCK            the PROFIT_LOCK_5 floor and whether it has latched
    CANCEL          a working order withdrawn
    COMPLETION      a completion executed, with the economics that admitted it
    RECONCILIATION  the venue's answer treated as authority over our books
    TERMINAL        the market's terminal/settlement state as the venue reports it

STALENESS IS OBSERVABLE, NOT ASSUMED. Every record carries a monotonic sequence
number and a UTC stamp, and `require_healthy()` refuses on a stale or missing
heartbeat rather than trusting silence.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple


class MonitorUnavailable(RuntimeError):
    """Observability failed or went uncertain, so the probe must abort."""


#: the required vocabulary. A record outside it is refused, so a typo cannot
#: silently become an unmonitored event class.
KINDS: Tuple[str, ...] = ("DECISION", "SUBMISSION", "ACK", "FILL", "INVENTORY",
                          "LOCK", "CANCEL", "COMPLETION", "RECONCILIATION",
                          "TERMINAL")

#: every kind that must have been seen at least once before a probe may be
#: called observed end to end. ACK/FILL/CANCEL/COMPLETION are NOT here: a probe
#: that legitimately never filled must still be able to close cleanly.
REQUIRED_BEFORE_CLOSE: Tuple[str, ...] = ("DECISION", "INVENTORY", "LOCK",
                                          "RECONCILIATION", "TERMINAL")

#: a heartbeat older than this means we no longer know what the probe is doing
MAX_HEARTBEAT_AGE_S = 90.0


class ProbeMonitor:
    """Append-only, fail-loud observability for exactly one probe."""

    def __init__(self, path: str, *, market: str, clock=time.time) -> None:
        self.path = str(path)
        self.market = str(market)
        self.clock = clock
        self.seq = 0
        self.seen: Dict[str, int] = {k: 0 for k in KINDS}
        self.last_t: Optional[float] = None
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise MonitorUnavailable(
                "journal is not writable at %s: %s" % (self.path, exc)) from exc

    def record(self, kind: str, payload: Mapping[str, Any]) -> dict:
        """Journal one event. Raises MonitorUnavailable on ANY failure."""
        if kind not in KINDS:
            raise MonitorUnavailable("unknown event kind %r" % (kind,))
        self.seq += 1
        now = self.clock()
        row = {
            "seq": self.seq,
            "t": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "market": self.market,
            "kind": kind,
            "payload": dict(payload or {}),
        }
        try:
            line = json.dumps(row, separators=(",", ":"), default=str)
        except Exception as exc:                               # noqa: BLE001
            # ANY failure, not just TypeError/ValueError. `default=str` calls
            # str() on an unserialisable object, and an object whose __repr__
            # raises would otherwise propagate ITS exception instead of the
            # documented abort -- a monitoring failure disguised as a crash.
            raise MonitorUnavailable(
                "event %s is not serialisable: %s" % (kind, exc)) from exc
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise MonitorUnavailable(
                "journal write failed for %s: %s" % (kind, exc)) from exc
        self.seen[kind] += 1
        self.last_t = now
        return row

    # ------------------------------------------------------------ health

    def heartbeat_age_s(self) -> Optional[float]:
        return None if self.last_t is None else (self.clock() - self.last_t)

    def require_healthy(self) -> None:
        """Abort unless we currently know what the probe is doing."""
        age = self.heartbeat_age_s()
        if age is None:
            raise MonitorUnavailable("no event has ever been recorded")
        if age > MAX_HEARTBEAT_AGE_S:
            raise MonitorUnavailable(
                "heartbeat is %.1fs old, over the %.0fs bound"
                % (age, MAX_HEARTBEAT_AGE_S))

    def require_observed_end_to_end(self) -> None:
        """Abort unless every kind a clean close requires has been seen."""
        missing = [k for k in REQUIRED_BEFORE_CLOSE if not self.seen[k]]
        if missing:
            raise MonitorUnavailable(
                "never observed: %s" % ", ".join(missing))

    def summary(self) -> dict:
        return {"market": self.market, "events": self.seq,
                "by_kind": dict(self.seen),
                "heartbeat_age_s": self.heartbeat_age_s(),
                "journal": self.path}
