"""One account-wide risk reservation, shared by every lane. FAIL-CLOSED.

WHY A FILE AND A LOCK. The four 5-minute lanes are SEPARATE PROCESSES
with SEPARATE ledgers -- that separation is what stops two assets
overwriting each other's orders. It also means no lane can see another's
exposure: each one's `own_usd` is its own ledger's, and four lanes each
checking a $10 cap against their own books can commit $40 between them.
`l5_persistent_mode.may_submit` closes that only if somebody hands it a
TRUE account-wide figure. This is where that figure comes from.

They already share one volume, so the reservation lives there and is
guarded by an exclusive `flock`. Read, decide and write happen inside
one lock hold, so two lanes cannot both see room that only one of them
can have. No new service, no database, no network.

WHAT IS RESERVED IS THE WORST CASE, not the expected cost: a maker buy
of N shares at P reserves N*P, the whole outlay, because a losing
contract pays nothing. The reservation is taken BEFORE the venue is
called and released only when the order reaches a TERMINAL state, so a
submission whose outcome is unknown keeps holding its risk. An uncertain
order is exposure.

LEAKS BLOCK, THEY DO NOT OVERDRAW. Nothing here expires a reservation on
a timer. A lane that died holding one keeps holding it, and the account
is under-committed until an operator or that lane's own restart clears
it. That is the safe direction: a stale reservation refuses trades, an
expired one would permit them. On restart a lane calls `reconcile` with
the orders its OWN durable ledger still shows live, and releases only
its own -- it can never release a sibling's, which is the one way this
file could silently grant someone else's room away.

EVERY FAILURE REFUSES. No path, an unreadable or corrupt file, a lock we
could not take, a write that did not land -- each returns refused, and
the caller submits nothing. An unreadable shared budget is not an empty
one.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional

try:                                                # pragma: no cover
    import fcntl
except ImportError:                                 # pragma: no cover
    fcntl = None                                    # type: ignore

D = Decimal
ZERO = D("0")

PATH_ENV = "L5_SHARED_RESERVATION"
#: the same ceiling `l5_persistent_mode` holds, repeated deliberately:
#: this file is the only thing that sees ALL lanes, so it must be able to
#: refuse on its own rather than trusting each caller's copy.
MAX_AGGREGATE_USD = D("10.00")


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def path() -> Optional[str]:
    p = str(os.environ.get(PATH_ENV) or "").strip()
    return p or None


class _Locked:
    """An exclusive hold on the reservation, or a refusal.

    THE LOCK IS ON A SEPARATE FILE THAT IS NEVER REPLACED, and that is
    not a detail. `write` publishes by `os.replace`, which swaps in a new
    INODE; a flock taken on the data file's own descriptor belongs to the
    inode that replace discarded, so the next process opens the new file,
    locks a different inode and excludes nobody.

    OBSERVED: with the lock on the data file, four processes making 32
    reservations of $1 against a $10 cap were ALL told yes and 9 landed.
    The reservation system reserved nothing. Caught by the concurrent
    test rather than in production, which is the only reason it is a
    comment and not an incident.
    """

    def __init__(self, p: str) -> None:
        self.p, self.fh, self.error = p, None, None

    def __enter__(self) -> "_Locked":
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.p)),
                        exist_ok=True)
            #: opened "a" and never renamed, so every process locks the
            #: same inode for as long as the directory lives.
            self.fh = open(self.p + ".lock", "a", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.error = type(exc).__name__
            if self.fh is not None:
                try:
                    self.fh.close()
                except BaseException:                           # noqa: BLE001
                    pass
                self.fh = None
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except BaseException:                                   # noqa: BLE001
            pass
        try:
            self.fh.close()
        except BaseException:                                   # noqa: BLE001
            pass

    def read(self) -> Optional[Dict[str, Any]]:
        """The data file, read while the lock is held."""
        if self.fh is None:
            return None
        try:
            with open(self.p, "r", encoding="utf-8") as data:
                raw = data.read()
        except FileNotFoundError:
            return {"holds": {}}                 # never written yet
        except BaseException:                                   # noqa: BLE001
            return None
        if not raw.strip():
            return {"holds": {}}
        try:
            doc = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("holds"), dict):
            return None
        return doc

    def write(self, doc: Dict[str, Any]) -> bool:
        if self.fh is None:
            return False
        folder = os.path.dirname(os.path.abspath(self.p))
        try:
            fd, tmp = tempfile.mkstemp(dir=folder, prefix=".l5resv.")
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(doc, out, sort_keys=True, default=str)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.p)
            return True
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False


def _total(holds: Dict[str, Any]) -> Optional[Decimal]:
    out = ZERO
    for h in holds.values():
        v = _dec((h or {}).get("usd"))
        if v is None or v < ZERO:
            return None                          # unreadable, not zero
        out += v
    return out


def reserve(*, lane: Any, key: Any, usd: Any, now: Any,
            cap_usd: Any = MAX_AGGREGATE_USD,
            file_path: Optional[str] = None) -> Dict[str, Any]:
    """Hold `usd` of the shared budget for `key`, or say why not.

    Read, decide and write happen inside ONE lock hold: two lanes cannot
    both be told there is room that only one can have.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)

    amount, cap = _dec(usd), _dec(cap_usd)
    lane_s, key_s = str(lane or "").strip(), str(key or "").strip()
    if not lane_s or not key_s:
        return no("RESERVATION_KEY_MISSING")
    if amount is None or amount <= ZERO:
        return no("RESERVATION_AMOUNT_UNREADABLE")
    if cap is None or cap <= ZERO:
        return no("RESERVATION_CAP_UNREADABLE")
    if cap > MAX_AGGREGATE_USD:
        #: a caller may ask for less and never for more
        return no("RESERVATION_CAP_ABOVE_CEILING",
                  asked=str(cap), ceiling=str(MAX_AGGREGATE_USD))
    p = file_path or path()
    if not p:
        return no("RESERVATION_PATH_UNSET", env=PATH_ENV)
    at = now.isoformat() if hasattr(now, "isoformat") else str(now)
    with _Locked(p) as lk:
        if lk.error or lk.fh is None:
            return no("RESERVATION_LOCK_FAILED", error=lk.error)
        doc = lk.read()
        if doc is None:
            #: an unreadable shared budget is not an empty one
            return no("RESERVATION_FILE_UNREADABLE")
        holds = doc["holds"]
        held = _total(holds)
        if held is None:
            return no("RESERVATION_FILE_UNREADABLE")
        if key_s in holds:
            #: the same order asking twice is not two risks. Idempotent,
            #: so a retry after an uncertain write cannot double-count.
            return {"ok": True, "reason": None, "already_held": True,
                    "usd": str(_dec(holds[key_s].get("usd"))),
                    "aggregate_usd": str(held), "cap_usd": str(cap)}
        if held + amount > cap:
            return no("RESERVATION_WOULD_EXCEED_CAP",
                      aggregate_usd=str(held), adding=str(amount),
                      cap_usd=str(cap))
        holds[key_s] = {"lane": lane_s, "usd": str(amount), "at_utc": at}
        if not lk.write(doc):
            return no("RESERVATION_NOT_WRITTEN")
        return {"ok": True, "reason": None, "already_held": False,
                "usd": str(amount), "aggregate_usd": str(held + amount),
                "cap_usd": str(cap)}


def release(*, lane: Any, key: Any,
            file_path: Optional[str] = None) -> Dict[str, Any]:
    """Give back one hold. A lane may release only its OWN."""
    lane_s, key_s = str(lane or "").strip(), str(key or "").strip()
    p = file_path or path()
    if not p:
        return {"ok": False, "reason": "RESERVATION_PATH_UNSET"}
    with _Locked(p) as lk:
        if lk.error or lk.fh is None:
            return {"ok": False, "reason": "RESERVATION_LOCK_FAILED",
                    "error": lk.error}
        doc = lk.read()
        if doc is None:
            return {"ok": False, "reason": "RESERVATION_FILE_UNREADABLE"}
        h = doc["holds"].get(key_s)
        if h is None:
            return {"ok": True, "reason": None, "released": False}
        if str((h or {}).get("lane")) != lane_s:
            #: the one way this file could silently grant away someone
            #: else's room.
            return {"ok": False, "reason": "RESERVATION_BELONGS_TO_ANOTHER_LANE",
                    "owner": str((h or {}).get("lane"))}
        del doc["holds"][key_s]
        if not lk.write(doc):
            return {"ok": False, "reason": "RESERVATION_NOT_WRITTEN"}
        return {"ok": True, "reason": None, "released": True}


def reconcile(*, lane: Any, live_keys: Iterable[Any],
              file_path: Optional[str] = None) -> Dict[str, Any]:
    """Release this lane's holds whose orders are no longer live.

    Called at startup from the lane's OWN durable ledger. It touches no
    sibling's holds, so a restarting lane cannot free room another lane
    is still using -- and a lane that died stays holding its risk until
    it comes back or an operator clears it, which refuses trades rather
    than permitting them.
    """
    lane_s = str(lane or "").strip()
    keep = {str(k).strip() for k in (live_keys or ())}
    p = file_path or path()
    if not p:
        return {"ok": False, "reason": "RESERVATION_PATH_UNSET"}
    with _Locked(p) as lk:
        if lk.error or lk.fh is None:
            return {"ok": False, "reason": "RESERVATION_LOCK_FAILED",
                    "error": lk.error}
        doc = lk.read()
        if doc is None:
            return {"ok": False, "reason": "RESERVATION_FILE_UNREADABLE"}
        gone = [k for k, h in doc["holds"].items()
                if str((h or {}).get("lane")) == lane_s and k not in keep]
        for k in gone:
            del doc["holds"][k]
        if gone and not lk.write(doc):
            return {"ok": False, "reason": "RESERVATION_NOT_WRITTEN"}
        return {"ok": True, "reason": None, "released": sorted(gone)}


def settle_to(*, lane: Any, keep: Mapping[Any, Any],
              file_path: Optional[str] = None) -> Dict[str, Any]:
    """Bring this lane's holds down to what its ledger says is still at risk.

    `keep` maps each of this lane's keys that still carries risk to either
    None -- leave the hold exactly as it is -- or the dollars still at risk.
    A hold of this lane that `keep` does not name is released.

    WHY THIS EXISTS. Live window 2026-11-20 14:20Z: holds were released only
    between windows, so every cancelled quote went on counting. Seven
    requote rounds reserved $0.5742 against a $0.60 envelope while $0.17 was
    actually at risk, and two-sided quoting stopped 68 s into a 300 s
    window.

    IT IS A STATEMENT OF STATE, NOT AN EVENT. The caller says what is at
    risk now; nothing here adds room per cancel seen. Saying the same thing
    twice changes nothing the second time, so a repeated reconciliation, a
    cancel reported twice or a restart cannot free the same money twice.

    A HOLD ONLY EVER SHRINKS HERE. An amount above the current hold leaves
    the hold where it is: growing one would take room without the cap check
    `reserve` makes. An amount that is unreadable or not positive also
    leaves the hold untouched -- "I cannot say what is at risk" is not
    "nothing is".

    One lock hold, own lane only, like `reconcile`.
    """
    lane_s = str(lane or "").strip()
    if not isinstance(keep, Mapping):
        return {"ok": False, "reason": "RESERVATION_KEEP_UNREADABLE"}
    want = {str(k).strip(): v for k, v in keep.items()}
    p = file_path or path()
    if not p:
        return {"ok": False, "reason": "RESERVATION_PATH_UNSET"}
    with _Locked(p) as lk:
        if lk.error or lk.fh is None:
            return {"ok": False, "reason": "RESERVATION_LOCK_FAILED",
                    "error": lk.error}
        doc = lk.read()
        if doc is None:
            return {"ok": False, "reason": "RESERVATION_FILE_UNREADABLE"}
        gone, reduced, freed = [], [], ZERO
        for k, h in list(doc["holds"].items()):
            if str((h or {}).get("lane")) != lane_s:
                continue
            cur = _dec((h or {}).get("usd"))
            if k not in want:
                gone.append(k)
                freed += cur if cur is not None and cur > ZERO else ZERO
                del doc["holds"][k]
                continue
            amt = want[k]
            if amt is None:
                continue
            new = _dec(amt)
            if new is None or new <= ZERO or cur is None or new >= cur:
                continue
            h["usd"] = str(new)
            reduced.append(k)
            freed += cur - new
        if (gone or reduced) and not lk.write(doc):
            return {"ok": False, "reason": "RESERVATION_NOT_WRITTEN"}
        return {"ok": True, "reason": None, "released": sorted(gone),
                "reduced": sorted(reduced), "freed_usd": str(freed)}


def aggregate(file_path: Optional[str] = None) -> Dict[str, Any]:
    """The account-wide reserved total, or why it is unknown."""
    p = file_path or path()
    if not p:
        return {"ok": False, "reason": "RESERVATION_PATH_UNSET", "usd": None}
    with _Locked(p) as lk:
        if lk.error or lk.fh is None:
            return {"ok": False, "reason": "RESERVATION_LOCK_FAILED",
                    "usd": None}
        doc = lk.read()
        if doc is None:
            return {"ok": False, "reason": "RESERVATION_FILE_UNREADABLE",
                    "usd": None}
        held = _total(doc["holds"])
        if held is None:
            return {"ok": False, "reason": "RESERVATION_FILE_UNREADABLE",
                    "usd": None}
        by_lane: Dict[str, Decimal] = {}
        for h in doc["holds"].values():
            by_lane[str((h or {}).get("lane"))] = (
                by_lane.get(str((h or {}).get("lane")), ZERO)
                + (_dec((h or {}).get("usd")) or ZERO))
        return {"ok": True, "reason": None, "usd": held,
                "holds": len(doc["holds"]),
                "by_lane": {k: str(v) for k, v in sorted(by_lane.items())}}
