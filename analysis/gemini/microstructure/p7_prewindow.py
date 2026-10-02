"""P7: SEPARATE pre-window placement diagnostic. Does arriving BEFORE the
5-minute window opens establish queue priority ahead of the blocking size?

DOES NOT MODIFY THE FROZEN DECISION GATE. Same queue discipline as frozen:
no touch=fill, displayed size is a PROXY and never proven queue position,
a cancellation does not advance us, through-prints are decomposed out,
post-fill horizons stay 5/15/30 s. No price level is chosen with future
information: at each horizon we join the touch OBSERVABLE AT THAT HORIZON.

HARD COVERAGE BOUND, measured not assumed
-----------------------------------------
lane_recorder.py subscribes at expiry-330 s; a 5 m window starts at
expiry-300 s. **Maximum observable pre-window lead is ~30 s.** Measured
across 45 BTC05M contracts: first observable event at T-29.5 s (min) /
T-28.6 s (p50), with 96% observed before T=0.

**T-5m, T-3m and T-1m are therefore NOT OBSERVABLE in this tape and no
claim is made about them.** The horizons actually tested are T-30s, T-20s,
T-10s, T-5s and T=0. Everything earlier is an UNRESOLVED measurement gap,
reported as such, requiring a new collection run with a longer subscribe
lead -- not inferred here.
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
HORIZONS_S = (-30.0, -20.0, -10.0, -5.0, 0.0)
ADV_H = (5.0, 15.0, 30.0)
REFS = (100.0, 1000.0)


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
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def load():
    ev, win = defaultdict(list), {}
    for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "window":
                if SYM.match(r.get("market") or ""):
                    win[r["market"]] = r
            elif k in ("depth_update", "depth_snapshot", "trade"):
                if SYM.match(r.get("market") or ""):
                    ev[r["market"]].append(r)
    for m in ev:
        ev[m].sort(key=lambda r: (r.get("t") or 0))
    return ev, win


def build_timeline(rows):
    """Replay into a timeline of (t, bids, asks, desync) plus trades, and
    record for every price level the FIRST time it was observed with size."""
    bids, asks = {}, {}
    seeded, desync, last_u = False, True, None
    tl, trades = [], []
    first_seen = {}      # ('bid'|'ask', px) -> (t, size_at_first_sight)
    for r in rows:
        k, t = r["kind"], r.get("t")
        if k == "depth_snapshot":
            if not seeded or desync:
                bids, asks = {}, {}
                apply_side(bids, r.get("bids"))
                apply_side(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded, desync = True, False
                for px, sz in (r.get("bids") or []):
                    first_seen.setdefault(("bid", float(px)), (t, float(sz)))
                for px, sz in (r.get("asks") or []):
                    first_seen.setdefault(("ask", float(px)), (t, float(sz)))
                tl.append((t, dict(bids), dict(asks), desync))
            continue
        if k == "depth_update":
            if not seeded:
                continue
            U, u = r.get("U"), r.get("u")
            if u is not None and last_u is not None and u <= last_u:
                continue
            if U is not None and last_u is not None and U != last_u:
                desync = True
            apply_side(bids, r.get("b"))
            apply_side(asks, r.get("a"))
            last_u = u
            for px, sz in (r.get("b") or []):
                if float(sz) > 0:
                    first_seen.setdefault(("bid", float(px)), (t, float(sz)))
            for px, sz in (r.get("a") or []):
                if float(sz) > 0:
                    first_seen.setdefault(("ask", float(px)), (t, float(sz)))
            tl.append((t, dict(bids), dict(asks), desync))
            continue
        if k == "trade":
            try:
                trades.append((t, float(r["price"]), float(r["qty"]),
                               r.get("buyer_is_maker")))
            except Exception:
                pass
    return tl, trades, first_seen


def state_at(tl, t):
    i = bisect.bisect_right([x[0] for x in tl], t) - 1
    return tl[i] if i >= 0 else None


def mids(tl):
    out = []
    for t, b, a, d in tl:
        if b and a:
            out.append((t, (max(b) + min(a)) / 2.0))
    return out


def adverse(mk, t0, side):
    if not mk:
        return {}
    ts = [m[0] for m in mk]
    i = bisect.bisect_right(ts, t0) - 1
    if i < 0:
        return {}
    m0 = mk[i][1]
    o = {}
    for h in ADV_H:
        j = bisect.bisect_right(ts, t0 + h) - 1
        if j < 0 or ts[j] <= t0:
            continue
        o[h] = (m0 - mk[j][1]) if side == "buy" else (mk[j][1] - m0)
    return o


def simulate(tl, trades, t_place, px, side, clip, expiry):
    """Place at t_place at `px`, hold without repricing, FIFO-defensible."""
    is_bid = side == "buy"
    passive = True if is_bid else False
    st = state_at(tl, t_place)
    if st is None:
        return None
    book = st[1] if is_bid else st[2]
    q_ahead = book.get(px)
    if q_ahead is None or q_ahead <= 0:
        return None
    rem = float(clip)
    res = {"q_ahead": q_ahead, "fills": [], "flow": 0.0,
           "level_alive_at_T0": None, "ambiguous": 0.0}
    for t, pxx, q, bim in trades:
        if t <= t_place or (expiry is not None and t > expiry):
            continue
        if bim is not passive:
            continue
        res["flow"] += q
        stt = state_at(tl, t)
        amb = bool(stt and stt[3])      # desynced book -> ambiguous
        through = (pxx < px) if is_bid else (pxx > px)
        at = (pxx == px)
        if through:
            got = min(q, rem)
            if got > 0:
                rem -= got
                res["fills"].append({"t": t, "qty": got,
                                     "basis": "ambiguous" if amb else "through"})
                if amb:
                    res["ambiguous"] += got
                if rem <= 0:
                    break
        elif at:
            take = min(q, q_ahead)
            q_ahead -= take
            over = q - take
            if over > 0:
                got = min(over, rem)
                if got > 0:
                    rem -= got
                    res["fills"].append({"t": t, "qty": got,
                                         "basis": "ambiguous" if amb else "same_price"})
                    if amb:
                        res["ambiguous"] += got
                    if rem <= 0:
                        break
    return res


def main():
    ev, win = load()
    acc = {(h, c): {"n": 0, "qa": [], "fills": 0, "qty": 0.0, "through": 0.0,
                    "same": 0.0, "amb": 0.0, "flow": 0.0, "wins": set(),
                    "adv": defaultdict(list), "survive": 0, "levelgone": 0}
           for h in HORIZONS_S for c in CLIPS}
    blockers = []     # when did large in-window liquidity first appear?
    persist = []

    for m, rows in ev.items():
        meta = win.get(m)
        if not meta:
            continue
        expiry = meta["expiry_t"]
        start = expiry - 300.0
        tl, trades, first_seen = build_timeline(rows)
        if not tl:
            continue
        mk = mids(tl)
        t_first_obs = tl[0][0]

        # --- item 4: when did the BLOCKING size first appear? ---
        for t, b, a, d in tl:
            if not (start <= t <= expiry):
                continue
            for sidename, book in (("bid", b), ("ask", a)):
                if not book:
                    continue
                px = max(book) if sidename == "bid" else min(book)
                sz = book[px]
                if sz >= REFS[0]:
                    fs = first_seen.get((sidename, px))
                    if fs:
                        blockers.append({
                            "size": sz,
                            "first_seen_rel_T0": fs[0] - start,
                            "size_at_first_sight": fs[1],
                            "observed_before_T0": fs[0] < start,
                            "at_our_first_observation":
                                abs(fs[0] - t_first_obs) < 1.0,
                        })
            break_outer = False
            if break_outer:
                break

        # --- items 5-7: place at each observable pre-window horizon ---
        for h in HORIZONS_S:
            t_place = start + h
            if t_place < t_first_obs:
                continue          # not observable; skipped, never assumed
            st = state_at(tl, t_place)
            if st is None:
                continue
            for side in ("buy", "sell"):
                book = st[1] if side == "buy" else st[2]
                if not book:
                    continue
                px = max(book) if side == "buy" else min(book)
                # item 6: does that level still exist at T=0, observationally?
                st0 = state_at(tl, start)
                alive = None
                if st0 is not None:
                    b0 = st0[1] if side == "buy" else st0[2]
                    alive = px in b0
                    persist.append({"h": h, "side": side, "alive_at_T0": alive})
                for clip in CLIPS:
                    s = simulate(tl, trades, t_place, px, side, clip, expiry)
                    a = acc[(h, clip)]
                    if s is None:
                        a["levelgone"] += 1
                        continue
                    a["n"] += 1
                    a["qa"].append(s["q_ahead"])
                    a["flow"] += s["flow"]
                    if alive:
                        a["survive"] += 1
                    for f in s["fills"]:
                        a["fills"] += 1
                        a["qty"] += f["qty"]
                        if f["basis"] == "through":
                            a["through"] += f["qty"]
                        elif f["basis"] == "same_price":
                            a["same"] += f["qty"]
                        else:
                            a["amb"] += f["qty"]
                        a["wins"].add(meta.get("window"))
                        for hh, v in adverse(mk, f["t"], side).items():
                            a["adv"][hh].append(v)

    print("=" * 80)
    print("P7 PRE-WINDOW PLACEMENT DIAGNOSTIC -- SEPARATE FROM THE FROZEN")
    print("DECISION GATE, WHICH IS UNCHANGED.")
    print("=" * 80)
    print("\nCOVERAGE BOUND: max observable pre-window lead ~30 s (subscribe at")
    print("expiry-330 s, window starts expiry-300 s). T-5m/T-3m/T-1m ARE NOT")
    print("OBSERVABLE HERE and no claim is made about them.\n")

    print("--- ITEM 4: when did the BLOCKING in-window liquidity (>=100) first appear? ---")
    if blockers:
        rel = [b["first_seen_rel_T0"] for b in blockers]
        print(f"  blocking touch-levels observed      : {len(blockers)}")
        print(f"  first seen, relative to T=0 (s)     : p10={pct(rel,.1):+.1f} "
              f"p50={pct(rel,.5):+.1f} p90={pct(rel,.9):+.1f}")
        bt = sum(1 for b in blockers if b["observed_before_T0"])
        fo = sum(1 for b in blockers if b["at_our_first_observation"])
        print(f"  first appeared BEFORE T=0           : {bt}/{len(blockers)} "
              f"({bt/len(blockers)*100:.1f}%)")
        print(f"  already present at OUR FIRST SIGHT  : {fo}/{len(blockers)} "
              f"({fo/len(blockers)*100:.1f}%)  <- formation UNOBSERVED")
        sfs = [b["size_at_first_sight"] for b in blockers]
        print(f"  size at first sight                 : p50={pct(sfs,.5):.1f} "
              f"p90={pct(sfs,.9):.1f} max={max(sfs):.1f}")
    print()
    print("--- ITEM 6: does a level placed pre-window still exist at T=0? ---")
    byh = defaultdict(lambda: [0, 0])
    for p in persist:
        byh[p["h"]][1] += 1
        if p["alive_at_T0"]:
            byh[p["h"]][0] += 1
    for h in HORIZONS_S:
        a, n = byh[h]
        if n:
            print(f"  placed at T{h:+06.1f}s : level still present at T=0 in "
                  f"{a}/{n} ({a/n*100:.1f}%)")
    print("  NOTE: level PRESENCE is observable; our QUEUE POSITION within it")
    print("  is NOT. Survival of the price level does not establish that our")
    print("  priority survived, and no such claim is made.")
    print()
    print("--- ITEMS 5+7: pre-window placement, FIFO-defensible ---")
    hdr = (f"{'horizon':>9s} {'clip':>5s} {'n':>5s} {'qa_p10':>8s} {'qa_p50':>9s} "
           f"{'qa_p90':>10s} {'fills':>6s} {'qty':>8s} {'cap%':>7s} "
           f"{'benign':>7s} {'thru':>7s} {'amb':>6s} {'wins':>5s}")
    print(hdr); print("-" * len(hdr))
    for h in HORIZONS_S:
        for c in CLIPS:
            a = acc[(h, c)]
            if not a["n"]:
                continue
            cap = (a["qty"] / a["flow"] * 100) if a["flow"] else 0.0
            print(f"{h:+9.1f} {c:5d} {a['n']:5d} {pct(a['qa'],.1):8.1f} "
                  f"{pct(a['qa'],.5):9.1f} {pct(a['qa'],.9):10.1f} {a['fills']:6d} "
                  f"{a['qty']:8.2f} {cap:6.2f}% {a['same']:7.2f} "
                  f"{a['through']:7.2f} {a['amb']:6.2f} {len(a['wins']):5d}")
    print()
    print("--- ITEM 9: post-fill move, FROZEN 5/15/30 s (positive = AGAINST maker) ---")
    for h in HORIZONS_S:
        parts = []
        for c in CLIPS:
            a = acc[(h, c)]
            seg = []
            for hh in ADV_H:
                v = a["adv"].get(hh, [])
                seg.append(f"{int(hh)}s:" + (f"{pct(v,.5):+.4f}(n={len(v)})" if v else "-"))
            parts.append(f"q{c} " + " ".join(seg))
        if any(acc[(h, c)]["fills"] for c in CLIPS):
            print(f"  T{h:+06.1f}s  " + "  |  ".join(parts))


if __name__ == "__main__":
    main()
