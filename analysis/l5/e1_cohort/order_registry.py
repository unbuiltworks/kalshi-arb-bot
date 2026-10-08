"""WHAT THIS PROCESS KNOWS ABOUT ITS OWN ORDERS, RESTORED FROM DISK.

WHY THIS EXISTS. `/v1/orders` is HTTP 501 on PMUS and every history and
trade endpoint is 404, so "what were all my orders" cannot be asked. Order
state is established by reading each id individually -- which works only if
the ids survive. In memory they do not: a restart came back with an empty
registry, and the only thing that then caught a pre-existing position was
the positions cross-check, which refuses rather than resumes. So the ids
have to come off the durable tape, which is where all forty of the
1215z/1230z ids were in fact found during the reconciliation.

THREE THINGS ARE RESTORED, and the third is the one that matters most:

  known_ids   every venue order id this cohort has submitted
  roles       first_leg or complement, per id
  pending     SUBMISSION INTENTS whose acknowledgement we never saw

An intent is recorded on the tape BEFORE the POST goes out and superseded
by the confirmed order id afterwards. So a process that dies between the
request and the acknowledgement leaves a durable intent behind, the restart
restores it as outstanding, and no second first leg can be placed on top of
an order that may or may not exist. Without that row, the dangerous window
is invisible to the next process -- which is how twenty orders can exist
while a process believes it has none.

READ-ONLY. This module opens the tape for reading and writes nothing.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from analysis.l5.e1_cohort.order_state import (COMPLEMENT, FIRST_LEG,
                                               LOCAL_INTENT)

#: the tape, relative to the cohort state dir.
TAPE_SUBDIR = "tape"
EVENTS_FILE = "events.jsonl"

#: events that name a CONFIRMED venue order id, and the role it played.
CONFIRMED_EVENTS: Dict[str, str] = {
    "FIRST_LEG_SUBMITTED": FIRST_LEG,
    "COMPLEMENT_SUBMITTED": COMPLEMENT,
    "COMPLEMENT_TRANCHE_SUBMITTED": COMPLEMENT,
}

#: events that record an INTENT -- we tried to submit and cannot prove the
#: outcome. These restore as pending and BLOCK.
INTENT_EVENTS: Dict[str, str] = {
    "FIRST_LEG_SUBMISSION_INTENT": FIRST_LEG,
    "COMPLEMENT_SUBMISSION_INTENT": COMPLEMENT,
    "AMBIGUOUS_SUBMISSION": FIRST_LEG,
}

ID_KEYS = ("order_id", "orderId", "venue_order_id")
INTENT_KEYS = ("intent_id", "e1_pending_order_id")


def tape_path(state_dir: str) -> str:
    return os.path.join(state_dir, TAPE_SUBDIR, EVENTS_FILE)


def _rows(path: str) -> Tuple[List[Dict[str, Any]], int]:
    """Every parseable row, and the count of rows that would not parse.

    A truncated final line is normal for an append-only file that was
    killed mid-write, and it is counted rather than raised: losing the
    whole registry because the last line is short would be the opposite of
    fail-closed.
    """
    rows: List[Dict[str, Any]] = []
    bad = 0
    if not os.path.exists(path):
        return rows, bad
    with open(path, encoding="utf8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:                                  # noqa: BLE001
                bad += 1
                continue
            if isinstance(o, dict):
                rows.append(o)
    return rows, bad


def _first(detail: Mapping[str, Any], keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        v = detail.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def load_from_tape(state_dir: str, *,
                   session_id: Optional[str] = None) -> Dict[str, Any]:
    """Rebuild the registry. Never raises on a damaged tape.

    `session_id`, when given, restricts the registry to one cohort session,
    so a new session does not inherit a previous one's ids. The tape is
    shared across sessions on the same volume and both prior cohorts are
    preserved there permanently, so this filter is what keeps them separate.
    """
    path = tape_path(state_dir)
    rows, unparseable = _rows(path)
    known: List[str] = []
    roles: Dict[str, str] = {}
    pending: List[str] = []
    confirmed_by_intent: Dict[str, str] = {}
    counts: Dict[str, int] = {}

    for r in rows:
        if session_id is not None:
            sid = r.get("session_id") or r.get("session") or \
                (r.get("detail") or {}).get("session_id")
            # a row with no session at all is KEPT: an unattributable order
            # of ours is exactly what must not be waved past.
            if sid is not None and str(sid) != str(session_id):
                continue
        ev = str(r.get("event") or "")
        detail = r.get("detail") if isinstance(r.get("detail"), Mapping) \
            else {}
        counts[ev] = counts.get(ev, 0) + 1

        if ev in CONFIRMED_EVENTS:
            oid = _first(detail, ID_KEYS)
            if oid:
                if oid not in known:
                    known.append(oid)
                roles[oid] = CONFIRMED_EVENTS[ev]
                # this confirmed id SUPERSEDES the intent that preceded it
                iid = _first(detail, INTENT_KEYS)
                if iid and iid.startswith(LOCAL_INTENT):
                    confirmed_by_intent[iid] = oid
            else:
                # a submission event with no id is an UNIDENTIFIED
                # submission, which is the ambiguous case under another
                # name. It blocks.
                pending.append("%sTAPE:%s:w%s:s%s" % (
                    LOCAL_INTENT, ev, r.get("window"), r.get("slot")))

        if ev in INTENT_EVENTS:
            iid = _first(detail, INTENT_KEYS) or ("%sTAPE:%s:w%s:s%s" % (
                LOCAL_INTENT, ev, r.get("window"), r.get("slot")))
            if not iid.startswith(LOCAL_INTENT):
                iid = LOCAL_INTENT + iid
            pending.append(iid)
            roles.setdefault(iid, INTENT_EVENTS[ev])

    # an intent the tape later confirmed is NOT pending any more: its real
    # id is in `known` and the venue can be asked about it directly.
    live_pending = [p for p in dict.fromkeys(pending)
                    if p not in confirmed_by_intent]

    return {
        "state_dir": state_dir,
        "tape": path,
        "tape_exists": os.path.exists(path),
        "rows_scanned": len(rows),
        "unparseable_rows": unparseable,
        "session_id": session_id,
        "known_ids": known,
        "roles": roles,
        "pending": live_pending,
        "intents_superseded_by_a_confirmed_id": confirmed_by_intent,
        "event_counts": counts,
        "READ_ONLY": True,
        "AN_INTENT_WITHOUT_A_CONFIRMED_ID_BLOCKS": True,
        "A_DAMAGED_LAST_LINE_DOES_NOT_DISCARD_THE_REGISTRY": True,
    }


def install(provider: Any, registry: Mapping[str, Any]) -> Dict[str, Any]:
    """Load a restored registry into a provider's live, shared-by-reference
    lists, without replacing the objects the runner also holds.

    Mutating in place matters: `submit_first_leg` appends to the very same
    list, so rebinding it here would silently disconnect the two halves and
    reintroduce exactly the class of defect this whole repair is about.
    """
    for oid in registry.get("known_ids") or []:
        if oid not in provider.known_order_ids:
            provider.known_order_ids.append(oid)
    provider.order_roles.update(dict(registry.get("roles") or {}))
    for p in registry.get("pending") or []:
        if p not in provider.pending_order_ids:
            provider.pending_order_ids.append(p)
    return {
        "known_ids": list(provider.known_order_ids),
        "roles": dict(provider.order_roles),
        "pending": list(provider.pending_order_ids),
        "mutated_in_place_not_rebound": True,
    }
