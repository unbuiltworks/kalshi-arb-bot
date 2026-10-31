"""One-shot, read-only recovery of ONE decision record from the tape.

WHY THIS EXISTS. On 1415z (2026-10-31) unmatched inventory reached 4.25
contracts against a 3-contract ceiling. The records that say WHY --
`RESIDUAL_CAP` and `INVENTORY_CLASS` -- were written to the durable decision
tape and were NOT in the emitted log line, so the cause could not be
established from the logs at all. The tape still holds them. This reads that
one record out and emits a sanitized view of it.

WHAT IT IS NOT, and these are load-bearing:

  * NOT a scanner. It stops at the first `MAX_HITS` matching records and
    never reads past `MAX_LINES`, so it cannot walk a growing tape.
  * NOT continuous. The caller runs it ONCE at startup. There is no loop
    here and nothing schedules it.
  * NOT on by default. With `L5_TAPE_RECOVERY_COID` unset it returns
    `{"ok": False, "reason": "DISABLED"}` and opens no file.
  * NOT a writer. The tape is opened "r" and nothing in this module writes,
    truncates, renames or deletes anything.
  * NOT a dump. It emits an explicit ALLOWLIST of fields. The book
    (`row`), the L5 action slot and every other stage are dropped, because
    the question is about the residual cap and nothing else needs to leave
    the container to answer it.

A record that cannot be parsed is reported as unparseable rather than
skipped silently: "the line was not JSON" and "the line held no cap note"
are different answers and the second is the interesting one.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

#: the coid to recover. ABSENT MEANS DISABLED -- no file is opened.
COID_ENV = "L5_TAPE_RECOVERY_COID"

#: hard bounds. A diagnostic that can read an unbounded file in a money
#: container is not bounded, however short the file is today.
MAX_LINES = 200_000
MAX_HITS = 8

#: the ONLY stages copied out. Everything else in `record` is dropped.
KEEP_STAGES = ("RESIDUAL_CAP", "INVENTORY_CLASS", "FINAL_RESIDUAL_GUARD")

#: the ONLY order fields copied out. `intent` is deliberately excluded: its
#: price and quantity are already in the lifecycle logs and it is not needed
#: to answer whether the cap bound.
KEEP_ORDER = ("client_order_id", "submitted", "reason", "venue_order_id")


def configured() -> Optional[str]:
    """The coid to recover, or None for OFF."""
    raw = (os.getenv(COID_ENV) or "").strip()
    return raw or None


def sanitize(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """One tape line reduced to the allowlist. Copies, never references."""
    rec = doc.get("record")
    rec = rec if isinstance(rec, Mapping) else {}
    order = rec.get("ORDER")
    order = order if isinstance(order, Mapping) else {}
    out: Dict[str, Any] = {
        "t": doc.get("t"),
        "armed": doc.get("armed"),
        "preflight": doc.get("preflight"),
        "decision_role": doc.get("decision_role"),
        "market": rec.get("market"),
        "provenance": rec.get("provenance"),
        "stage_reached": rec.get("stage_reached"),
        "refused_by_layer": rec.get("refused_by_layer"),
        "refused_reason": rec.get("refused_reason"),
        "order": {k: order.get(k) for k in KEEP_ORDER if k in order},
    }
    for name in KEEP_STAGES:
        # PRESENT-BUT-NULL AND ABSENT ARE DIFFERENT ANSWERS, and the whole
        # investigation turns on which one this is: an absent RESIDUAL_CAP
        # means the gate was never reached, while a present one means it ran
        # and its inputs are in the payload. So absence is reported by name.
        out[name] = rec[name] if name in rec else "__ABSENT__"
    return out


def recover(path: str, coid: str, *, max_lines: int = MAX_LINES,
            max_hits: int = MAX_HITS) -> Dict[str, Any]:
    """Find up to `max_hits` records for `coid`. READ-ONLY and bounded."""
    if not coid:
        return {"ok": False, "reason": "NO_COID"}
    if not path or not os.path.exists(path):
        return {"ok": False, "reason": "TAPE_NOT_FOUND", "path": str(path)}
    hits: List[Dict[str, Any]] = []
    scanned = 0
    unparseable = 0
    try:
        with open(path, "r") as fh:              # "r": never "a", never "w"
            for line in fh:
                if scanned >= max_lines or len(hits) >= max_hits:
                    break
                scanned += 1
                # cheap substring reject before the JSON parse, so a long
                # tape costs a scan and not a parse per line
                if coid not in line:
                    continue
                try:
                    doc = json.loads(line)
                except BaseException:                      # noqa: BLE001
                    unparseable += 1
                    continue
                if isinstance(doc, Mapping):
                    hits.append(sanitize(doc))
    except BaseException as exc:                           # noqa: BLE001
        return {"ok": False, "reason": "TAPE_UNREADABLE:%s"
                % type(exc).__name__, "scanned": scanned}
    return {
        "ok": True, "coid": coid, "path": str(path),
        "scanned_lines": scanned, "unparseable_lines": unparseable,
        "hits": len(hits), "records": hits,
        "truncated": bool(len(hits) >= max_hits or scanned >= max_lines),
        "labels": ("READ-ONLY, one-shot, bounded. The tape is opened 'r' and "
                   "is not modified. Only RESIDUAL_CAP, INVENTORY_CLASS, "
                   "FINAL_RESIDUAL_GUARD, four order fields and the refusal "
                   "outcome are emitted; the book, the L5 action slot and "
                   "every other stage are dropped. '__ABSENT__' means the "
                   "stage was NOT in the record, which is a different and "
                   "more serious answer than a present stage that did not "
                   "bind."),
    }


#: WHICH COIDS THIS PROCESS HAS ALREADY RECOVERED. The latch that makes
#: "one-shot" true rather than merely intended.
_RAN: Dict[str, bool] = {}


def reset_for_tests() -> None:
    """Clear the one-shot latch. Tests only."""
    _RAN.clear()


def from_env(root: str) -> Optional[Dict[str, Any]]:
    """The caller's one line. None when off OR already run this process.

    THE LATCH IS HERE AND NOT IN THE CALLER, and it is here because the
    first version did not have one. The call site is `Runner.roll`, which
    fires on every window roll -- so with the variable set, the diagnostic
    re-read 143,829 lines at 17:26, 18:00, 18:15 and 18:30. It was
    read-only and inside its bounds every time, and it was still a
    continuous scan of the whole tape, which is exactly what a one-shot
    diagnostic must not be. Latching on the coid means the answer is
    emitted once per process and the file is not opened again.
    """
    coid = configured()
    if coid is None:
        return None
    if _RAN.get(coid):
        return None
    _RAN[coid] = True
    return recover(os.path.join(str(root), "decisions.jsonl"), coid)
