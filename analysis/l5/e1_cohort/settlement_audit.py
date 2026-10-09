"""THE SETTLEMENT AUDIT'S OWN SCANNER. Bounded, resumable, 429-aware.

WHY THIS IS A SEPARATE MODULE. The trading lifecycle is frozen at commit
8b729a94 / fingerprint c576f741, and `order_state.py` is imported by the
decision loop. Editing it to fix an AUDIT problem would change a frozen
trading module for a reason that has nothing to do with trading. So the
scanner lives here, nothing in the loop imports it, and the frozen modules
stay byte-for-byte identical. `order_state.settlement_for` is REUSED
unchanged -- it already accepts pre-fetched rows.

WHAT THE VENUE ACTUALLY SUPPORTS, established by probe rather than assumed:

    &types=ACTIVITY_TYPE_POSITION_RESOLUTION   HONOURED
    &marketSlug= / &market= / &slug= / &marketSlugs=   IGNORED
    &type= / &activityType=                    IGNORED
    &startDate= / &start= / &from=             IGNORED
    &limit=100                                 REJECTED 400 (50 is the cap)

Each verdict came from BEHAVIOUR against an unfiltered baseline, because a
parameter a server ignores returns 200 with rows exactly like one that
works. `type` and `types` differ by one character and only the plural is
real -- which is precisely why none of this was assumed.

So the walk that hit HTTP 429 after twenty pages of 1,100 rows now asks for
the 92 position resolutions directly: about two pages instead of twenty-two.
The resumable machinery below is still here, because a filter that narrows
a feed does not make a rate limit impossible -- it makes it rare.

  * the type filter is VERIFIED at runtime: if a row comes back that is not
    a resolution, the filter is no longer being honoured, and the scan says
    so instead of silently trusting it;
  * HTTP 429 backs off exponentially within a bounded budget;
  * progress is durable -- the cursor and the rows are written as they
    arrive, so a scan killed mid-walk resumes instead of restarting;
  * an incomplete scan is reported as incomplete and NEVER as an absence.

Read-only. GETs only, no order body, nothing that can mutate.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from analysis.l5.e1_cohort import order_state as OS

#: the one filter the venue honours, established by probe.
RESOLUTION_TYPE = "ACTIVITY_TYPE_POSITION_RESOLUTION"
TYPES_PARAM = "&types=%s" % RESOLUTION_TYPE

#: 50 is the venue's page cap: limit=100 is rejected with HTTP 400.
PAGE_SIZE = 50
BASE_PATH = "/v1/portfolio/activities?limit=%d" % PAGE_SIZE

#: 429 handling. Bounded: an audit that retries forever is a new outage.
MAX_PAGES = 40
MAX_RETRIES_PER_PAGE = 4
BACKOFF_S = (2.0, 5.0, 12.0, 30.0)
PAGE_PAUSE_S = 0.5


def _is_rate_limit(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 429


class ResumableScan:
    """Durable progress for one scan.

    The state file holds the cursor and the rows gathered so far. It is
    rewritten atomically on each page, so a scan that dies mid-walk comes
    back knowing where it was. It is pure audit state: no ledger, no latch,
    no cohort progress, and nothing here is append-only evidence -- a
    re-scan may legitimately replace it.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.cursor = ""
        self.rows: List[Dict[str, Any]] = []
        self.pages_read = 0
        self.complete = False
        self.rate_limit_hits = 0
        self.resumed_from_page = 0
        if path:
            self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf8") as fh:
                st = json.load(fh)
        except Exception:                                      # noqa: BLE001
            return                     # a damaged state file starts over
        if not isinstance(st, dict):
            return
        self.cursor = str(st.get("cursor") or "")
        rows = st.get("rows")
        self.rows = [r for r in rows if isinstance(r, dict)] \
            if isinstance(rows, list) else []
        self.pages_read = int(st.get("pages_read") or 0)
        self.complete = bool(st.get("complete"))
        self.resumed_from_page = self.pages_read

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf8") as fh:
            json.dump({"cursor": self.cursor, "rows": self.rows,
                       "pages_read": self.pages_read,
                       "complete": self.complete,
                       "rate_limit_hits": self.rate_limit_hits,
                       "saved_at": time.time()}, fh, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def scan_resolutions(reader: Callable[[str], Any], *,
                     state_path: Optional[str] = None,
                     max_pages: int = MAX_PAGES,
                     refresh: bool = False,
                     sleep_fn: Callable[[float], None] = time.sleep
                     ) -> Dict[str, Any]:
    """Walk the position resolutions, filtered, resumable and 429-aware.

    `refresh` RE-READS rather than serving a finished scan from durable
    progress. A completed scan means "every page up to the moment it
    finished", not "nothing will ever resolve again": the 22:00z and
    00:00z pairs settled AFTER the first scan completed, and without this
    the audit answered UNCONFIRMED for them forever while cheerfully
    reporting complete: true and 154 rows. Durable progress is there to
    survive a 429 mid-walk, not to make the audit blind to new
    settlements.
    """
    scan = ResumableScan(state_path)
    if refresh:
        # re-walk from the first page, keeping nothing stale behind.
        scan.complete = False
        scan.pages_read = 0
        scan.rows = []
    out: Dict[str, Any] = {
        "filter": TYPES_PARAM,
        "refresh": bool(refresh),
        "page_size": PAGE_SIZE,
        "resumed_from_page": scan.resumed_from_page,
        "filter_honoured": None,
        "rate_limit_hits": scan.rate_limit_hits,
        "errors": [],
    }
    if scan.complete and not refresh:
        out.update({"rows": scan.rows, "pages_read": scan.pages_read,
                    "complete": True, "filter_honoured": True,
                    "served_entirely_from_durable_progress": True})
        return out

    while scan.pages_read < int(max_pages):
        path = BASE_PATH + TYPES_PARAM + (
            "&cursor=%s" % scan.cursor if scan.cursor else "")
        body = None
        for attempt in range(MAX_RETRIES_PER_PAGE + 1):
            try:
                body = reader(path)
                break
            except Exception as exc:                           # noqa: BLE001
                if _is_rate_limit(exc) and attempt < MAX_RETRIES_PER_PAGE:
                    scan.rate_limit_hits += 1
                    sleep_fn(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                    continue
                out["errors"].append({
                    "page": scan.pages_read + 1,
                    "error": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                    "retries_spent": attempt})
                scan.save()
                out.update({"rows": scan.rows,
                            "pages_read": scan.pages_read,
                            "complete": False,
                            "rate_limit_hits": scan.rate_limit_hits,
                            "ABSENCE_IS_NOT_EVIDENCE": True})
                return out
        if not isinstance(body, Mapping):
            out["errors"].append({"page": scan.pages_read + 1,
                                  "error": "UNPARSABLE"})
            scan.save()
            out.update({"rows": scan.rows, "pages_read": scan.pages_read,
                        "complete": False,
                        "ABSENCE_IS_NOT_EVIDENCE": True})
            return out

        page = [r for r in (body.get("activities") or [])
                if isinstance(r, Mapping)]
        # VERIFY THE FILTER EVERY PAGE. If a non-resolution arrives, the
        # venue has stopped honouring `types` and this scan is no longer
        # the narrow one it believes itself to be.
        off = [str(r.get("type")) for r in page
               if str(r.get("type")) != RESOLUTION_TYPE]
        if off:
            out["filter_honoured"] = False
            out["filter_violation_types"] = sorted(set(off))
        elif out["filter_honoured"] is None:
            out["filter_honoured"] = True

        scan.rows.extend(dict(r) for r in page)
        scan.pages_read += 1
        scan.cursor = str(body.get("nextCursor") or "")
        if body.get("eof") or not scan.cursor:
            scan.complete = True
            scan.save()
            out.update({"rows": scan.rows, "pages_read": scan.pages_read,
                        "complete": True,
                        "rate_limit_hits": scan.rate_limit_hits})
            return out
        scan.save()
        sleep_fn(PAGE_PAUSE_S)

    scan.save()
    out.update({"rows": scan.rows, "pages_read": scan.pages_read,
                "complete": False, "hit_page_budget": True,
                "rate_limit_hits": scan.rate_limit_hits,
                "ABSENCE_IS_NOT_EVIDENCE": True})
    return out


def reconcile(reader: Callable[[str], Any], *, slug: str,
              expected_qty: Any, state_path: Optional[str] = None,
              sleep_fn: Callable[[float], None] = time.sleep
              ) -> Dict[str, Any]:
    """Scan, then attribute through the FROZEN `order_state.settlement_for`.

    Attribution is not reimplemented here: `settlement_for` already matches
    on exact quantity and is covered by the frozen suite. This only feeds
    it rows it could not otherwise obtain.
    """
    scan = scan_resolutions(reader, state_path=state_path,
                            sleep_fn=sleep_fn)
    res = {"rows": scan["rows"], "complete": scan["complete"],
           "pages_read": scan["pages_read"],
           "error": (scan["errors"][0]["error"] if scan["errors"]
                     else None)}
    out = OS.settlement_for(reader, slug=slug, expected_qty=expected_qty,
                            resolutions=res)
    out["scan"] = {k: v for k, v in scan.items() if k != "rows"}
    out["scan"]["rows_gathered"] = len(scan["rows"])
    out["attribution_is_the_frozen_settlement_for"] = True
    return out
