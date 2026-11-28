"""BEFORE/AFTER benchmark for the shadow-ledger persistence bottleneck (#209).

WHAT PRODUCTION REPORTED. Deployment 18deb02d, executor service, read from
the service's own `L5_SHADOW_LEDGER` line at 2026-11-08T00:06:24Z:

    {"finalized": 2294, "pending": 0, "bytes": 292732040,
     "last_save_s": 6.7605, "saves_this_process": 9, "max_pending": 16}

So the hypothesis the v1 `save` docstring refused to act on is now MEASURED
on the running service: 2,294 scored windows, 292.7 MB, 6.76 s per save,
and `save` is called on every park and every finalize -- twice a window.
Mean record size is 292732040 / 2294 = 127,608 bytes.

WHAT THIS SCRIPT MEASURES. The same two code paths at the same record size,
on local disk, so that before and after are compared on identical data:

    v1   the whole-file rewrite: `json.dump(state)` to a tmp and os.replace
    v2   the append-only log: one line appended per finalized record

`LegacyLedger` below is the v1 `load`/`save` verbatim. It is kept here, and
not deleted with the old class, so the comparison stays runnable and so the
migration has something to read. `test_l5_shadow_ledger_retention.py` pins
that v2 reads exactly what this writer writes.

    python3 analysis/l5/issue209/shadow_ledger_benchmark.py --counts 96,480,960,1440,2294

Numbers are latency and bytes only. Nothing here is a trading result.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_two_sided_shadow as TS           # noqa: E402

#: the production mean, 292732040 / 2294
RECORD_BYTES = 127608


class LegacyLedger:
    """The v1 `ShadowLedger` persistence, verbatim, for comparison only."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.state = {"pending": {}, "finalized": {}}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r") as fh:
                got = json.load(fh)
            if isinstance(got, dict):
                self.state = {"pending": dict(got.get("pending") or {}),
                              "finalized": dict(got.get("finalized") or {})}
        except FileNotFoundError:
            pass
        except Exception:                                      # noqa: BLE001
            self.state = {"pending": {}, "finalized": {}}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        t0 = time.monotonic()
        with open(tmp, "w") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.path)
        self.last_save_s = round(time.monotonic() - t0, 4)


def record(slug: str, *, target_bytes: int = RECORD_BYTES) -> dict:
    """A scored-report-shaped record of the production mean size."""
    rec = {
        "LABEL": "L5_TWO_SIDED_SHADOW",
        "market": slug,
        "state": TS.FINALIZED,
        "ticks": 4000,
        "spread_histogram": {str(i): i for i in range(40)},
        "cells": [{"policy": "SKEWED", "envelope": "100", "depth_ticks": i % 5,
                   "matched_shares": "10", "residual_shares": "-1",
                   "residual_cost": "0.42", "matched_pnl_modelled": "0.03",
                   "residual_pnl_modelled": "-0.14",
                   "net_pnl_modelled": "-0.11", "state": TS.FINALIZED,
                   "detail": ""} for i in range(27)],
    }
    short = target_bytes - len(json.dumps(rec))
    if short > 0:
        pad = short // len(rec["cells"])
        for c in rec["cells"]:
            c["detail"] = "x" * max(0, pad)
    return rec


def seed_legacy(path: str, n: int) -> float:
    """Write a v1 file holding `n` scored windows. Returns its MB."""
    led = LegacyLedger(path)
    led.state["finalized"] = {"w%05d" % i: record("w%05d" % i)
                              for i in range(n)}
    led.save()
    return os.path.getsize(path) / 1e6


def bench(n: int, root: str) -> dict:
    d = os.path.join(root, "n%d" % n)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "two_sided_shadow.json")
    mb = seed_legacy(path, n)

    # the SAME seeded history for both paths, before either adds to it
    p2 = os.path.join(d, "v2.json")
    shutil.copy(path, p2)

    # --- v1: one more scored window costs a whole-file rewrite
    led1 = LegacyLedger(path)
    t0 = time.monotonic()
    led1.load()
    v1_load = time.monotonic() - t0
    led1.state["finalized"]["fresh"] = record("fresh")
    led1.save()
    v1_save = led1.last_save_s

    # --- v2: same starting data, migrated once, then one append
    t0 = time.monotonic()
    led2 = TS.ShadowLedger(p2)
    v2_migrate = time.monotonic() - t0
    t0 = time.monotonic()
    led2 = TS.ShadowLedger(p2)                       # already migrated
    v2_load = time.monotonic() - t0
    led2.state["finalized"]["fresh"] = record("fresh")
    led2.save()
    v2_save = led2.last_save_s
    return {"n": n, "mb": round(mb, 1),
            "v1_save_s": round(v1_save, 4), "v1_load_s": round(v1_load, 4),
            "v2_save_s": round(v2_save, 4), "v2_load_s": round(v2_load, 4),
            "v2_migrate_s": round(v2_migrate, 2),
            "v2_bytes_written": getattr(led2, "last_save_bytes_written", None),
            "v2_finalized": len(led2.state["finalized"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", default="96,480,960,1440,2294")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    root = a.root or tempfile.mkdtemp(prefix="shadow209-")
    rows = []
    for n in [int(x) for x in a.counts.split(",") if x.strip()]:
        r = bench(n, root)
        rows.append(r)
        print(json.dumps(r), flush=True)
    print()
    hdr = ("     N | file MB | v1 save s | v1 load s | v2 save s | "
           "v2 load s | v2 append B | migrate s")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%6d | %7.1f | %9.4f | %9.4f | %9.4f | %9.4f | %11s | %9.2f"
              % (r["n"], r["mb"], r["v1_save_s"], r["v1_load_s"],
                 r["v2_save_s"], r["v2_load_s"],
                 r["v2_bytes_written"], r["v2_migrate_s"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
