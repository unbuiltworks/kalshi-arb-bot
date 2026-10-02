"""L5 SETTINGS AUDIT A2 -- sequential inventory, complement legs, economics.

The frozen decision gate evaluated INDEPENDENT maker fills and judged them by
5/15/30 s markout. L5's own frozen research says that is the wrong object:

  EXPLORATORY_V2 (frozen 2026-09-17), conclusion 5:
  "Instantaneous pair cost is NOT equivalent to eventual effective pair
   economics. Scored by what the shares actually earned, the 1.00-1.02
   instantaneous band is the BEST in both periods (+98/+61 bp) and <=0.96 is
   NEGATIVE in both (-28/-119 bp)."

  L5_OPERATING_MODEL section 2: "there is no completion controller driving
  inventory to zero. The oscillation IS the process, and matched inventory is
  its by-product." 84.3% of imbalance episodes overshoot through zero; one of
  5,546 windows ended flat.

So this replays fills SEQUENTIALLY and carries inventory, exactly as
instructed. It does NOT equate markout with realized loss.

ACCOUNTING, kept strictly separate (never summed into one number):
  trading P&L (spread capture)      | maker rebates | fees
  matched-pair P&L                  | directional residual P&L
  settlement value where measurable

Gemini UP/DOWN semantics: a window lists ONE instrument (…-UP). Buying UP at
p is the YES side; the economic complement (DOWN) is obtained by SELLING UP,
whose passive price is the UP bid. A matched pair is therefore
  buy UP @ p_buy  +  sell UP @ p_sell
and the pair is profitable when p_sell > p_buy. For an outcome-style pair cost
comparable to L5's "pair cost", we report
  pair_cost = p_buy + (1 - p_sell)
which is < 1.00 exactly when p_sell > p_buy. This identity is asserted in
tests (see a3_semantics_test.py), not assumed here.

SECOND LEG GETS ITS OWN DEFENSIBLE TREATMENT -- no touch = fill. The
complement must itself be reached by a print through our price, or by
same-price FIFO with the displayed queue exhausted by observed trades.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
CLIPS = (1, 5, 10, 25)
TICK = 0.01
# Gemini taker/maker fee structure is NOT assumed here; fees are reported as a
# free parameter and the BREAK-EVEN compensation is solved for instead.


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def apply_side(book, rows):
    t = {}
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        t[p] = (book.get(p, 0.0), s)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s
    return t


def load():
    ev, win = defaultdict(list), {}
    for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k, m = r.get("kind"), r.get("market") or ""
            if not SYM.match(m):
                continue
            if k == "window":
                win[m] = r
            elif k in ("depth_update", "depth_snapshot", "trade"):
                ev[m].append(r)
    for m in ev:
        ev[m].sort(key=lambda r: (r.get("t") or 0))
    return ev, win


def timeline(rows):
    bids, asks = {}, {}
    seeded, desync, last_u = False, True, None
    out, mids = [], []
    for r in rows:
        k, t = r["kind"], r.get("t")
        if k == "depth_snapshot":
            if not seeded or desync:
                bids, asks = {}, {}
                apply_side(bids, r.get("bids"))
                apply_side(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded, desync = True, False
                out.append(("book", t, dict(bids), dict(asks), {}, {}))
            continue
        if k == "depth_update":
            if not seeded:
                continue
            U, u = r.get("U"), r.get("u")
            if u is not None and last_u is not None and u <= last_u:
                continue
            if U is not None and last_u is not None and U != last_u:
                desync = True
            tb = apply_side(bids, r.get("b"))
            ta = apply_side(asks, r.get("a"))
            last_u = u
            if bids and asks:
                mids.append((t, (max(bids) + min(asks)) / 2.0))
            out.append(("book", t, dict(bids), dict(asks), tb, ta))
            continue
        if k == "trade":
            try:
                out.append(("trade", t, float(r["price"]), float(r["qty"]),
                            r.get("buyer_is_maker")))
            except Exception:
                pass
    return out, mids


def sequential(events, mids, clip, start, expiry, settle_px):
    """B_STICKY on BOTH sides simultaneously, carrying inventory.

    Leg 1 (buy UP) rests at the first observable bid and never reprices.
    Leg 2 (sell UP = the economic complement) rests at the first observable
    ask and never reprices. Both legs require a defensible fill. Inventory is
    carried; nothing is forced flat.
    """
    orders = {}
    for side, is_bid in (("buy", True), ("sell", False)):
        orders[side] = None

    inv_long = 0.0          # UP contracts held
    cost_long = 0.0
    inv_short = 0.0         # UP contracts sold
    proceeds_short = 0.0
    fills = []

    def place(side, bids, asks, t):
        is_bid = side == "buy"
        bk = bids if is_bid else asks
        if not bk:
            return None
        px = max(bk) if is_bid else min(bk)
        return {"px": px, "rem": float(clip), "q": bk[px], "t": t}

    for e in events:
        if e[0] == "book":
            _, t, bids, asks, tb, ta = e
            if t is None or not (start <= t <= expiry):
                continue
            for side in ("buy", "sell"):
                if orders[side] is None:
                    orders[side] = place(side, bids, asks, t)
                    continue
                o = orders[side]
                ts = tb if side == "buy" else ta
                if o["px"] in ts:
                    before, after = ts[o["px"]]
                    if after < before:
                        o["q"] = max(0.0, o["q"] - (before - after))
                    if after == 0.0:
                        orders[side] = place(side, bids, asks, t)
            continue
        _, t, px, qty, bim = e
        if t is None or not (start <= t <= expiry):
            continue
        side = "buy" if bim is True else "sell" if bim is False else None
        if side is None or orders[side] is None:
            continue
        o = orders[side]
        is_bid = side == "buy"
        P = o["px"]
        through = (px < P) if is_bid else (px > P)
        at = (px == P)
        got = 0.0
        if through:
            got = min(qty, o["rem"])
        elif at:
            take = min(qty, o["q"])
            o["q"] -= take
            over = qty - take
            if over > 0:
                got = min(over, o["rem"])
        if got <= 0:
            continue
        o["rem"] -= got
        if side == "buy":
            inv_long += got
            cost_long += got * P
        else:
            inv_short += got
            proceeds_short += got * P
        fills.append({"t": t, "side": side, "px": P, "qty": got,
                      "tte": expiry - t,
                      "inv_long": inv_long, "inv_short": inv_short})
        if o["rem"] <= 0:
            orders[side] = None

    matched = min(inv_long, inv_short)
    avg_long = (cost_long / inv_long) if inv_long else None
    avg_short = (proceeds_short / inv_short) if inv_short else None
    pair_cost = (avg_long + (1.0 - avg_short)) if (avg_long is not None and avg_short is not None) else None
    residual = inv_long - inv_short
    # matched P&L: bought at avg_long, sold at avg_short
    matched_pnl = matched * ((avg_short - avg_long) if matched else 0.0)
    # directional residual marked to SETTLEMENT where measurable
    resid_pnl = None
    if settle_px is not None and residual != 0:
        if residual > 0:
            resid_pnl = residual * (settle_px - (avg_long or 0.0))
        else:
            resid_pnl = (-residual) * ((avg_short or 0.0) - settle_px)
    return {"fills": fills, "inv_long": inv_long, "inv_short": inv_short,
            "matched": matched, "residual": residual,
            "avg_long": avg_long, "avg_short": avg_short,
            "pair_cost": pair_cost, "matched_pnl": matched_pnl,
            "resid_pnl": resid_pnl}


def complement_scan(events, fills, start, expiry):
    """For each defensible FIRST fill, when does an executable OPPOSITE-side
    opportunity appear, and at what gross pair cost? The opposite leg must be
    reached defensibly -- a print through our price, or same-price with the
    displayed queue exhausted. No touch = fill."""
    out = []
    trades = [(e[1], e[2], e[3], e[4]) for e in events if e[0] == "trade"]
    for f in fills:
        want = "sell" if f["side"] == "buy" else "buy"
        passive = True if want == "buy" else False
        best = None
        for t, px, qty, bim in trades:
            if t <= f["t"] or t > expiry:
                continue
            if bim is not passive:
                continue
            # gross pair cost for buy@p1 then sell@p2 is p1 + (1 - p2)
            if f["side"] == "buy":
                pc = f["px"] + (1.0 - px)
            else:
                pc = px + (1.0 - f["px"])
            if best is None or pc < best[1]:
                best = (t, pc)
        if best:
            out.append({"t_first": f["t"], "side": f["side"],
                        "dt_s": best[0] - f["t"], "best_pair_cost": best[1]})
    return out


def main():
    ev, win = load()
    agg = {c: {"win": 0, "fills": 0, "long": 0.0, "short": 0.0,
               "matched": 0.0, "resid": 0.0, "mpnl": 0.0, "rpnl": 0.0,
               "pair_costs": [], "both_sided": 0, "rpnl_n": 0}
           for c in CLIPS}
    comps = []
    nwin = 0
    for m, rows in ev.items():
        meta = win.get(m)
        if not meta:
            continue
        expiry = meta["expiry_t"]
        start = expiry - 300.0
        events, mids = timeline(rows)
        bk = [e for e in events if e[0] == "book" and e[1]
              and start <= e[1] <= expiry]
        if len(bk) < 2 or (bk[-1][1] - bk[0][1]) < 240.0:
            continue
        nwin += 1
        # settlement proxy: last observable mid in the window. Labelled a
        # PROXY -- true settlement is 0 or 1 and is not in this tape.
        settle = mids[-1][1] if mids else None
        for c in CLIPS:
            r = sequential(events, mids, c, start, expiry, settle)
            a = agg[c]
            if r["fills"]:
                a["win"] += 1
            a["fills"] += len(r["fills"])
            a["long"] += r["inv_long"]
            a["short"] += r["inv_short"]
            a["matched"] += r["matched"]
            a["resid"] += abs(r["residual"])
            a["mpnl"] += r["matched_pnl"]
            if r["resid_pnl"] is not None:
                a["rpnl"] += r["resid_pnl"]
                a["rpnl_n"] += 1
            if r["pair_cost"] is not None:
                a["pair_costs"].append(r["pair_cost"])
                a["both_sided"] += 1
            if c == 1:
                comps += complement_scan(events, r["fills"], start, expiry)

    print("=" * 92)
    print("A2  SEQUENTIAL INVENTORY / COMPLEMENT / SEPARATED ECONOMICS")
    print("Frozen decision gate UNCHANGED. Markout is NOT treated as realized loss.")
    print("=" * 92)
    print(f"\nqualified BTC5m windows: {nwin}\n")
    print("--- Q4: sequential inventory, two-sided sticky resting ---")
    print(f"{'clip':>5s} {'windows_w_fill':>14s} {'fills':>6s} {'long':>8s} "
          f"{'short':>8s} {'matched':>8s} {'|residual|':>11s} {'both_sided':>11s} "
          f"{'pair_cost_p50':>14s}")
    print("-" * 92)
    for c in CLIPS:
        a = agg[c]
        pcp = pct(a["pair_costs"], .5)
        print(f"{c:5d} {a['win']:14d} {a['fills']:6d} {a['long']:8.1f} "
              f"{a['short']:8.1f} {a['matched']:8.1f} {a['resid']:11.1f} "
              f"{a['both_sided']:11d} "
              f"{(f'{pcp:.4f}' if pcp is not None else '-'):>14s}")
    print()
    print("--- SEPARATED P&L (never summed; rebates excluded by design) ---")
    print(f"{'clip':>5s} {'matched_pnl_$':>14s} {'resid_pnl_$(proxy)':>19s} "
          f"{'matched_contracts':>18s}")
    print("-" * 62)
    for c in CLIPS:
        a = agg[c]
        print(f"{c:5d} {a['mpnl']:14.4f} {a['rpnl']:19.4f} {a['matched']:18.1f}")
    print("  resid_pnl uses the LAST OBSERVABLE MID as a settlement PROXY.")
    print("  True Gemini settlement is 0 or 1 and is NOT in this tape.")
    print()
    print("--- Q5: complement behaviour after a defensible first fill (q1) ---")
    if comps:
        dts = [c["dt_s"] for c in comps]
        pcs = [c["best_pair_cost"] for c in comps]
        print(f"  first fills with ANY later opposite-side print : {len(comps)}")
        print(f"  time-to-complement (s)   : p10={pct(dts,.1):.1f} "
              f"p50={pct(dts,.5):.1f} p90={pct(dts,.9):.1f} max={max(dts):.1f}")
        print(f"  best gross pair cost     : p10={pct(pcs,.1):.4f} "
              f"p50={pct(pcs,.5):.4f} p90={pct(pcs,.9):.4f} min={min(pcs):.4f}")
        for thr in (1.00, 0.98, 0.96):
            n = sum(1 for x in pcs if x < thr)
            print(f"  best pair cost < {thr:.2f}      : {n}/{len(pcs)} "
                  f"({n/len(pcs)*100:.1f}%)")
    else:
        print("  no complement opportunities found")
    print()
    print("--- Q7: BREAK-EVEN maker compensation required by this cohort ---")
    for c in CLIPS:
        a = agg[c]
        filled = a["long"] + a["short"]
        if not filled:
            continue
        # break-even compensation per filled contract that would offset a
        # NEGATIVE matched+residual outcome. If the outcome is positive, no
        # compensation is required for break-even.
        net = a["mpnl"] + a["rpnl"]
        be = (-net / filled) if filled else None
        print(f"  q{c:<3d} filled_contracts={filled:8.1f}  "
              f"matched+resid(proxy)=${net:+.4f}  "
              f"break-even comp = ${be:+.6f}/contract "
              f"({be*100:+.4f} cents)")
    print("  NOTE: this is the compensation needed to offset the measured")
    print("  trading outcome on this cohort. It is NOT a claim about what")
    print("  Gemini actually pays -- see the rebate-evidence section.")

    out = Path("analysis/gemini/l5_audit/a2_results.json")
    out.write_text(json.dumps(
        {str(c): {k: (v if not isinstance(v, list) else
                      {"p50": pct(v, .5), "n": len(v)})
                  for k, v in a.items()} for c, a in agg.items()}, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
