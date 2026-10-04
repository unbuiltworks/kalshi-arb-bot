"""Prove the raw-frame derivation against the DEVELOPMENT tape, on overlap.

The OOS rows are derived from the volume's raw `ws` frames, while the
development rows came from the recorder's printed log lines. Those are two
different pipelines, so the derivation must be shown to reproduce the
development rows before any OOS number is believed.

The volume still holds 2026-09-30 parts, and the development tape covers
2026-09-30 windows, so there is genuine overlap to check. For every slug present
in both, this compares the derived book/trade rows against the development rows
on the fields the frozen replay actually reads, and reports exact matches,
development rows the derivation MISSED, and rows it added.

A derivation that misses development rows is disqualifying. Extra derived rows
are expected -- the printed rows were a capped sample of the same history -- and
are reported separately rather than counted as disagreement.
"""
from __future__ import annotations

import collections
import gzip
import io
import json
import os
import sys

DEV_TAPE = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def key_book(r):
    return (r.get("slug"), repr(float(r["t"])), str(r.get("bid")),
            str(r.get("ask")), str(r.get("bid_qty")), str(r.get("ask_qty")))


def key_trade(r):
    return (r.get("slug"), repr(float(r["t"])), str(r.get("px")),
            str(r.get("qty")), str(r.get("dir")))


def load(path):
    out = {"book": set(), "trade": set()}
    slugs = collections.Counter()
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            k = r.get("kind")
            if k == "book":
                out["book"].add(key_book(r))
            elif k == "trade":
                out["trade"].add(key_trade(r))
            else:
                continue
            slugs[r.get("slug")] += 1
    return out, slugs


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: pmus_oos_calibrate_derivation.py <derived.jsonl> "
              "[dev tape]", file=sys.stderr)
        return 2
    derived_path = sys.argv[1]
    dev_path = sys.argv[2] if len(sys.argv) == 3 else DEV_TAPE
    der, der_slugs = load(derived_path)
    dev, dev_slugs = load(dev_path)
    overlap = sorted(set(der_slugs) & set(dev_slugs))
    report = {"derived_file": derived_path, "dev_tape": dev_path,
              "slugs_derived": len(der_slugs), "slugs_dev": len(dev_slugs),
              "slugs_overlapping": overlap, "per_kind": {}}
    verdict = "PASS"
    for kind, kf in (("book", key_book), ("trade", key_trade)):
        d_ov = {k for k in der[kind] if k[0] in overlap}
        v_ov = {k for k in dev[kind] if k[0] in overlap}
        missed = v_ov - d_ov
        extra = d_ov - v_ov
        report["per_kind"][kind] = {
            "dev_rows_on_overlap": len(v_ov),
            "derived_rows_on_overlap": len(d_ov),
            "matched": len(v_ov & d_ov),
            "dev_rows_MISSED_by_derivation": len(missed),
            "extra_derived_rows": len(extra),
            "recall_of_dev_rows": (round(len(v_ov & d_ov) / len(v_ov), 6)
                                   if v_ov else None),
            "sample_missed": [list(k) for k in sorted(missed)[:5]],
        }
        if v_ov and missed:
            verdict = "FAIL"
    if not overlap:
        verdict = "INCONCLUSIVE_NO_OVERLAP"
    report["VERDICT"] = verdict
    print(json.dumps(report, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
