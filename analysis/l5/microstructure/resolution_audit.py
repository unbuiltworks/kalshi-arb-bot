"""What temporal resolution does the verified L2 archive ACTUALLY provide?

THIS RUNS BEFORE ANY MICROSTRUCTURE CLAIM, because every later statement is
bounded by the answer. The question is not what the file's dtype can hold but
what it actually contains: a `timestamp[us]` column whose microsecond field is
always zero carries second resolution, and treating it as finer would invent
precision the archive does not have.

MEASURED, NOT ASSUMED, per coin and contract length:

  precision        are microseconds ever non-zero? what is the GCD of the
                   inter-observation gaps?
  duplicates       more than one row for the same (token, timestamp) -- and if
                   so, do those rows differ? Identical duplicates are
                   redundancy; DIFFERING duplicates at the same timestamp are
                   unorderable, and any event-driven replay has to say so.
  gaps             distribution of seconds between consecutive observations on
                   one token, and the count beyond the capture's own 60 s
                   REST resync bound.
  snapshot vs event-driven
                   if a row is written only when the book CHANGES, then
                   consecutive rows should essentially never carry identical
                   ladders. The share of consecutive-identical rows settles
                   this, and it decides whether a gap means "quiet" (carry the
                   last row) or "missing" (refuse it).

NOTHING HERE INTERPOLATES. No row is created, merged or shifted.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from math import gcd
from typing import Any, Dict, List, Optional, Tuple

COLS = ["market_slug", "coin", "contract_length", "token_id", "outcome",
        "captured_at", "market_end_at", "seconds_to_close",
        "bid_prices", "bid_sizes", "ask_prices", "ask_sizes",
        "best_bid", "best_ask"]


def q(v: List[float], p: float):
    if not v:
        return None
    s = sorted(v)
    return s[int(p * (len(s) - 1))]


def ladder_key(bp, bs, ap, asz) -> Tuple:
    return (tuple(bp or []), tuple(bs or []), tuple(ap or []),
            tuple(asz or []))


def audit(parquet: str, label: str, coin: str = "btc") -> Dict[str, Any]:
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(parquet)
    sch = pf.schema_arrow
    d = pq.read_table(parquet, columns=COLS).to_pydict()

    n = len(d["captured_at"])
    micros = Counter()
    per_tok: Dict[str, List[Tuple[int, Tuple]]] = defaultdict(list)
    lens = Counter()
    clen = Counter()
    for i in range(n):
        if d["coin"][i] != coin:
            continue
        clen[d["contract_length"][i]] += 1
        ts = d["captured_at"][i]
        micros[getattr(ts, "microsecond", 0)] += 1
        sec = int(ts.timestamp())
        per_tok[str(d["token_id"][i])].append(
            (sec, ladder_key(d["bid_prices"][i], d["bid_sizes"][i],
                             d["ask_prices"][i], d["ask_sizes"][i])))
        lens[len(d["bid_prices"][i] or [])] += 1

    rows = sum(len(v) for v in per_tok.values())
    gaps: List[int] = []
    dup_same = dup_diff = 0
    consec_identical = consec_total = 0
    for tok, obs in per_tok.items():
        obs.sort(key=lambda x: x[0])
        for a, b in zip(obs, obs[1:]):
            dt = b[0] - a[0]
            if dt == 0:
                if a[1] == b[1]:
                    dup_same += 1
                else:
                    dup_diff += 1
                continue
            gaps.append(dt)
            consec_total += 1
            if a[1] == b[1]:
                consec_identical += 1

    g = 0
    for x in set(gaps):
        g = gcd(g, x)
    hist = Counter(gaps)
    return {
        "label": label,
        "file": os.path.basename(parquet),
        "coin": coin,
        "captured_at_dtype": str(sch.field("captured_at").type),
        "rows_total_file": n,
        "rows_for_coin": rows,
        "contract_lengths_present": dict(clen),
        "distinct_tokens": len(per_tok),
        "PRECISION": {
            "distinct_microsecond_values": len(micros),
            "microsecond_histogram_top5": micros.most_common(5),
            "all_microseconds_zero": list(micros) == [0],
            "gcd_of_inter_observation_gaps_s": g,
            "verdict": ("WHOLE SECONDS: the column is microsecond-capable but "
                        "every microsecond is zero, so no sub-second state "
                        "exists to preserve"
                        if list(micros) == [0] else
                        "SUB-SECOND CONTENT PRESENT -- do not bucket"),
        },
        "DUPLICATE_TIMESTAMPS": {
            "same_token_same_second_identical_ladder": dup_same,
            "same_token_same_second_DIFFERENT_ladder": dup_diff,
            "note": ("differing rows at the same second are UNORDERABLE from "
                     "the archive alone; an event-driven replay must declare "
                     "them rather than pick an order"),
        },
        "UPDATE_FREQUENCY": {
            "n_gaps": len(gaps),
            "gap_s_p05": q(gaps, 0.05), "gap_s_p50": q(gaps, 0.50),
            "gap_s_p90": q(gaps, 0.90), "gap_s_p95": q(gaps, 0.95),
            "gap_s_p99": q(gaps, 0.99), "gap_s_max": q(gaps, 1.0),
            "gap_1s_share": (round(hist.get(1, 0) / len(gaps), 4)
                             if gaps else None),
            "gaps_over_60s": sum(1 for x in gaps if x > 60),
            "gaps_over_60s_share": (round(sum(1 for x in gaps if x > 60)
                                          / len(gaps), 6) if gaps else None),
            "gap_histogram_top10": hist.most_common(10),
        },
        "SNAPSHOT_OR_EVENT_DRIVEN": {
            "consecutive_pairs_compared": consec_total,
            "consecutive_with_IDENTICAL_ladders": consec_identical,
            "identical_share": (round(consec_identical / consec_total, 6)
                                if consec_total else None),
            "verdict": ("EVENT-DRIVEN (written on change): consecutive rows "
                        "almost never repeat a ladder, so a GAP MEANS THE "
                        "BOOK DID NOT MOVE and the last row is the current "
                        "state"
                        if consec_total and
                        consec_identical / consec_total < 0.05 else
                        "LOOKS LIKE PERIODIC SNAPSHOTS: identical "
                        "consecutive ladders are common"),
        },
        "DEPTH": {"bid_levels_p50": q(list(lens.elements()), 0.5),
                  "bid_levels_p95": q(list(lens.elements()), 0.95),
                  "bid_levels_max": max(lens) if lens else None},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", action="append", required=True,
                    help="label=path, repeatable")
    ap.add_argument("--coin", default="btc")
    ap.add_argument("--out",
                    default="analysis/l5/microstructure/L2_RESOLUTION.json")
    a = ap.parse_args(argv)
    out = {"purpose": ("establish the archive's true temporal resolution "
                       "before any microstructure claim"),
           "no_interpolation_anywhere": True,
           "archives": []}
    for spec in a.parquet:
        label, _, path = spec.partition("=")
        out["archives"].append(audit(path, label or path, a.coin))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
