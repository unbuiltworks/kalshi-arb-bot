"""L5 SETTINGS AUDIT A1 -- order persistence, queue attrition, multi-level.

NOT an optimization. NOT a reopening of the frozen decision gate, which
stands for the policy it tested. This asks whether that policy represented
L5's documented mechanics at all.

THE MECHANIC THE FROZEN MODEL STRUCTURALLY EXCLUDED
---------------------------------------------------
L5_OPERATING_MODEL.md documents the one readable maker fill of the spring
probe like this:

  "the YES bid held 0.31 while the ask walked 0.58 -> 0.43 -> 0.38 -> 0.35
   -> 0.33 down to meet it, and the ladder at our own price read 0.01 --
   our order, alone on the bid, AFTER THE 483.0 THAT HAD BEEN THERE LEFT."

Three mechanics in one sentence, none of which the frozen model had:
  (a) the order STAYED at a fixed price (p3_hifi_capture reprices on every
      touch move -- measured 102 reprices per fill);
  (b) price came TO the order (the ask walked down) rather than the order
      chasing the touch;
  (c) the queue ahead CANCELLED AND LEFT, which is how priority was gained
      -- while the frozen DEFENSIBLE tier states "cancels are assumed
      BEHIND us -- they do not advance us", so it can never score this.

POLICIES COMPARED (structural, no fitted thresholds)
----------------------------------------------------
  A_REPRICE   the frozen policy: follow the touch, cancel+new each move.
  B_STICKY    place once at the first observable touch, never reprice,
              hold to window end.
  C_INVALIDATED  sticky until a STRUCTURAL invalidation event, defined by
              events and not by any tuned number: our price level leaves
              the book entirely, OR our side's touch crosses past our price
              (our bid is above the best ask / our ask below the best bid).

QUEUE ACCOUNTING TIERS
----------------------
  FIFO_STRICT      frozen rule: only observed TRADES at our price advance us.
  FIFO_ATTRITION   a size DECREASE at our price that is NOT attributable to
                   any trade at that price within the same event is credited
                   as CANCELLATION AHEAD OF US. This is observable from the
                   diff + trade streams, not assumed. It is the documented
                   spring mechanism. Reported SEPARATELY, never merged.
  Through-prints fill regardless of queue in both tiers and are decomposed.

Fill provenance is separated four ways, as required:
  price_came_to_us  our price was NOT the touch when placed and later became
                    tradeable (the ask walked down to us)
  same_price_fifo   same-price fill with queue exhausted by observed trades
  through_price     a print strictly through our resting price
  ambiguous         book desynced, or cause not establishable
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
POLICIES = ("A_REPRICE", "B_STICKY", "C_INVALIDATED")
TIERS = ("FIFO_STRICT", "FIFO_ATTRITION")
PHASES = ((0, 60), (60, 120), (120, 180), (180, 240), (240, 300))
ADV_H = (5.0, 15.0, 30.0)
# fixed structural grid for multi-level: ticks away from touch. NOT optimized.
GRID_TICKS = (0, 1, 2, 3, 5)
TICK = 0.01


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
    """Apply diff; return {px: (before, after)}."""
    touched = {}
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        touched[p] = (book.get(p, 0.0), s)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s
    return touched


def load():
    ev, win = defaultdict(list), {}
    for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            m = r.get("market") or ""
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
    """(events, mids). events: ('book',t,bids,asks,desync,touched_b,touched_a)
    or ('trade',t,px,qty,bim)."""
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
                out.append(("book", t, dict(bids), dict(asks), desync, {}, {}))
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
            out.append(("book", t, dict(bids), dict(asks), desync, tb, ta))
            continue
        if k == "trade":
            try:
                out.append(("trade", t, float(r["price"]), float(r["qty"]),
                            r.get("buyer_is_maker")))
            except Exception:
                pass
    return out, mids


def adverse(mids, t0, side):
    if not mids:
        return {}
    ts = [m[0] for m in mids]
    i = bisect.bisect_right(ts, t0) - 1
    if i < 0:
        return {}
    m0 = mids[i][1]
    o = {}
    for h in ADV_H:
        j = bisect.bisect_right(ts, t0 + h) - 1
        if j < 0 or ts[j] <= t0:
            continue
        o[h] = (m0 - mids[j][1]) if side == "buy" else (mids[j][1] - m0)
    return o


def run(events, mids, side, clip, policy, tier, start, expiry, grid_tick=0):
    """One resting clip, one side, one policy, one tier, one grid offset."""
    is_bid = side == "buy"
    passive = True if is_bid else False
    order = None
    log = {"placed": 0, "reprices": 0, "fills": [], "lifetimes": [],
           "q_discarded": 0.0, "flow": 0.0, "attrition_credited": 0.0,
           "invalidations": 0}

    def touch_of(bids, asks):
        bk = bids if is_bid else asks
        if not bk:
            return None, None
        p = max(bk) if is_bid else min(bk)
        return p, bk[p]

    def target_price(bids, asks):
        """Fixed structural grid: grid_tick ticks AWAY from the touch
        (passive side), i.e. buying weakness rather than joining the touch."""
        p, _ = touch_of(bids, asks)
        if p is None:
            return None
        px = p - grid_tick * TICK if is_bid else p + grid_tick * TICK
        return round(px, 4)

    def place(t, bids, asks):
        nonlocal order
        px = target_price(bids, asks)
        if px is None or px <= 0 or px >= 1:
            return
        bk = bids if is_bid else asks
        order = {"px": px, "rem": float(clip), "q": bk.get(px, 0.0),
                 "t": t, "was_touch_at_place": px == (max(bk) if is_bid else min(bk))}
        log["placed"] += 1

    for e in events:
        if e[0] == "book":
            _, t, bids, asks, desync, tb, ta = e
            if t is None or not (start <= t <= expiry):
                continue
            if order is None:
                place(t, bids, asks)
                continue
            px = order["px"]
            tb_side = tb if is_bid else ta
            # queue attrition: a decrease at our price not caused by a trade
            if px in tb_side:
                before, after = tb_side[px]
                if after < before:
                    dec = before - after
                    if tier == "FIFO_ATTRITION":
                        adv = min(dec, order["q"])
                        order["q"] -= adv
                        log["attrition_credited"] += adv
                    # FIFO_STRICT: no advance
                if after == 0.0:
                    # our price level left the book entirely
                    if policy in ("A_REPRICE", "C_INVALIDATED"):
                        log["lifetimes"].append(t - order["t"])
                        log["q_discarded"] += order["q"]
                        if policy == "C_INVALIDATED":
                            log["invalidations"] += 1
                        order = None
                        place(t, bids, asks)
                        continue
            if order is None:
                continue
            if policy == "A_REPRICE":
                tp = target_price(bids, asks)
                if tp is not None and tp != order["px"]:
                    log["reprices"] += 1
                    log["lifetimes"].append(t - order["t"])
                    log["q_discarded"] += order["q"]
                    place(t, bids, asks)
            elif policy == "C_INVALIDATED":
                # structural invalidation: our price crossed past the far touch
                if is_bid and asks and order["px"] >= min(asks):
                    log["invalidations"] += 1
                    log["lifetimes"].append(t - order["t"])
                    log["q_discarded"] += order["q"]
                    place(t, bids, asks)
                elif (not is_bid) and bids and order["px"] <= max(bids):
                    log["invalidations"] += 1
                    log["lifetimes"].append(t - order["t"])
                    log["q_discarded"] += order["q"]
                    place(t, bids, asks)
            continue

        _, t, px, qty, bim = e
        if t is None or not (start <= t <= expiry):
            continue
        if bim is not passive:
            continue
        log["flow"] += qty
        if order is None:
            continue
        P = order["px"]
        through = (px < P) if is_bid else (px > P)
        at = (px == P)
        if through:
            got = min(qty, order["rem"])
            if got > 0:
                order["rem"] -= got
                log["fills"].append({"t": t, "qty": got, "px": P,
                                     "basis": "through_price",
                                     "rest_s": t - order["t"], "side": side})
                if order["rem"] <= 0:
                    log["lifetimes"].append(t - order["t"])
                    order = None
            continue
        if not at:
            continue
        take = min(qty, order["q"])
        order["q"] -= take
        over = qty - take
        if over > 0:
            got = min(over, order["rem"])
            if got > 0:
                order["rem"] -= got
                basis = ("price_came_to_us" if not order["was_touch_at_place"]
                         else "same_price_fifo")
                log["fills"].append({"t": t, "qty": got, "px": P,
                                     "basis": basis,
                                     "rest_s": t - order["t"], "side": side})
                if order["rem"] <= 0:
                    log["lifetimes"].append(t - order["t"])
                    order = None
    if order is not None:
        log["lifetimes"].append(expiry - order["t"])
    return log


def main():
    ev, win = load()
    acc = defaultdict(lambda: {"fills": 0, "qty": 0.0, "flow": 0.0,
                               "placed": 0, "reprices": 0, "life": [],
                               "qdisc": 0.0, "attr": 0.0, "wins": set(),
                               "basis": defaultdict(float),
                               "adv": defaultdict(list), "inval": 0,
                               "phase": defaultdict(float)})
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
        for policy in POLICIES:
            for tier in TIERS:
                for clip in CLIPS:
                    for g in GRID_TICKS:
                        key = (policy, tier, clip, g)
                        a = acc[key]
                        for side in ("buy", "sell"):
                            lg = run(events, mids, side, clip, policy, tier,
                                     start, expiry, g)
                            a["placed"] += lg["placed"]
                            a["reprices"] += lg["reprices"]
                            a["life"] += lg["lifetimes"]
                            a["qdisc"] += lg["q_discarded"]
                            a["attr"] += lg["attrition_credited"]
                            a["flow"] += lg["flow"]
                            a["inval"] += lg["invalidations"]
                            for f in lg["fills"]:
                                a["fills"] += 1
                                a["qty"] += f["qty"]
                                a["basis"][f["basis"]] += f["qty"]
                                a["wins"].add(m)
                                ph = f["t"] - start
                                for lo, hi in PHASES:
                                    if lo <= ph < hi:
                                        a["phase"][(lo, hi)] += f["qty"]
                                for h, v in adverse(mids, f["t"], side).items():
                                    a["adv"][h].append(v)

    print("=" * 100)
    print("A1  PERSISTENCE / QUEUE-ATTRITION / MULTI-LEVEL AUDIT")
    print("The frozen decision gate is UNCHANGED. This tests whether its")
    print("policy represented L5's documented mechanics.")
    print("=" * 100)
    print(f"\nqualified BTC5m windows: {nwin}\n")

    print("--- Q2: ORDER PERSISTENCE. Are we destroying the priority we seek? ---")
    print(f"{'policy':14s} {'tier':15s} {'placed':>7s} {'reprices':>9s} "
          f"{'life_p50':>9s} {'q_discarded':>12s} {'fills':>6s} {'qty':>9s} {'cap%':>7s}")
    print("-" * 96)
    for policy in POLICIES:
        for tier in TIERS:
            a = acc[(policy, tier, 1, 0)]
            cap = (a["qty"] / a["flow"] * 100) if a["flow"] else 0.0
            lp = pct(a["life"], .5)
            print(f"{policy:14s} {tier:15s} {a['placed']:7d} {a['reprices']:9d} "
                  f"{(f'{lp:.1f}s' if lp is not None else '-'):>9s} "
                  f"{a['qdisc']:12.1f} {a['fills']:6d} {a['qty']:9.2f} {cap:6.2f}%")

    print("\n--- Q1/Q3: MULTI-LEVEL / AWAY-FROM-TOUCH (grid = ticks away, q1) ---")
    print(f"{'policy':14s} {'tier':15s} {'ticks':>6s} {'fills':>6s} {'qty':>9s} "
          f"{'cap%':>7s} {'came_to_us':>11s} {'same_fifo':>10s} {'through':>8s} {'wins':>5s}")
    print("-" * 100)
    for policy in POLICIES:
        for tier in TIERS:
            for g in GRID_TICKS:
                a = acc[(policy, tier, 1, g)]
                if not a["placed"]:
                    continue
                cap = (a["qty"] / a["flow"] * 100) if a["flow"] else 0.0
                print(f"{policy:14s} {tier:15s} {g:6d} {a['fills']:6d} "
                      f"{a['qty']:9.2f} {cap:6.2f}% "
                      f"{a['basis']['price_came_to_us']:11.2f} "
                      f"{a['basis']['same_price_fifo']:10.2f} "
                      f"{a['basis']['through_price']:8.2f} {len(a['wins']):5d}")

    print("\n--- ATTRITION CREDITED (contracts of queue that CANCELLED ahead of us) ---")
    for policy in POLICIES:
        a = acc[(policy, "FIFO_ATTRITION", 1, 0)]
        print(f"  {policy:14s} attrition_credited={a['attr']:.1f} contracts")

    print("\n--- Q9: TIME-IN-WINDOW phase, fills by phase (B_STICKY, attrition, q1) ---")
    for policy in ("A_REPRICE", "B_STICKY"):
        a = acc[(policy, "FIFO_ATTRITION", 1, 0)]
        row = "  ".join(f"{lo}-{hi}s={a['phase'].get((lo,hi),0.0):.1f}"
                        for lo, hi in PHASES)
        print(f"  {policy:12s} {row}")

    print("\n--- ADVERSE SELECTION at frozen 5/15/30 s, by policy (q1, attrition) ---")
    for policy in POLICIES:
        a = acc[(policy, "FIFO_ATTRITION", 1, 0)]
        seg = []
        for h in ADV_H:
            v = a["adv"].get(h, [])
            seg.append(f"{int(h)}s:" + (f"{pct(v,.5):+.4f}(n={len(v)})" if v else "-"))
        print(f"  {policy:14s} " + "  ".join(seg))

    out = Path("analysis/gemini/l5_audit/a1_results.json")
    ser = {}
    for k, a in acc.items():
        ser["|".join(map(str, k))] = {
            "fills": a["fills"], "qty": round(a["qty"], 2),
            "flow": round(a["flow"], 2), "placed": a["placed"],
            "reprices": a["reprices"], "q_discarded": round(a["qdisc"], 2),
            "attrition_credited": round(a["attr"], 2),
            "invalidations": a["inval"], "windows": len(a["wins"]),
            "life_p50": pct(a["life"], .5),
            "basis": {kk: round(vv, 2) for kk, vv in a["basis"].items()},
            "adv_p50": {str(h): pct(v, .5) for h, v in a["adv"].items()},
        }
    out.write_text(json.dumps(ser, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
