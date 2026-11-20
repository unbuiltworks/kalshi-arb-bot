"""Has money moved in or out of the account since the equity baseline?

A paced, GET-only read of the venue's activity feed, newest first, that
keeps the NON-trading rows (deposits, transfers, bonuses) and how far back
the read is good for. `l5_equity_baseline.net_transfers` turns that into
either "nothing moved" or a refusal. This module decides nothing.

HOW FAR BACK IS FAR ENOUGH. The feed is newest-first but NOT strictly
ordered (the full walk of 2026-11-20 reported `monotonic: false`), so the
first row older than the baseline proves nothing. The read stops only at the
end of the feed, or after `OLD_PAGES` consecutive pages whose every dated
row is more than `MARGIN_S` older than the baseline. That margin is an
ASSUMPTION about how far out of order the venue serves rows; it is stated in
the result (`by`) and not hidden. A read that hits `MAX_PAGES` first is
incomplete, and incomplete refuses.

It writes no state anywhere, calls no ledger method and makes no request but
the feed's GET. Its result lives in memory and goes stale after `STALE_S`,
after which it again refuses.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Mapping, Optional

from src import l5_cost_basis_audit as CBA
from src import l5_venue_cost_attestation as VCA

REFRESH_S = 300.0
STALE_S = 900.0
PAGES_PER_STEP = 2
MAX_PAGES = 80
OLD_PAGES = 2
MARGIN_S = 86400.0
ERROR_BACKOFF_S = 60.0

TRADING_KINDS = frozenset(CBA.FILL_KINDS) | frozenset(
    ("activity_type_position_resolution", "position_resolution"))


def non_trading(row: Any) -> Optional[Dict[str, Any]]:
    """{"type", "time"} for a row that is not a fill or a settlement."""
    if not isinstance(row, Mapping):
        return None
    kind = str(row.get("type") or row.get("kind") or "").lower()
    if not kind or kind in TRADING_KINDS:
        return None
    t = VCA.row_time(row)
    if t is None:
        for v in row.values():
            if isinstance(v, Mapping):
                t = VCA.row_time(v)
                if t is not None:
                    break
    return {"type": kind, "time": None if t is None else t.isoformat()}


class TransferWatch:
    """Memory only. `step` on the service's tick; `view` from any thread."""

    def __init__(self) -> None:
        self._walk: Optional[Dict[str, Any]] = None
        self._seen: Optional[Dict[str, Any]] = None
        self._seen_t: Optional[float] = None
        self._since: Optional[dt.datetime] = None
        self.next_allowed = 0.0
        self.last_error: Optional[str] = None
        self.reads = 0

    def step(self, transport: Any, since: dt.datetime, now: float) -> None:
        if transport is None or now < self.next_allowed:
            return
        if self._since != since:
            #: a new baseline: nothing read for the old one applies
            self._walk, self._seen, self._seen_t = None, None, None
            self._since = since
        if self._walk is None:
            if self._seen_t is not None and now - self._seen_t < REFRESH_S:
                return
            self._walk = {"cursor": None, "pages": 0, "rows": [],
                          "old": 0}
        w = self._walk
        floor = since - dt.timedelta(seconds=MARGIN_S)
        for _ in range(PAGES_PER_STEP):
            got = CBA.walk_activities(transport, max_pages=w["pages"] + 1,
                                      cursor=w["cursor"], rows=[],
                                      pages_done=w["pages"])
            ended = str(got.get("ended") or "")
            if ended.startswith("error"):
                self.last_error = ended
                self._walk = None
                self.next_allowed = now + ERROR_BACKOFF_S
                return
            w["pages"] = int(got.get("pages") or w["pages"] + 1)
            times: List[dt.datetime] = []
            for r in got.get("rows") or ():
                if not isinstance(r, Mapping):
                    continue
                t = VCA.row_time(r)
                if t is not None:
                    times.append(t)
                rec = non_trading(r)
                if rec is not None:
                    w["rows"].append(rec)
            w["old"] = w["old"] + 1 if (times and max(times) < floor) else 0
            if ended == "eof":
                return self._finish(now, floor, "END_OF_FEED")
            if w["old"] >= OLD_PAGES:
                return self._finish(now, floor, "TIME_FLOOR_WITH_MARGIN")
            nxt = got.get("next_cursor")
            if not nxt or w["pages"] >= MAX_PAGES:
                return self._finish(now, None, "INCOMPLETE")
            w["cursor"] = str(nxt)

    def _finish(self, now: float, back: Optional[dt.datetime],
                by: str) -> None:
        w = self._walk or {}
        self._seen = {"complete_back_to": (None if back is None
                                           else back.isoformat()),
                      "rows": list(w.get("rows") or ()),
                      "by": by, "pages": w.get("pages"),
                      "margin_s": MARGIN_S}
        self._seen_t = now
        self._walk = None
        self.last_error = None
        self.reads += 1

    def view(self, since: dt.datetime, now: float) -> Dict[str, Any]:
        """What `net_transfers` reads. Never raises; stale or absent is a
        result with `complete_back_to` None, which refuses."""
        if (self._seen is None or self._seen_t is None
                or self._since != since):
            return {"complete_back_to": None, "rows": [],
                    "by": "NOT_READ_YET", "last_error": self.last_error}
        age = now - self._seen_t
        if age > STALE_S or age < 0:
            return {"complete_back_to": None, "rows": [], "by": "STALE",
                    "age_s": round(age, 1), "last_error": self.last_error}
        return dict(self._seen, age_s=round(age, 1),
                    last_error=self.last_error)
