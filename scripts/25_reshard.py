"""Stage B2 -- re-shard the enriched fill table into upload-sized parts.

Monthly shards are uneven (August alone is 424,475 fills / 46 MB gzipped),
and both Grok and Perplexity cap per-file upload size well below that. This
splits the table into parts of at most --max-rows fills.

INVARIANT: a window is never split across two parts. Sequential-decision
analysis is meaningless on a half window, so a part boundary is only ever
placed between windows. Parts are therefore not exactly equal in size.

Also emits a stratified sample for fast first-pass analysis: every Nth
window, so the sample is whole windows spanning the entire corpus rather
than a truncated prefix.
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--max-rows", type=int, default=55000)
    ap.add_argument("--sample-every-nth-window", type=int, default=14)
    a = ap.parse_args()

    srcs = sorted(glob.glob(os.path.join(a.dataset,
                                         "l5_fills_enriched_2026-*.csv.gz")))
    if not srcs:
        print("no monthly shards found", file=sys.stderr)
        return 1

    header = None
    parts = []
    part_rows = []
    pending = []          # current window's rows
    pending_slug = None
    sample = []
    win_idx = 0
    total = 0

    outdir = os.path.join(a.dataset, "fills")
    os.makedirs(outdir, exist_ok=True)

    def flush_part():
        nonlocal part_rows
        if not part_rows:
            return
        n = len(parts) + 1
        d0 = part_rows[0][header.index("datetime_utc")][:10]
        d1 = part_rows[-1][header.index("datetime_utc")][:10]
        name = f"l5_fills_enriched_part{n:02d}_{d0}_to_{d1}.csv.gz"
        p = os.path.join(outdir, name)
        with gzip.open(p, "wt", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(part_rows)
        parts.append({"part": n, "file": name, "rows": len(part_rows),
                      "first_date": d0, "last_date": d1,
                      "bytes": os.path.getsize(p)})
        part_rows = []

    def close_window():
        nonlocal pending, pending_slug, win_idx
        if not pending:
            return
        win_idx += 1
        if len(part_rows) + len(pending) > a.max_rows:
            flush_part()
        part_rows.extend(pending)
        if win_idx % a.sample_every_nth_window == 0:
            sample.extend(pending)
        pending = []
        pending_slug = None

    for src in srcs:
        with gzip.open(src, "rt", newline="", encoding="utf-8") as fh:
            rd = csv.reader(fh)
            h = next(rd)
            if header is None:
                header = h
            elif header != h:
                print(f"header mismatch in {src}", file=sys.stderr)
                return 1
            si = header.index("event_slug")
            for row in rd:
                total += 1
                if row[si] != pending_slug:
                    close_window()
                    pending_slug = row[si]
                pending.append(row)
    close_window()
    flush_part()

    sp = os.path.join(a.dataset, "l5_fills_enriched_SAMPLE.csv.gz")
    with gzip.open(sp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(sample)

    rep = {
        "rows_in": total,
        "rows_in_parts": sum(p["rows"] for p in parts),
        "parts": parts,
        "n_parts": len(parts),
        "windows": win_idx,
        "max_rows_per_part": a.max_rows,
        "largest_part_bytes": max(p["bytes"] for p in parts),
        "sample": {"file": os.path.basename(sp), "rows": len(sample),
                   "bytes": os.path.getsize(sp),
                   "every_nth_window": a.sample_every_nth_window},
        "reconciles": total == sum(p["rows"] for p in parts),
    }
    with open(os.path.join(a.dataset, "RESHARD_REPORT.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep, indent=1))
    # monthly shards superseded by parts/ -- remove so nothing ambiguous ships
    for src in srcs:
        os.remove(src)
    return 0 if rep["reconciles"] else 1


if __name__ == "__main__":
    sys.exit(main())
