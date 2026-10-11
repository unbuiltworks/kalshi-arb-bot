"""DISCOVERY SCAN on the FROZEN DEV cohort only.

Objective is NOT profitability. It is: does any plausible trade exist at all?
A state qualifies only if the side the BTC state predicted was ACTUALLY
PURCHASABLE with enough displayed depth to target ~$5 of winning profit.

COHORT DISCIPLINE
  Reads only tickers listed in DEV_COHORT_FROZEN.json (expiry <= 01:50:00Z
  2026-10-03). Windows expiring after that boundary are PROSPECTIVE and are
  never touched here. The sealed prospective set is not read.

n = 48 windows. This is DISCOVERY, not evidence. Any rule that emerges is a
hypothesis to be frozen and tested forward, never a result.

EXECUTION
  Entry is a TAKER lift of the executable price for the predicted side
  (side-symmetric: predicted UP -> best UP ask; predicted DOWN -> 1 - best UP
  bid). Size = min(depth, 5/(1-price)). Fees ceil-to-cent; a taker entry earns
  NO rebate, so any apparent advantage cannot come from rebates unless a maker
  leg is involved -- which the attribution below makes explicit.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import sys

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_core import taker_fee, maker_fee, rebate, ONE

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TTES = (120, 90, 60, 45, 30, 20, 15, 10, 5)
TARGET = 5.0


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def ap(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def load_dev():
    dev = json.loads(Path("analysis/gemini/info_edge/DEV_COHORT_FROZEN.json").read_text())
    keep = set(dev["tickers"])
    boundary = datetime.fromisoformat(dev["frozen_at_utc"].replace("Z", "+00:00")).timestamp()
    settled, expiry, cb = {}, {}, []
    ev = defaultdict(list)
    for f in sorted(Path("data/gemini_infoedge").glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if r.get("ticker") in keep:
                    settled[r["ticker"]] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m or m.group(1) not in keep:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll") and r.get("expiry"):
                expiry[tk] = datetime.fromisoformat(
                    r["expiry"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot"):
                ev[tk].append(r)
    cb.sort()
    return keep, settled, expiry, ev, cb, boundary


def ladder(settled, expiry, ev, cb):
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    def cb_ret(t, h):
        a, b = cb_at(t - h), cb_at(t)
        return ((b - a) / a) if (a and b) else None

    rows = []
    for tk, s in settled.items():
        E = expiry.get(tk)
        if E is None:
            continue
        try:
            strike = float(s["strike"])
        except Exception:
            continue
        up_won = s["resolution_side"] == "yes"
        evs = sorted(ev.get(tk, []), key=lambda r: r["t_ns"])
        bids, asks, seeded, last_u = {}, {}, False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        state = {}
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                bids, asks = {}, {}
                ap(bids, r.get("bids"))
                ap(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                ap(bids, r.get("b"))
                ap(asks, r.get("a"))
                last_u = u
            while wi < len(want) and (E - t) <= want[wi]:
                state[want[wi]] = (dict(bids), dict(asks), t)
                wi += 1
        for tte in TTES:
            st = state.get(tte)
            if st is None:
                continue
            b, a, t = st
            c = cb_at(t)
            if c is None:
                continue
            bid = max(b) if b else None
            ask = min(a) if a else None
            dist = c - strike
            side = "UP" if dist > 0 else "DOWN"
            if side == "UP":
                px, sz = ask, (a[ask] if ask is not None else None)
            else:
                px = (1 - bid) if bid is not None else None
                sz = b[bid] if bid is not None else None
            correct = up_won if side == "UP" else (not up_won)
            # realized vol of coinbase over trailing 300s
            j0 = bisect.bisect_left(cbt, t - 300.0)
            j1 = bisect.bisect_right(cbt, t)
            seg = [x[1] for x in cb[j0:j1]]
            sig = None
            if len(seg) > 5:
                d = [seg[i + 1] - seg[i] for i in range(len(seg) - 1)]
                sig = math.sqrt(sum(x * x for x in d) / len(d)) * math.sqrt(len(d) / 300.0)
            rows.append({
                "ticker": tk, "tte": tte, "t": t, "strike": strike,
                "coinbase": c, "dist": round(dist, 2),
                "dist_norm": (round(dist / (sig * math.sqrt(tte)), 3)
                              if (sig and sig > 0 and tte > 0) else None),
                "sigma": sig,
                "ret_5s": cb_ret(t, 5), "ret_30s": cb_ret(t, 30),
                "ret_60s": cb_ret(t, 60),
                "side": side, "price": px, "size": sz,
                "bid": bid, "ask": ask,
                "available": px is not None,
                "qty_for_5": (TARGET / (1 - px) if (px is not None and px < 1) else None),
                "correct": correct, "up_won": up_won,
            })
    return rows


def fmtp(av):
    v = pct([x["price"] for x in av], .5) if av else None
    return "-" if v is None else f"{v:.3f}"


def main():
    keep, settled, expiry, ev, cb, boundary = load_dev()
    rows = ladder(settled, expiry, ev, cb)
    nw = len({r["ticker"] for r in rows})
    print("=" * 84)
    print("DISCOVERY SCAN -- FROZEN DEV COHORT ONLY (prospective set untouched)")
    print("=" * 84)
    print(f"DEV windows with ladder states: {nw}   ladder observations: {len(rows)}")
    print("n is small. This is DISCOVERY, not evidence.\n")

    print("--- STEP 1: was the PREDICTED side purchasable at all? ---")
    print(f"  {'TTE':>5s} {'obs':>5s} {'avail':>6s} {'avail%':>8s} {'px_p50':>8s} "
          f"{'size_p50':>9s} {'qty_for_$5_p50':>14s} {'DEPTH_OK':>9s}")
    cands = []
    for tte in TTES:
        g = [r for r in rows if r["tte"] == tte]
        if not g:
            continue
        av = [r for r in g if r["available"]]
        ok = [r for r in av if r["size"] is not None and r["qty_for_5"] is not None
              and r["size"] >= r["qty_for_5"]]
        cands += ok
        px50 = pct([x["price"] for x in av], .5) if av else None
        sz50 = pct([x["size"] for x in av if x["size"] is not None], .5) if av else None
        q50 = pct([x["qty_for_5"] for x in av if x["qty_for_5"] is not None], .5) if av else None
        f3 = lambda v: "-" if v is None else f"{v:.3f}"
        f1 = lambda v: "-" if v is None else f"{v:.1f}"
        print(f"  {tte:5d} {len(g):5d} {len(av):6d} {len(av)/len(g)*100:7.1f}% "
              f"{f3(px50):>8s} {f1(sz50):>9s} {f1(q50):>14s} {len(ok):9d}")
    print(f"\n  TOTAL CANDIDATE STATES (available AND depth for ~$5): {len(cands)}")
    print(f"  independent windows represented: {len({r['ticker'] for r in cands})}")
    Path("analysis/gemini/info_edge/ie_discovery_rows.json").write_text(
        json.dumps(rows, indent=1))
    Path("analysis/gemini/info_edge/ie_discovery_candidates.json").write_text(
        json.dumps(cands, indent=1))
    return rows, cands


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# STEP 2: replay A (one entry / hold), B (conditional flip), C (extend)
# --------------------------------------------------------------------------
def replay(rows, cands):
    by_w = defaultdict(list)
    for r in rows:
        by_w[r["ticker"]].append(r)
    for w in by_w:
        by_w[w].sort(key=lambda r: -r["tte"])

    def entry(c):
        """Taker lift of the predicted side, sized for ~$5 if correct."""
        px = c["price"]
        qty = min(c["size"], TARGET / (1 - px))
        fee = float(taker_fee(D(str(round(qty, 2))),
                              D(str(px if c["side"] == "UP" else 1 - px))))
        gross = qty * (1 - px) if c["correct"] else -qty * px
        return {"qty": qty, "px": px, "fee": fee, "gross": gross,
                "net": gross - fee, "correct": c["correct"],
                "tte": c["tte"], "ticker": c["ticker"], "side": c["side"],
                "dist": c["dist"], "rebate": 0.0}

    # ---- A: ONE ENTRY / HOLD. First candidate per window (earliest TTE). ----
    firsts = {}
    for c in sorted(cands, key=lambda r: -r["tte"]):
        firsts.setdefault(c["ticker"], c)
    A = [entry(c) for c in firsts.values()]

    # ---- B: conditional flip. Flip only if the BTC side genuinely reverses
    #         AND the opposite side is then independently purchasable +EV. ----
    B = []
    for tk, c in firsts.items():
        e = entry(c)
        flips = 0
        later = [r for r in by_w[tk] if r["tte"] < c["tte"]]
        for r in later:
            if r["side"] == c["side"]:
                continue                      # thesis intact -> do nothing
            if not r["available"] or r["size"] is None:
                continue                      # reversal, but nothing to trade
            q2 = min(r["size"], TARGET / (1 - r["price"]))
            if q2 <= 0:
                continue
            flips += 1
            f2 = float(taker_fee(D(str(round(q2, 2))),
                                 D(str(r["price"] if r["side"] == "UP" else 1 - r["price"]))))
            g2 = q2 * (1 - r["price"]) if r["correct"] else -q2 * r["price"]
            e = dict(e)
            e["gross"] += g2
            e["fee"] += f2
            e["net"] = e["gross"] - e["fee"]
            break                              # one flip max
        e["flips"] = flips
        B.append(e)

    # ---- C: extend. Additional SAME-side fills while still purchasable. ----
    C = []
    for tk, c in firsts.items():
        e = entry(c)
        adds = 0
        for r in [x for x in by_w[tk] if x["tte"] < c["tte"]]:
            if r["side"] != c["side"] or not r["available"] or r["size"] is None:
                continue
            q2 = min(r["size"], TARGET / (1 - r["price"]))
            if q2 <= 0:
                continue
            adds += 1
            f2 = float(taker_fee(D(str(round(q2, 2))),
                                 D(str(r["price"] if r["side"] == "UP" else 1 - r["price"]))))
            g2 = q2 * (1 - r["price"]) if r["correct"] else -q2 * r["price"]
            e = dict(e)
            e["gross"] += g2
            e["fee"] += f2
            e["net"] = e["gross"] - e["fee"]
        e["adds"] = adds
        C.append(e)

    def rep(name, S):
        if not S:
            print(f"\n{name}: no entries")
            return
        nets = [x["net"] for x in S]
        wins = [x for x in S if x["correct"]]
        print(f"\n--- {name} ---")
        print(f"  independent windows : {len({x['ticker'] for x in S})}")
        print(f"  entries             : {len(S)}")
        print(f"  win rate            : {len(wins)}/{len(S)} = {len(wins)/len(S)*100:.1f}%")
        print(f"  mean entry price    : {sum(x['px'] for x in S)/len(S):.3f}")
        print(f"  GROSS total         : ${sum(x['gross'] for x in S):+.2f}")
        print(f"  fees                : ${sum(x['fee'] for x in S):.2f}")
        print(f"  rebates             : ${sum(x['rebate'] for x in S):.2f}  (taker entry earns none)")
        print(f"  NET total           : ${sum(nets):+.2f}")
        print(f"  NET per window      : ${sum(nets)/len({x['ticker'] for x in S}):+.3f}")
        print(f"  MAX SINGLE LOSS     : ${min(nets):+.2f}")
        print(f"  worst 3             : {', '.join(f'${x:+.2f}' for x in sorted(nets)[:3])}")
        print(f"  total positive      : ${sum(x for x in nets if x>0):+.2f}")
        print(f"  total negative      : ${sum(x for x in nets if x<=0):+.2f}")
        return S

    print("\n" + "=" * 84)
    print("STEP 2: ARCHITECTURE REPLAY (frozen DEV only)")
    print("=" * 84)
    rep("A -- ONE ENTRY / HOLD", A)
    rep("B -- ONE ENTRY / CONDITIONAL FLIP", B)
    rep("C -- EXTEND (same side while purchasable)", C)
    print(f"\n  B flips actually taken : {sum(x.get('flips',0) for x in B)}")
    print(f"  C extensions taken     : {sum(x.get('adds',0) for x in C)}")

    # ---- ATTRIBUTION ----
    print("\n" + "=" * 84)
    print("ATTRIBUTION: prediction, entry price, or rebates?")
    print("=" * 84)
    wr = sum(1 for x in A if x["correct"]) / len(A) if A else 0
    mp = sum(x["px"] for x in A) / len(A) if A else 0
    print(f"  win rate of predicted side        : {wr*100:.1f}%")
    print(f"  mean executable price paid        : {mp*100:.1f}c")
    print(f"  implied break-even win rate       : {mp*100:.1f}%")
    print(f"  PREDICTION EDGE (win% - price)    : {(wr-mp)*100:+.1f} pts")
    print(f"  rebates earned                    : $0.00 -- entries are taker")
    print(f"     => any advantage CANNOT come from rebates here.")
    print(f"  entry-price advantage             : none by construction; we pay")
    print(f"     the executable ask/complement, never a mid or a maker price.")
    return A, B, C


if __name__ == "__main__":
    pass
