"""BTC5m favorite accumulation on INDEPENDENT probabilities. READ-ONLY.

The shipped controller (`l5_btc5m_controller.decide`, ACCUMULATE, $0.45 cap)
replayed over the synchronized dataset `build_aligned.py` produces, with the
favorite chosen by the VENUE INDEX rather than by the book mid. Three
variants, each reported separately and never blended:

  A  BOOK          p from the book mid -- the shipped default, the baseline
  B  INDEPENDENT   p from the venue index (BRTI 60s average vs the strike),
                   model `l5_btc_reference.probability_up`. No gate.
  C  INDEPENDENT   as B, plus an OFFLINE edge filter: an entry is taken only
     + GATE        when  p_side - price - adverse_selection >= min_edge.

VARIANT C IS ANALYSIS ONLY AND IS NOT WIRED TO ANYTHING. It is applied here
as a post-filter on the controller's own chosen entries, so no gate is
introduced into the controller, the executor or any live path. Measuring a
gate is not the same as shipping one, and the owner's instruction is to
measure first.

CHRONOLOGICAL OUT-OF-SAMPLE. Windows are split by time, never shuffled:
the earlier block is where a threshold may be looked at, the later block is
where the chosen threshold is scored once. Nothing fitted on the later block
is reported as a result for it, and no parameter from either block is
carried into code by this script.

FILLS are the same two bounds as `replay.py` (ADVERSE / FAVOURED) -- the
tape carries no prints and no depth, so a maker fill is bounded, never
observed. FEES: maker commission zero, read back from the venue.

Usage: python3 -I analysis/l5/btc5m_accumulation/replay_independent.py
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ["L5_BTC5M_CONTROLLER"] = "ON"

from src import l5_btc5m_controller as C          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGNED = os.path.join(HERE, "btc5m_aligned_20261112.jsonl.gz")

STEP_S = 5.0
CANCEL_LEAD_S = 30.0
WINDOW_S = 300.0
DWELL_S = 10.0
CAP = D("0.45")
CLIP_USD = D("0.10")
MAKER_FEE_PER_SHARE = D("0")
ADVERSE = D("0.036")          # MEASURED on this tape, not the assumed 0.06
MIN_EDGE = D("0.01")

PARAMS = C.Params(entry_mode=C.ACCUMULATE, hedge_enabled=False,
                  reserve_mode="NONE", max_exposure_usd=CAP,
                  clip_usd=CLIP_USD, max_resting_entries=2,
                  accumulate_depth_ticks=0)


def load():
    w = collections.defaultdict(list)
    with gzip.open(ALIGNED, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            w[r["market"]].append(r)
    for rows in w.values():
        rows.sort(key=lambda r: r["t_in_window_s"])
    return w


def at(rows, t):
    best = None
    for r in rows:
        if r["t_in_window_s"] <= t:
            best = r
        else:
            break
    return best


def own_bid(row, side):
    leg = row["up"] if side == C.UP else row["down"]
    b = leg.get("bid")
    return None if b is None else D(str(b))


class Order:
    __slots__ = ("oid", "side", "price", "qty", "placed_t", "at_since")

    def __init__(self, oid, side, price, qty, t):
        self.oid, self.side, self.price, self.qty = oid, side, price, qty
        self.placed_t = t
        self.at_since = t


def run_window(rows, *, tier, variant):
    won = rows[0].get("settled_outcome")
    if won is None:
        return None
    inv = {C.UP: {"qty": D(0), "cost": D(0)},
           C.DOWN: {"qty": D(0), "cost": D(0)}}
    resting, fills = {}, []
    gated_out = 0
    nid = 0
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:
        row = at(rows, t)
        if row is None:
            t += STEP_S
            continue
        age = t - row["t_in_window_s"]

        for o in list(resting.values()):
            b = own_bid(row, o.side)
            if b is None:
                continue
            if b < o.price:
                hit, why = True, "BID_FELL_THROUGH"
            elif tier == "FAVOURED" and b == o.price and (
                    t - o.at_since) >= DWELL_S:
                hit, why = True, "DWELL_AT_PRICE"
            else:
                if b != o.price:
                    o.at_since = t
                hit, why = False, None
            if hit:
                inv[o.side]["qty"] += o.qty
                inv[o.side]["cost"] += o.qty * o.price
                fills.append({"side": o.side, "price": o.price, "qty": o.qty,
                              "t": t, "why": why, "won": o.side == won,
                              "p_side": o.__dict__ if False else None})
                del resting[o.oid]

        pu, psrc = None, None
        if variant in ("B", "C"):
            p = row.get("p_up") or {}
            if not p.get("ok") or p.get("value") is None:
                # no model belief: the live caller gets nothing, and this
                # replay must not quietly fall back to the book
                t += STEP_S
                continue
            pu, psrc = str(p["value"]), "VENUE_INDEX"

        out = C.decide(
            book={"bid": row["up"]["bid"], "ask": row["up"]["ask"],
                  "age_s": age},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s]["qty"]),
                           "cost": str(inv[s]["cost"])} for s in C.SIDES},
            resting=[{"id": o.oid, "side": o.side, "price": o.price,
                      "qty": o.qty, "age_s": t - o.placed_t}
                     for o in resting.values()],
            params=PARAMS, p_up=pu, p_source=psrc)

        for op in out["orders"]:
            if op["op"] == "CANCEL":
                resting.pop(op["id"], None)
                continue
            if variant == "C":
                # OFFLINE post-filter. Not a controller change.
                pv = D(str((row["p_up"] or {})["value"]))
                p_side = pv if op["side"] == C.UP else D(1) - pv
                if p_side - op["price"] - ADVERSE < MIN_EDGE:
                    gated_out += 1
                    continue
            nid += 1
            resting["o%d" % nid] = Order("o%d" % nid, op["side"], op["price"],
                                         op["qty"], t)
        t += STEP_S

    placed = nid
    return {"won": won, "fills": fills, "placed": placed,
            "gated_out": gated_out}


def stats(vals):
    n = len(vals)
    if n < 2:
        return (statistics.fmean(vals) if n else 0.0), 0.0, 0.0
    m = statistics.fmean(vals)
    se = statistics.stdev(vals) / math.sqrt(n)
    return m, se, (m / se if se else 0.0)


def score(results):
    """Per-window and per-share net, with standard errors."""
    us = [r for r in results if r]
    pw = [float(sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                    for f in r["fills"])) for r in us]
    fl = [f for r in us for f in r["fills"]]
    ps = [float((D(1) if f["won"] else D(0)) - f["price"]) for f in fl]
    m, se, t = stats(pw)
    m2, se2, t2 = stats(ps)
    return {
        "windows": len(us), "placed": sum(r["placed"] for r in us),
        "gated_out": sum(r["gated_out"] for r in us), "fills": len(fl),
        "net_total": sum(pw), "per_window": m, "pw_se": se, "pw_t": t,
        "per_share": m2, "ps_se": se2, "ps_t": t2,
        "win_rate": (sum(1 for f in fl if f["won"]) / len(fl)) if fl else 0.0,
        "avg_price": (statistics.fmean(float(f["price"]) for f in fl)
                      if fl else 0.0),
    }


def line(label, s):
    print("%-26s %7d %7d %7d %9.4f %+9.4f %8.4f %+6.2f %+9.4f %+6.2f"
          % (label, s["windows"], s["placed"], s["fills"], s["win_rate"],
             s["per_window"], s["pw_se"], s["pw_t"], s["per_share"],
             s["ps_t"]))


def header():
    print("%-26s %7s %7s %7s %9s %9s %8s %6s %9s %6s"
          % ("variant / block", "wins", "placed", "fills", "win_rate",
             "net/win", "se", "t", "net/sh", "t"))
    print("-" * 108)


def main():
    w = load()
    slugs = sorted(w)
    cut = slugs[len(slugs) * 2 // 3]
    early = [s for s in slugs if s < cut]
    late = [s for s in slugs if s >= cut]
    print("ALIGNED %s" % os.path.basename(ALIGNED))
    print("windows %d   chronological split at %s  (early %d / late %d)"
          % (len(slugs), cut[-16:], len(early), len(late)))
    print("adverse_selection used in variant C: %s a share (MEASURED on the "
          "whole tape)" % ADVERSE)
    print()
    for tier in ("ADVERSE", "FAVOURED"):
        print("=" * 108)
        print("FILL RULE: %s" % tier)
        print("=" * 108)
        header()
        for variant, name in (("A", "A book mid, no gate"),
                              ("B", "B venue index, no gate"),
                              ("C", "C venue index + gate")):
            runs = {s: run_window(w[s], tier=tier, variant=variant)
                    for s in slugs}
            line("%s / ALL" % name, score(runs.values()))
            line("%s / early" % name, score([runs[s] for s in early]))
            line("%s / late  (OUT-OF-SAMPLE)" % name,
                 score([runs[s] for s in late]))
            if variant == "C":
                g = sum(r["gated_out"] for r in runs.values() if r)
                print("%-26s entries refused by the offline gate: %d" % ("", g))
            print("-" * 108)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
