"""TRACK A -- maker/taker reconstruction from the takerOnly feed.

THE ENDPOINT, VERIFIED BEFORE USE (Track A step 2).

  url      https://data-api.polymarket.com/trades
  params   takerOnly=true, user=<L5>, sortBy=TIMESTAMP, sortDirection=DESC,
           limit=500
  envelopes 27, all HTTP 200, every payload exactly 500 rows
  offsets  {0,500,...,4000} -- NINE distinct offsets per collection run
  raw rows 13,500 -> 11,810 unique on the conflict-free key
  span     2026-08-28 00:07:57Z .. 2026-08-31 23:11:19Z (3.96 days)

THE CRITICAL SEMANTIC. The feed returns only trades where L5 was the
TAKER. So presence proves taker. ABSENCE PROVES MAKER ONLY IF THE FEED IS
COMPLETE over the window being tested. It is paginated DESC from the
newest trade and stopped at offset 4000, i.e. each run retrieved roughly
the newest 4,500 taker trades at that moment. Three runs at different
anchors give 11,810 unique.

So absence is informative only inside the window the union actually
covers, and only if that coverage has no holes. This script therefore
MEASURES completeness first -- per-day and per-hour taker counts, and the
largest gap between consecutive taker trades -- and restricts every
maker/taker claim to the interval where coverage is dense. Outside it,
fills are labelled COVERAGE_UNKNOWN and are never counted as maker.

Everything reported is split into:
  OBSERVED  -- present in the takerOnly feed: taker, directly evidenced
  INFERRED  -- absent from it inside dense coverage: maker by inference
  UNKNOWN   -- outside dense coverage: no claim

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import os
import statistics
import sys

KEY = lambda r: (r.get("transactionHash"), r.get("conditionId"),      # noqa
                 r.get("outcomeIndex"), r.get("timestamp"),
                 str(r.get("price")), str(r.get("size")))


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def bucket(v, edges, label):
    if v is None:
        return f"{label}_NA"
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{label}_[{lo:g},{hi:g})"
    return f"{label}_>={edges[-1]:g}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takeronly", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    # ---- load and verify the feed -------------------------------------
    envs = [json.loads(l) for l in open(a.takeronly, encoding="utf-8")]
    uniq = {}
    offsets = collections.Counter()
    statuses = collections.Counter()
    raw = 0
    for e in envs:
        statuses[e.get("status")] += 1
        offsets[(e.get("params") or {}).get("offset")] += 1
        for r in e.get("payload") or []:
            raw += 1
            uniq[KEY(r)] = r

    tak_ts = sorted(r["timestamp"] for r in uniq.values())
    lo_ts, hi_ts = tak_ts[0], tak_ts[-1]
    gaps = [b - a_ for a_, b in zip(tak_ts, tak_ts[1:])]
    per_hour = collections.Counter(t // 3600 for t in tak_ts)
    hours_spanned = (hi_ts // 3600) - (lo_ts // 3600) + 1
    hours_with_data = len(per_hour)

    # Dense coverage = the contiguous interval where every hour has >=1
    # taker trade. Outside it we make no maker claim.
    hrs = sorted(per_hour)
    best_run, run = (hrs[0], hrs[0]), (hrs[0], hrs[0])
    for h in hrs[1:]:
        if h == run[1] + 1:
            run = (run[0], h)
        else:
            run = (h, h)
        if run[1] - run[0] > best_run[1] - best_run[0]:
            best_run = run
    dense_lo, dense_hi = best_run[0] * 3600, (best_run[1] + 1) * 3600 - 1

    feed = {
        "url": (envs[0].get("params") or {}) and
        "https://data-api.polymarket.com/trades",
        "params": envs[0].get("params"),
        "envelopes": len(envs), "statuses": dict(statuses),
        "distinct_offsets": sorted(x for x in offsets if x is not None),
        "max_offset_reached": max(x for x in offsets if x is not None),
        "raw_rows": raw, "unique_rows": len(uniq),
        "span_epoch": [lo_ts, hi_ts],
        "span_days": round((hi_ts - lo_ts) / 86400, 3),
        "hours_spanned": hours_spanned, "hours_with_any_taker": hours_with_data,
        "hourly_coverage": round(hours_with_data / hours_spanned, 4),
        "largest_gap_between_taker_trades_s": max(gaps) if gaps else None,
        "median_gap_s": statistics.median(gaps) if gaps else None,
        "dense_window_epoch": [dense_lo, dense_hi],
        "dense_window_hours": best_run[1] - best_run[0] + 1,
        "PAGINATION_TRUNCATED": True,
        "_truncation_note":
            "Paginated DESC from newest and stopped at offset "
            f"{max(x for x in offsets if x is not None)}, so each run saw "
            "roughly the newest 4,500 taker trades. Absence from the feed is "
            "therefore evidence of maker execution ONLY inside the dense "
            "window, and even there it assumes no hole within an hour.",
    }

    tak_keys = set()
    for r in uniq.values():
        tak_keys.add((r.get("transactionHash"), r.get("conditionId"),
                      r.get("outcomeIndex"), r.get("timestamp")))

    # ---- classify corpus fills ---------------------------------------
    cls = collections.Counter()
    by = {k: collections.defaultdict(collections.Counter)
          for k in ("market", "leg", "price", "size", "tts", "hour")}
    rows_out = []
    px_by_cls = collections.defaultdict(list)
    sz_by_cls = collections.defaultdict(list)
    tts_by_cls = collections.defaultdict(list)

    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = int(r["timestamp"])
                if not (lo_ts <= t <= hi_ts):
                    continue
                k = (r["tx_hash"], r["condition_id"],
                     int(r["outcome_index"]) if r["outcome_index"] else None,
                     t)
                in_dense = dense_lo <= t <= dense_hi
                if k in tak_keys:
                    c = "TAKER_OBSERVED"
                elif in_dense:
                    c = "MAKER_INFERRED"
                else:
                    c = "COVERAGE_UNKNOWN"
                cls[c] += 1
                px, sz = f(r["price"]), f(r["size"])
                tts = f(r["seconds_to_settlement"])
                px_by_cls[c].append(px)
                sz_by_cls[c].append(sz)
                if tts is not None:
                    tts_by_cls[c].append(tts)
                by["leg"][r["leg"]][c] += 1
                by["market"][r["asset"] + "-" + str(r["duration_minutes"])][c] += 1
                by["price"][bucket(px, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9],
                                   "px")][c] += 1
                by["size"][bucket(sz, [0, 5, 10, 20, 50, 100], "sz")][c] += 1
                by["tts"][bucket(tts, [-60, 0, 30, 60, 120, 300, 600],
                                 "tts")][c] += 1
                rows_out.append((r["event_slug"], r["trade_seq"], t, r["leg"],
                                 px, sz, tts, c))

    tot = sum(cls.values())
    obs = cls["TAKER_OBSERVED"]
    inf = cls["MAKER_INFERRED"]
    unk = cls["COVERAGE_UNKNOWN"]
    denom = obs + inf

    def share(d):
        return {k: round(v / sum(d.values()), 4) for k, v in d.items()} \
            if sum(d.values()) else {}

    R = {
        "feed_verification": feed,
        "classification": {
            "corpus_fills_inside_feed_span": tot,
            "TAKER_OBSERVED": obs,
            "MAKER_INFERRED": inf,
            "COVERAGE_UNKNOWN": unk,
            "taker_share_of_classifiable": round(obs / denom, 4)
            if denom else None,
            "maker_share_of_classifiable": round(inf / denom, 4)
            if denom else None,
            "_what_is_evidence":
                "TAKER_OBSERVED is directly evidenced -- the row is in the "
                "takerOnly feed. MAKER_INFERRED is an INFERENCE from absence "
                "inside the dense window. COVERAGE_UNKNOWN carries no claim.",
        },
        "by_leg": {k: dict(v) for k, v in by["leg"].items()},
        "by_asset_duration": {k: dict(v) for k, v in by["market"].items()},
        "taker_share_by_price_bucket": {
            k: round(v["TAKER_OBSERVED"] /
                     (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]), 4)
            for k, v in sorted(by["price"].items())
            if (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]) >= 200},
        "taker_share_by_size_bucket": {
            k: round(v["TAKER_OBSERVED"] /
                     (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]), 4)
            for k, v in sorted(by["size"].items())
            if (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]) >= 200},
        "taker_share_by_time_to_settlement": {
            k: round(v["TAKER_OBSERVED"] /
                     (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]), 4)
            for k, v in sorted(by["tts"].items())
            if (v["TAKER_OBSERVED"] + v["MAKER_INFERRED"]) >= 200},
        "price_and_size_by_class": {
            c: {"n": len(px_by_cls[c]),
                "mean_price": round(statistics.fmean(
                    [x for x in px_by_cls[c] if x is not None]), 5)
                if any(x is not None for x in px_by_cls[c]) else None,
                "median_size": round(statistics.median(
                    [x for x in sz_by_cls[c] if x is not None]), 4)
                if any(x is not None for x in sz_by_cls[c]) else None,
                "median_seconds_to_settlement": round(statistics.median(
                    tts_by_cls[c]), 1) if tts_by_cls[c] else None}
            for c in ("TAKER_OBSERVED", "MAKER_INFERRED", "COVERAGE_UNKNOWN")
            if px_by_cls[c]},
    }

    outdir = os.path.dirname(a.out)
    lab = os.path.join(outdir, "A1_fill_labels.csv.gz")
    with gzip.open(lab, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["event_slug", "trade_seq", "timestamp", "leg", "price",
                    "size", "seconds_to_settlement", "classification"])
        w.writerows(rows_out)
    R["labels_file"] = os.path.basename(lab)

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps({k: R[k] for k in
                      ("feed_verification", "classification",
                       "taker_share_by_price_bucket",
                       "taker_share_by_size_bucket",
                       "taker_share_by_time_to_settlement",
                       "price_and_size_by_class", "by_leg")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
