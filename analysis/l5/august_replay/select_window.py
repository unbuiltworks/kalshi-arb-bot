"""Pick ONE complete August BTC15m window, on criteria that never mention S1.

WHY THE CRITERIA ARE FIXED BEFORE ANY REPLAY. If a window were chosen because
S1 did well or badly in it, every number downstream would be a selection
artifact rather than a measurement. So the gates below read ONLY:

    * the Zenodo L2 parquet  (book coverage)
    * the chain backfill     (L5's own fills, by TOKEN ID)

and nothing produced by S1. The frozen candidate is not imported here, and
this module has no notion of a hypothetical order, a fill standard or a P&L.

THE FOUR GATES, in the order the task states them:

  G1 COMPLETE L2 COVERAGE -- every one of the window's 900 seconds must be
     resolvable on BOTH tokens under the dataset's own carry bound
     (MAX_CARRY_S = 60 s, the collector's REST resync). A second that is not
     resolvable is a second the replay would have to guess at, so a window
     with any such second is rejected outright rather than patched.
  G2 L5 ACTIVE THROUGHOUT -- L5 fills must appear in every one of five equal
     180 s sub-intervals. A window where he trades only in the first minute is
     "active" by fill count but cannot support a 15-minute side-by-side.
  G3 SUBSTANTIAL L5 ACTIVITY -- rank by fill count among windows passing the
     gates; a hard floor rejects token activity.
  G4 BOTH MECHANISMS PRESENT -- the window must contain BOTH
     RESTING_BID_CONSISTENT and mint-consistent (AT_OR_ABOVE_ASK + feasible)
     fills, because the forensic question is about the difference between
     them. Classified with the SAME functions the prior verified analysis
     used, imported, not re-implemented.

ZERO CHUNK HOLES is also required: a window whose chain scan had an unqueried
block range cannot distinguish "no fill" from "not asked".
"""
from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import (MAX_CARRY_S, Book,
                                                  placement_consistency)
from analysis.l5.historical_l2.mint_match import feasible

WINDOW_S = 900
SUBINTERVALS = 5
MIN_FILLS = 50
#: the lead at which a fill is classified, matching the prior analysis
CLASSIFY_LEAD_S = 1


def load_books(parquet: str, slugs: Optional[set] = None
               ) -> Tuple[Dict[str, Book], Dict[str, Dict[str, Any]]]:
    """token -> Book, and slug -> {tokens, end}. BTC only, as L5 trades btc."""
    import pyarrow.parquet as pq
    cols = ["coin", "market_slug", "token_id", "outcome", "captured_at",
            "best_bid", "best_ask", "bid_prices", "bid_sizes",
            "ask_prices", "ask_sizes"]
    t = pq.read_table(parquet, columns=cols)
    d = t.to_pydict()
    books: Dict[str, Book] = {}
    wins: Dict[str, Dict[str, Any]] = {}
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        slug = d["market_slug"][i]
        if slugs is not None and slug not in slugs:
            continue
        tok = str(d["token_id"][i])
        wins.setdefault(slug, {"tokens": {}})["tokens"][tok] = \
            d["outcome"][i]
        ts = d["captured_at"][i]
        ts = ts.timestamp() if hasattr(ts, "timestamp") else float(ts)
        books.setdefault(tok, Book()).add(ts, {
            "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
            "bid_prices": list(d["bid_prices"][i] or []),
            "bid_sizes": list(d["bid_sizes"][i] or []),
            "ask_prices": list(d["ask_prices"][i] or []),
            "ask_sizes": list(d["ask_sizes"][i] or []),
        })
    for b in books.values():
        b.sort()
    return books, wins


def coverage(books: Dict[str, Book], tokens: List[str], start: int
             ) -> Dict[str, Any]:
    """G1. Resolvable seconds on BOTH tokens, under the carry bound."""
    unresolved: List[int] = []
    for s in range(start, start + WINDOW_S):
        for tok in tokens:
            bk = books.get(tok)
            if bk is None or bk.at(float(s), max_age_s=MAX_CARRY_S) is None:
                unresolved.append(s)
                break
    per_tok = {}
    for tok in tokens:
        bk = books.get(tok)
        ts = [t for t in (bk.t if bk else []) if start <= t < start + WINDOW_S]
        gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
        per_tok[tok] = {"rows_in_window": len(ts),
                        "max_gap_s": (max(gaps) if gaps else None),
                        "first_row_ts": (ts[0] if ts else None),
                        "last_row_ts": (ts[-1] if ts else None)}
    return {"seconds_resolvable": WINDOW_S - len(unresolved),
            "seconds_total": WINDOW_S,
            "unresolved_seconds": unresolved[:20],
            "n_unresolved": len(unresolved),
            "COMPLETE_L2_COVERAGE": not unresolved,
            "per_token": per_tok}


def activity(fill_ts: List[float], start: int) -> Dict[str, Any]:
    """G2 + G3. Spread across five equal sub-intervals, and the count."""
    width = WINDOW_S // SUBINTERVALS
    buckets = [0] * SUBINTERVALS
    for t in fill_ts:
        k = int((t - start) // width)
        if 0 <= k < SUBINTERVALS:
            buckets[k] += 1
    return {"n_fills_in_window": len(fill_ts),
            "subinterval_width_s": width,
            "fills_per_subinterval": buckets,
            "L5_ACTIVE_THROUGHOUT": all(b > 0 for b in buckets),
            "SUBSTANTIAL": len(fill_ts) >= MIN_FILLS}


def mechanisms(fills: List[dict], books: Dict[str, Book],
               comp: Dict[str, str]) -> Dict[str, Any]:
    """G4. Classify each timed maker fill exactly as the prior analysis did."""
    counts: Dict[str, int] = {}
    mint_ok = 0
    resting = 0
    for f in fills:
        ts = f.get("_ts")
        if ts is None:
            counts["NO_TIMESTAMP"] = counts.get("NO_TIMESTAMP", 0) + 1
            continue
        bk = books.get(f["token_id"])
        snap = bk.at(ts - CLASSIFY_LEAD_S, max_age_s=MAX_CARRY_S) if bk \
            else None
        cls = placement_consistency(snap, f["price"])
        counts[cls] = counts.get(cls, 0) + 1
        if cls == "RESTING_BID_CONSISTENT":
            resting += 1
        elif cls == "AT_OR_ABOVE_ASK_NOT_MEASURABLE":
            other = comp.get(f["token_id"])
            osnap = books[other].at(ts - CLASSIFY_LEAD_S,
                                    max_age_s=MAX_CARRY_S) \
                if other in books else None
            cb = osnap.get("best_bid") if osnap else None
            if feasible(f["price"], cb):
                mint_ok += 1
    return {"classes": counts,
            "n_resting_bid_consistent": resting,
            "n_mint_consistent": mint_ok,
            "BOTH_MECHANISMS_PRESENT": resting > 0 and mint_ok > 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--out",
                    default="analysis/l5/august_replay/WINDOW_SELECTION.json")
    a = ap.parse_args(argv)

    bf = json.load(open(a.backfill))
    bt = {int(k): v for k, v in
          json.load(open(a.block_times))["block_times"].items()}
    traded = {w["market_slug"] for w in bf["rows"] if w["n_fills"] > 0}
    books, wins = load_books(a.parquet, slugs=traded)

    rows: List[Dict[str, Any]] = []
    for w in bf["rows"]:
        slug = w["market_slug"]
        if slug not in traded:
            continue
        start = int(slug.rsplit("-", 1)[1])
        tokens = sorted(wins.get(slug, {}).get("tokens", {}))
        if len(tokens) != 2:
            rows.append({"market_slug": slug, "REJECT": "NOT_TWO_TOKENS",
                         "tokens_in_parquet": len(tokens)})
            continue
        comp = {tokens[0]: tokens[1], tokens[1]: tokens[0]}
        fills = []
        for f in w["fills"]:
            g = dict(f)
            g["_ts"] = bt.get(f.get("block"))
            fills.append(g)
        in_win = [f["_ts"] for f in fills
                  if f["_ts"] is not None and start <= f["_ts"] < start + WINDOW_S]
        cov = coverage(books, tokens, start)
        act = activity(in_win, start)
        mech = mechanisms([f for f in fills if f["_ts"] is not None],
                          books, comp)
        gates = {
            "G1_complete_l2_coverage": cov["COMPLETE_L2_COVERAGE"],
            "G2_l5_active_throughout": act["L5_ACTIVE_THROUGHOUT"],
            "G3_substantial_activity": act["SUBSTANTIAL"],
            "G4_both_mechanisms": mech["BOTH_MECHANISMS_PRESENT"],
            "G5_zero_chunk_holes": w["chunk_holes"] == 0,
        }
        rows.append({"market_slug": slug, "window_start_utc": start,
                     "window_end_utc": start + WINDOW_S,
                     "tokens": {t: wins[slug]["tokens"][t] for t in tokens},
                     "chunk_holes": w["chunk_holes"],
                     "n_fills_total_chain": w["n_fills"],
                     "coverage": cov, "activity": act, "mechanisms": mech,
                     "gates": gates,
                     "PASSES_ALL_GATES": all(gates.values())})

    passing = [r for r in rows if r.get("PASSES_ALL_GATES")]
    # G3 is the only ranking key, and it is applied AFTER the gates.
    passing.sort(key=lambda r: (-r["activity"]["n_fills_in_window"],
                                r["market_slug"]))
    sel = passing[0] if passing else None
    rep = {
        "purpose": ("select ONE complete August BTC15m window for the "
                    "forensic replay, on criteria defined WITHOUT reference "
                    "to S1 performance"),
        "criteria_are_s1_independent": True,
        "s1_imported_here": False,
        "gates": {
            "G1": "all 900 seconds resolvable on BOTH tokens, carry <= %ds"
                  % MAX_CARRY_S,
            "G2": "L5 fills in every one of %d equal %ds sub-intervals"
                  % (SUBINTERVALS, WINDOW_S // SUBINTERVALS),
            "G3": "L5 fills in window >= %d (ranking key)" % MIN_FILLS,
            "G4": "both RESTING_BID_CONSISTENT and mint-consistent present",
            "G5": "zero chunk holes in the chain scan",
        },
        "windows_considered": len(rows),
        "windows_passing": len(passing),
        "selected": (sel["market_slug"] if sel else None),
        "selection_rank_key": "n_fills_in_window desc, then slug asc",
        "rows": rows,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    for r in rows:
        if "gates" in r:
            print("%-44s fills=%4d %s %s" % (
                r["market_slug"], r["activity"]["n_fills_in_window"],
                "".join("1" if v else "0" for v in r["gates"].values()),
                "<== SELECTED" if sel and r["market_slug"] == sel["market_slug"] else ""))
    print("\nSELECTED:", rep["selected"])
    return 0 if sel else 3


if __name__ == "__main__":
    raise SystemExit(main())
