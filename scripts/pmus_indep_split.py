"""Split a PMUS BTC15m state table into the preregistered D and C sets.

analysis/pmus_independent/PREREGISTRATION.md fixes the boundary:
  D = venue window start in [2026-10-02 00:00Z, 2026-10-04 00:00Z)
  C = window start >= 2026-10-04 00:00Z
Any window starting before 2026-10-02 00:00Z is a hard error: 2026-10-01 is the
reserved holdout and must never reach this lane.

Output is a compact CSV (one row per causal second) with only the columns the
preregistered rules use. No value is recomputed; strings are copied as written
by pmus_state_table.py.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import sys

COLS = ("slug", "t", "seconds_remaining", "z", "sigma_1s", "raw_distance",
        "n_published", "up_ask", "up_ask_qty", "down_px", "down_qty",
        "spread", "book_age_s", "trades_60s", "signed_vol_60s", "outcome_up")

D_LO = int(dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc).timestamp())
C_LO = int(dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc).timestamp())


def wstart(slug: str) -> int:
    p = slug.split("-")
    y, m, d, hm = int(p[4]), int(p[5]), int(p[6]), p[7]
    return int(dt.datetime(y, m, d, int(hm[:2]), int(hm[2:4]),
                           tzinfo=dt.timezone.utc).timestamp())


def main() -> int:
    src, out_d, out_c = sys.argv[1:4]
    fd = gzip.open(out_d, "wt", newline="")
    fc = gzip.open(out_c, "wt", newline="")
    wd, wc = csv.writer(fd), csv.writer(fc)
    wd.writerow(COLS)
    wc.writerow(COLS)
    seen = {"D": set(), "C": set()}
    rows = {"D": 0, "C": 0}
    with open(src) as f:
        for line in f:
            r = json.loads(line)
            ws = wstart(r["slug"])
            if ws < D_LO:
                raise SystemExit("RESERVED WINDOW REACHED THE LANE: %s"
                                 % r["slug"])
            k = "D" if ws < C_LO else "C"
            (wd if k == "D" else wc).writerow([r.get(c) for c in COLS])
            seen[k].add(r["slug"])
            rows[k] += 1
    fd.close()
    fc.close()
    print(json.dumps({"windows_D": len(seen["D"]), "rows_D": rows["D"],
                      "windows_C": len(seen["C"]), "rows_C": rows["C"],
                      "first_D": min(seen["D"]) if seen["D"] else None,
                      "last_D": max(seen["D"]) if seen["D"] else None,
                      "first_C": min(seen["C"]) if seen["C"] else None,
                      "last_C": max(seen["C"]) if seen["C"] else None},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
