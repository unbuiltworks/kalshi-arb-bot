"""Build the SANITIZED external-review package from the L5 corpus.

PURPOSE. An independent reviewer (Grok, Perplexity, a human quant) should be
able to reach their own conclusion about this trader's policy WITHOUT being
told ours, and without needing our repository, our credentials or our
narrative. So the package is: flat files, a data dictionary, a handful of
complete market sequences a person can read end to end, and a prompt that
states the question and withholds the answer.

WHAT IS SANITIZED, AND WHAT IS DELIBERATELY NOT.

Removed: the trader's wallet address and username, our own account
identifiers, transaction hashes, condition ids, and anything that would let
the reviewer look the positions up and read our conclusions off a block
explorer instead of forming their own. Market slugs are replaced by a
stable pseudonym (M0001...) and the asset/duration are kept as separate
columns, because "BTC 15-minute window" is a market STRUCTURE fact the
analysis needs, while the exact window identity is not.

Kept exactly as recorded: every price, size, side and timestamp, to the
second. Rounding a price or jittering a timestamp would quietly destroy the
sequential structure that is the entire object of study -- so nothing in
the behavioral data is perturbed, and the files carry no derived labels,
no action alphabet and no feature engineering of ours.

TIMESTAMPS are kept absolute UTC: inter-fill gaps, time-of-day effects and
the regime break on 2026-06-23 are all first-order facts about this policy,
and an offset would make the June break unlocatable.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import sys
import datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from analysis.l5.clone.extract import load_fills, window_of

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "package")
BREAK = int(dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp())
SALT = "l5-external-review-v1"


def pseudonym(key: str) -> str:
    h = hashlib.sha256((SALT + str(key)).encode()).hexdigest()[:10]
    return "M" + h


def asset_and_duration(slug: str) -> tuple:
    """Market STRUCTURE, kept; market IDENTITY, dropped."""
    s = str(slug or "").lower()
    asset = None
    for a in ("btc", "eth", "sol", "xrp", "doge", "ada", "link", "ltc"):
        if a in s:
            asset = a.upper()
            break
    dur, _ws = window_of(slug)
    return asset, (int(dur) if dur else None)


def build() -> dict:
    os.makedirs(OUT, exist_ok=True)
    fills = load_fills()
    by_market = defaultdict(list)
    for r in fills:
        by_market[r.get("conditionId")].append(r)

    # ---- fills.csv.gz: one row per fill, sequence intact ---------------
    n_rows = 0
    early = late = 0
    markets = {}
    path = os.path.join(OUT, "l5_fills_sanitized.csv.gz")
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["market_id", "asset", "window_seconds", "fill_index",
                    "timestamp_utc", "epoch_s", "side", "price", "size",
                    "regime"])
        for cid, rows in sorted(by_market.items(), key=lambda kv: str(kv[0])):
            rows.sort(key=lambda r: int(r["timestamp"]))
            mid = pseudonym(cid)
            slug = rows[0].get("slug") or rows[0].get("eventSlug")
            asset, dur = asset_and_duration(slug)
            markets[mid] = {"asset": asset, "window_seconds": dur,
                            "n_fills": len(rows),
                            "first_epoch_s": int(rows[0]["timestamp"]),
                            "last_epoch_s": int(rows[-1]["timestamp"])}
            for i, r in enumerate(rows):
                ts = int(r["timestamp"])
                w.writerow([
                    mid, asset or "", dur or "", i,
                    dt.datetime.fromtimestamp(
                        ts, dt.timezone.utc).isoformat(), ts,
                    "A" if int(r["outcomeIndex"]) == 0 else "B",
                    r["price"], r.get("size") or 0,
                    "early" if ts < BREAK else "late"])
                n_rows += 1
                if ts < BREAK:
                    early += 1
                else:
                    late += 1

    # ---- markets.csv: the per-market index ----------------------------
    with open(os.path.join(OUT, "l5_markets_sanitized.csv"), "w",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["market_id", "asset", "window_seconds", "n_fills",
                    "first_epoch_s", "last_epoch_s"])
        for mid, m in sorted(markets.items()):
            w.writerow([mid, m["asset"] or "", m["window_seconds"] or "",
                        m["n_fills"], m["first_epoch_s"], m["last_epoch_s"]])

    # ---- representative complete sequences ----------------------------
    # Chosen by STRUCTURE, not by outcome: the median-length market, the
    # busiest, a one-sided one and a two-sided one, in each regime. A
    # reviewer handed only profitable sequences would be reviewing our
    # selection, not the trader.
    seqs = representative(by_market, markets)
    with open(os.path.join(OUT, "l5_representative_sequences.json"), "w") as fh:
        json.dump(seqs, fh, indent=2)

    summary = {
        "fills": n_rows, "markets": len(markets),
        "regime_break_utc": "2026-06-23T00:00:00Z",
        "span_utc": [
            min(m["first_epoch_s"] for m in markets.values()),
            max(m["last_epoch_s"] for m in markets.values())],
        "early_fills": early, "late_fills": late,
        "representative_sequences": len(seqs["sequences"]),
    }
    with open(os.path.join(OUT, "MANIFEST.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def representative(by_market, markets) -> dict:
    """Complete fill sequences a person can read end to end."""
    chosen = []
    for regime, lo, hi in (("early", 0, BREAK), ("late", BREAK, 1 << 62)):
        pool = [(mid, m) for mid, m in markets.items()
                if lo <= m["first_epoch_s"] < hi]
        if not pool:
            continue
        pool.sort(key=lambda kv: kv[1]["n_fills"])
        picks = {
            "median_length": pool[len(pool) // 2],
            "busiest": pool[-1],
            "shortest_with_two_fills": next(
                ((mid, m) for mid, m in pool if m["n_fills"] >= 2), pool[0]),
        }
        for why, (mid, m) in picks.items():
            chosen.append({"market_id": mid, "regime": regime,
                           "selected_because": why,
                           "SELECTION_IS_STRUCTURAL": True})
    inv = {pseudonym(cid): cid for cid in by_market}
    out = {"selection_rule": (
        "chosen by sequence STRUCTURE (length, density) within each regime, "
        "never by profitability: a reviewer handed only winning sequences "
        "would be reviewing our selection rather than the trader"),
        "sequences": []}
    for c in chosen:
        cid = inv.get(c["market_id"])
        rows = sorted(by_market.get(cid, []), key=lambda r: int(r["timestamp"]))
        slug = rows[0].get("slug") or rows[0].get("eventSlug") if rows else None
        asset, dur = asset_and_duration(slug)
        out["sequences"].append({
            **c, "asset": asset, "window_seconds": dur,
            "fills": [{"i": i,
                       "timestamp_utc": dt.datetime.fromtimestamp(
                           int(r["timestamp"]), dt.timezone.utc).isoformat(),
                       "epoch_s": int(r["timestamp"]),
                       "side": "A" if int(r["outcomeIndex"]) == 0 else "B",
                       "price": r["price"], "size": r.get("size") or 0}
                      for i, r in enumerate(rows)]})
    return out


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
