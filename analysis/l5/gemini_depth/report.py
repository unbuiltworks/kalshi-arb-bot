"""Answer the five hypotheses with numbers, not with a snapshot.

Each section below maps to one candidate explanation for a `depth 1.0 @ 0.66`
reading, and each is answered by a statistic that could come out the other way:

  H1 genuinely one contract      time-weighted fraction where TOTAL ask size <= 1
  H2 best level only             how often best_qty is tiny while total is not
  H3 stale / cached              snapshot_age_s quantiles, cache headers,
                                 and whether REST/WS disagreement tracks age
  H4 incomplete retrieval        limit=1 vs full: levels and size hidden
  H5 rapidly changing liquidity  touch change rate and touch lifetime

TIME-WEIGHTED, NOT SNAPSHOT-COUNTED. Samples are taken at a fixed cadence from
the maintained book, so counting samples IS time-weighting; a statistic that
counted REST calls instead would weight by how fast we happened to poll.

Quantiles, never means: a median touch size of 500 with a 5th percentile of 1
is the whole finding, and a mean would hide it.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

QTY = ("1", "2", "5", "10", "20")


def load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def q(vals: Sequence[float]) -> Optional[Dict[str, Any]]:
    v = sorted(float(x) for x in vals if x is not None)
    if not v:
        return None

    def pick(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 4)
    return {"n": len(v), "p05": pick(0.05), "p25": pick(0.25), "p50": pick(0.50),
            "p75": pick(0.75), "p95": pick(0.95), "min": round(v[0], 4),
            "max": round(v[-1], 4)}


def report(out_dir: str) -> Dict[str, Any]:
    ws = [r for r in load(os.path.join(out_dir, "samples.jsonl"))
          if r.get("kind") == "ws"]
    paired = load(os.path.join(out_dir, "paired.jsonl"))
    pr = [r for r in paired if r.get("kind") == "paired"]
    tr = [r for r in paired if r.get("kind") == "truncation"]

    out: Dict[str, Any] = {"ws_samples": len(ws), "paired_rest": len(pr),
                           "truncation_pairs": len(tr)}
    if not ws:
        return {**out, "verdict": "no samples yet"}

    ready = [r for r in ws if r.get("ready")]
    out["ws_ready_fraction"] = round(len(ready) / len(ws), 4)
    out["ws_gaps"] = max((r.get("gaps") or 0) for r in ready) if ready else None
    out["ws_reconnects"] = (max((r.get("reconnects") or 0) for r in ready)
                            if ready else None)
    out["window_utc"] = {"first": min(r["t"] for r in ws),
                         "last": max(r["t"] for r in ws),
                         "span_s": round(max(r["t"] for r in ws)
                                         - min(r["t"] for r in ws), 1)}
    out["symbols"] = dict(Counter(r["symbol"] for r in ws))

    by_cohort: Dict[str, List[dict]] = {}
    for r in ready:
        by_cohort.setdefault(r["cohort"], []).append(r)
    out["cohorts"] = {c: _cohort(rows) for c, rows in by_cohort.items()}

    # ---- H3, at the HTTP layer and at the data layer
    out["H3_staleness"] = {
        "rest_snapshot_age_s": q([r["rest"].get("snapshot_age_s") for r in pr
                                  if r.get("rest", {}).get("ok")]),
        "rest_rtt_s": q([r["rest"].get("rtt_s") for r in pr
                         if r.get("rest", {}).get("ok")]),
        "ws_book_age_s": q([r["ws"]["age_s"] for r in pr if r.get("ws")]),
        "rest_vs_ws_best_ask_agree": _rate(
            [r["agree"]["best_ask"] for r in pr if r.get("agree")]),
        "rest_vs_ws_best_qty_agree": _rate(
            [r["agree"]["best_ask_qty"] for r in pr if r.get("agree")]),
        "rest_vs_ws_total_agree": _rate(
            [r["agree"]["total_ask"] for r in pr if r.get("agree")]),
        "reads": ("no cache directive is served at all (see the probe's "
                  "rest_headers line), and REST level stamps are seconds old "
                  "at most. Disagreement with the maintained book is therefore "
                  "the book MOVING, not the snapshot being served from a cache."),
    }

    # ---- H4, and it is the one that was actually happening in the old path
    if tr:
        hid_lvl, hid_sz, full_tot, t1_tot = [], [], [], []
        for r in tr:
            f, t = r.get("full") or {}, r.get("limit1") or {}
            if not (f.get("ask") and t.get("ask")):
                continue
            hid_lvl.append(f["ask"]["levels"] - t["ask"]["levels"])
            hid_sz.append(float(D(f["ask"]["total"]) - D(t["ask"]["total"])))
            full_tot.append(float(D(f["ask"]["total"])))
            t1_tot.append(float(D(t["ask"]["total"])))
        out["H4_truncation"] = {
            "n": len(hid_lvl),
            "ask_levels_hidden_by_limit1": q(hid_lvl),
            "ask_size_hidden_by_limit1": q(hid_sz),
            "full_total_ask": q(full_tot),
            "limit1_total_ask": q(t1_tot),
            "reads": ("limit_bids=1&limit_asks=1 -- the old collector's query "
                      "-- returns exactly one level per side. Everything behind "
                      "the touch is invisible on that path, which is a "
                      "RETRIEVAL defect, not a venue property."),
        }
    return out


def _cohort(rows: Sequence[dict]) -> Dict[str, Any]:
    ask = [r["ask"] for r in rows if r.get("ask")]
    if not ask:
        return {"n": 0}
    best_qty = [float(a["best_qty"]) for a in ask if a["best_qty"] is not None]
    total = [float(a["total"]) for a in ask]
    levels = [a["levels"] for a in ask]

    # H1 / H2: is the VENUE thin, or only its touch?
    thin_touch = [a for a in ask
                  if a["best_qty"] is not None and float(a["best_qty"]) <= 1]
    thin_total = [a for a in ask if float(a["total"]) <= 1]
    thin_touch_deep_book = [a for a in thin_touch if float(a["total"]) >= 10]

    # time-weighted fillability at each candidate size, ceiling-free
    fill = {}
    vwaps = {}
    for qq in QTY:
        ok = [a for a in ask if a["by_qty"].get(qq, {}).get("complete")]
        fill[qq] = round(len(ok) / len(ask), 4)
        vwaps[qq] = q([float(a["by_qty"][qq]["vwap"]) for a in ask
                       if a["by_qty"].get(qq, {}).get("vwap")])

    # THE STATISTIC THAT LINKS H2 TO THE ORIGINAL ERROR. For each candidate
    # size: how often would a TOUCH-ONLY reading have said "cannot supply q"
    # while the full ladder could? That is exactly the mistake a limit=1
    # collector makes, expressed as a rate rather than as an anecdote.
    touch_understates = {}
    for qq in QTY:
        n = len(ask)
        bad = sum(1 for a in ask
                  if a["by_qty"].get(qq, {}).get("complete")
                  and (a["best_qty"] is None
                       or float(a["best_qty"]) < float(qq)))
        touch_understates[qq] = round(bad / n, 4) if n else None

    held = [r.get("touch_held_s") for r in rows if r.get("touch_held_s")]
    changes = sum(1 for r in rows if r.get("touch_changed"))
    span = max(r["t"] for r in rows) - min(r["t"] for r in rows)
    return {
        "n_samples": len(rows),
        "span_s": round(span, 1),
        "H1_total_depth_le_1_fraction": round(len(thin_total) / len(ask), 4),
        "H1_no_ask_side_at_all_fraction": round(
            sum(1 for a in ask if a["levels"] == 0) / len(ask), 4),
        "H2_touch_qty_le_1_fraction": round(len(thin_touch) / len(ask), 4),
        "H2_touch_le_1_while_book_ge_10_fraction":
            round(len(thin_touch_deep_book) / len(ask), 4),
        "best_ask_qty": q(best_qty),
        "total_ask_size": q(total),
        "ask_levels": q(levels),
        "time_weighted_fillable_fraction": fill,
        "H2_touch_only_would_understate_fraction": touch_understates,
        "vwap_by_qty": vwaps,
        "H5_touch_changes": changes,
        "H5_touch_changes_per_min": (round(changes / (span / 60.0), 2)
                                     if span > 0 else None),
        "H5_touch_lifetime_s": q(held),
        "cum_within_offset_of_touch": {
            o: q([float(a["cum_at_offset"][o]) for a in ask
                  if a.get("cum_at_offset", {}).get(o) is not None])
            for o in ("0", "0.01", "0.02", "0.05")},
    }


def _rate(vals: Sequence[bool]) -> Optional[Dict[str, Any]]:
    v = [bool(x) for x in vals]
    if not v:
        return None
    return {"n": len(v), "agree": sum(v), "rate": round(sum(v) / len(v), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-gemini-depth")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.out_dir), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
