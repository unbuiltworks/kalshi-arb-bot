"""Stage C -- select and render complete chronological window case studies.

Selection is by explicit, auditable strata so that nobody has to take the
sample on trust. For every window we compute features from the enriched
table, then fill each stratum with the window that best exemplifies it,
subject to a minimum fill count so the sequence is actually long enough to
read a decision process out of.

STRATA (each requested twice -- an early-regime and a late-regime instance
-- unless noted):

  R1  earliest-regime window              (regime coverage)
  R2  latest-regime window
  P1  most profitable window
  P2  most unprofitable window
  P3  near-zero-P&L window                (the modal boring case)
  D1  most one-sided accumulation         (largest residual share, never paired)
  D2  one-sided accumulation that LOST
  C1  deepest repeated pair-cost compression
  C2  pair cost that moved the wrong way throughout
  S1  highest leg-switch rate
  S2  longest single-leg run before switching
  V1  largest window by notional spend
  V2  largest window by fill count
  T1  fastest fill cadence (median inter-fill gap)
  T2  slowest fill cadence
  A1  ETH window                          (asset coverage)
  A2  5-minute window
  A3  15-minute window
  X1  window that ended perfectly matched (no residual at all -- rare, 138)
  X2  window whose residual side WON
  X3  window whose residual side LOST

Each rendered case study contains EVERY fill in the window, in order, with
the reconstructed inventory after each one. Nothing is summarised away and
no fill is omitted.

NO INTERPRETATION is written into these files. They state what happened,
not why. The "why" is the question put to Grok and Perplexity.
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

MIN_FILLS_RICH = 40          # strata that need a readable sequence
MIN_FILLS_ANY = 3


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load_windows(dataset):
    """event_slug -> list of enriched fill dicts, plus computed features."""
    wins = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(dataset, "fills",
                                           "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wins[r["event_slug"]].append(r)
    return wins


def features(slug, rows):
    n = len(rows)
    last = rows[-1]
    gaps = [int(r["seconds_since_prev_fill"])
            for r in rows if r["seconds_since_prev_fill"] not in ("", None)]
    switches = sum(int(r["leg_switched_from_prev"] or 0) for r in rows)
    # longest run on one leg
    best = cur = 1
    for i in range(1, n):
        if rows[i]["leg"] == rows[i - 1]["leg"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    pcs = [f(r["effective_pair_cost"]) for r in rows]
    pcs = [x for x in pcs if x is not None]
    compressions = sum(1 for i in range(1, len(pcs)) if pcs[i] < pcs[i - 1])
    expansions = sum(1 for i in range(1, len(pcs)) if pcs[i] > pcs[i - 1])
    matched = f(last["matched_qty"], 0.0)
    resid = f(last["residual_qty"], 0.0)
    spend = f(last["total_spend"], 0.0)
    pair_cost = f(last["effective_pair_cost"])
    st = (last["settled_outcome"] or "").strip().lower()
    rside = (last["residual_side"] or "").strip().lower()
    proceeds = matched + (resid if (rside and rside == st) else 0.0)
    pnl = proceeds - spend
    return {
        "event_slug": slug,
        "asset": last["asset"],
        "duration_minutes": int(last["duration_minutes"] or 0),
        "start": rows[0]["datetime_utc"],
        "month": rows[0]["datetime_utc"][:7],
        "n_fills": n,
        "spend": spend,
        "matched_qty": matched,
        "residual_qty": resid,
        "residual_side": last["residual_side"],
        "residual_share": resid / (matched + resid) if (matched + resid) else 0.0,
        "pair_cost": pair_cost,
        "pair_cost_first": pcs[0] if pcs else None,
        "compressions": compressions,
        "expansions": expansions,
        "compression_net": compressions - expansions,
        "switches": switches,
        "switch_rate": switches / (n - 1) if n > 1 else 0.0,
        "longest_leg_run": best,
        "median_gap": statistics.median(gaps) if gaps else None,
        "settled_outcome": last["settled_outcome"],
        "settlement_evidence": last["settlement_evidence"],
        "pnl": pnl,
        "pnl_per_pair": pnl / matched if matched else None,
        "residual_won": bool(rside and st and rside == st),
        "ended_flat": resid <= 1e-9,
    }


def pick(cands, key, reverse=True, min_fills=MIN_FILLS_RICH, used=None,
         where=None):
    pool = [c for c in cands
            if c["n_fills"] >= min_fills
            and c["event_slug"] not in (used or set())
            and (where is None or where(c))
            and c.get(key) is not None]
    if not pool:
        return None
    pool.sort(key=lambda c: c[key], reverse=reverse)
    return pool[0]


TABLE_COLS = [
    ("trade_seq", "#"), ("datetime_utc", "utc"),
    ("seconds_from_window_start", "t+s"), ("seconds_to_settlement", "t-s"),
    ("seconds_since_prev_fill", "dt"), ("leg", "leg"),
    ("price", "px"), ("size", "qty"),
    ("cum_up_qty", "upQ"), ("up_vwap", "upVWAP"),
    ("cum_down_qty", "dnQ"), ("down_vwap", "dnVWAP"),
    ("matched_qty", "matched"), ("effective_pair_cost", "pairCost"),
    ("locked_pair_profit", "lockedP"),
    ("residual_side", "resSide"), ("residual_qty", "resQ"),
    ("residual_vwap", "resVWAP"), ("total_spend", "spend"),
]


def trim(v, nd=4):
    x = f(v)
    if x is None:
        return v if v not in (None, "") else ""
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


def render(stratum, why, ft, rows):
    L = []
    L.append(f"## {stratum} — `{ft['event_slug']}`")
    L.append("")
    L.append(f"*Selected as:* {why}")
    L.append("")
    L.append("| property | value |")
    L.append("|---|---|")
    for k, lab in [
        ("asset", "asset"), ("duration_minutes", "window length (min)"),
        ("start", "first fill (UTC)"), ("n_fills", "fills in window"),
        ("spend", "total spend (USDC)"),
        ("matched_qty", "matched pairs at end"),
        ("residual_side", "residual side at end"),
        ("residual_qty", "residual size at end"),
        ("pair_cost", "effective pair cost at end"),
        ("pair_cost_first", "effective pair cost at first pairing"),
        ("compressions", "fills that lowered pair cost"),
        ("expansions", "fills that raised pair cost"),
        ("switches", "leg switches"),
        ("longest_leg_run", "longest consecutive run on one leg"),
        ("median_gap", "median seconds between fills"),
        ("settled_outcome", "settled outcome"),
        ("settlement_evidence", "settlement evidence"),
        ("pnl", "window P&L (USDC, excl. account-level rebates)"),
        ("pnl_per_pair", "P&L per matched pair"),
    ]:
        v = ft.get(k)
        L.append(f"| {lab} | {trim(v) if isinstance(v, float) else v} |")
    L.append("")
    L.append("### Complete fill sequence with reconstructed inventory")
    L.append("")
    L.append("| " + " | ".join(lab for _, lab in TABLE_COLS) + " |")
    L.append("|" + "|".join("---" for _ in TABLE_COLS) + "|")
    for r in rows:
        L.append("| " + " | ".join(
            trim(r[c]) if c not in ("datetime_utc", "leg", "residual_side")
            else (r[c] or "") for c, _ in TABLE_COLS) + " |")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    wins = load_windows(a.dataset)
    fts = [features(s, r) for s, r in wins.items()]
    early = {"2026-03", "2026-04", "2026-05"}
    is_early = lambda c: c["month"] in early                   # noqa: E731
    is_late = lambda c: c["month"] in {"2026-08"}              # noqa: E731

    plan = [
        ("R1-early-regime", "earliest-regime window with a readable sequence",
         "start", False, {"where": is_early}),
        ("R2-late-regime", "latest-regime window",
         "start", True, {"where": is_late}),
        ("P1-most-profitable", "highest window P&L", "pnl", True, {}),
        ("P1b-most-profitable-early", "highest window P&L, early regime",
         "pnl", True, {"where": is_early}),
        ("P2-most-unprofitable", "lowest window P&L", "pnl", False, {}),
        ("P2b-most-unprofitable-early", "lowest window P&L, early regime",
         "pnl", False, {"where": is_early}),
        ("P3-near-zero-pnl", "window P&L closest to zero",
         "abs_pnl", False, {}),
        ("D1-one-sided-accumulation",
         "largest share of final inventory left unpaired",
         "residual_share", True, {}),
        ("D1b-one-sided-accumulation-early",
         "largest unpaired share, early regime",
         "residual_share", True, {"where": is_early}),
        ("D2-one-sided-accumulation-that-lost",
         "large unpaired residual whose side LOST",
         "residual_share", True,
         {"where": lambda c: not c["residual_won"] and c["residual_qty"] > 0}),
        ("C1-deepest-compression", "most fills that lowered the pair cost",
         "compressions", True, {}),
        ("C1b-deepest-compression-early",
         "most pair-cost-lowering fills, early regime",
         "compressions", True, {"where": is_early}),
        ("C2-pair-cost-worsened",
         "pair cost moved against L5 on the most fills",
         "expansions", True, {}),
        ("C3-highest-final-pair-cost",
         "worst effective pair cost reached at window end",
         "pair_cost", True, {}),
        ("C4-lowest-final-pair-cost",
         "best effective pair cost reached at window end",
         "neg_pair_cost", True, {}),
        ("S1-highest-switch-rate", "highest leg-switch rate per fill",
         "switch_rate", True, {}),
        ("S1b-highest-switch-rate-early",
         "highest leg-switch rate, early regime",
         "switch_rate", True, {"where": is_early}),
        ("S2-longest-single-leg-run",
         "longest consecutive run on one leg before switching",
         "longest_leg_run", True, {}),
        ("S3-zero-switch", "many fills, never switched leg",
         "n_fills", True, {"where": lambda c: c["switches"] == 0}),
        ("V1-largest-notional", "largest total spend", "spend", True, {}),
        ("V2-most-fills", "most fills in a single window", "n_fills",
         True, {}),
        ("T1-fastest-cadence", "lowest median seconds between fills",
         "median_gap", False, {}),
        ("T2-slowest-cadence", "highest median seconds between fills",
         "median_gap", True, {}),
        ("A1-eth", "ETH window", "n_fills", True,
         {"where": lambda c: c["asset"] == "ETH"}),
        ("A1b-eth-early", "ETH window, early regime", "n_fills", True,
         {"where": lambda c: c["asset"] == "ETH" and is_early(c)}),
        ("A2-5-minute", "5-minute window", "n_fills", True,
         {"where": lambda c: c["duration_minutes"] == 5}),
        ("A3-15-minute", "15-minute window", "n_fills", True,
         {"where": lambda c: c["duration_minutes"] == 15}),
        ("X1-ended-perfectly-matched",
         "ended with zero residual -- every contract paired",
         "n_fills", True, {"where": lambda c: c["ended_flat"]}),
        ("X2-residual-side-won", "residual side settled in the money",
         "residual_qty", True, {"where": lambda c: c["residual_won"]}),
        ("X3-residual-side-lost", "residual side settled worthless",
         "residual_qty", True,
         {"where": lambda c: not c["residual_won"] and c["residual_qty"] > 0}),
    ]

    for c in fts:
        c["abs_pnl"] = abs(c["pnl"]) if c["pnl"] is not None else None
        c["neg_pair_cost"] = -c["pair_cost"] if c["pair_cost"] is not None \
            else None

    used, chosen = set(), []
    for stratum, why, key, rev, kw in plan:
        c = pick(fts, key, rev, used=used, **kw)
        if c is None:
            c = pick(fts, key, rev, min_fills=MIN_FILLS_ANY, used=used, **kw)
        if c is None:
            print(f"  (no window satisfies {stratum})", file=sys.stderr)
            continue
        used.add(c["event_slug"])
        chosen.append((stratum, why, c))

    index = ["# L5 window case studies — complete chronological sequences",
             "",
             f"{len(chosen)} windows, each rendered in full: every fill in "
             "timestamp order with the reconstructed inventory after it.",
             "",
             "These files are descriptive only. They record what happened in "
             "each window. They contain no hypothesis about why.",
             "",
             "| # | stratum | window | selected as | fills | asset | min | "
             "spend | pair cost | residual | settled | P&L |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, (stratum, why, c) in enumerate(chosen, 1):
        index.append(
            f"| {i} | `{stratum}` | `{c['event_slug']}` | {why} | "
            f"{c['n_fills']} | {c['asset']} | {c['duration_minutes']} | "
            f"{trim(c['spend'], 2)} | {trim(c['pair_cost'])} | "
            f"{trim(c['residual_qty'], 2)} {c['residual_side']} | "
            f"{c['settled_outcome']} | {trim(c['pnl'], 2)} |")

    for i, (stratum, why, c) in enumerate(chosen, 1):
        body = render(stratum, why, c, wins[c["event_slug"]])
        with open(os.path.join(a.out, f"CASE_{i:02d}_{stratum}.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(body + "\n")

    with open(os.path.join(a.out, "CASE_STUDY_INDEX.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(index) + "\n")

    allp = os.path.join(a.out, "CASE_STUDIES_ALL.md")
    with open(allp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(index) + "\n\n---\n\n")
        for i, (stratum, why, c) in enumerate(chosen, 1):
            fh.write(render(stratum, why, c, wins[c["event_slug"]]))
            fh.write("\n---\n\n")

    rep = {"windows_scanned": len(fts), "case_studies": len(chosen),
           "strata_requested": len(plan),
           "combined_bytes": os.path.getsize(allp),
           "selected": [{"stratum": s, "event_slug": c["event_slug"],
                         "n_fills": c["n_fills"], "pnl": round(c["pnl"], 4)}
                        for s, _, c in chosen]}
    with open(os.path.join(a.out, "CASE_STUDY_REPORT.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: rep[k] for k in
                      ("windows_scanned", "case_studies", "strata_requested",
                       "combined_bytes")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
