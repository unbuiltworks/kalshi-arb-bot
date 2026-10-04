"""Reduce one raw recorder part to the rows the OOS replay needs.

Run on the retrieval runner, immediately after each hourly part is read, so
only a few megabytes travel instead of the whole 12 GiB tape. The reduction is
a FILTER and nothing else: no row is rewritten, reordered, resampled or
deduplicated, and every kept line is the recorder's own bytes.

Kept:
  * `book` and `trade` rows for BTC 15-minute up/down markets -- what the
    replay reads;
  * `provenance` and `session` rows -- the recorder's own record that
    collection was continuous, which is the completeness evidence.

Dropped: rows for every other market. Those cannot affect a BTC 15m window.
"""
from __future__ import annotations

import json
import sys

MARKET = "btc-updown-15m"
KEEP_KINDS = ("provenance", "session")


def reduce_part(src: str, dst: str) -> tuple:
    kept = total = bad = 0
    with open(src) as f, open(dst, "w") as o:
        for line in f:
            total += 1
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                bad += 1
                continue
            k = r.get("kind")
            if k in ("book", "trade"):
                if MARKET not in (r.get("slug") or ""):
                    continue
            elif k not in KEEP_KINDS:
                continue
            o.write(line if line.endswith("\n") else line + "\n")
            kept += 1
    return kept, total, bad


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pmus_oos_reduce_part.py <raw part> <out>",
              file=sys.stderr)
        return 2
    kept, total, bad = reduce_part(sys.argv[1], sys.argv[2])
    print(json.dumps({"kept": kept, "total": total, "unparsable": bad}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
