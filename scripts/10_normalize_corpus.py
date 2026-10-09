"""Stage A -- normalize the canonical L5 public-activity corpus.

Reads the two raw envelope files committed on
`claude/august-l5-forensic-replay-788cml`:

    data/l5/raw/activity.jsonl          (named records,   78,000)
    data/l5/raw/activity_gap.jsonl.gz   (nameless records, 706,500)

and applies, unchanged, the admission and dedupe rules established by
`scripts/l5_corpus_validation.py` on that branch:

  ADMISSION  a record is admitted iff its envelope's `params.user` is L5's
             address AND the record carries no CONTRADICTING `name`.
             `name` is never used as a positive filter -- 90% of the
             corpus has no name block at all.

  DEDUPE     key = (transactionHash, conditionId, outcomeIndex, timestamp,
                    price, size, type).
             The shorter keys that look natural here collide with
             DIFFERING fields ~33k times (silent loss, worst on REDEEM).
             This key has zero such collisions.

Outputs (streaming, constant memory except the dedupe set):

    fills_raw.jsonl.gz      TRADE fills, chronological, normalized fields
    cashflow.csv            REDEEM / MAKER_REBATE / TAKER_REBATE / REWARD
    settlement.csv          per-event settled outcome, with its evidence
    NORMALIZE_REPORT.json   counts that must reconcile with the branch doc

No network. No account data. Read-only on the sources.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
import re
import sys

L5_ADDRESS = "0xb945945d5bca"
L5_NAME = "l5Zn1bWoM8eTsK"
SOURCES = (("data/l5/raw/activity.jsonl", False),
           ("data/l5/raw/activity_gap.jsonl.gz", True))
MARKETS = "data/l5/raw/markets.jsonl"
SLUG_EPOCH = re.compile(r"-(\d{9,11})$")
SLUG_DUR = re.compile(r"-(\d+)m-")
CASH_TYPES = ("REDEEM", "MAKER_REBATE", "TAKER_REBATE", "REWARD")


def opener(path, gz):
    return gzip.open(path, "rt", encoding="utf-8", errors="ignore") if gz \
        else open(path, "rt", encoding="utf-8", errors="ignore")


def envelopes(root):
    for rel, gz in SOURCES:
        path = os.path.join(root, rel)
        with opener(path, gz) as fh:
            for lineno, line in enumerate(fh):
                try:
                    yield rel, lineno, json.loads(line)
                except Exception:                              # noqa: BLE001
                    yield rel, lineno, {"__malformed__": True}


def parse_slug(slug):
    """asset, duration_minutes, window_start_epoch -- or Nones. No guessing."""
    if not slug:
        return None, None, None
    asset = None
    low = slug.lower()
    if low.startswith("btc-"):
        asset = "BTC"
    elif low.startswith("eth-"):
        asset = "ETH"
    dur = SLUG_DUR.search(slug)
    ep = SLUG_EPOCH.search(slug)
    return (asset,
            int(dur.group(1)) if dur else None,
            int(ep.group(1)) if ep else None)


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True,
                    help="checkout holding data/l5/raw/*")
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rep = {
        # Deliberately NOT the absolute path: the report ships inside the
        # research package, and a local checkout path is infrastructure
        # detail that has no business being published.
        "sources": [rel for rel, _ in SOURCES],
        "envelopes": collections.Counter(),
        "envelope_status": collections.Counter(),
        "malformed_envelopes": 0,
        "payload_not_list": 0,
        "records_seen": collections.Counter(),
        "provenance": collections.Counter(),
        "name_block": collections.Counter(),
        "contradicting_names": 0,
        "admitted": 0,
        "dup_exact": 0,
        "dup_key_conflicts": 0,
        "unique": 0,
        "by_type": collections.Counter(),
        "fill_side": collections.Counter(),
        "fill_asset": collections.Counter(),
        "fill_duration": collections.Counter(),
        "fill_missing": collections.Counter(),
        "price_out_of_range": 0,
        "qty_not_positive": 0,
        "ts_min": None,
        "ts_max": None,
    }

    seen = set()
    fills = []            # kept in RAM: ~760k small tuples, ~1.5 GB headroom
    cash = []

    for rel, lineno, env in envelopes(a.source_root):
        if env.get("__malformed__"):
            rep["malformed_envelopes"] += 1
            continue
        rep["envelopes"][rel] += 1
        rep["envelope_status"][f"{rel}:{env.get('status')}"] += 1
        user = str((env.get("params") or {}).get("user") or "").lower()
        ok_prov = L5_ADDRESS in user
        payload = env.get("payload")
        if not isinstance(payload, list):
            rep["payload_not_list"] += 1
            continue
        for r in payload:
            if not isinstance(r, dict):
                continue
            rep["records_seen"][rel] += 1
            nm = r.get("name")
            if nm is None:
                rep["name_block"]["absent"] += 1
            elif nm == L5_NAME:
                rep["name_block"]["matches_L5"] += 1
            else:
                rep["name_block"]["CONTRADICTING"] += 1
                rep["contradicting_names"] += 1
                continue
            rep["provenance"]["ok" if ok_prov else "MISMATCH"] += 1
            if not ok_prov:
                continue
            rep["admitted"] += 1

            typ = r.get("type")
            key = (r.get("transactionHash"), r.get("conditionId"),
                   r.get("outcomeIndex"), r.get("timestamp"),
                   str(r.get("price")), str(r.get("size")), typ)
            if key in seen:
                rep["dup_exact"] += 1
                continue
            seen.add(key)
            rep["unique"] += 1
            rep["by_type"][typ] += 1

            ts = r.get("timestamp")
            if isinstance(ts, (int, float)):
                rep["ts_min"] = ts if rep["ts_min"] is None \
                    else min(rep["ts_min"], ts)
                rep["ts_max"] = ts if rep["ts_max"] is None \
                    else max(rep["ts_max"], ts)

            slug = r.get("eventSlug") or r.get("slug") or ""
            asset, dur, wstart = parse_slug(slug)

            if typ == "TRADE":
                px, sz = num(r.get("price")), num(r.get("size"))
                if px is None or not (0.0 <= px <= 1.0):
                    rep["price_out_of_range"] += 1
                if sz is None or sz <= 0:
                    rep["qty_not_positive"] += 1
                rep["fill_side"][r.get("side") or "EMPTY"] += 1
                rep["fill_asset"][asset or "UNRESOLVED"] += 1
                rep["fill_duration"][str(dur) if dur else "UNRESOLVED"] += 1
                for f in ("eventSlug", "conditionId", "outcome", "side"):
                    if not r.get(f):
                        rep["fill_missing"][f] += 1
                fills.append((
                    ts, slug, asset, dur, wstart,
                    r.get("conditionId") or "", r.get("outcome") or "",
                    r.get("outcomeIndex"), r.get("side") or "",
                    px, sz, num(r.get("usdcSize")),
                    r.get("transactionHash") or "", rel,
                ))
            elif typ in CASH_TYPES:
                cash.append((
                    ts, typ, slug, asset, dur, wstart,
                    r.get("conditionId") or "", r.get("outcome") or "",
                    r.get("outcomeIndex"), num(r.get("size")),
                    num(r.get("usdcSize")), r.get("transactionHash") or "",
                    rel,
                ))

    # ---- settlement labels -------------------------------------------------
    # A REDEEM names the outcome that actually paid: on Polymarket only the
    # winning token redeems, the loser is worthless. That makes settlement
    # recoverable from public activity alone, with no price feed. Where
    # markets.jsonl covers an event we ALSO carry its resolved outcomePrices
    # and record whether the two agree.
    redeem_outcome = {}
    redeem_conflict = set()
    for row in cash:
        if row[1] != "REDEEM" or not row[2] or not row[7]:
            continue
        prev = redeem_outcome.get(row[2])
        if prev is None:
            redeem_outcome[row[2]] = row[7]
        elif prev != row[7]:
            redeem_conflict.add(row[2])

    gamma = {}
    mpath = os.path.join(a.source_root, MARKETS)
    if os.path.exists(mpath):
        with open(mpath, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                for m in env.get("payload") or []:
                    if not isinstance(m, dict):
                        continue
                    slug = m.get("slug")
                    try:
                        outs = json.loads(m.get("outcomes") or "[]")
                        pxs = [float(x) for x in
                               json.loads(m.get("outcomePrices") or "[]")]
                    except Exception:                          # noqa: BLE001
                        continue
                    if not slug or len(outs) != len(pxs) or not pxs:
                        continue
                    if max(pxs) >= 0.999 and sorted(pxs)[-2:][0] <= 0.001:
                        gamma[slug] = outs[pxs.index(max(pxs))]

    settle_rows = []
    agree = disagree = 0
    for slug in sorted(set(redeem_outcome) | set(gamma)):
        rd = redeem_outcome.get(slug)
        gm = gamma.get(slug)
        if rd and gm:
            if rd == gm:
                agree += 1
                src = "redeem+gamma_agree"
            else:
                disagree += 1
                src = "redeem+gamma_DISAGREE"
        elif rd:
            src = "redeem_only"
        else:
            src = "gamma_only"
        settle_rows.append((slug, rd or gm, rd or "", gm or "", src,
                            int(slug in redeem_conflict)))

    rep["settlement"] = {
        "events_with_label": len(settle_rows),
        "from_redeem": len(redeem_outcome),
        "from_gamma_markets_jsonl": len(gamma),
        "both_sources_agree": agree,
        "both_sources_DISAGREE": disagree,
        "events_with_conflicting_redeem_outcomes": len(redeem_conflict),
    }

    # ---- write -------------------------------------------------------------
    fills.sort(key=lambda t: (t[0] if t[0] is not None else 0, t[12]))
    cash.sort(key=lambda t: (t[0] if t[0] is not None else 0, t[11]))

    fp = os.path.join(a.out, "fills_raw.jsonl.gz")
    cols = ("timestamp", "event_slug", "asset", "duration_minutes",
            "window_start_epoch", "condition_id", "outcome", "outcome_index",
            "side", "price", "size", "usdc_size", "tx_hash", "source_file")
    with gzip.open(fp, "wt", encoding="utf-8") as fh:
        for t in fills:
            fh.write(json.dumps(dict(zip(cols, t)),
                                separators=(",", ":")) + "\n")

    with open(os.path.join(a.out, "cashflow.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "type", "event_slug", "asset",
                    "duration_minutes", "window_start_epoch", "condition_id",
                    "outcome", "outcome_index", "size", "usdc_size",
                    "tx_hash", "source_file"])
        w.writerows(cash)

    with open(os.path.join(a.out, "settlement.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["event_slug", "settled_outcome", "redeem_outcome",
                    "gamma_outcome", "evidence", "redeem_outcome_conflict"])
        w.writerows(settle_rows)

    rep["rows_written"] = {"fills_raw.jsonl.gz": len(fills),
                           "cashflow.csv": len(cash),
                           "settlement.csv": len(settle_rows)}
    for k in ("envelopes", "envelope_status", "records_seen", "provenance",
              "name_block", "by_type", "fill_side", "fill_asset",
              "fill_duration", "fill_missing"):
        rep[k] = dict(rep[k])
    with open(os.path.join(a.out, "NORMALIZE_REPORT.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)

    print(json.dumps({k: rep[k] for k in
                      ("admitted", "unique", "dup_exact", "by_type",
                       "rows_written", "settlement", "ts_min", "ts_max")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
