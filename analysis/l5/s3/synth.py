"""Synthetic two-sided book sequences for S3 verification.

The recorded PMUS tape carries one outcome per row, so it cannot exercise
the two-sided reservation path. These generators produce both outcomes with
declared ground truth, so a test can assert what the instrument SHOULD
conclude rather than only that it ran.

Synthetic data is for VERIFICATION ONLY and is never a measurement result.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

LABEL = "SYNTHETIC_VERIFICATION_ONLY_NOT_A_MEASUREMENT"


def row(slug, t, outcome, bid, ask, depth=100.0):
    return {"kind": "book", "slug": slug, "outcome": outcome,
            "t": float(t), "state": "MARKET_STATE_OPEN",
            "bids": [[("%.4f" % bid), ("%.4f" % depth)]],
            "offers": [[("%.4f" % ask), "100.0000"]],
            "labels": LABEL}


def cheap_pair(slug="synth-cheap", n=300):
    """Both sides cheap: pair sum ~0.90, well inside the 0.96 reservation.
    Depth drains on UP so a fill becomes evidenced; DOWN holds."""
    out = []
    for t in range(n):
        up_bid = 0.45
        dn_bid = 0.45
        depth = max(5.0, 100.0 - t)
        out.append(row(slug, t, "up", up_bid, up_bid + 0.01, depth))
        out.append(row(slug, t, "down", dn_bid, dn_bid + 0.01, 100.0))
    # trade through UP at the end -> EVIDENCED_FILL
    out.append(row(slug, n, "up", 0.42, 0.43, 50.0))
    out.append(row(slug, n, "down", 0.45, 0.46, 100.0))
    for t in range(n + 1, n + 90):
        out.append(row(slug, t, "up", 0.40, 0.41, 80.0))
        out.append(row(slug, t, "down", 0.45, 0.46, 100.0))
    return out


def expensive_pair(slug="synth-expensive", n=200):
    """Pair sum ~1.02, above the 0.96 reservation on both sides: the
    instrument must stand down and never place."""
    out = []
    for t in range(n):
        out.append(row(slug, t, "up", 0.52, 0.53))
        out.append(row(slug, t, "down", 0.50, 0.51))
    return out


def no_execution(slug="synth-noexec", n=400):
    """Cheap enough to place, but the touch never moves and depth never
    changes: orders rest and nothing is ever evidenced. This is the VALID
    ZERO-FILL case -- a real answer, not a broken instrument."""
    return [r for t in range(n)
            for r in (row(slug, t, "up", 0.45, 0.46, 100.0),
                      row(slug, t, "down", 0.45, 0.46, 100.0))]


GENERATORS = {"cheap_pair": cheap_pair, "expensive_pair": expensive_pair,
              "no_execution": no_execution}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=sorted(GENERATORS), required=True)
    ap.add_argument("--markets", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows: List[Dict[str, Any]] = []
    for i in range(a.markets):
        rows.extend(GENERATORS[a.kind](slug="synth-%s-%02d" % (a.kind, i)))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("wrote %d rows to %s" % (len(rows), a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
