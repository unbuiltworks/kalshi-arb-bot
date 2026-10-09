"""Stage G -- uncompressed views, for a reader that cannot gunzip.

An AI reading this package out of the Git repository (rather than by file
upload) generally cannot decompress a `.csv.gz`. Without this stage such a
reader sees the prose and the case studies but none of the actual tables,
which is most of the package.

So the two tables that matter most are also written as plain CSV:

  plain/l5_window_summary.csv   all 17,046 windows, ~5 MB
  plain/l5_fills_sample.csv     whole windows, every Nth, sized to a budget

The full 760,817-fill table is NOT duplicated uncompressed: it is ~240 MB
and no repo reader will ingest it. That table stays gzipped in
`dataset/fills/`, for upload or for local use.

The sample is whole windows, never truncated mid-window, so a sequential
read of it is still meaningful.
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
    ap.add_argument("--package", required=True)
    ap.add_argument("--budget-mb", type=float, default=8.0)
    a = ap.parse_args()
    out = os.path.join(a.package, "plain")
    os.makedirs(out, exist_ok=True)
    rep = {}

    src = os.path.join(a.package, "dataset", "l5_window_summary.csv.gz")
    dst = os.path.join(out, "l5_window_summary.csv")
    n = 0
    with gzip.open(src, "rt", newline="", encoding="utf-8") as fi, \
            open(dst, "w", newline="", encoding="utf-8") as fo:
        for line in fi:
            fo.write(line)
            n += 1
    rep["l5_window_summary.csv"] = {"rows": n - 1,
                                    "bytes": os.path.getsize(dst)}

    # Size the stride from a measured average row width rather than a guess.
    parts = sorted(glob.glob(os.path.join(
        a.package, "dataset", "fills", "l5_fills_enriched_part*.csv.gz")))
    header = None
    sample_bytes = 0
    rows_measured = 0
    with gzip.open(parts[0], "rt", newline="", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i == 0:
                header = line
                continue
            sample_bytes += len(line.encode())
            rows_measured += 1
            if rows_measured >= 20000:
                break
    avg = sample_bytes / rows_measured
    want_rows = int(a.budget_mb * 1e6 / avg)
    stride = max(1, round(760817 / want_rows))

    dst2 = os.path.join(out, "l5_fills_sample.csv")
    win_idx = 0
    prev_slug = None
    keep = False
    written = 0
    with open(dst2, "w", newline="", encoding="utf-8") as fo:
        fo.write(header)
        for p in parts:
            with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
                rd = csv.reader(fh)
                h = next(rd)
                si = h.index("event_slug")
                for row in rd:
                    if row[si] != prev_slug:
                        prev_slug = row[si]
                        win_idx += 1
                        keep = (win_idx % stride == 0)
                    if keep:
                        fo.write(",".join(
                            '"' + c.replace('"', '""') + '"'
                            if ("," in c or '"' in c) else c
                            for c in row) + "\n")
                        written += 1
    rep["l5_fills_sample.csv"] = {
        "rows": written, "bytes": os.path.getsize(dst2),
        "every_nth_window": stride, "windows": win_idx // stride,
        "budget_mb": a.budget_mb}

    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(f"""# Uncompressed views

For a reader that cannot decompress `.csv.gz` — which includes most AI
repository browsers. Same data, same columns, no gzip.

| file | rows | size | what |
|---|---|---|---|
| `l5_window_summary.csv` | {rep['l5_window_summary.csv']['rows']:,} | {rep['l5_window_summary.csv']['bytes'] / 1e6:.1f} MB | **every** window in the corpus, one row each |
| `l5_fills_sample.csv` | {written:,} | {rep['l5_fills_sample.csv']['bytes'] / 1e6:.1f} MB | individual fills — every {stride}th window, {win_idx // stride:,} whole windows |

`l5_window_summary.csv` is **complete**: all 17,046 windows, nothing
sampled away. Any population-level question about windows can be answered
from it exactly.

`l5_fills_sample.csv` is a **sample of whole windows**, never truncated
mid-window, so reading it sequentially is still meaningful. It spans the
full corpus period rather than a prefix.

The complete 760,817-fill table is **not** duplicated here: uncompressed
it is roughly 240 MB and no repository reader will ingest it. It lives in
`../dataset/fills/` as 14 gzipped parts, for file upload or local use. If
a conclusion needs the full table rather than this sample, say so — the
parts can be uploaded directly, and `../scripts/50_test_hypothesis.py`
runs against all 760,817 fills.

Column meanings: `../DATA_DICTIONARY.md`.
""")
    with open(os.path.join(out, "PLAIN_REPORT.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
