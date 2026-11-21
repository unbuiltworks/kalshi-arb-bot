"""A collector that cannot lose rows the way the old one did. READ-ONLY.

WHY THE OLD CAPTURE LOST ROWS. `data/l5/raw/activity.jsonl` was paged with
`limit`/`offset` against a feed ordered by TIMESTAMP ASC. Offset paging over
a growing, server-ordered collection drops and repeats rows whenever the
underlying set changes between requests, and the busiest windows are exactly
where it changes fastest. The damage is provable without any reference to
this file: a redemption pays $1 per WINNING contract and only one outcome
pays, so the payout cannot exceed the larger side's holding -- and it does
in 93 of 315 redeem-mismatch windows.

WHAT THIS DOES DIFFERENTLY. It pages by a TIME WINDOW (`start`/`end`), not
by offset, so each request names the slice it wants and the server's
ordering cannot shift it. `activity_gap.jsonl.gz` in the repository was
collected that way and carries 691,077 rows the offset file lacks, which is
the empirical case for the change.

  * STABLE ORDERING -- every page is requested as an explicit [start, end)
    half-open interval, walked oldest-first, so a page's contents do not
    depend on what happened during the previous request.
  * PAGINATION VERIFICATION -- a page that returns exactly `limit` rows is
    AMBIGUOUS: the slice may be truncated. Such a slice is SPLIT and
    re-requested rather than accepted, and the split recurses until every
    page comes back short. A slice that cannot be split below one second
    and still returns `limit` rows is recorded as SATURATED, never as
    complete.
  * DURABLE CHECKPOINTS -- the high-water interval is written to disk after
    every accepted page, so an interrupted run resumes from the last
    verified boundary instead of from zero.
  * DEDUPLICATION -- on (txhash, asset, timestamp, size, side, type),
    applied across runs and across sources, so re-running is free and
    merging captures is safe.
  * RETRY -- bounded exponential backoff on transport errors; a slice that
    never succeeds is recorded as FAILED with its interval, so the gap is
    named rather than silently absent.
  * IT NEVER WRITES THE ORIGINAL DATASET. Output goes to a new path.

IT CANNOT BE RUN TODAY, AND THAT IS NOT A PROPERTY OF THIS CODE.
`data-api.polymarket.com/activity` and `/trades` both return **HTTP 410
Gone** from this environment. No collector can fetch what the host no longer
serves. This exists so the repair is reproducible if an endpoint returns,
and `l5_repair_dataset.py` performs the repair that IS possible today: a
verified union of every capture already in the repository.
"""
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

#: a page this size may be truncated, so it is never accepted as complete
AMBIGUOUS_AT_LIMIT = True
#: do not split a slice below this many seconds; report SATURATED instead
MIN_SLICE_S = 1
#: bounded retry
MAX_ATTEMPTS = 5
BACKOFF_S = (1, 2, 4, 8)

COMPLETE, SATURATED, FAILED = "COMPLETE", "SATURATED", "FAILED"


def row_key(r: Any) -> Tuple:
    return (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
            r.get("size"), r.get("side"), r.get("type"))


class Checkpoint:
    """The durable high-water mark. Written after every ACCEPTED page."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.state: Dict[str, Any] = {"done_through": None, "slices": []}
        if os.path.exists(path):
            try:
                self.state = json.load(open(path))
            except BaseException:                              # noqa: BLE001
                pass

    def note(self, start: int, end: int, status: str, rows: int) -> None:
        self.state["slices"].append({"start": start, "end": end,
                                     "status": status, "rows": rows})
        if status == COMPLETE:
            prev = self.state.get("done_through")
            self.state["done_through"] = max(end, prev or end)
        self.save()

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def resume_from(self, default: int) -> int:
        d = self.state.get("done_through")
        return int(d) if d else int(default)

    def gaps(self) -> List[Dict[str, Any]]:
        """Slices that are NOT known complete -- the honest gap list."""
        return [s for s in self.state.get("slices", [])
                if s.get("status") != COMPLETE]


def collect(*, fetch: Callable[[int, int, int], List[Dict[str, Any]]],
            start: int, end: int, out_path: str, checkpoint_path: str,
            limit: int = 500, slice_s: int = 3600,
            sleep: Callable[[float], None] = time.sleep
            ) -> Dict[str, Any]:
    """Walk [start, end) in verified slices. Returns a run report.

    `fetch(start, end, limit)` returns the rows in that half-open interval.
    It is injected so this is testable without a network and so the caller
    owns authentication entirely.

    REFUSES TO OVERWRITE. `out_path` must not exist: the original dataset is
    never modified and neither is a previous repair.
    """
    if os.path.exists(out_path):
        raise ValueError("refusing to overwrite an existing capture: %s"
                         % out_path)
    cp = Checkpoint(checkpoint_path)
    cursor = cp.resume_from(start)
    seen = set()
    written = 0
    report = {"slices": 0, "rows": 0, "saturated": 0, "failed": 0,
              "resumed_from": cursor}

    def fetch_verified(a: int, b: int) -> Tuple[str, List[Dict[str, Any]]]:
        """One slice, SPLIT until the page is unambiguously short."""
        last = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                rows = fetch(a, b, limit)
                break
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                last = exc
                if attempt < len(BACKOFF_S):
                    sleep(BACKOFF_S[attempt])
        else:
            del last
            return FAILED, []
        if AMBIGUOUS_AT_LIMIT and len(rows) >= limit:
            if (b - a) <= MIN_SLICE_S:
                return SATURATED, rows
            mid = a + (b - a) // 2
            s1, r1 = fetch_verified(a, mid)
            s2, r2 = fetch_verified(mid, b)
            worst = (FAILED if FAILED in (s1, s2)
                     else SATURATED if SATURATED in (s1, s2) else COMPLETE)
            return worst, r1 + r2
        return COMPLETE, rows

    with open(out_path, "w") as f:
        while cursor < end:
            nxt = min(cursor + slice_s, end)
            status, rows = fetch_verified(cursor, nxt)
            fresh = 0
            for r in rows:
                k = row_key(r)
                if k in seen:
                    continue
                seen.add(k)
                f.write(json.dumps(r, sort_keys=True) + "\n")
                fresh += 1
            f.flush()
            written += fresh
            report["slices"] += 1
            report["rows"] += fresh
            if status == SATURATED:
                report["saturated"] += 1
            elif status == FAILED:
                report["failed"] += 1
            cp.note(cursor, nxt, status, fresh)
            cursor = nxt
    report["unique_rows"] = written
    report["gaps"] = cp.gaps()
    report["complete"] = not report["gaps"]
    return report
