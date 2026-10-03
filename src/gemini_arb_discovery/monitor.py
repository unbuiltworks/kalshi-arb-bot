"""Repeated passes, to answer "repeatable?" rather than "true right now?".

ONE SCAN CANNOT ANSWER THE QUESTION ASKED. A single pass finding nothing is
consistent with both "this platform has no arbitrage" and "the pass landed in a
quiet second". So this module repeats the discovery-and-screen cycle and records,
per pass, how many candidates existed and HOW FAR the closest relation was from
becoming one.

THE NEAR-MISS METRIC, and why it carries the weight here. For every verified
monotonicity pair the scanner computes

    approach = bid(short leg) - ask(long leg)

which is the per-unit gross edge: positive is an arbitrage, zero is exactly
touching, negative is the distance still to travel. Reporting the DISTRIBUTION of
`approach` turns a bare zero into a measurement: a platform whose best pair sits
at -0.01 is one tick from printing arbitrage all day, and one sitting at -0.15 is
nowhere near it. The two deserve very different conclusions and a candidate count
of zero cannot tell them apart.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_arb_discovery import scan as SC
from src.gemini_arb_discovery import structure as ST
from src.gemini_arb_discovery import universe as UN


def _approaches(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals: List[D] = []
    best: Optional[Dict[str, Any]] = None
    pairs = 0
    for e in events:
        cs = UN.active_contracts(e)
        by_t = {c.get("ticker"): c for c in cs}
        for rel in ST.ladders(e, cs):
            if rel.get("relation") not in (ST.LADDER_ABOVE, ST.LADDER_BELOW):
                continue
            mem = rel["members"]
            for i in range(len(mem)):
                for j in range(i + 1, len(mem)):
                    lo, hi = by_t.get(mem[i]["ticker"]), by_t.get(mem[j]["ticker"])
                    if not lo or not hi:
                        continue
                    long_c, short_c = ((lo, hi) if rel["relation"] == ST.LADDER_ABOVE
                                       else (hi, lo))
                    _, ask = SC.touch(long_c)
                    bid, _ = SC.touch(short_c)
                    if ask is None or bid is None:
                        continue
                    pairs += 1
                    ap = bid - ask
                    vals.append(ap)
                    if best is None or ap > best["approach"]:
                        best = {"approach": ap, "event": e.get("ticker"),
                                "category": e.get("category"),
                                "measure": rel.get("measure"),
                                "long": long_c.get("label"),
                                "short": short_c.get("label"),
                                "long_ask": str(ask), "short_bid": str(bid)}
    s = sorted(vals)
    def q(p):
        return str(s[min(len(s) - 1, int(p * (len(s) - 1)))]) if s else None
    return {"pairs_priced": pairs, "crossed": sum(1 for v in vals if v > 0),
            "approach_max": q(1.0), "approach_p99": q(0.99),
            "approach_p90": q(0.90), "approach_median": q(0.50),
            "closest_pair": ({**best, "approach": str(best["approach"])}
                             if best else None)}


def run(out_dir: str, *, passes: int = 6, interval_s: float = 60.0) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for k in range(passes):
        t0 = time.time()
        disc = UN.fetch_all_events()
        events = UN.active_events(disc["events"])
        cands: List[Dict[str, Any]] = []
        parts = 0
        for e in events:
            cs = UN.active_contracts(e)
            cands.extend(SC.ladder_candidates(e, ST.ladders(e, cs)))
            pc, ver = SC.partition_candidates(e)
            cands.extend(pc)
            if ver.get("exhaustive"):
                parts += 1
        cands.extend(SC.duplicate_candidates(events))
        row = {"pass": k, "at": t0, "active_events": len(events),
               "active_contracts": sum(len(UN.active_contracts(e)) for e in events),
               "verified_partitions": parts,
               "quoted_candidates": len(cands),
               "candidates": cands[:20],
               "discovery_errors": disc["errors"],
               **_approaches(events)}
        rows.append(row)
        print("pass %d/%d  events=%d contracts=%d pairs=%d crossed=%d "
              "candidates=%d closest=%s" % (
                  k + 1, passes, row["active_events"], row["active_contracts"],
                  row["pairs_priced"], row["crossed"], row["quoted_candidates"],
                  row["approach_max"]), flush=True)
        if k < passes - 1:
            time.sleep(max(0.0, interval_s - (time.time() - t0)))
    summary = {
        "passes": len(rows), "interval_s": interval_s,
        "window_s": round(rows[-1]["at"] - rows[0]["at"], 1) if rows else 0,
        "passes_with_any_candidate": sum(1 for r in rows if r["quoted_candidates"]),
        "total_candidate_sightings": sum(r["quoted_candidates"] for r in rows),
        "pairs_priced_per_pass_median": sorted(
            r["pairs_priced"] for r in rows)[len(rows) // 2] if rows else None,
        "best_approach_seen": max((D(r["approach_max"]) for r in rows
                                   if r["approach_max"] is not None),
                                  default=None),
        "passes_detail": rows,
    }
    if summary["best_approach_seen"] is not None:
        summary["best_approach_seen"] = str(summary["best_approach_seen"])
    with open(os.path.join(out_dir, "monitor_result.json"), "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    return summary


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="analysis/gemini_arb_discovery/monitor")
    ap.add_argument("--passes", type=int, default=6)
    ap.add_argument("--interval", type=float, default=60.0)
    a = ap.parse_args()
    s = run(a.out, passes=a.passes, interval_s=a.interval)
    print(json.dumps({k: v for k, v in s.items() if k != "passes_detail"},
                     indent=2, default=str))


if __name__ == "__main__":
    main()
