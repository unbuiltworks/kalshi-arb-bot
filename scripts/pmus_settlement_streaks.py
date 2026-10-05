"""Base rates, streaks and transitions over the PMUS BTC15m settlement series.

SCOPE, AND WHAT THIS CANNOT SAY. Settlement outcomes are enough to count
windows, streaks and conditional continuation. They are NOT enough to price a
trade: there is no book, no executable price and no fee here, so nothing in this
file is evidence that any betting strategy is profitable, and nothing in it
should be quoted as P&L. A continuation frequency is a frequency.

THREE DATASETS, KEPT APART. This reads the settlement-history series only. It
never pools with the 178-window generating sample (executable economics, which
generated the reversal hypothesis and is retired from validating it) or with the
43-window independent executable holdout. Those are different samples measuring
a different quantity.

GAPS ARE NOT OUTCOMES. A missing 15-minute interval BREAKS a streak rather than
being skipped over, because a streak counted across a hole is not a streak that
happened. Statistics are therefore reported over the LARGEST CONTINUOUS run of
consecutive 15-minute windows, and the whole-series figures are reported
alongside it, labelled, so the difference is visible.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys

STEP = dt.timedelta(minutes=15)


def load(path: str) -> list:
    """Resolved rows only, chronological, deduplicated on the window start.

    A window the venue did not resolve to UP or DOWN is NOT coerced. It is
    dropped here and shows up as a gap, which is the honest treatment: an
    unresolved window is exactly as unknown as an absent one.
    """
    by = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            o = r.get("outcome")
            if o not in ("UP", "DOWN") or not r.get("window_start"):
                continue
            by[r["window_start"]] = r
    rows = [by[k] for k in sorted(by)]
    for r in rows:
        r["_t"] = dt.datetime.fromisoformat(r["window_start"])
    return rows


def runs(rows: list) -> list:
    """Split into maximal blocks of strictly consecutive 15-minute windows."""
    blocks, cur = [], []
    for r in rows:
        if cur and r["_t"] - cur[-1]["_t"] != STEP:
            blocks.append(cur)
            cur = []
        cur.append(r)
    if cur:
        blocks.append(cur)
    return blocks


def streaks_of(seq: list) -> list:
    """(outcome, length, first_window_start, last_window_start) per streak."""
    out = []
    for r in seq:
        if out and out[-1][0] == r["outcome"]:
            prev = out[-1]
            out[-1] = (prev[0], prev[1] + 1, prev[2], r["window_start"])
        else:
            out.append((r["outcome"], 1, r["window_start"], r["window_start"]))
    return out


def analyse(seq: list, label: str) -> dict:
    n = len(seq)
    counts = collections.Counter(r["outcome"] for r in seq)
    st = streaks_of(seq)

    # "streaks reaching >= k" counts STREAKS, not windows: a run of 7 reaches
    # 2, 3, 4, 5, 6 and 7, and is counted once at each. That is the quantity
    # the question asks for and it is not the same as the number of windows
    # that sat inside a long streak.
    reaching = {side: {k: sum(1 for s in st if s[0] == side and s[1] >= k)
                       for k in range(2, 11)}
                for side in ("UP", "DOWN")}

    # Conditional continuation. At a point where the current run has length
    # exactly k and ANOTHER window follows inside this continuous block, did
    # the next window repeat? Counting this from the streak list alone would
    # silently treat a block boundary as a non-continuation, so it is counted
    # by walking the sequence.
    cont = {side: {k: {"opportunities": 0, "continued": 0}
                   for k in range(1, 10)}
            for side in ("UP", "DOWN")}
    run_len, run_side = 0, None
    for i, r in enumerate(seq):
        if r["outcome"] == run_side:
            run_len += 1
        else:
            run_side, run_len = r["outcome"], 1
        if i + 1 < len(seq) and 1 <= run_len <= 9:
            c = cont[run_side][run_len]
            c["opportunities"] += 1
            if seq[i + 1]["outcome"] == run_side:
                c["continued"] += 1
    for side in cont:
        for k, c in cont[side].items():
            c["frequency"] = (None if not c["opportunities"]
                              else round(c["continued"] / c["opportunities"], 4))

    # First-order transition matrix, descriptive only. No strategy is built
    # from it here and none may be built from it elsewhere on this data.
    trans = collections.Counter()
    for a, b in zip(seq, seq[1:]):
        trans[(a["outcome"], b["outcome"])] += 1

    long_streaks = [{"outcome": s[0], "length": s[1],
                     "first_window_start": s[2], "last_window_start": s[3]}
                    for s in st if s[1] >= 6]

    return {
        "label": label,
        "windows": n,
        "first_window_start": seq[0]["window_start"] if seq else None,
        "last_window_start": seq[-1]["window_start"] if seq else None,
        "up_count": counts["UP"],
        "down_count": counts["DOWN"],
        "up_pct": (None if not n else round(100.0 * counts["UP"] / n, 3)),
        "down_pct": (None if not n else round(100.0 * counts["DOWN"] / n, 3)),
        "longest_up_streak": max([s[1] for s in st if s[0] == "UP"] or [0]),
        "longest_down_streak": max([s[1] for s in st if s[0] == "DOWN"] or [0]),
        "streaks_total": len(st),
        "streaks_reaching_at_least": reaching,
        "up_10_plus_occurrences": reaching["UP"][10],
        "down_10_plus_occurrences": reaching["DOWN"][10],
        "streaks_of_6_or_more": long_streaks,
        "conditional_continuation": cont,
        "transitions": {"%s->%s" % k: v for k, v in sorted(trans.items())},
        "labels": ("settlement outcomes only; no book, no executable price, "
                   "no fee, no P&L. A frequency is not an edge and not a "
                   "profitable strategy."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settlements", required=True)
    ap.add_argument("--out", default="analysis/l5/settlement_history/"
                                    "settlement_streaks.json")
    a = ap.parse_args()

    rows = load(a.settlements)
    if not rows:
        print("no resolved settlement rows in %s" % a.settlements)
        return 1
    blocks = runs(rows)
    blocks_sorted = sorted(blocks, key=len, reverse=True)
    largest = blocks_sorted[0]

    # Every hole in the series, named. A gap is reported, never papered over.
    gaps = []
    for b1, b2 in zip(blocks, blocks[1:]):
        missing = int((b2[0]["_t"] - b1[-1]["_t"]) / STEP) - 1
        gaps.append({"after_window_start": b1[-1]["window_start"],
                     "before_window_start": b2[0]["window_start"],
                     "missing_windows": missing})

    doc = {
        "LABEL": "PMUS_BTC15M_SETTLEMENT_STREAKS",
        "dataset": "settlement-history (dataset 1 of 3; NOT the 178-window "
                   "generating sample, NOT the 43-window executable holdout)",
        "source": a.settlements,
        "resolved_windows": len(rows),
        "continuous_blocks": len(blocks),
        "block_sizes_top10": [len(b) for b in blocks_sorted[:10]],
        "gaps": gaps,
        "largest_continuous_block": analyse(largest, "largest_continuous_block"),
        "whole_resolved_series_streaks_broken_at_gaps":
            analyse_whole(blocks),
        "labels": ("Statistics are computed over the largest CONTINUOUS run of "
                   "consecutive 15-minute windows. The whole-series figures "
                   "break every streak at every gap rather than counting "
                   "across a hole. Settlement outcomes cannot establish "
                   "profitability of any strategy."),
    }
    import os
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("largest_continuous_block",
                                   "whole_resolved_series_streaks_broken_at_gaps",
                                   "gaps")}, indent=1, default=str))
    print("\n== largest continuous block ==")
    print(json.dumps(doc["largest_continuous_block"], indent=1, default=str))
    print("\nwrote %s" % a.out)
    return 0


def analyse_whole(blocks: list) -> dict:
    """Pooled counts with streaks broken at every gap.

    Implemented by analysing each block and SUMMING the streak counts, which is
    exactly "no streak crosses a hole". The base rate is pooled over all rows.
    """
    per = [analyse(b, "block") for b in blocks]
    tot = sum(p["windows"] for p in per)
    reaching = {side: {k: sum(p["streaks_reaching_at_least"][side][k]
                              for p in per)
                       for k in range(2, 11)}
                for side in ("UP", "DOWN")}
    cont = {side: {k: {"opportunities": 0, "continued": 0}
                   for k in range(1, 10)}
            for side in ("UP", "DOWN")}
    for p in per:
        for side in cont:
            for k, c in cont[side].items():
                src = p["conditional_continuation"][side][k]
                c["opportunities"] += src["opportunities"]
                c["continued"] += src["continued"]
    for side in cont:
        for k, c in cont[side].items():
            c["frequency"] = (None if not c["opportunities"]
                              else round(c["continued"] / c["opportunities"], 4))
    up = sum(p["up_count"] for p in per)
    down = sum(p["down_count"] for p in per)
    trans: collections.Counter = collections.Counter()
    for p in per:
        for k, v in p["transitions"].items():
            trans[k] += v
    sixes = [s for p in per for s in p["streaks_of_6_or_more"]]
    return {
        "label": "whole_resolved_series_streaks_broken_at_gaps",
        "windows": tot, "up_count": up, "down_count": down,
        "up_pct": (None if not tot else round(100.0 * up / tot, 3)),
        "down_pct": (None if not tot else round(100.0 * down / tot, 3)),
        "longest_up_streak": max([p["longest_up_streak"] for p in per] or [0]),
        "longest_down_streak": max([p["longest_down_streak"] for p in per]
                                   or [0]),
        "streaks_reaching_at_least": reaching,
        "up_10_plus_occurrences": reaching["UP"][10],
        "down_10_plus_occurrences": reaching["DOWN"][10],
        "streaks_of_6_or_more": sorted(sixes,
                                       key=lambda s: s["first_window_start"]),
        "conditional_continuation": cont,
        "transitions": dict(sorted(trans.items())),
    }


if __name__ == "__main__":
    sys.exit(main())
