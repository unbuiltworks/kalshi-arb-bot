"""Reconstruct L5's inventory and realized economics after every transaction.

READ-ONLY over L5's own public BTC15m history. For each window this walks the
ordered trade sequence and carries, after every transaction:

    up_qty, dn_qty, weighted-average basis per side, matched qty,
    unmatched exposure, and the cash committed so far

then closes the window against L5's OWN REDEEM rows, which name the winning
outcome and the exact dollars redeemed. Realized net is therefore
`redeemed - cost` from L5's on-chain record, not a model.

TWO FACTS ABOUT THE SOURCE THAT SHAPE EVERYTHING BELOW:

  `usdcSize == size * price` exactly on every trade row, so these costs carry
  NO fee. Polymarket's taker fee does not appear in this record, where PMUS
  charges theta=0.0695 on a taker fill. Any comparison that ignores that is
  comparing two different fee regimes.

  A matched pair redeems $1.00 and the losing residual redeems $0, so
  REDEEM size = (holding of the winning outcome) = matched + (residual if the
  residual sits on the winning side).

Usage: python3 -I analysis/l5/stage_c/l5_inventory_reconstruction.py
"""

from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import statistics as st
import sys
from decimal import Decimal as D

SRC = ("data/l5/raw/activity.jsonl",
       "data/l5/raw/activity_gap.jsonl.gz",
       "data/l5/ops/activity_20260901.jsonl")
OUT = "/tmp/claude-0/l5_windows.jsonl"


def _rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            pay = d.get("payload")
            if isinstance(pay, list):
                for r in pay:
                    yield r
            elif isinstance(d, dict) and d.get("slug"):
                yield d


def load():
    seen = set()
    trades = collections.defaultdict(list)
    redeems = collections.defaultdict(list)
    for p in SRC:
        if not os.path.exists(p):
            continue
        for r in _rows(p):
            k = (r.get("transactionHash"), r.get("asset"),
                 r.get("timestamp"), r.get("size"), r.get("side"),
                 r.get("type"))
            if k in seen:
                continue
            seen.add(k)
            s = str(r.get("slug") or "")
            if not s.startswith("btc-updown-15m-"):
                continue
            try:
                ws = int(s.rsplit("-", 1)[1])
            except Exception:
                continue
            if r.get("type") == "TRADE":
                trades[s].append({
                    "t": int(r["timestamp"]) - ws,
                    "side": str(r.get("side") or ""),
                    "oc": str(r.get("outcome") or ""),
                    "q": D(str(r.get("size") or 0)),
                    "px": D(str(r.get("price") or 0)),
                    "usd": D(str(r.get("usdcSize") or 0))})
            elif r.get("type") == "REDEEM":
                redeems[s].append({
                    "oc": str(r.get("outcome") or ""),
                    "usd": D(str(r.get("usdcSize") or 0))})
    return trades, redeems


def reconstruct(acts):
    """Walk the sequence, carrying inventory after every transaction."""
    qty = {"Up": D(0), "Down": D(0)}
    cost = {"Up": D(0), "Down": D(0)}
    steps = []
    for a in sorted(acts, key=lambda x: x["t"]):
        oc = a["oc"]
        if oc not in qty:
            continue
        before_imb = abs(qty["Up"] - qty["Down"])
        qty[oc] += a["q"]
        cost[oc] += a["usd"]
        matched = min(qty["Up"], qty["Down"])
        imb = abs(qty["Up"] - qty["Down"])
        steps.append({
            "t": a["t"], "oc": oc, "q": str(a["q"]), "px": str(a["px"]),
            "up": str(qty["Up"]), "dn": str(qty["Down"]),
            "b_up": str(cost["Up"] / qty["Up"]) if qty["Up"] else None,
            "b_dn": str(cost["Down"] / qty["Down"]) if qty["Down"] else None,
            "matched": str(matched),
            "unmatched": str(imb),
            "heavy": ("Up" if qty["Up"] > qty["Down"]
                      else ("Down" if qty["Down"] > qty["Up"] else None)),
            # DOES THIS TRADE INCREASE OR REDUCE IMBALANCE? The segmentation
            # in item 3 turns on this and on nothing else.
            "imb_delta": str(imb - before_imb),
            "reduces": bool(imb < before_imb),
            "cash": str(cost["Up"] + cost["Down"])})
    return qty, cost, steps


def main():
    trades, redeems = load()
    print("BTC15m windows with trades: %d | with REDEEM rows: %d"
          % (len(trades), len(redeems)))
    out = open(OUT, "w")
    nrec = 0
    for slug, acts in trades.items():
        qty, cost, steps = reconstruct(acts)
        if not steps:
            continue
        up, dn = qty["Up"], qty["Down"]
        matched = min(up, dn)
        resid = up - dn
        rside = "Up" if resid > 0 else ("Down" if resid < 0 else None)
        b_up = (cost["Up"] / up) if up else D(0)
        b_dn = (cost["Down"] / dn) if dn else D(0)
        total_cost = cost["Up"] + cost["Down"]
        rr = redeems.get(slug) or []
        redeemed = sum((r["usd"] for r in rr), D(0))
        # the winner is the outcome L5 redeemed a POSITIVE amount of
        win = None
        for r in rr:
            if r["usd"] > 0:
                win = r["oc"]
                break
        rec = {
            "slug": slug, "n_trades": len(steps),
            "up": str(up), "dn": str(dn), "matched": str(matched),
            "residual": str(abs(resid)), "residual_side": rside,
            "b_up": str(b_up), "b_dn": str(b_dn),
            "pair_cost": str(b_up + b_dn) if (up and dn) else None,
            "cost_total": str(total_cost),
            "redeemed": str(redeemed),
            "winner": win,
            "has_redeem": bool(rr),
            "first_t": steps[0]["t"], "last_t": steps[-1]["t"],
            "peak_unmatched": str(max(D(s["unmatched"]) for s in steps)),
            "reduce_frac": round(
                sum(1 for s in steps if s["reduces"]) / len(steps), 4),
        }
        if rr:
            rec["realized_net"] = str(redeemed - total_cost)
            # DECOMPOSITION. A matched pair redeems $1.00 either way; the
            # residual redeems $1 only if it sits on the winning side.
            rec["matched_pnl"] = str(matched * (D(1) - (b_up + b_dn))
                                     if (up and dn) else D(0))
            if rside is not None and win is not None:
                rb = b_up if rside == "Up" else b_dn
                won = (rside == win)
                rec["residual_pnl"] = str(
                    abs(resid) * ((D(1) if won else D(0)) - rb))
                rec["residual_won"] = won
            else:
                rec["residual_pnl"] = "0"
                rec["residual_won"] = None
        out.write(json.dumps(rec) + "\n")
        nrec += 1
    out.close()
    print("reconstructed %d windows -> %s" % (nrec, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
