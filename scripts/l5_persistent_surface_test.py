"""Does L5's own tape require CONCURRENT resting orders, or is it reproducible
by a strictly sequential fill -> decide -> post -> wait -> cancel controller?

THE ANTI-ARTIFACT PROBLEM COMES FIRST, because it decides what is answerable.

  * Polymarket's activity `timestamp` is SECOND RESOLUTION. Five fills stamped
    12:01:03 are somewhere in a 1000 ms window; they are NOT evidence of
    simultaneity, and no p10/p25 of inter-fill time below one second can be
    computed from this source at all. Any "sub-second burst" claim built on
    these timestamps is an artifact of the clock, not a finding.
  * One economic maker order is consumed by however many counterparties take
    it, and each appears as its own activity row. Counting those rows as
    separate resting rungs would manufacture exactly the concurrency the
    hypothesis is being tested for.

SO THE TEST DOES NOT REST ON TIMING. It rests on `transactionHash`, which is
the on-chain transaction that produced the fill. A single transaction is a
single taker action against the book at one instant. Therefore:

  if ONE transactionHash consumed L5 liquidity at TWO OR MORE DISTINCT PRICES,
  then at that instant L5 had two or more orders RESTING SIMULTANEOUSLY.

That inference needs no clock. It is immune to second-resolution timestamps,
and it is the minimum-concurrency measurement the question actually asks for.
The converse is reported too: fills sharing a transactionHash at ONE price are
FRAGMENTATION of a single order and are collapsed, never counted as rungs.

Read-only. Touches no strategy, no envelope, no authorization, no venue.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List

ACTIVITY = "data/l5/raw/activity.jsonl"
LABEL = "L5_PERSISTENT_SURFACE_TEST_V1"

FAMILIES = {"btc-updown-5m": "BTC5m", "btc-updown-15m": "BTC15m",
            "eth-updown-5m": "ETH5m", "eth-updown-15m": "ETH15m"}


def load(path: str) -> List[Dict[str, Any]]:
    """Every TRADE row, deduplicated. Paging overlap repeats rows verbatim."""
    seen = set()
    out = []
    with open(path) as fh:
        for line in fh:
            for p in (json.loads(line).get("payload") or []):
                if p.get("type") != "TRADE":
                    continue
                key = (p.get("transactionHash"), p.get("asset"),
                       p.get("timestamp"), p.get("price"), p.get("size"),
                       p.get("side"), p.get("outcomeIndex"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
    out.sort(key=lambda r: (int(r.get("timestamp") or 0),
                            str(r.get("transactionHash"))))
    return out


def family(r: Dict[str, Any]) -> str:
    s = str(r.get("eventSlug") or "")
    return FAMILIES.get(s.rsplit("-", 1)[0], "OTHER")


def timestamp_resolution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Establish the clock BEFORE using it. Integer seconds means no
    sub-second question is answerable from this source."""
    ints = sum(1 for r in rows if float(r["timestamp"]) == int(r["timestamp"]))
    return {
        "n": len(rows),
        "all_integer_seconds": ints == len(rows),
        "n_integer": ints,
        "distinct_fractional_parts": len(
            {round(float(r["timestamp"]) % 1, 6) for r in rows}),
        "implication": ("timestamps are whole seconds: inter-fill gaps below "
                        "1s are NOT measurable here, and rows sharing a second "
                        "are NOT evidence of simultaneity"),
    }


def by_transaction(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """THE CORE TEST. One transactionHash = one taker action = one instant."""
    tx: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        tx[str(r.get("transactionHash"))].append(r)

    multi_price_same_side = []       # >=2 distinct prices, one outcome
    both_outcomes = []               # both outcomes in ONE transaction
    n_frag = 0
    levels_hist = Counter()
    for h, rs in tx.items():
        prices_by_outcome = defaultdict(set)
        for r in rs:
            prices_by_outcome[str(r.get("outcome"))].add(round(
                float(r.get("price") or 0), 4))
        levels = sum(len(v) for v in prices_by_outcome.values())
        levels_hist[levels] += 1
        if len(rs) > 1 and levels == 1:
            n_frag += 1              # same price, several counterparties
        for oc, pxs in prices_by_outcome.items():
            if len(pxs) >= 2:
                multi_price_same_side.append({
                    "tx": h, "outcome": oc, "n_levels": len(pxs),
                    "prices": sorted(pxs), "family": family(rs[0]),
                    "slug": rs[0].get("eventSlug"),
                    "timestamp": rs[0].get("timestamp"),
                    "total_size": round(sum(float(x.get("size") or 0)
                                            for x in rs
                                            if str(x.get("outcome")) == oc), 4),
                })
        if len(prices_by_outcome) >= 2:
            both_outcomes.append({
                "tx": h, "family": family(rs[0]),
                "slug": rs[0].get("eventSlug"),
                "timestamp": rs[0].get("timestamp"),
                "outcomes": {k: sorted(v)
                             for k, v in prices_by_outcome.items()},
            })
    return {
        "n_transactions": len(tx),
        "n_rows": len(rows),
        "rows_per_tx": dict(Counter(len(v) for v in tx.values()).most_common(8)),
        "distinct_price_levels_per_tx": dict(sorted(levels_hist.items())),
        "n_pure_fragmentation_tx": n_frag,
        "multi_price_same_side": multi_price_same_side,
        "both_outcomes_one_tx": both_outcomes,
    }


def per_family(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    fams = defaultdict(list)
    for r in rows:
        fams[family(r)].append(r)
    for fam, rs in sorted(fams.items()):
        tx = by_transaction(rs)
        mp = tx["multi_price_same_side"]
        bo = tx["both_outcomes_one_tx"]
        out[fam] = {
            "n_rows": len(rs),
            "n_transactions": tx["n_transactions"],
            "rows_per_tx": tx["rows_per_tx"],
            "distinct_price_levels_per_tx": tx["distinct_price_levels_per_tx"],
            "n_pure_fragmentation_tx": tx["n_pure_fragmentation_tx"],
            "n_tx_multi_price_same_side": len(mp),
            "max_levels_one_side_one_tx": max([m["n_levels"] for m in mp],
                                              default=0),
            "n_tx_both_outcomes": len(bo),
            "examples_multi_price": mp[:5],
            "examples_both_outcomes": bo[:5],
        }
    return out


def second_level_cooccurrence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reported ONLY as an upper bound, never as simultaneity.

    Rows sharing a whole second may be 1 ms or 999 ms apart. This counts them
    so the number exists, and labels it so it cannot be read as a burst.
    """
    per_sec = defaultdict(list)
    for r in rows:
        per_sec[(family(r), int(r["timestamp"]),
                 str(r.get("eventSlug")))].append(r)
    multi = {k: v for k, v in per_sec.items() if len(v) > 1}
    both = 0
    distinct_tx = Counter()
    for k, v in multi.items():
        if len({str(x.get("outcome")) for x in v}) >= 2:
            both += 1
        distinct_tx[len({str(x.get("transactionHash")) for x in v})] += 1
    return {
        "n_second_buckets_with_multiple_rows": len(multi),
        "n_such_buckets_with_both_outcomes": both,
        "distinct_transactions_within_those_buckets":
            dict(sorted(distinct_tx.items())[:8]),
        "label": ("UPPER BOUND ONLY. A shared whole second is not "
                  "simultaneity at this clock resolution; where the rows also "
                  "share ONE transactionHash they are one taker action, and "
                  "where they are several transactions they may be up to a "
                  "second apart."),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--activity", default=ACTIVITY)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows = load(a.activity)
    rep = {
        "label": LABEL,
        "source": a.activity,
        "n_trade_rows_deduped": len(rows),
        "timestamp_resolution": timestamp_resolution(rows),
        "per_family": per_family(rows),
        "second_level_cooccurrence": second_level_cooccurrence(rows),
        "what_this_can_and_cannot_answer": {
            "cannot": ("any sub-second timing distribution, because the "
                       "source clock is whole seconds"),
            "can": ("minimum simultaneous resting orders, because one "
                    "transactionHash consuming two distinct prices on one "
                    "outcome proves two orders were resting at that instant"),
        },
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)

    print(json.dumps({
        "n_rows": rep["n_trade_rows_deduped"],
        "clock": rep["timestamp_resolution"]["all_integer_seconds"],
        "per_family": {k: {kk: v[kk] for kk in
                           ("n_rows", "n_transactions",
                            "n_tx_multi_price_same_side",
                            "max_levels_one_side_one_tx",
                            "n_tx_both_outcomes",
                            "n_pure_fragmentation_tx")}
                       for k, v in rep["per_family"].items()},
    }, indent=1))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
