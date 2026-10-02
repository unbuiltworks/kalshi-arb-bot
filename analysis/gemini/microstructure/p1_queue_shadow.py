"""P1: how much observed flow could a REALISTICALLY RESTING maker capture?

Lifecycle is explicit. An order is PLACE(price, qty, queue_ahead) and ends
FILL / PARTIAL / CANCEL / EXPIRE. The rules, all of them binding:

  * NO touch = fill. A print at our price does not fill us by itself; it
    first consumes the queue ahead of us.
  * NO invented queue. queue_ahead is the DISPLAYED size at our price from
    the last BBO strictly BEFORE placement. Joining a level means joining
    the BACK of it.
  * NO zero-latency pegging. Repricing is CANCEL + NEW and FORFEITS queue
    position -- the new order takes the then-displayed size as queue_ahead.
  * Side discipline from buyer_is_maker, which names the PASSIVE side:
      buyer_is_maker=True  -> taker SOLD into the bid; a passive BUY fills.
      buyer_is_maker=False -> taker BOUGHT from the ask; a passive SELL fills.
    A print only ever fills an order on the side that was actually passive.
  * Price must match exactly. We do not fill on prints through our level
    that we never displayed at.

Two policies, neither of which is allowed to cheat:
  STATIC   -- place once at the first two-sided BBO in the window, hold to
              expiry. Never repriced. This is the honest floor.
  REQUOTE  -- follow the touch, but every move is cancel+new and the new
              order goes to the back of the new level's displayed queue.

An OPTIMISTIC_UPPER variant (queue_ahead forced to 0) is reported strictly
as an UNREACHABLE UPPER BOUND, never as capture.

KNOWN MEASUREMENT LIMIT, stated not buried: these tapes sample the book at
~5 s. Queue sizes are therefore as of up to 5 s before the print, and any
level that appeared and vanished between samples is invisible. Every number
here is conditional on that sampling.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARKET_RE = re.compile(r"^GEMI-((?:BTC|ETH)(?:05|15)M\d{10})-(UP|DOWN)$")
BARE_RE = re.compile(r"^((?:BTC|ETH)(?:05|15)M\d{10})$")
CLIP_SIZES = (1, 5, 25)


def key_of(row):
    m = row.get("market") or ""
    h = MARKET_RE.match(m) or BARE_RE.match(m)
    return h.group(1) if h else None


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def load(tape_dir, asset, dur):
    expiry, vol, prints, bbo = {}, defaultdict(list), defaultdict(list), defaultdict(list)
    for p in sorted(Path(tape_dir).glob("tape_*.jsonl")):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("venue") != "gemini":
                continue
            k = key_of(r)
            if not k or not k.startswith(asset) or k[3:6] != dur:
                continue
            kind, t = r.get("kind"), r.get("t")
            if kind == "vol":
                e = r.get("expiry")
                if e and k not in expiry:
                    expiry[k] = datetime.fromisoformat(
                        e.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
            elif kind == "print":
                try:
                    prints[k].append((t, float(r["qty"]), float(r["price"]),
                                      r.get("buyer_is_maker"), r.get("trade_id")))
                except Exception:
                    pass
            elif kind == "bbo":
                b, a = r.get("bids") or [], r.get("asks") or []
                bbo[k].append((
                    t,
                    float(b[0][0]) if b else None, float(b[0][1]) if b else None,
                    float(a[0][0]) if a else None, float(a[0][1]) if a else None,
                ))
    return expiry, prints, bbo


def simulate(ob, pr, side, clip, policy, optimistic=False):
    """Walk one window's event stream for one side. Returns a lifecycle log.

    ob: sorted [(t, bid, bid_sz, ask, ask_sz)]
    pr: sorted [(t, qty, price, buyer_is_maker)]
    side: 'buy' (rests at bid) or 'sell' (rests at ask)
    """
    PX, SZ = (1, 2) if side == "buy" else (3, 4)
    passive_flag = True if side == "buy" else False

    events = sorted([("bbo",) + o for o in ob] + [("print",) + p for p in pr],
                    key=lambda e: e[1])

    order = None          # dict(price, remaining, queue_ahead, t_place)
    log = {"placed": 0, "filled_qty": 0.0, "fills": 0, "cancels": 0,
           "expired": 0, "queue_ahead_at_place": [], "fill_prices": [],
           "fill_times": [], "reprices": 0}

    def place(t, price, size):
        nonlocal order
        if price is None:
            return
        order = {"price": price, "remaining": float(clip),
                 "queue_ahead": 0.0 if optimistic else float(size or 0.0),
                 "t": t}
        log["placed"] += 1
        log["queue_ahead_at_place"].append(order["queue_ahead"])

    for ev in events:
        if ev[0] == "bbo":
            _, t, bid, bsz, ask, asz = ev
            px, sz = (bid, bsz) if side == "buy" else (ask, asz)
            if order is None:
                place(t, px, sz)
            elif policy == "REQUOTE" and px is not None and px != order["price"]:
                # the touch moved: CANCEL + NEW, queue position forfeited
                log["cancels"] += 1
                log["reprices"] += 1
                place(t, px, sz)
            # STATIC: leave the order exactly where it is, however stale
        else:
            _, t, qty, price, bim = ev
            if order is None or bim is not passive_flag:
                continue
            if price != order["price"]:
                continue
            # the print first consumes the queue ahead of us
            take = min(qty, order["queue_ahead"])
            order["queue_ahead"] -= take
            resid = qty - take
            if resid <= 0:
                continue
            got = min(resid, order["remaining"])
            if got > 0:
                order["remaining"] -= got
                log["filled_qty"] += got
                log["fills"] += 1
                log["fill_prices"].append(price)
                log["fill_times"].append(t)
                if order["remaining"] <= 0:
                    order = None   # fully filled; flat, awaiting next placement
    if order is not None:
        log["expired"] += 1
    return log


def main(tape_dir="data/policy_d_port", asset="BTC", dur="05M", min_cov=240.0):
    dur_s = 300.0 if dur == "05M" else 900.0
    expiry, prints, bbo = load(tape_dir, asset, dur)

    results = []
    for k, exp in expiry.items():
        start = exp - dur_s
        ob = sorted([o for o in bbo.get(k, []) if start <= o[0] <= exp])
        if len(ob) < 2 or (ob[-1][0] - ob[0][0]) < min_cov:
            continue
        seen, pr = set(), []
        for t, qty, price, bim, tid in prints.get(k, []):
            if not (start <= t <= exp):
                continue
            if tid is not None and tid in seen:
                continue
            if tid is not None:
                seen.add(tid)
            pr.append((t, qty, price, bim))
        pr.sort()
        offered = sum(q for _, q, _, _ in pr)
        rec = {"window": k, "cov_s": round(ob[-1][0] - ob[0][0], 1),
               "offered_prints": len(pr), "offered_contracts": round(offered, 2)}
        for policy in ("STATIC", "REQUOTE"):
            for clip in CLIP_SIZES:
                tot = 0.0
                agg = {"placed": 0, "fills": 0, "cancels": 0, "reprices": 0}
                for side in ("buy", "sell"):
                    lg = simulate(ob, pr, side, clip, policy)
                    tot += lg["filled_qty"]
                    for f in agg:
                        agg[f] += lg[f]
                rec[f"{policy}_q{clip}_filled"] = round(tot, 2)
                rec[f"{policy}_q{clip}_placed"] = agg["placed"]
                rec[f"{policy}_q{clip}_fills"] = agg["fills"]
                rec[f"{policy}_q{clip}_reprices"] = agg["reprices"]
        # unreachable upper bound, labelled as such
        for clip in CLIP_SIZES:
            tot = 0.0
            agg = {"placed": 0, "fills": 0, "reprices": 0}
            for side in ("buy", "sell"):
                lg = simulate(ob, pr, side, clip, "REQUOTE", optimistic=True)
                tot += lg["filled_qty"]
                for f in agg:
                    agg[f] += lg[f]
            rec[f"OPTIMISTIC_UPPER_q{clip}_filled"] = round(tot, 2)
            rec[f"OPTIMISTIC_UPPER_q{clip}_placed"] = agg["placed"]
            rec[f"OPTIMISTIC_UPPER_q{clip}_fills"] = agg["fills"]
            rec[f"OPTIMISTIC_UPPER_q{clip}_reprices"] = agg["reprices"]
        results.append(rec)

    results.sort(key=lambda r: -r["offered_contracts"])
    out = Path(__file__).with_name(f"p1_queue_shadow_{asset}{dur}.json")
    out.write_text(json.dumps(results, indent=1))

    n = len(results)
    off_c = sum(r["offered_contracts"] for r in results)
    off_p = sum(r["offered_prints"] for r in results)
    print(f"{asset}{dur} windows simulated (cov >= {int(min_cov)} s): {n}")
    print(f"observed in-window flow offered  : {off_p} prints / {round(off_c,2)} contracts")
    print()
    hdr = f"{'policy/clip':22s} {'fills':>6s} {'filled_qty':>11s} {'share_of_flow':>14s} {'placed':>7s} {'reprices':>9s}"
    print(hdr)
    print("-" * len(hdr))
    for policy in ("STATIC", "REQUOTE", "OPTIMISTIC_UPPER"):
        for clip in CLIP_SIZES:
            kf = f"{policy}_q{clip}_filled"
            q = sum(r[kf] for r in results)
            fills = sum(r.get(f"{policy}_q{clip}_fills", 0) for r in results)
            placed = sum(r.get(f"{policy}_q{clip}_placed", 0) for r in results)
            rep = sum(r.get(f"{policy}_q{clip}_reprices", 0) for r in results)
            share = (q / off_c * 100) if off_c else 0.0
            print(f"{policy+' q'+str(clip):22s} {fills:6d} {q:11.2f} "
                  f"{share:13.2f}% {placed:7d} {rep:9d}")
    print()
    print("per-window filled contracts (REQUOTE):")
    for clip in CLIP_SIZES:
        v = [r[f"REQUOTE_q{clip}_filled"] for r in results]
        nz = sum(1 for x in v if x > 0)
        print(f"  q{clip:<3d} p50={pct(v,0.5)} p90={pct(v,0.9)} max={max(v)} "
              f"| windows with any fill: {nz}/{n}")
    print()
    print("CAPACITY CHECK -- does capture scale with clip size?")
    for policy in ("STATIC", "REQUOTE"):
        qs = [sum(r[f"{policy}_q{c}_filled"] for r in results) for c in CLIP_SIZES]
        base = qs[0] if qs[0] else float("nan")
        print(f"  {policy:8s} " + "  ".join(
            f"q{c}={q:.1f} ({q/base:.2f}x q1)" for c, q in zip(CLIP_SIZES, qs)))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:4] or ["data/policy_d_port", "BTC", "05M"])))
