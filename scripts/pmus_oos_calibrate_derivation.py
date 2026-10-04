"""Prove the raw-frame derivation against the DEVELOPMENT tape, on overlap.

The OOS rows are derived from the volume's raw `ws` frames; the development rows
came from the recorder's printed log lines. Two pipelines, so the derivation is
checked against rows this programme already trusts before any OOS number is
believed.

TIMESTAMPS CANNOT BE COMPARED FOR EQUALITY, AND THAT IS NOT A DEFECT. The
recorder reads the clock TWICE per frame: once for the raw row it persists
(`append(root, {"kind": "ws", "t": datetime.now(timezone.utc).isoformat(), ...})`)
and again when it folds that frame into state (`apply_events(mstate, msg,
time.time())`). The development rows carry the SECOND reading; a derivation
replaying the persisted frames can only know the FIRST. The two differ by the
microseconds between those statements, so an exact-equality key reports zero
matches on rows that are in fact the same event -- which is exactly what the
first version of this harness did.

So a development row is considered reproduced when some derived row for the same
market carries IDENTICAL content -- every field the frozen replay reads -- at a
timestamp within TOLERANCE_S. Content is matched exactly and never fuzzily; only
the clock is given slack, and the observed slack is reported so the allowance can
be judged rather than trusted.

A development row with no such match is a MISS and is disqualifying. Extra
derived rows are expected, because the printed rows were a capped sample of the
same history, and are reported separately rather than scored as disagreement.
"""
from __future__ import annotations

import bisect
import collections
import gzip
import io
import json
import sys

DEV_TAPE = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"
#: the gap between the recorder's two clock reads for one frame. Generous by
#: three orders of magnitude relative to the statements between them, and still
#: far below the 1-second feature bucket the state table uses.
TOLERANCE_S = 0.050


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def content_book(r):
    return (r.get("slug"), str(r.get("bid")), str(r.get("ask")),
            str(r.get("bid_qty")), str(r.get("ask_qty")))


def content_trade(r):
    return (r.get("slug"), str(r.get("px")), str(r.get("qty")),
            str(r.get("dir")), str(r.get("bid_at_trade")),
            str(r.get("ask_at_trade")))


CONTENT = {"book": content_book, "trade": content_trade}


def load(path):
    rows = {"book": [], "trade": []}
    slugs = collections.Counter()
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            k = r.get("kind")
            if k not in rows:
                continue
            try:
                t = float(r["t"])
            except Exception:                                  # noqa: BLE001
                continue
            rows[k].append((CONTENT[k](r), t))
            slugs[r.get("slug")] += 1
    return rows, slugs


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
              "tolerance_s": TOLERANCE_S,
              "why_tolerance": "the recorder reads the clock twice per frame: "
                               "the persisted raw row carries the first "
                               "reading, the development row the second",
              "slugs_derived": len(der_slugs), "slugs_dev": len(dev_slugs),
              "slugs_overlapping": overlap, "per_kind": {}}
    verdict = "PASS"
    for kind in ("book", "trade"):
        idx: dict = {}
        for c, t in der[kind]:
            if c[0] in overlap:
                idx.setdefault(c, []).append(t)
        for v in idx.values():
            v.sort()
        dv = [(c, t) for c, t in dev[kind] if c[0] in overlap]
        matched, missed, deltas = 0, [], []
        for c, t in dv:
            ts = idx.get(c)
            if not ts:
                missed.append((c, t, "no_identical_content"))
                continue
            i = bisect.bisect_left(ts, t)
            best = None
            for j in (i - 1, i):
                if 0 <= j < len(ts):
                    d = abs(ts[j] - t)
                    if best is None or d < best:
                        best = d
            if best is not None and best <= TOLERANCE_S:
                matched += 1
                deltas.append(best)
            else:
                missed.append((c, t, "nearest_dt_%.6f" % (best or -1)))
        n_der = sum(len(v) for v in idx.values())
        report["per_kind"][kind] = {
            "dev_rows_on_overlap": len(dv),
            "derived_rows_on_overlap": n_der,
            "matched": matched,
            "dev_rows_MISSED_by_derivation": len(missed),
            "recall_of_dev_rows": (round(matched / len(dv), 6) if dv else None),
            "extra_derived_rows": n_der - matched,
            "observed_dt_max_s": (round(max(deltas), 6) if deltas else None),
            "observed_dt_mean_s": (round(sum(deltas) / len(deltas), 6)
                                   if deltas else None),
            "sample_missed": [[list(c), t, why] for c, t, why in missed[:5]],
        }
        if dv and missed:
            verdict = "FAIL"
    if not overlap:
        verdict = "INCONCLUSIVE_NO_OVERLAP"
    report["VERDICT"] = verdict
    print(json.dumps(report, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
