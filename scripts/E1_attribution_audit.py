"""Item 1 -- audit the +6,077 / -5,628 / +2,413 decomposition.

THE SUSPICION THIS TESTS. The decomposition attributed residual lots to the
mode that bought them, using FIFO pairing. But FIFO consumes the EARLIEST
lots first, so whatever remains unpaired is composed of the LATEST lots --
and taker fills are late by construction (median 144s to settlement versus
441s for maker). So FIFO may assign the residual to the taker mode
mechanically, not economically. The symptom is already visible: taker
residual (143,582 contracts) exceeds maker residual (75,656) despite taker
having four times fewer fills.

Three attribution rules are therefore computed on identical fills:

  FIFO      pair the earliest lots first; residual = the latest lots
  LIFO      pair the latest lots first; residual = the earliest lots
  PRO_RATA  residual split across modes in proportion to each mode's share
            of total quantity bought on the residual side -- order-free, so
            it is the neutral reference

If the sign of the per-mode residual P&L flips between FIFO and LIFO, the
original finding is an artefact of the pairing convention and must be
withdrawn. If it survives all three, it is real.

RECONCILIATION. The components must sum to the window P&L actually
observed:
    window P&L  ==  locked matched profit  +  residual settlement P&L
and the audit reports the residual of that identity, per rule, so the
accounting can be checked rather than trusted.

FEES. Polymarket per-fill fees are NOT in the corpus (confirmed only where
a position snapshot carries entryFeesUsdc, a minority). Every figure here
is therefore GROSS of venue fees and is an upper bound on net. Account-level
rebates/rewards carry an empty slug and cannot enter a per-mode
decomposition at all.

DATASET BOUNDARY. Only the 72,550 fills inside the takerOnly dense window
carry an execution label, and only fills labelled TAKER_OBSERVED or
MAKER_INFERRED are attributed. COVERAGE_UNKNOWN fills are carried in the
replay (they are real inventory) but excluded from mode attribution, and
the quantity they account for is reported so the gap is visible.

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
from collections import deque

EPS = 1e-9


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def pair_off(lots_up, lots_dn, lifo):
    """Consume matched quantity from both queues. Returns matched qty and
    spend. Mutates the queues."""
    mq = ms = 0.0
    while True:
        uq = sum(l[0] for l in lots_up)
        dq = sum(l[0] for l in lots_dn)
        pair = min(uq, dq)
        if pair <= EPS:
            break
        for q in (lots_up, lots_dn):
            need = pair
            while need > EPS:
                lot = q[-1] if lifo else q[0]
                use = min(lot[0], need)
                ms += use * lot[1]
                lot[0] -= use
                need -= use
                if lot[0] <= EPS:
                    if lifo:
                        q.pop()
                    else:
                        q.popleft()
        mq += pair
    return mq, ms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    lab = {}
    for r in csv.DictReader(gzip.open(a.labels, "rt")):
        lab[(r["event_slug"], r["trade_seq"])] = r["classification"]

    wins = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if (r["event_slug"], r["trade_seq"]) in lab:
                    wins[r["event_slug"]].append(r)

    RULES = ("FIFO", "LIFO", "PRO_RATA")
    agg = {k: {c: {"resid_qty": 0.0, "resid_spend": 0.0, "resid_won": 0.0}
               for c in ("TAKER_OBSERVED", "MAKER_INFERRED",
                         "COVERAGE_UNKNOWN")} for k in RULES}
    totals = {"matched_qty": 0.0, "matched_spend": 0.0, "spend": 0.0,
              "window_pnl": 0.0, "windows": 0, "fills": 0,
              "qty_by_class": collections.Counter()}
    recon = {k: [] for k in RULES}

    for slug, rows in wins.items():
        rows.sort(key=lambda r: int(r["trade_seq"]))
        winner = (rows[-1]["settled_outcome"] or "").strip().lower()
        if not winner:
            continue
        totals["windows"] += 1
        fills = []
        for r in rows:
            px, sz = f(r["price"]), f(r["size"])
            if px is None or sz is None:
                continue
            c = lab[(r["event_slug"], r["trade_seq"])]
            fills.append((r["leg"], px, sz, c))
            totals["fills"] += 1
            totals["qty_by_class"][c] += sz

        for rule in RULES:
            lifo = rule == "LIFO"
            up, dn = deque(), deque()
            spend = 0.0
            mq = ms = 0.0
            # Replay in order; pair as inventory allows, per the rule.
            for leg, px, sz, c in fills:
                (up if leg == "UP" else dn).append([sz, px, c])
                spend += sz * px
                if rule == "PRO_RATA":
                    continue            # pair once at the end, order-free
                dmq, dms = pair_off(up, dn, lifo)
                mq += dmq
                ms += dms
            if rule == "PRO_RATA":
                mq, ms = pair_off(up, dn, lifo=False)
            resid = list(up) + list(dn)
            rq = sum(l[0] for l in resid)
            rside = "up" if sum(l[0] for l in up) > EPS else (
                "down" if sum(l[0] for l in dn) > EPS else "")
            won = bool(rside and rside == winner)

            if rule == "PRO_RATA" and rq > EPS:
                # split the residual by each mode's share of quantity bought
                # on the residual side -- independent of ordering
                side = "UP" if rside == "up" else "DOWN"
                byc = collections.Counter()
                spc = collections.Counter()
                for leg, px, sz, c in fills:
                    if leg == side:
                        byc[c] += sz
                        spc[c] += sz * px
                tot = sum(byc.values())
                for c, q in byc.items():
                    share = q / tot if tot else 0.0
                    vwap = spc[c] / q if q else 0.0
                    agg[rule][c]["resid_qty"] += rq * share
                    agg[rule][c]["resid_spend"] += rq * share * vwap
                    if won:
                        agg[rule][c]["resid_won"] += rq * share
            else:
                for lot in resid:
                    q, px, c = lot[0], lot[1], lot[2]
                    agg[rule][c]["resid_qty"] += q
                    agg[rule][c]["resid_spend"] += q * px
                    if won:
                        agg[rule][c]["resid_won"] += q

            locked = mq - ms
            resid_pnl = (rq if won else 0.0) - sum(l[0] * l[1] for l in resid)
            wpnl = mq + (rq if won else 0.0) - spend
            recon[rule].append(wpnl - (locked + resid_pnl))
            if rule == "FIFO":
                totals["matched_qty"] += mq
                totals["matched_spend"] += ms
                totals["spend"] += spend
                totals["window_pnl"] += wpnl

    R = {
        "dataset_boundary": {
            "scope": "fills carrying an execution label inside the "
                     "takerOnly dense window",
            "windows_with_settlement": totals["windows"],
            "fills": totals["fills"],
            "quantity_by_class": {k: round(v, 2) for k, v in
                                  totals["qty_by_class"].items()},
            "_note": "COVERAGE_UNKNOWN fills are replayed as real inventory "
                     "but are not a mode; their quantity is shown so the "
                     "unattributed share is visible.",
        },
        "fees": {
            "venue_fees_included": False,
            "why": "Polymarket per-fill fees are absent from the corpus "
                   "(confirmed only where a position snapshot carries "
                   "entryFeesUsdc, a minority), so every figure is GROSS "
                   "and an upper bound on net.",
            "incentives_included": False,
            "incentive_reason": "MAKER_REBATE / TAKER_REBATE / REWARD carry "
                                "an empty slug; account-level only, so they "
                                "cannot enter a per-mode decomposition.",
        },
        "matched_book_FIFO": {
            "matched_pairs": round(totals["matched_qty"], 2),
            "matched_spend": round(totals["matched_spend"], 2),
            "locked_profit": round(totals["matched_qty"]
                                   - totals["matched_spend"], 2),
            "_note": "settlement-independent; a pair needs one unit from "
                     "each leg so it is not attributable to one mode.",
        },
        "total_window_pnl_FIFO": round(totals["window_pnl"], 2),
        "residual_attribution_by_rule": {},
        "reconciliation": {},
    }
    for rule in RULES:
        block = {}
        for c, d in agg[rule].items():
            if d["resid_qty"] <= EPS:
                continue
            block[c] = {
                "resid_contracts": round(d["resid_qty"], 2),
                "resid_spend": round(d["resid_spend"], 2),
                "resid_won_contracts": round(d["resid_won"], 2),
                "win_rate": round(d["resid_won"] / d["resid_qty"], 4),
                "resid_settlement_pnl": round(
                    d["resid_won"] - d["resid_spend"], 2),
            }
        R["residual_attribution_by_rule"][rule] = block
        res = recon[rule]
        R["reconciliation"][rule] = {
            "identity": "window_pnl == locked_matched + residual_pnl",
            "max_abs_error": round(max(abs(x) for x in res), 8) if res else None,
            "mean_abs_error": round(
                statistics.fmean(abs(x) for x in res), 10) if res else None,
        }

    tk = {r: R["residual_attribution_by_rule"][r]
          .get("TAKER_OBSERVED", {}).get("resid_settlement_pnl")
          for r in RULES}
    mk = {r: R["residual_attribution_by_rule"][r]
          .get("MAKER_INFERRED", {}).get("resid_settlement_pnl")
          for r in RULES}
    signs_tk = {None if v is None else (v > 0) for v in tk.values()}
    signs_mk = {None if v is None else (v > 0) for v in mk.values()}
    R["VERDICT"] = {
        "taker_residual_pnl_by_rule": tk,
        "maker_residual_pnl_by_rule": mk,
        "taker_sign_stable": len(signs_tk) == 1,
        "maker_sign_stable": len(signs_mk) == 1,
        "ruling": (
            "ROBUST: the per-mode residual P&L keeps its sign under FIFO, "
            "LIFO and pro-rata, so the finding is not an artefact of the "
            "pairing convention."
            if len(signs_tk) == 1 and len(signs_mk) == 1 else
            "ARTEFACT: the sign of at least one mode's residual P&L flips "
            "with the pairing convention. The original decomposition is an "
            "accounting artefact and is WITHDRAWN."),
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
