"""Who else is managing resting orders on this shared account.

ONE DEFINITION, THREE CONSUMERS. A "managed lane" is another process on
the same venue account that rests maker orders for minutes BY DESIGN and
says so, freshly, over HTTP. Three independent guards need the same answer
and must never drift apart about it:

  1. `l5_watchdog.Watchdog.managed_lane_ids` -- exempts these ids from the
     order-age latch, so a lane's long-resting order is not read as an
     abandoned one (2026-11-14: it was, and it halted the whole account).
  2. `scripts/l5_halt_reset.managed_lane_claims` -- lets the reset
     ceremony's venue precondition classify them as attributed rather than
     as unattributed-automated.
  3. `l5_pmus_transport._managed_lane_ids` -- stops the executor's
     `no_unexpected_open_orders` preflight refusing to arm because of
     them.

WHY THIS FILE EXISTS RATHER THAN A SHARED IMPORT OF THE WATCHDOG. The
executor's image ships a deliberately narrow set of src/ files;
`l5_watchdog` is not one of them and pulls `l5_order_registry_server`
behind it. Importing the watchdog from the executor therefore fails at
runtime in the image -- caught by
test_l5_btc5m_image_deps.test_lazy_src_imports_on_the_live_path_are_shipped
on 2026-11-14, which is the only reason it was not shipped broken. This
module has no src/ dependencies at all, so every image can carry it.

FAIL CLOSED IS THE WHOLE CONTRACT. A lane that is unreachable, stale,
malformed, or does not claim to manage its own resting orders contributes
NOTHING. Callers are then exactly as strict as they were before any of
this existed. An exemption is never a permission: naming an id here only
says "somebody fresh is managing it", never "it may be cancelled" and
never "it is ours".
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, List, Optional, Tuple

ENV = "L5_WATCHDOG_MANAGED_LANE_URLS"

#: a lane attestation older than this is not proof of management.
MAX_AGE_S = 60.0

#: and one dated in the future is a clock problem, not an attestation. A
#: few seconds of skew between containers is ordinary; minutes is not.
MAX_SKEW_S = 5.0

DOC_PATH = "/l5/live_orders.json"


def from_env(env: Optional[str] = None) -> List[str]:
    """Base URLs of lanes the operator has declared, or []."""
    raw = str(os.getenv(env or ENV, "")).strip()
    #: NOT rstrip("/") here. The watchdog's original helper returned the
    #: urls verbatim and normalised only when building the request; a test
    #: pins that. `lane_ids` does the stripping instead, so this stays
    #: byte-compatible with what callers already assert.
    return [u.strip() for u in raw.split(",") if u.strip()]


def _parse_t(v: Any) -> Optional[dt.datetime]:
    try:
        out = dt.datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def lane_ids(urls: Optional[List[str]] = None, *,
             opener: Any = None,
             now: Optional[dt.datetime] = None) -> Tuple[set, List[str]]:
    """({venue_order_id, ...}, [notes]) attested by FRESH managed lanes.

    `opener(url, timeout) -> bytes` is injectable so tests need no socket.
    Notes are per-lane and safe to log: a base URL and one of OK:n,
    UNREACHABLE:<ExcType>, NOT_A_MANAGED_LANE or STALE:<age>.
    """
    ids: set = set()
    notes: List[str] = []
    now = now or dt.datetime.now(dt.timezone.utc)
    for base in (urls if urls is not None else from_env()):
        url = "%s%s" % (str(base).rstrip("/"), DOC_PATH)
        try:
            if opener is not None:
                raw = opener(url, 3.0)
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=3.0) as r:
                    raw = r.read()
            doc = json.loads(raw or b"{}")
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            notes.append("%s:UNREACHABLE:%s" % (base, type(exc).__name__))
            continue
        if not isinstance(doc, dict) or doc.get("readable") is not True \
                or doc.get("manages_own_resting") is not True:
            notes.append("%s:NOT_A_MANAGED_LANE" % base)
            continue
        t = _parse_t(doc.get("as_of_utc"))
        age = None if t is None else (now - t).total_seconds()
        if age is None or age > MAX_AGE_S or age < -MAX_SKEW_S:
            notes.append("%s:STALE:%s" % (base, age))
            continue
        rows = doc.get("orders") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("venue_order_id") or "").strip()
            if vid:
                ids.add(vid)
        notes.append("%s:OK:%d" % (base, len(rows)))
    return ids, notes
