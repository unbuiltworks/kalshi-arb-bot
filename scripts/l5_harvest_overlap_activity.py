"""Harvest L5 public activity for 2026-09-27..09-30 -- the PMUS overlap window.

WHY THIS EXISTS. L5's existing harvests stop at 2026-09-23, while the PMUS
recorder archive starts 2026-09-27, so no replayed window has ever contained an
L5 action. This closes that gap so a window-level (NOT same-book) cross-venue
comparison becomes possible on the same underlying and the same 15m epochs.

OFFSET TRUNCATION IS THE REAL RISK. `?offset=` caps out, so a busy window
silently returns a PREFIX of its rows. Accepting that would understate L5's
action rate -- the very quantity under study. So this harvester:

  * slices by `start`/`end` and pages inside each slice;
  * treats a slice that reaches OFFSET_SUSPECT while still returning full pages
    as UNTRUSTED and SUBDIVIDES it, recursively, down to MIN_SLICE_S;
  * records, per slice, whether it terminated on a short page (trustworthy) or
    on the cap (not), so completeness is auditable rather than assumed;
  * writes raw rows verbatim with the exact query params and fetched_at.

Nothing here infers maker/taker, cancels, resting orders or book state. The feed
does not carry them.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
LIMIT = 500
OFFSET_SUSPECT = 4500          # below the ~5,500 cap the README documents
MIN_SLICE_S = 300
TYPES = ("TRADE", "REDEEM", "REWARD", "MAKER_REBATE", "TAKER_REBATE")

START = 1790467200             # 2026-09-27T00:00:00Z
END = 1790812800               # 2026-10-01T00:00:00Z


def get(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as fh:
                return json.loads(fh.read().decode())
        except Exception:                                    # noqa: BLE001
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def key(r):
    return (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
            str(r.get("size")), str(r.get("price")), r.get("type"))


def page_slice(kind, s, e, audit):
    """Page one slice. Returns (rows, trusted). `trusted` is False when paging
    stopped because of the offset cap rather than a short page."""
    rows, off, trusted = {}, 0, True
    while True:
        url = (f"{BASE}/activity?user={ADDR}&limit={LIMIT}&offset={off}"
               f"&type={kind}&start={s}&end={e}")
        b = get(url)
        if b is None:
            audit.append({"kind": kind, "start": s, "end": e, "offset": off,
                          "result": "request_failed"})
            return list(rows.values()), False
        for r in b:
            rows[key(r)] = r
        if len(b) < LIMIT:
            return list(rows.values()), trusted      # short page == complete
        off += LIMIT
        if off >= OFFSET_SUSPECT:
            audit.append({"kind": kind, "start": s, "end": e,
                          "offset": off, "result": "offset_cap_subdivide"})
            return list(rows.values()), False


def harvest(kind, s, e, audit, depth=0):
    rows, trusted = page_slice(kind, s, e, audit)
    if trusted or (e - s) <= MIN_SLICE_S:
        if not trusted:
            audit.append({"kind": kind, "start": s, "end": e,
                          "result": "UNTRUSTED_AT_MIN_SLICE"})
        return rows
    mid = s + (e - s) // 2
    return harvest(kind, s, mid, audit, depth + 1) + \
        harvest(kind, mid, e, audit, depth + 1)


def main(argv):
    out_path = argv[1] if len(argv) > 1 else \
        "data/l5/raw/sep/l5_activity_sep2730_raw.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    audit, all_rows, per = [], {}, {}
    for kind in TYPES:
        got = {}
        for h in range(START, END, 3600):
            for r in harvest(kind, h, min(h + 3600, END), audit):
                got[key(r)] = r
        per[kind] = len(got)
        all_rows.update(got)
        print(f"{kind:<14} {len(got):7d}", flush=True)
    rows = [r for r in all_rows.values()
            if START <= (r.get("timestamp") or 0) < END]
    doc = {
        "source": f"{BASE}/activity",
        "params": "user,limit,offset,type,start,end",
        "user": ADDR,
        "fetched_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": "2026-09-27T00:00Z..2026-10-01T00:00Z",
        "window_start_epoch": START, "window_end_epoch": END,
        "slice_seconds": 3600, "min_slice_seconds": MIN_SLICE_S,
        "offset_suspect": OFFSET_SUSPECT,
        "counts_by_type": per,
        "n_rows": len(rows),
        "audit": audit,
        "untrusted_slices": [a for a in audit
                             if a["result"] != "offset_cap_subdivide"],
        "note": "Raw rows verbatim. No maker/taker, cancel, resting-order or "
                "book state is present in this feed and none is inferred.",
        "rows": rows,
    }
    with open(out_path, "w") as fh:
        json.dump(doc, fh)
    print(f"saved {out_path}  rows={len(rows)}  "
          f"subdivisions={sum(1 for a in audit if a['result'] == 'offset_cap_subdivide')}  "
          f"untrusted={len(doc['untrusted_slices'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
