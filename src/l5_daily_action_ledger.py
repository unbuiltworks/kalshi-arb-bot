"""Per-UTC-day action allowances. Counters that MAY reset; nothing else does.

WHAT THIS BOUNDS AND WHAT IT DELIBERATELY CANNOT TOUCH. A standing mandate
states `max_windows_per_day` and `max_submissions_per_day`. Those are RATE
limits: how much ACTIVITY is permitted in a UTC day, and they roll at
midnight because that is what makes a mandate standing instead of a campaign
that must be reissued when its total is spent.

Three things must NEVER roll, and this module has no writer for any of them:

  * cumulative realized losses -- owned by the executor's store and read by
    `ValidationGates`/`l5_campaign_budget` on every evaluation;
  * unresolved inventory -- owned by `InventoryStore`, retired only by
    venue-confirmed settlement;
  * mandate-wide capital limits -- inside the signed payload.

A rate ledger that could reset any of those would turn "four windows a day"
into "four windows and a fresh loss budget a day", which is the opposite of a
bound. So the only state here is COUNTS OF CLAIMS, and the only verb that
writes is an O_EXCL create.

CONCURRENCY WITHOUT A LOCK. Each claim is its own file created with
O_CREAT|O_EXCL, exactly as `l5_live_authorization.claim_once` does it: the
filesystem decides the race, two processes cannot both win the same name, and
a crash between deciding and recording costs a claim rather than granting
one. Counting is then just listing the directory, so there is no
read-modify-write to interleave.

RESERVING THE SAME WINDOW TWICE IS IDEMPOTENT. A restart that re-arms the
same slug RESUMES its claim instead of spending a second one -- the same
property `WindowLedger` has, and the reason the 1600z window loss on
2026-10-20 is not repeated here.

AN UNREADABLE LEDGER RAISES. "I could not tell" and "nothing used today"
differ by the whole day's allowance.
"""
from __future__ import annotations

import datetime as dt
import errno
import json
import os
from typing import Any, Dict, List, Optional

LEDGER_DIR = "daily_actions"
ROOT_ENV = "L5_LIVE_ROOT"

#: claim name prefixes. One character each so the directory listing is cheap
#: and the two kinds can never be confused for one another.
WINDOW_PREFIX = "w."
SUBMISSION_PREFIX = "s."


class DailyLedgerUnavailable(RuntimeError):
    """The answer could not be established. NOT 'nothing used today'."""


def utc_day(now: Optional[dt.datetime] = None) -> str:
    """The rollover key. A NAIVE datetime is refused rather than assumed UTC:
    the whole point of this key is which UTC day it is, and guessing the zone
    would move the boundary by up to a day."""
    n = now or dt.datetime.now(dt.timezone.utc)
    if n.tzinfo is None:
        raise DailyLedgerUnavailable("naive datetime has no UTC day")
    return n.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")


def _safe(s: str) -> str:
    out = "".join(c for c in str(s) if c.isalnum() or c in ".-_")
    if not out:
        raise ValueError("empty ledger component")
    return out


class DailyActionLedger:
    """Counts of today's claims, on the executor's own volume."""

    def __init__(self, root: Optional[str] = None) -> None:
        base = root or os.getenv(ROOT_ENV, "").strip() or "."
        self.dir = os.path.join(base, LEDGER_DIR)

    # ------------------------------------------------------------- paths

    def day_dir(self, mandate_id: str, day: str) -> str:
        return os.path.join(self.dir, _safe(mandate_id), _safe(day))

    def _list(self, mandate_id: str, day: str, prefix: str) -> List[str]:
        d = self.day_dir(mandate_id, day)
        try:
            return sorted(n for n in os.listdir(d) if n.startswith(prefix))
        except FileNotFoundError:
            return []                      # a day with no claims yet
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise DailyLedgerUnavailable(
                "daily ledger unreadable: %s" % type(exc).__name__) from exc

    # -------------------------------------------------------------- read

    def used(self, mandate_id: str, *, now: Optional[dt.datetime] = None,
             day: Optional[str] = None) -> Dict[str, Any]:
        """Today's counts. Raises if they cannot be established."""
        d = day or utc_day(now)
        w = self._list(mandate_id, d, WINDOW_PREFIX)
        s = self._list(mandate_id, d, SUBMISSION_PREFIX)
        return {"utc_day": d, "mandate_id": str(mandate_id),
                "windows_used": len(w), "submissions_used": len(s),
                "windows": w}

    def would_reserve_window(self, mandate_id: str, market: str, *,
                             max_windows_per_day: int,
                             now: Optional[dt.datetime] = None
                             ) -> Dict[str, Any]:
        """Would this window be granted? WRITES NOTHING.

        Requirement: explicit non-reserving verification. A window reserved
        while submission is structurally blocked is a window spent on
        nothing, so the activation sequence can ask without paying.
        """
        return self._decide_window(mandate_id, market,
                                   max_windows_per_day=max_windows_per_day,
                                   now=now, write=False)

    # ------------------------------------------------------------- write

    def reserve_window(self, mandate_id: str, market: str, *,
                       max_windows_per_day: int,
                       now: Optional[dt.datetime] = None) -> Dict[str, Any]:
        """Claim one window of today's allowance, or refuse."""
        return self._decide_window(mandate_id, market,
                                   max_windows_per_day=max_windows_per_day,
                                   now=now, write=True)

    def _decide_window(self, mandate_id: str, market: str, *,
                       max_windows_per_day: int,
                       now: Optional[dt.datetime], write: bool
                       ) -> Dict[str, Any]:
        try:
            cap = int(max_windows_per_day)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "DAILY_WINDOW_CAP_UNREADABLE"}
        if cap < 1:
            return {"ok": False, "reason": "DAILY_WINDOW_CAP_BELOW_ONE"}
        try:
            d = utc_day(now)
            name = WINDOW_PREFIX + _safe(market) + ".json"
            existing = self._list(mandate_id, d, WINDOW_PREFIX)
        except DailyLedgerUnavailable as exc:
            return {"ok": False, "reason": "DAILY_LEDGER_UNAVAILABLE",
                    "detail": {"error": str(exc)[:120]}}
        except ValueError as exc:
            return {"ok": False, "reason": "DAILY_LEDGER_BAD_COMPONENT",
                    "detail": {"error": str(exc)[:120]}}

        # RESUME, DO NOT RESPEND. Re-arming the same slug after a restart is
        # the same window, so it is granted without consuming another.
        if name in existing:
            return {"ok": True, "resumed": True, "utc_day": d,
                    "windows_used": len(existing), "max": cap,
                    "market": str(market)}
        if len(existing) >= cap:
            return {"ok": False, "reason": "DAILY_WINDOW_BUDGET_SPENT",
                    "utc_day": d, "windows_used": len(existing), "max": cap}
        if not write:
            return {"ok": True, "would_reserve": True, "utc_day": d,
                    "windows_used": len(existing), "max": cap,
                    "market": str(market)}
        return self._claim(mandate_id, d, name,
                           {"market": str(market), "utc_day": d},
                           used=len(existing), cap=cap)

    def claim_submission(self, mandate_id: str, market: str, ordinal: int, *,
                         max_submissions_per_day: int,
                         now: Optional[dt.datetime] = None) -> Dict[str, Any]:
        """Claim one submission of today's allowance, or refuse.

        `ordinal` makes the claim name unique per intended submission, so the
        same (day, market, ordinal) cannot be claimed twice -- the O_EXCL
        create is what decides a race between two workers.
        """
        try:
            cap = int(max_submissions_per_day)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "DAILY_SUBMISSION_CAP_UNREADABLE"}
        if cap < 1:
            return {"ok": False, "reason": "DAILY_SUBMISSION_CAP_BELOW_ONE"}
        try:
            d = utc_day(now)
            name = "%s%s.%d.json" % (SUBMISSION_PREFIX, _safe(market),
                                     int(ordinal))
            existing = self._list(mandate_id, d, SUBMISSION_PREFIX)
        except DailyLedgerUnavailable as exc:
            return {"ok": False, "reason": "DAILY_LEDGER_UNAVAILABLE",
                    "detail": {"error": str(exc)[:120]}}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "reason": "DAILY_LEDGER_BAD_COMPONENT",
                    "detail": {"error": str(exc)[:120]}}
        if name in existing:
            return {"ok": False, "reason": "SUBMISSION_ORDINAL_ALREADY_CLAIMED",
                    "utc_day": d, "submissions_used": len(existing),
                    "max": cap}
        if len(existing) >= cap:
            return {"ok": False, "reason": "DAILY_SUBMISSION_BUDGET_SPENT",
                    "utc_day": d, "submissions_used": len(existing),
                    "max": cap}
        return self._claim(mandate_id, d, name,
                           {"market": str(market), "ordinal": int(ordinal),
                            "utc_day": d},
                           used=len(existing), cap=cap)

    def _claim(self, mandate_id: str, day: str, name: str,
               body: Dict[str, Any], *, used: int, cap: int) -> Dict[str, Any]:
        """The one write. O_EXCL, so the filesystem decides the race."""
        d = self.day_dir(mandate_id, day)
        try:
            os.makedirs(d, exist_ok=True)
            fd = os.open(os.path.join(d, name),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                # ANOTHER WORKER WON. Not an error and not a grant: the
                # caller asked for a claim it does not hold.
                return {"ok": False, "reason": "CLAIM_RACE_LOST",
                        "utc_day": day, "name": name}
            return {"ok": False, "reason": "DAILY_CLAIM_WRITE_FAILED",
                    "detail": {"errno": exc.errno}}
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(body, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # THE CLAIM FILE STAYS. It was created, so it is spent; removing
            # it on a write failure would hand the same claim out twice.
            return {"ok": False, "reason": "DAILY_CLAIM_BODY_FAILED",
                    "detail": {"error": type(exc).__name__},
                    "note": "the claim is spent and deliberately not removed"}
        return {"ok": True, "resumed": False, "utc_day": day,
                "used_before": used, "max": cap, "name": name}
