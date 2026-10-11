"""Mechanism diagnostic: is any winning-side offer plausibly STALE liquidity?

DEV-only (frozen 48 windows). Prospective untouched. No thresholds optimized,
no rule fitted, no live orders. This asks ONE question:

  When the BTC-state-favoured side became executable, had that resting level
  been posted BEFORE the BTC move that made it favoured -- and is that
  observable BEFORE a fill?

STALENESS, DEFINED STRUCTURALLY (not tuned)
  level_age = time since that exact price level first appeared on that side
              with non-zero size, tracked from the @depth diff stream.
  A level is PRE-MOVE at horizon h if level_age > h, i.e. it was already
  resting before the last h seconds of Coinbase movement. h is taken from the
  horizons named in the question: 1, 2, 5, 10 s. Nothing is optimized.

WHY THIS COULD MATTER
  The pooled result so far is that availability is adversely selected
  (P(correct|available) 0.705 vs 0.908 unconditional). If a stale-liquidity
  sub-population exists, it should show the OPPOSITE: offers that predate a
  fast favourable move, left behind by a maker who has not yet pulled them.
  If no such sub-population exists, the adverse selection is uniform and there
  is nothing to exploit.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
TTES = (120, 90, 60, 45, 30, 20, 15, 10, 5)
HORIZONS = (1, 2, 5, 10)


def ap(book, rows, seen, t):
    """Apply diff; maintain first-seen time per (price) on this side."""
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
            seen.pop(p, None)
        else:
            if p not in book:
                seen[p] = t          # level (re)appeared now
            book[p] = s


def main():
    dev = json.loads(Path("analysis/gemini/info_edge/DEV_COHORT_FROZEN.json").read_text())
    keep = set(dev["tickers"])
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
            elif k in ("p_depth", "p_snapshot", "p_trade"):
                ev[tk].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    inst = []
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
        bids, asks = {}, {}
        bseen, aseen = {}, {}
        seeded, last_u = False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        trades = []
        snaps = []     # (t, bids, asks, bseen, aseen)
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_trade":
                try:
                    trades.append((t, float(r["price"]), float(r["qty"]),
                                   bool(r.get("buyer_is_maker"))))
                except Exception:
                    pass
                continue
            if r["kind"] == "p_snapshot":
                # a snapshot re-asserts state; keep first-seen for levels that
                # persist, so a level is not falsely counted as "new"
                nb, na = {}, {}
                ap(nb, r.get("bids"), {}, t)
                ap(na, r.get("asks"), {}, t)
                for p in nb:
                    bseen.setdefault(p, t)
                for p in na:
                    aseen.setdefault(p, t)
                for p in list(bseen):
                    if p not in nb:
                        bseen.pop(p, None)
                for p in list(aseen):
                    if p not in na:
                        aseen.pop(p, None)
                bids, asks = nb, na
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                ap(bids, r.get("b"), bseen, t)
                ap(asks, r.get("a"), aseen, t)
                last_u = u
            snaps.append((t, dict(bids), dict(asks), dict(bseen), dict(aseen)))
            while wi < len(want) and (E - t) <= want[wi]:
                tte = want[wi]
                wi += 1
                c = cb_at(t)
                if c is None:
                    continue
                dist = c - strike
                side = "UP" if dist > 0 else "DOWN"
                if side == "UP":
                    lvl = min(asks) if asks else None
                    px = lvl
                    sz = asks.get(lvl) if lvl is not None else None
                    first = aseen.get(lvl) if lvl is not None else None
                else:
                    lvl = max(bids) if bids else None
                    px = (1 - lvl) if lvl is not None else None
                    sz = bids.get(lvl) if lvl is not None else None
                    first = bseen.get(lvl) if lvl is not None else None
                if px is None:
                    continue
                moves = {}
                for h in HORIZONS:
                    a0 = cb_at(t - h)
                    moves[h] = (c - a0) if a0 is not None else None
                inst.append({
                    "ticker": tk, "tte": tte, "t": t, "side": side,
                    "price": px, "size": sz, "level": lvl,
                    "level_first_seen": first,
                    "level_age": (t - first) if first else None,
                    "dist": round(dist, 2),
                    **{f"cb_move_{h}s": (round(moves[h], 2)
                                         if moves[h] is not None else None)
                       for h in HORIZONS},
                    "correct": (up_won if side == "UP" else (not up_won)),
                    "up_won": up_won, "_snaps_idx": len(snaps) - 1,
                })
        # level persistence after the observation, and trades at that level
        for r in inst:
            if r["ticker"] != tk or "persist_s" in r:
                continue
            lvl, t0 = r["level"], r["t"]
            side = r["side"]
            gone = None
            for (ts, b, a, bs, as_) in snaps:
                if ts <= t0:
                    continue
                book = a if side == "UP" else b
                if lvl not in book:
                    gone = ts
                    break
            r["persist_s"] = (gone - t0) if gone else None
            r["still_there_at_end"] = gone is None
            r["trades_at_level_after"] = sum(
                1 for (ts, p, q, m2) in trades if ts > t0 and abs(p - lvl) < 1e-9)

    print("=" * 88)
    print("STALE-LIQUIDITY MECHANISM DIAGNOSTIC -- DEV ONLY, n=48 windows")
    print("=" * 88)
    print(f"executable instances (favoured side had a price): {len(inst)}")
    print(f"  independent windows: {len({r['ticker'] for r in inst})}")
    corr = [r for r in inst if r["correct"]]
    print(f"  predicted side went on to WIN: {len(corr)}/{len(inst)} "
          f"= {len(corr)/len(inst)*100:.1f}%\n")

    def pq(v, q):
        v = [x for x in v if x is not None]
        if not v:
            return None
        v = sorted(v)
        return v[min(int(q * (len(v) - 1)), len(v) - 1)]

    print("--- LEVEL AGE: had the offer been resting before the recent move? ---")
    print(f"  {'group':>10s} {'n':>4s} {'age_p10':>8s} {'age_p50':>8s} "
          f"{'age_p90':>8s} {'persist_p50':>12s} {'still_there':>12s}")
    for name, g in (("WINNERS", corr), ("LOSERS", [r for r in inst if not r["correct"]])):
        ages = [r["level_age"] for r in g]
        per = [r.get("persist_s") for r in g]
        st = sum(1 for r in g if r.get("still_there_at_end"))
        f = lambda v: "-" if v is None else f"{v:.1f}"
        print(f"  {name:>10s} {len(g):4d} {f(pq(ages,.1)):>8s} {f(pq(ages,.5)):>8s} "
              f"{f(pq(ages,.9)):>8s} {f(pq(per,.5)):>12s} {st:12d}")

    print("\n--- PRE-MOVE CLASSIFICATION (fixed horizons, nothing tuned) ---")
    print("  A level is PRE-MOVE at h if level_age > h: it was already resting")
    print("  before the last h seconds of Coinbase movement.")
    print(f"  {'h':>4s} {'pre-move n':>11s} {'win%':>7s} {'post-move n':>12s} "
          f"{'win%':>7s} {'delta':>8s}")
    for h in HORIZONS:
        pre = [r for r in inst if r["level_age"] is not None and r["level_age"] > h]
        post = [r for r in inst if r["level_age"] is not None and r["level_age"] <= h]
        wp = (sum(1 for r in pre if r["correct"]) / len(pre) * 100) if pre else None
        wq = (sum(1 for r in post if r["correct"]) / len(post) * 100) if post else None
        f = lambda v: "-" if v is None else f"{v:.1f}"
        d = (wp - wq) if (wp is not None and wq is not None) else None
        print(f"  {h:4d} {len(pre):11d} {f(wp):>6s}% {len(post):12d} {f(wq):>6s}% "
              f"{('-' if d is None else f'{d:+.1f}'):>8s}")

    print("\n--- COINBASE MOVE PRECEDING THE EXECUTABLE INSTANT ---")
    print(f"  {'group':>10s} " + " ".join(f"{'mv'+str(h)+'s_p50':>11s}" for h in HORIZONS)
          + f" {'|mv10s|_p90':>12s}")
    for name, g in (("WINNERS", corr), ("LOSERS", [r for r in inst if not r["correct"]])):
        line = f"  {name:>10s} "
        for h in HORIZONS:
            v = pq([r[f"cb_move_{h}s"] for r in g], .5)
            line += f"{('-' if v is None else f'{v:+.2f}'):>11s} "
        v = pq([abs(r["cb_move_10s"]) for r in g if r["cb_move_10s"] is not None], .9)
        line += f"{('-' if v is None else f'{v:.2f}'):>12s}"
        print(line)

    print("\n--- THE STALE-LIQUIDITY SIGNATURE ---")
    print("  A plausibly stale winning offer = favoured side CORRECT, the level")
    print("  already resting before the move (age > 10 s), and a material")
    print("  Coinbase move in those 10 s pushing the side further into the money.")
    stale = [r for r in inst if r["correct"] and r["level_age"] is not None
             and r["level_age"] > 10 and r["cb_move_10s"] is not None
             and ((r["side"] == "UP" and r["cb_move_10s"] > 0)
                  or (r["side"] == "DOWN" and r["cb_move_10s"] < 0))]
    print(f"  instances matching the full signature : {len(stale)} / {len(inst)}")
    if stale:
        print(f"  across independent windows            : {len({r['ticker'] for r in stale})}")
        print(f"  their price p50                       : {pq([r['price'] for r in stale],.5)}")
        print(f"  their size p50                        : {pq([r['size'] for r in stale],.5)}")
        print(f"  their level age p50                   : {pq([r['level_age'] for r in stale],.5):.1f}s")
        print("\n  per-instance detail:")
        for r in sorted(stale, key=lambda x: -x["tte"]):
            print(f"    {r['ticker']} T-{r['tte']:<4d} {r['side']:>4s} px={r['price']:.2f} "
                  f"sz={r['size']:.1f} age={r['level_age']:.1f}s "
                  f"cb10s={r['cb_move_10s']:+.2f} dist={r['dist']:+.1f} "
                  f"persist={r.get('persist_s')}")
    Path("analysis/gemini/info_edge/ie_staleness.json").write_text(
        json.dumps([{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in inst], indent=1))
    return inst


if __name__ == "__main__":
    main()
