"""HOW should frozen R2 be executed? Five preregistered policies, existing tapes.

R2 IS NOT TOUCHED. Qualification is imported from the frozen rule; this file
only asks what to DO once R2 has already said yes.

THE POLICIES, and nothing else is searched:

  A   immediate taker          buy the favoured side at the executable ask,
                               exactly as the live runner does
  B   maker at best bid        rest q1 on the favoured side at the
                               contemporaneous best bid, leave it while R2
                               remains valid
  C   improve by one tick      rest q1 one tick above best bid, never crossing
                               the ask
  D5  maker-first 5 s          B for 5 s, then cancel and cross ONLY if the
                               side is still R2-qualified and the taker price
                               has not exceeded the original economic ceiling
  D15 maker-first 15 s         the same at 15 s

NO touch=fill. A resting order is filled only on the proven Gemini
methodology, and the tiers are reported SEPARATELY rather than blended:

  THROUGH    a print strictly through our price on our side. Price-time
             priority cannot leave us behind it, so this is defensible
             regardless of queue position.
  DEPLETION  cumulative same-price volume since placement EXCEEDS the
             displayed size that was ahead of us at placement. Defensible
             because the queue we joined behind has demonstrably cleared.
  OPTIMISTIC any same-price print, i.e. front of queue, zero wait. An
             explicit UPPER BOUND and never the headline.

If the queue ahead cannot be established at placement, the opportunity is
reported as QUEUE_UNKNOWN and BOUNDED rather than filled.

SIDE MAPPING, stated because it is the easiest thing to get backwards.
Buying the favoured outcome:
  favoured UP   -> our resting order is a BID on the UP ladder at p.
                   It fills against SELLERS of UP. Through = print < p.
  favoured DOWN -> buying DOWN is selling UP, so our resting order is an ASK
                   on the UP ladder at 1-p. It fills against BUYERS of UP.
                   Through = print > that ask.

FEES AND REBATES ARE SEPARATE LINES, never netted into a price.
  taker fee  = ceil(0.07 * C * P * (1-P), cent)        -- charged, modelled
  maker fee  = ceil(0.0175 * C * P * (1-P), cent)      -- charged, modelled
  maker rebate = floor(0.021 * C * P * (1-P), cent), eligible only for
                 0.20 <= P <= 0.80, max 0.525c/contract
  NO rebate is called realized. Rebates are reported in their own column and
  the net EV columns are given BOTH with and without them.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TICK = D("0.01")

RULE: Dict[str, Any] = {"__file__": "analysis/gemini/info_edge/r2_frozen.py"}
exec(Path("analysis/gemini/info_edge/r2_frozen.py").read_text()
     .split("def main")[0], RULE)


def taker_fee(p: D, c: D = D("1")) -> D:
    return (D("0.07") * c * p * (D("1") - p)).quantize(
        D("0.01"), rounding=ROUND_CEILING)


def maker_fee(p: D, c: D = D("1")) -> D:
    return (D("0.0175") * c * p * (D("1") - p)).quantize(
        D("0.01"), rounding=ROUND_CEILING)


def maker_rebate(p: D, c: D = D("1")) -> D:
    """Eligible band only, floor-rounded, capped. NEVER called realized."""
    if not (D("0.20") <= p <= D("0.80")):
        return D("0")
    raw = D("0.021") * c * p * (D("1") - p)
    cap = D("0.00525") * c
    return min(raw, cap).quantize(D("0.01"), rounding=ROUND_FLOOR)


def _apply(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def load():
    """Every settled BTC5m window with a strike, plus its full event tape."""
    settled, expiry, strike_at, cb = {}, {}, {}, []
    ev = defaultdict(list)
    for f in sorted(Path("data/gemini_infoedge").glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                settled[r.get("ticker")] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll"):
                if r.get("expiry"):
                    expiry[tk] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).timestamp()
                s = r.get("strike")
                if isinstance(s, dict) and s.get("availableAt"):
                    strike_at[tk] = datetime.fromisoformat(
                        s["availableAt"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot", "p_trade"):
                ev[tk].append(r)
    cb.sort()
    return settled, expiry, strike_at, cb, ev


def r2_events():
    """The frozen rule's own R2 hits on the PROSPECTIVE cohort, de-duplicated,
    with the FIRST qualifying instant per (window, side, level)."""
    inst, elig = RULE["extract"]("PROSPECTIVE")
    hits = RULE["r2"](inst)
    return hits, elig


def replay(tk: str, t0: float, E: float, side: str, evs: List[Dict[str, Any]]):
    """Book + trade timeline from t0 to expiry, as (t, bids, asks, trades)."""
    bids, asks = {}, {}
    seeded, last_u = False, None
    frames = []
    trades = []
    for r in sorted(evs, key=lambda x: x["t_ns"]):
        t = r["t_ns"] / 1e9
        if r["kind"] == "p_trade":
            if t >= t0:
                try:
                    trades.append((t, float(r["price"]), float(r["qty"]),
                                   bool(r.get("buyer_is_maker"))))
                except Exception:
                    pass
            continue
        if r["kind"] == "p_snapshot":
            nb, na = {}, {}
            _apply(nb, r.get("bids"))
            _apply(na, r.get("asks"))
            bids, asks = nb, na
            last_u = r.get("lastUpdateId")
            seeded = True
        else:
            if not seeded:
                continue
            u = r.get("u")
            if u is not None and last_u is not None and u <= last_u:
                continue
            _apply(bids, r.get("b"))
            _apply(asks, r.get("a"))
            last_u = u
        if t >= t0:
            frames.append((t, dict(bids), dict(asks)))
    return frames, trades


def first_executable(frames, side, t0):
    """TIME TO FIRST EXECUTABLE on the favoured side, and at what price.

    Answers the simplest question in the brief: does NOT_EXECUTABLE mean
    'never available' or merely 'not at the instant z crossed 1.0'?
    """
    for (t, b, a) in frames:
        lvl, price, size, depth = RULE["executable"](side, b, a)
        if price is not None and 0 < price < 1:
            return {"t_s": round(t - t0, 2), "price": round(price, 2),
                    "size": size, "depth": depth}
    return None


def maker_fill(frames, trades, side, our_price: D, t0: float,
               deadline: Optional[float]):
    """Defensible fill for ONE resting q1 order. Tiers reported separately.

    `our_price` is the price of the favoured outcome we are bidding. On the UP
    ladder that is a BID at `our_price` when side==UP, and an ASK at
    1-our_price when side==DOWN.
    """
    up_px = float(our_price) if side == "UP" else float(D("1") - our_price)
    # queue ahead at placement: the displayed size at our own level on our own
    # side of the UP ladder.
    q_ahead = None
    for (t, b, a) in frames:
        book = b if side == "UP" else a
        q_ahead = book.get(up_px, 0.0)
        break
    if q_ahead is None:
        return {"tier": "QUEUE_UNKNOWN", "why": "no book frame at placement"}

    same = 0.0
    out = {"q_ahead_at_placement": q_ahead, "through_qty": 0.0,
           "same_price_qty": 0.0}
    for (t, px, qty, buyer_is_maker) in trades:
        if deadline is not None and t > deadline:
            break
        if t > frames[-1][0] if frames else False:
            pass
        through = (px < up_px) if side == "UP" else (px > up_px)
        at = abs(px - up_px) < 1e-9
        if through:
            out["through_qty"] += qty
            out.update(tier="THROUGH", fill_t_s=round(t - t0, 2),
                       fill_price=float(our_price))
            return out
        if at:
            same += qty
            out["same_price_qty"] = same
            if same > q_ahead + 1.0:          # our 1 contract must also clear
                out.update(tier="DEPLETION", fill_t_s=round(t - t0, 2),
                           fill_price=float(our_price))
                return out
    if out["same_price_qty"] > 0:
        out.update(tier="OPTIMISTIC_ONLY",
                   why=("same-price prints occurred but never exceeded the "
                        "%.1f contracts displayed ahead of us" % q_ahead))
    else:
        out.update(tier="NO_FILL", why="no print at or through our price")
    return out


def evaluate():
    settled, expiry, strike_at, cb, ev = load()
    hits, elig = r2_events()
    rows = []
    for h in hits:
        tk, side = h["ticker"], h["side"]
        E = expiry.get(tk)
        s = settled.get(tk)
        if E is None or s is None:
            continue
        t0 = h["t"]
        frames, trades = replay(tk, t0, E, side, ev.get(tk, []))
        if not frames:
            continue
        correct = h["correct"]
        ask = D(str(round(h["price"], 2)))        # the executable ask at t0
        # contemporaneous best bid on the FAVOURED outcome
        _, b0, a0 = frames[0]
        if side == "UP":
            bid = max(b0) if b0 else None
            bid_px = D(str(round(bid, 2))) if bid is not None else None
            opp = ask
        else:
            # favoured DOWN: its bid is 1 - best UP ask
            ua = min(a0) if a0 else None
            bid_px = (D("1") - D(str(round(ua, 2)))) if ua is not None else None
            opp = ask
        rec = {"ticker": tk, "side": side, "correct": correct,
               "taker_price": ask, "maker_bid": bid_px,
               "t0": t0, "tte_at_q": round(E - t0, 1),
               "size_at_touch": h.get("size"), "depth": h.get("depth"),
               "first_exec": first_executable(frames, side, t0)}
        # ---- A: immediate taker
        rec["A"] = {"filled": True, "px": ask, "fee": taker_fee(ask),
                    "rebate": D("0"), "tier": "TAKER", "fill_t_s": 0.0}
        # ---- B: maker at best bid
        for name, px in (("B", bid_px),
                         ("C", (bid_px + TICK) if bid_px is not None else None)):
            if px is None or not (D("0") < px < D("1")):
                rec[name] = {"filled": False, "tier": "NO_VALID_PRICE"}
                continue
            if name == "C" and px >= ask:
                rec[name] = {"filled": False, "tier": "WOULD_CROSS_ASK"}
                continue
            f = maker_fill(frames, trades, side, px, t0, None)
            ok = f["tier"] in ("THROUGH", "DEPLETION")
            rec[name] = dict(f, filled=ok, px=px,
                             fee=maker_fee(px) if ok else D("0"),
                             rebate=maker_rebate(px) if ok else D("0"))
        # ---- D: maker-first then taker fallback
        for name, dur in (("D5", 5.0), ("D15", 15.0)):
            px = bid_px
            if px is None:
                rec[name] = {"filled": False, "tier": "NO_VALID_PRICE"}
                continue
            f = maker_fill(frames, trades, side, px, t0, t0 + dur)
            if f["tier"] in ("THROUGH", "DEPLETION"):
                rec[name] = dict(f, filled=True, px=px, fee=maker_fee(px),
                                 rebate=maker_rebate(px), leg="MAKER")
                continue
            # fallback: still qualified, and the taker price must not exceed
            # the ORIGINAL economic ceiling (the ask we would have paid at t0)
            later = [fr for fr in frames if fr[0] >= t0 + dur]
            if not later:
                rec[name] = {"filled": False, "tier": "NO_FRAME_AT_FALLBACK",
                             "leg": "MAKER_THEN_NONE"}
                continue
            t1, b1, a1 = later[0]
            _, p1, sz1, dep1 = RULE["executable"](side, b1, a1)
            if p1 is None:
                rec[name] = {"filled": False, "tier": "FALLBACK_NOT_EXECUTABLE",
                             "leg": "MAKER_THEN_NONE"}
                continue
            p1d = D(str(round(p1, 2)))
            if p1d > ask:
                rec[name] = {"filled": False, "tier": "FALLBACK_ABOVE_CEILING",
                             "leg": "MAKER_THEN_NONE",
                             "fallback_price": p1d, "ceiling": ask}
                continue
            rec[name] = {"filled": True, "tier": "TAKER_FALLBACK",
                         "leg": "TAKER", "px": p1d, "fee": taker_fee(p1d),
                         "rebate": D("0"),
                         "fill_t_s": round(t1 - t0, 2)}
        rows.append(rec)
    return rows, elig


def summarise(rows, elig):
    POL = ("A", "B", "C", "D5", "D15")
    print("=" * 108)
    print("R2 EXECUTION POLICY COMPARISON -- existing prospective tapes, R2 FROZEN")
    print("=" * 108)
    wins = {r["ticker"] for r in rows}
    print("R2 opportunities: %d across %d independent windows "
          "(eligible windows %d)" % (len(rows), len(wins), len(elig)))
    print("NO touch=fill. THROUGH and DEPLETION are defensible; OPTIMISTIC is")
    print("reported as a bound and never counted as a fill.\n")

    print("--- TIME TO FIRST EXECUTABLE on the favoured side ---")
    print("  Does NOT_EXECUTABLE mean 'never' or 'not at the instant z>=1'?")
    fe = [r["first_exec"] for r in rows]
    have = [x for x in fe if x]
    print("  R2 events where the favoured side is EVER executable before "
          "expiry: %d/%d" % (len(have), len(rows)))
    if have:
        ts = sorted(x["t_s"] for x in have)
        ps = sorted(x["price"] for x in have)
        pq = lambda v, a: v[min(int(a * (len(v) - 1)), len(v) - 1)]
        print("  time to first executable  p10 %.1fs  p50 %.1fs  p90 %.1fs"
              % (pq(ts, .1), pq(ts, .5), pq(ts, .9)))
        print("  price at first executable p10 %.2f   p50 %.2f   p90 %.2f"
              % (pq(ps, .1), pq(ps, .5), pq(ps, .9)))
        print("  executable AT the qualifying instant (t=0): %d"
              % sum(1 for x in have if x["t_s"] <= 0.001))
    print()

    print("--- POLICY COMPARISON ---")
    hdr = ("pol", "opps", "fills", "fill%", "avg_px", "corr|fill",
           "gross", "fees", "rebate", "EV/opp", "EV/fill", "EV/win", "t_fill")
    print("  %-4s %5s %6s %6s %7s %9s %8s %7s %7s %8s %8s %8s %7s" % hdr)
    out = {}
    for p in POL:
        fl = [r for r in rows if r[p].get("filled")]
        n = len(rows)
        nf = len(fl)
        if nf == 0:
            print("  %-4s %5d %6d %6s" % (p, n, 0, "--"))
            out[p] = {"opps": n, "fills": 0}
            continue
        px = sum(r[p]["px"] for r in fl) / nf
        fee = sum(r[p]["fee"] for r in fl)
        reb = sum(r[p]["rebate"] for r in fl)
        corr = sum(1 for r in fl if r["correct"])
        gross = sum((D("1") if r["correct"] else D("0")) - r[p]["px"]
                    for r in fl)
        net = gross - fee
        tf = [r[p].get("fill_t_s", 0.0) for r in fl]
        w = len({r["ticker"] for r in fl})
        out[p] = {"opps": n, "fills": nf, "fill_rate": nf / n,
                  "avg_px": float(px), "corr_given_fill": corr / nf,
                  "gross": float(gross), "fees": float(fee),
                  "rebate": float(reb), "net": float(net),
                  "ev_per_opp": float(net / n), "ev_per_fill": float(net / nf),
                  "windows_filled": w,
                  "ev_per_window": float(net / len(
                      {r["ticker"] for r in rows})),
                  "t_fill_p50": sorted(tf)[len(tf) // 2]}
        print("  %-4s %5d %6d %5.1f%% %7.3f %8.1f%% %+8.3f %7.3f %7.3f "
              "%+8.4f %+8.4f %+8.4f %7.1f"
              % (p, n, nf, 100 * nf / n, float(px), 100 * corr / nf,
                 float(gross), float(fee), float(reb),
                 float(net / n), float(net / nf),
                 float(net / len({r["ticker"] for r in rows})),
                 sorted(tf)[len(tf) // 2]))
    print()
    print("  EV columns EXCLUDE rebates (they are not realized). With rebates")
    print("  added, net per opportunity would be:")
    for p in POL:
        o = out[p]
        if o.get("fills"):
            print("    %-4s %+.4f  (vs %+.4f without)"
                  % (p, (o["net"] + o["rebate"]) / o["opps"], o["ev_per_opp"]))
    print()

    print("--- WHY THE POLICIES DIFFER: price, or adverse selection? ---")
    base = {r["ticker"] + r["side"]: r for r in rows}
    for p in ("B", "C", "D5", "D15"):
        fl = [r for r in rows if r[p].get("filled")]
        nf = [r for r in rows if not r[p].get("filled")]
        if not fl:
            continue
        c_fl = sum(1 for r in fl if r["correct"]) / len(fl)
        c_nf = (sum(1 for r in nf if r["correct"]) / len(nf)) if nf else None
        c_all = sum(1 for r in rows if r["correct"]) / len(rows)
        px_saved = (sum(r["taker_price"] - r[p]["px"] for r in fl) / len(fl))
        print("  %-4s filled %3d  correct|filled %5.1f%%   "
              "correct|NOT filled %s   correct|all %5.1f%%"
              % (p, len(fl), 100 * c_fl,
                 ("%5.1f%%" % (100 * c_nf)) if c_nf is not None else "   n/a",
                 100 * c_all))
        print("       price advantage vs taker: %+.3f/contract   "
              "selection penalty: %+.1f pp"
              % (float(px_saved), 100 * (c_fl - c_all)))
    print()

    print("--- MISSED PROFITABLE OUTCOMES (not filled, would have won) ---")
    for p in POL:
        miss = [r for r in rows if not r[p].get("filled") and r["correct"]]
        print("  %-4s %3d of %d unfilled opportunities settled CORRECT"
              % (p, len(miss), sum(1 for r in rows if not r[p].get("filled"))))
    print()

    print("--- FILL-TIER BREAKDOWN (defensible vs bound) ---")
    for p in ("B", "C", "D5", "D15"):
        t = defaultdict(int)
        for r in rows:
            t[r[p].get("tier", "?")] += 1
        print("  %-4s %s" % (p, dict(sorted(t.items(),
                                            key=lambda x: -x[1]))))
    print()

    print("--- CAPACITY at the touch (contracts displayed) ---")
    sz = sorted((r["size_at_touch"] or 0) for r in rows)
    pq = lambda v, a: v[min(int(a * (len(v) - 1)), len(v) - 1)]
    print("  size p10 %.1f  p50 %.1f  p90 %.1f" % (pq(sz, .1), pq(sz, .5),
                                                   pq(sz, .9)))

    Path("analysis/gemini/info_edge/r2_execution_policies.json").write_text(
        json.dumps({"policies": out,
                    "n_opportunities": len(rows),
                    "n_windows": len({r["ticker"] for r in rows}),
                    "rows": rows}, indent=2, default=str))
    print("\nwrote analysis/gemini/info_edge/r2_execution_policies.json")


if __name__ == "__main__":
    rows, elig = evaluate()
    summarise(rows, elig)
