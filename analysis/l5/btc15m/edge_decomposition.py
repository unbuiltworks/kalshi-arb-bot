"""WHERE DOES THE COMPETITOR'S MONEY COME FROM? Per-unit, against ours.

THE THREE CANDIDATE EDGES, and the one measurement that separates them.

A matched UP/DOWN pair redeems exactly $1.00 whatever the index does, so a
window's economics decompose into exactly two prices and two volumes:

    net = matched_pairs * (1 - pair_cost)  +  residual_units * residual_pnl_per_unit

ACQUISITION advantage shows up as a lower `pair_cost`.
PAIRING advantage shows up as a higher matched:residual VOLUME RATIO.
INVENTORY/directional advantage shows up as a better `residual_pnl_per_unit`.
LATENCY, if it exists, has to express itself through one of those three --
there is no fourth place for money to enter this arithmetic.

So the four candidate explanations are not independent stories to rank by
narrative; three of them are terms in one identity and the fourth is a
mechanism that would have to move one of the three. This script measures all
three for the competitor and prints them beside our own venue-confirmed
figures, which were computed by the same average-cost convention
(`analysis/l5/btc15m_live_ledger.json`, 106 settled windows).

MATCHING CONVENTION, stated once and used everywhere:
  matched_units   = min(total UP units, total DOWN units) after SELLs
  pair_cost       = avg cost of a UP unit + avg cost of a DOWN unit
  matched_pnl     = matched_units * (1 - pair_cost)
  residual_units  = |UP - DOWN|
  residual_pnl    = total_pnl - matched_pnl, where total_pnl is the
                    AUTHORITATIVE cashflow (REDEEM received - cost paid)
No contract and no rebate is counted twice: matched_pnl is derived from
average costs and residual_pnl is the remainder of the real cashflow.

UNCERTAINTY is bootstrapped over WINDOWS, never over fills. Fills inside a
window share one settlement, so resampling fills would overstate n by the
number of fills per window -- an error I made earlier in this project and
which erased an apparent 7-cent edge once corrected.
"""
from __future__ import annotations

import collections
import json
import random
import statistics as st
import sys

UP, DOWN = "Up", "Down"
CLUSTER_S = 1


def load(path, slug_filter=None):
    w = collections.defaultdict(lambda: {"fills": [], "redeem": 0.0,
                                         "reb": 0.0})
    for ln in open(path):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        slug = r.get("slug")
        if not slug or (slug_filter and slug_filter not in slug):
            continue
        k = r.get("type", "TRADE")
        if k == "TRADE":
            try:
                w[slug]["fills"].append({
                    "ts": int(r["timestamp"]), "side": str(r["side"]),
                    "outcome": str(r["outcome"]), "size": float(r["size"]),
                    "price": float(r["price"])})
            except Exception:
                continue
        elif k == "REDEEM":
            w[slug]["redeem"] += float(r.get("usdc_size") or 0.0)
        elif k in ("MAKER_REBATE", "TAKER_REBATE"):
            w[slug]["reb"] += float(r.get("usdc_size") or 0.0)
    for v in w.values():
        v["fills"].sort(key=lambda f: f["ts"])
    return w


def window_economics(v):
    """The identity above, for one window. None when it never settled."""
    inv = {UP: 0.0, DOWN: 0.0}
    cost = {UP: 0.0, DOWN: 0.0}
    for f in v["fills"]:
        s = f["size"] if f["side"] == "BUY" else -f["size"]
        inv[f["outcome"]] += s
        cost[f["outcome"]] += s * f["price"]
    spent = cost[UP] + cost[DOWN]
    if v["redeem"] <= 0:
        return None
    matched = max(min(inv[UP], inv[DOWN]), 0.0)
    resid = abs(inv[UP] - inv[DOWN])
    avg = {s: (cost[s] / inv[s]) if inv[s] > 0 else None for s in (UP, DOWN)}
    pair_cost = (avg[UP] + avg[DOWN]
                 if avg[UP] is not None and avg[DOWN] is not None else None)
    total = v["redeem"] - spent
    matched_pnl = (matched * (1.0 - pair_cost)
                   if pair_cost is not None else None)
    resid_pnl = total - matched_pnl if matched_pnl is not None else None
    return {"matched_units": matched, "residual_units": resid,
            "pair_cost": pair_cost, "matched_pnl": matched_pnl,
            "residual_pnl": resid_pnl, "total_pnl": total, "spent": spent,
            "rebate": v["reb"]}


def boot_ratio(rows, num, den, iters=10000, seed=20261119):
    """Window-clustered CI for a RATIO of sums (per-unit figures are ratios)."""
    rnd = random.Random(seed)
    def ratio(sample):
        d = sum(den(r) for r in sample)
        return (sum(num(r) for r in sample) / d) if d else float("nan")
    point = ratio(rows)
    vals = sorted(x for x in (ratio(rnd.choices(rows, k=len(rows)))
                              for _ in range(iters)) if x == x)
    return point, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def report(label, rows):
    rows = [r for r in rows if r is not None]
    pr = [r for r in rows if r["pair_cost"] is not None]
    mu = sum(r["matched_units"] for r in rows)
    ru = sum(r["residual_units"] for r in rows)
    print("=" * 78)
    print("%s -- %d settled windows" % (label, len(rows)))
    print("=" * 78)
    print("  total spent      $%14.2f" % sum(r["spent"] for r in rows))
    print("  realized net     $%+14.2f" % sum(r["total_pnl"] for r in rows))
    print("  rebates credited $%+14.2f" % sum(r["rebate"] for r in rows))
    print()
    p, lo, hi = boot_ratio(pr, lambda r: r["matched_pnl"],
                           lambda r: r["matched_units"])
    print("  ACQUISITION  gain per matched pair   %+0.4f  [%+0.4f, %+0.4f]"
          % (p, lo, hi))
    pcs = sorted(r["pair_cost"] for r in pr)
    print("               implied pair cost       %0.4f  (median %0.4f)"
          % (1.0 - p, st.median(pcs)))
    p, lo, hi = boot_ratio(rows, lambda r: r["residual_pnl"] or 0.0,
                           lambda r: r["residual_units"])
    print("  INVENTORY    P&L per residual unit   %+0.4f  [%+0.4f, %+0.4f]"
          % (p, lo, hi))
    p, lo, hi = boot_ratio(rows, lambda r: r["matched_units"],
                           lambda r: r["residual_units"])
    print("  PAIRING      matched:residual units  %0.4f  [%0.4f, %0.4f]"
          % (p, lo, hi))
    print("               matched units %12.1f | residual units %12.1f"
          % (mu, ru))
    #: the break-even ratio implied by THIS sample's own per-unit prices
    gpp, _, _ = boot_ratio(pr, lambda r: r["matched_pnl"],
                           lambda r: r["matched_units"])
    lpr, _, _ = boot_ratio(rows, lambda r: r["residual_pnl"] or 0.0,
                           lambda r: r["residual_units"])
    if gpp > 0 and lpr < 0:
        need = -lpr / gpp
        print("               ratio REQUIRED to break even %0.4f -> %s"
              % (need, "CLEARS IT" if (mu / ru) > need else "SHORT OF IT"))
    print("  windows net positive: %d of %d (%.1f%%)"
          % (sum(1 for r in rows if r["total_pnl"] > 0), len(rows),
             100.0 * sum(1 for r in rows if r["total_pnl"] > 0) / len(rows)))
    print()
    return {"gain_per_pair": gpp, "pnl_per_residual": lpr,
            "ratio": mu / ru if ru else None}


def main(path):
    out = {}
    for tag, filt in (("0x8dxd BTC15m", "btc-updown-15m-"),
                      ("0x8dxd BTC5m", "btc-updown-5m-")):
        w = load(path, filt)
        if not w:
            continue
        rows = [window_economics(v) for v in w.values()]
        out[tag] = report(tag, rows)

    #: OUR OWN, venue-confirmed, same convention
    led = json.load(open("analysis/l5/btc15m_live_ledger.json"))
    print("=" * 78)
    print("OURS BTC15m -- %d settled windows, VENUE_CONFIRMED_LIVE"
          % led["counts"]["settled"])
    print("=" * 78)
    cp, rs = led["completed_pairs"], led["residual"]
    print("  ACQUISITION  gain per matched pair   %+0.4f"
          % float(cp["gain_per_matched_pair_usd"]))
    print("  INVENTORY    P&L per residual unit   %+0.4f"
          % float(rs["loss_per_residual_unit_usd"]))
    print("  PAIRING      matched:residual units  %0.4f"
          % (float(cp["matched_pairs_contracts"]) / float(rs["residual_units"])))
    print("               matched units %12.1f | residual units %12.1f"
          % (float(cp["matched_pairs_contracts"]), float(rs["residual_units"])))
    print("               ratio REQUIRED to break even %s -> SHORT OF IT"
          % led["breakeven"]["matched_to_residual_ratio_required"][:6])
    print("  realized net     $%+14.2f  (placeholder-bounded true range %s)"
          % (float(led["authoritative_cashflow"]["realized_net_usd"]),
             led["account_total_corrected"]["account_true_range_usd"]))
    print()
    o = {"gain_per_pair": float(cp["gain_per_matched_pair_usd"]),
         "pnl_per_residual": float(rs["loss_per_residual_unit_usd"]),
         "ratio": float(cp["matched_pairs_contracts"]) / float(rs["residual_units"])}

    print("=" * 78)
    print("SIDE BY SIDE -- where the difference actually is")
    print("=" * 78)
    print("%-22s %14s %14s %14s" % ("", "gain/pair", "P&L/residual", "matched:resid"))
    for tag, v in list(out.items()) + [("OURS BTC15m", o)]:
        print("%-22s %+14.4f %+14.4f %14.4f"
              % (tag, v["gain_per_pair"], v["pnl_per_residual"],
                 v["ratio"] if v["ratio"] else float("nan")))
    print()
    print("Competitor figures are OBSERVED fills + REDEEM cashflow. Ours are")
    print("VENUE_CONFIRMED settled rows. Neither is a model. Rebates are")
    print("reported separately and are never netted into a per-unit price.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
