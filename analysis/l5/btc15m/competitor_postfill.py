"""What 0x8dxd actually does immediately after each confirmed fill. BTC15m.

EVIDENCE, and its exact provenance. `data/l5/competitor_0x8dxd_full/
activity.jsonl`, filtered to `btc-updown-15m-*`: 228,561 rows of type TRADE
and 2,041 of type REDEEM across 1,033 windows, 2026-03-12 to 2026-04-06.

WHAT IS OBSERVED vs WHAT IS NOT, because the brief turns on this:

  OBSERVED   fills -- a TRADE row is an executed trade with side, outcome,
             size, price and a 1-second timestamp, and a REDEEM row is an
             actual settlement cashflow.
  NOT OBSERVED  order submissions, cancellations, repricings, queue position,
             and anything resting that never filled. This feed carries no
             order book of theirs. So "they replenished the same side" here
             means THE NEXT FILL WAS ON THE SAME SIDE -- it does not mean a
             new order was placed at that moment, and it cannot: a fill may
             come from an order that had been resting for minutes.
  INFERRED   inventory, matched pairs and residual, which are arithmetic on
             the observed fills, and are exact only if this wallet's BTC15m
             activity is complete in this feed.

k9Q2 IS NOT IN THIS ANALYSIS because there is no evidence for it. The name
appears in two places in the repository -- a comment in
`l5_btc5m_controller` and one markdown file -- and no order, fill or activity
data for it exists anywhere in the tree. Reconstructing its post-fill
behaviour is therefore not possible, and nothing here should be read as
having done so.

WHAT PROFITABILITY HERE DOES AND DOES NOT MEAN. A window that settled
positive does not make each decision inside it correct, and this script
reports the per-window decomposition (matched vs residual) precisely so that
a profitable total cannot hide a losing component.
"""
from __future__ import annotations

import collections
import json
import statistics as st
import sys

UP, DOWN = "Up", "Down"


def load(path):
    """slug -> {"fills": [...], "redeem": usdc}, fills in observed order."""
    w = collections.defaultdict(lambda: {"fills": [], "redeem": 0.0,
                                         "rebate_maker": 0.0,
                                         "rebate_taker": 0.0})
    bad = 0
    for ln in open(path):
        try:
            r = json.loads(ln)
        except Exception:
            bad += 1
            continue
        slug = r.get("slug")
        if not slug:
            bad += 1
            continue
        kind = r.get("type")
        if kind == "TRADE":
            try:
                w[slug]["fills"].append({
                    "ts": int(r["timestamp"]), "side": str(r["side"]),
                    "outcome": str(r["outcome"]), "size": float(r["size"]),
                    "price": float(r["price"])})
            except Exception:
                bad += 1
        elif kind == "REDEEM":
            try:
                w[slug]["redeem"] += float(r.get("usdc_size") or 0.0)
            except Exception:
                bad += 1
        elif kind == "MAKER_REBATE":
            w[slug]["rebate_maker"] += float(r.get("usdc_size") or 0.0)
        elif kind == "TAKER_REBATE":
            w[slug]["rebate_taker"] += float(r.get("usdc_size") or 0.0)
    for v in w.values():
        v["fills"].sort(key=lambda f: f["ts"])
    return w, bad


CLUSTER_S = 1


def cluster(fills):
    """Consecutive fills of the same (outcome, side) within CLUSTER_S seconds
    collapsed into ONE decision, size-weighted on price.

    WHY THIS IS NECESSARY. Measured on the raw fills, the median gap to the
    next fill is 0 seconds and the 75th percentile is also 0: most adjacent
    fills are one order being swept in pieces, not a new decision. Counting
    them raw reported 76.7% "SAME_SIDE_BUY", which mostly measured partial
    fills of a single order. A transition statistic is only about behaviour
    once each order's pieces count once.
    """
    out = []
    for f in fills:
        if (out and out[-1]["outcome"] == f["outcome"]
                and out[-1]["side"] == f["side"]
                and f["ts"] - out[-1]["ts_last"] <= CLUSTER_S):
            c = out[-1]
            tot = c["size"] + f["size"]
            c["price"] = (c["price"] * c["size"] + f["price"] * f["size"]) / tot
            c["size"] = tot
            c["ts_last"] = f["ts"]
            c["pieces"] += 1
            continue
        out.append({"ts": f["ts"], "ts_last": f["ts"], "side": f["side"],
                    "outcome": f["outcome"], "size": f["size"],
                    "price": f["price"], "pieces": 1})
    return out


def signed(f):
    """Shares added to that outcome. SELL reduces it."""
    return f["size"] if f["side"] == "BUY" else -f["size"]


def reconstruct(fills):
    """Per-fill transitions plus the window's inventory and cost history."""
    inv = {UP: 0.0, DOWN: 0.0}
    cost = {UP: 0.0, DOWN: 0.0}
    out = []
    for i, f in enumerate(fills):
        o = f["outcome"]
        inv[o] += signed(f)
        cost[o] += signed(f) * f["price"]
        nxt = fills[i + 1] if i + 1 < len(fills) else None
        if nxt is None:
            action = "TERMINAL"
            gap = None
            dpx = None
        else:
            same = nxt["outcome"] == o
            buy = nxt["side"] == "BUY"
            action = ("SAME_SIDE_BUY" if (same and buy) else
                      "SAME_SIDE_SELL" if same else
                      "OPPOSITE_SIDE_BUY" if buy else
                      "OPPOSITE_SIDE_SELL")
            gap = nxt["ts"] - f["ts"]
            dpx = (nxt["price"] - f["price"]) if same else None
        heavy = (UP if inv[UP] > inv[DOWN] else
                 (DOWN if inv[DOWN] > inv[UP] else None))
        out.append({
            "i": i, "outcome": o, "side": f["side"], "price": f["price"],
            "size": f["size"], "next": action, "gap_s": gap,
            "same_side_dpx": dpx,
            "inv_up": inv[UP], "inv_down": inv[DOWN],
            "imbalance": inv[UP] - inv[DOWN],
            "filled_heavy_side": (heavy is not None and o == heavy),
            "next_adds_to_heavy": (nxt is not None and heavy is not None
                                   and nxt["side"] == "BUY"
                                   and nxt["outcome"] == heavy),
        })
    return out, inv, cost


def economics(inv, cost, redeem):
    """Matched-pair and residual decomposition. Settlement-anchored."""
    matched = min(inv[UP], inv[DOWN])
    matched = max(matched, 0.0)
    resid_side = UP if inv[UP] > inv[DOWN] else DOWN
    resid = abs(inv[UP] - inv[DOWN])
    spent = cost[UP] + cost[DOWN]
    #: average cost of a matched pair, where one exists
    avg_px = {}
    for s in (UP, DOWN):
        avg_px[s] = (cost[s] / inv[s]) if inv[s] > 0 else None
    pair_cost = (avg_px[UP] + avg_px[DOWN]
                 if avg_px[UP] is not None and avg_px[DOWN] is not None
                 else None)
    #: matched pairs pay exactly 1.00 a pair whatever settles
    matched_pnl = (matched * (1.0 - pair_cost)
                   if pair_cost is not None else None)
    #: everything else is the residual's, and REDEEM is the only cashflow
    total_pnl = redeem - spent
    resid_pnl = (total_pnl - matched_pnl
                 if matched_pnl is not None else None)
    return {"matched_pairs": matched, "residual_shares": resid,
            "residual_side": resid_side, "spent": spent, "redeem": redeem,
            "pair_cost": pair_cost, "matched_pnl": matched_pnl,
            "residual_pnl": resid_pnl, "total_pnl": total_pnl}


def main(path):
    w, bad = load(path)
    print("=" * 78)
    print("0x8dxd BTC15m POST-FILL RECONSTRUCTION")
    print("=" * 78)
    fills_n = sum(len(v["fills"]) for v in w.values())
    dec_n = sum(len(cluster(v["fills"])) for v in w.values())
    print("windows %d | observed fills %d | unparseable rows %d"
          % (len(w), fills_n, bad))
    print("fills collapsed into DECISIONS (same side/outcome within %ds): %d"
          % (CLUSTER_S, dec_n))
    print("  -> %.1f fills per decision; a transition statistic on raw fills"
          % (fills_n / max(1, dec_n)))
    print("     would mostly be measuring partial fills of one order.")
    reb_m = sum(v["rebate_maker"] for v in w.values())
    reb_t = sum(v["rebate_taker"] for v in w.values())
    print("rebates credited in this slice: maker $%.2f, taker $%.2f"
          % (reb_m, reb_t))

    trans = collections.Counter()
    gaps, dpx_same, imb_at_fill = [], [], []
    heavy_then_heavier = both_cnt = 0
    rows = []
    for slug, v in sorted(w.items()):
        if not v["fills"]:
            continue
        seq, inv, cost = reconstruct(cluster(v["fills"]))
        for s in seq:
            trans[s["next"]] += 1
            if s["gap_s"] is not None:
                gaps.append(s["gap_s"])
            if s["same_side_dpx"] is not None:
                dpx_same.append(s["same_side_dpx"])
            imb_at_fill.append(abs(s["imbalance"]))
            if s["filled_heavy_side"]:
                both_cnt += 1
                if s["next_adds_to_heavy"]:
                    heavy_then_heavier += 1
        e = economics(inv, cost, v["redeem"])
        e["slug"] = slug
        e["fills"] = len(v["fills"])
        rows.append(e)

    tot = sum(trans.values())
    print("\n--- WHAT HAPPENS AFTER A FILL (next observed fill, n=%d) ---" % tot)
    for k, n in trans.most_common():
        print("  %-20s %7d  %5.1f%%" % (k, n, 100.0 * n / tot))

    print("\n--- TIMING AND PRICE ---")
    gaps.sort()
    print("  gap to next fill (s): median %.0f  p25 %.0f  p75 %.0f  p95 %.0f"
          % (st.median(gaps), gaps[len(gaps) // 4],
             gaps[3 * len(gaps) // 4], gaps[int(0.95 * len(gaps))]))
    print("  same-side price change: mean %+0.4f  median %+0.4f"
          % (st.fmean(dpx_same), st.median(dpx_same)))
    print("  |imbalance| at a fill: median %.1f  mean %.1f  p95 %.1f shares"
          % (st.median(imb_at_fill), st.fmean(imb_at_fill),
             sorted(imb_at_fill)[int(0.95 * len(imb_at_fill))]))
    if both_cnt:
        print("  filled on the HEAVY side, then added to it again: %d/%d (%.1f%%)"
              % (heavy_then_heavier, both_cnt,
                 100.0 * heavy_then_heavier / both_cnt))

    print("\n--- PAIR ECONOMICS AND SETTLEMENT (per window) ---")
    with_pair = [r for r in rows if r["pair_cost"] is not None]
    pcs = sorted(r["pair_cost"] for r in with_pair)
    print("  windows with both sides held: %d of %d" % (len(with_pair), len(rows)))
    print("  pair acquisition cost: median %.4f  p10 %.4f  p90 %.4f"
          % (st.median(pcs), pcs[len(pcs) // 10], pcs[9 * len(pcs) // 10]))
    print("  pair cost < 1.00 in %d of %d (%.1f%%)"
          % (sum(1 for x in pcs if x < 1.0), len(pcs),
             100.0 * sum(1 for x in pcs if x < 1.0) / len(pcs)))
    resid = [r["residual_shares"] for r in rows]
    print("  residual carried into settlement: median %.1f  mean %.1f shares"
          % (st.median(resid), st.fmean(resid)))
    print("  windows carrying ZERO residual: %d (%.1f%%)"
          % (sum(1 for x in resid if x < 1e-9),
             100.0 * sum(1 for x in resid if x < 1e-9) / len(resid)))

    settled = [r for r in rows if r["redeem"] > 0]
    print("\n--- REALIZED, from REDEEM cashflow (%d windows) ---" % len(settled))
    tp = sum(r["total_pnl"] for r in settled)
    print("  total spent $%.2f | total redeemed $%.2f | net $%+.2f"
          % (sum(r["spent"] for r in settled),
             sum(r["redeem"] for r in settled), tp))
    print("  mean per window $%+.4f | median $%+.4f"
          % (tp / len(settled),
             st.median([r["total_pnl"] for r in settled])))
    win = sum(1 for r in settled if r["total_pnl"] > 0)
    print("  windows net positive: %d of %d (%.1f%%)"
          % (win, len(settled), 100.0 * win / len(settled)))
    mp = [r for r in settled if r["matched_pnl"] is not None]
    if mp:
        print("  matched-pair P&L  $%+.2f over %d windows (mean $%+.4f)"
              % (sum(r["matched_pnl"] for r in mp), len(mp),
                 st.fmean([r["matched_pnl"] for r in mp])))
        print("  residual P&L      $%+.2f over %d windows (mean $%+.4f)"
              % (sum(r["residual_pnl"] for r in mp), len(mp),
                 st.fmean([r["residual_pnl"] for r in mp])))
        turnover = sum(r["spent"] for r in settled)
        print("\n--- RETURN ON TURNOVER, and what it demands of capital ---")
        print("  net $%+.2f on $%.2f spent = %.4f%% of turnover"
              % (tp, turnover, 100.0 * tp / turnover))
        print("  mean spend per window: $%.2f" % (turnover / len(settled)))
        for target in (480.0,):
            need_turn = target / (tp / turnover)
            print("  to net $%.0f/day at that rate: $%.0f of turnover a day,"
                  % (target, need_turn))
            print("     i.e. $%.0f per window across 96 BTC15m windows"
                  % (need_turn / 96.0))
    print("\nFills and REDEEM cashflows are OBSERVED. Order submissions,"
          " cancellations and\nintentions are NOT in this feed and are not"
          " claimed here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
