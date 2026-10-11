"""P1b: tiered maker capture, CORRECTING the tier error in p1_queue_shadow.py.

WHAT p1_queue_shadow.py GOT WRONG
---------------------------------
It required a print's price to EQUAL our resting price, and discarded every
print that traded THROUGH our level:

    if price != order["price"]: continue

That is not conservative, it is simply wrong. A print strictly through our
resting price means the book traded past our level, so we were reached
WHATEVER our queue position -- it is the one fill class that needs no queue
assumption at all. Discarding it understated defensible capture. On the
historical cohort those discarded prints were 41 of 241 (BELOW_BID 29,
ABOVE_ASK 12), carrying 8,935 of 34,071 contracts.

The prior `analysis/l5/gemini_trajectory/continuous_inventory.py` had this
right, and this module adopts its tier definitions.

THREE BOUNDS, KEPT SEPARATE AND NEVER MIXED, NEVER AVERAGED
-----------------------------------------------------------
  DEFENSIBLE   A print strictly THROUGH our resting price (no queue
               assumption required), OR a print AT our price once the
               CUMULATIVE size traded at-or-through our price since we
               joined EXCEEDS the size displayed at that level when we
               joined. The second clause still assumes the visible queue
               does not refresh, which is unobservable -- it is reported
               split out as DEFENSIBLE_THROUGH vs DEFENSIBLE_DEPLETION so
               the queue-free part is never hidden inside the other.
  PLAUSIBLE    DEFENSIBLE, plus a PRO-RATA share of at-price prints:
               instead of strict FIFO behind the whole displayed queue, our
               clip is allocated qty * clip/(queue_ahead+clip). Pro-rata is
               a STRUCTURAL assumption about allocation, not a tuned
               parameter -- there is no threshold here to fit. It is the
               honest middle: real books are neither pure FIFO-with-no-
               refresh nor front-of-queue.
  ZERO_QUEUE   Any print at-or-through our price. Front of queue, zero
               latency. PHYSICALLY UNREACHABLE -- a ceiling, never capture.

Never touch = fill: every tier requires an actual PRINT.
Repricing is CANCEL + NEW and forfeits queue position in every tier.

Queue-position provenance remains UNKNOWN from public data. `queue_ahead` is
the DISPLAYED size at our level, which is a proxy and an upper bound on the
visible queue only.
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
CLIPS = (1, 5, 10, 25)
TIERS = ("DEFENSIBLE", "PLAUSIBLE", "ZERO_QUEUE")


def key_of(m):
    h = MARKET_RE.match(m or "") or BARE_RE.match(m or "")
    return h.group(1) if h else None


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def load_legacy(tape_dir, asset, dur):
    """Old ~5 s-sampled policy_d_port tapes."""
    exp, prints, bbo = {}, defaultdict(list), defaultdict(list)
    for p in sorted(Path(tape_dir).glob("tape_*.jsonl")):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("venue") != "gemini":
                continue
            k = key_of(r.get("market"))
            if not k or not k.startswith(asset) or k[3:6] != dur:
                continue
            kind = r.get("kind")
            if kind == "vol":
                e = r.get("expiry")
                if e and k not in exp:
                    exp[k] = datetime.fromisoformat(
                        e.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
            elif kind == "print":
                try:
                    prints[k].append((r["t"], float(r["qty"]), float(r["price"]),
                                      bool(r.get("buyer_is_maker")), r.get("trade_id")))
                except Exception:
                    pass
            elif kind == "bbo":
                b, a = r.get("bids") or [], r.get("asks") or []
                bbo[k].append((r["t"],
                               float(b[0][0]) if b else None, float(b[0][1]) if b else None,
                               float(a[0][0]) if a else None, float(a[0][1]) if a else None))
    return exp, prints, bbo


def simulate(ob, pr, side, clip, tier):
    """One side, one clip, one tier. Explicit PLACE -> FILL/CANCEL/EXPIRE.

    ob: [(t, bid, bid_sz, ask, ask_sz)] sorted
    pr: [(t, qty, price, buyer_is_maker)] sorted
    """
    passive = True if side == "buy" else False
    order = None
    log = {"placed": 0, "fills": 0, "qty": 0.0, "reprices": 0,
           "through_qty": 0.0, "depletion_qty": 0.0, "plausible_qty": 0.0,
           "queue_ahead": [], "rest_s": [], "fill_t": []}

    def place(t, px, sz):
        nonlocal order
        if px is None:
            return
        order = {"px": px, "rem": float(clip), "q_ahead": float(sz or 0.0),
                 "t": t, "traded_since_join": 0.0, "touched": False}
        log["placed"] += 1
        log["queue_ahead"].append(order["q_ahead"])

    ev = sorted([("b",) + o for o in ob] + [("p",) + x for x in pr], key=lambda e: e[1])
    for e in ev:
        if e[0] == "b":
            _, t, bid, bsz, ask, asz = e
            px, sz = (bid, bsz) if side == "buy" else (ask, asz)
            if order is None:
                place(t, px, sz)
            elif px is not None and px != order["px"]:
                log["reprices"] += 1          # CANCEL + NEW: queue forfeited
                place(t, px, sz)
            continue

        _, t, qty, price, bim = e
        if order is None or bim is not passive:
            continue
        P = order["px"]
        through = (price < P) if side == "buy" else (price > P)
        at = (price == P)
        if not (through or at):
            continue

        if through:
            # traded past our level: reached regardless of queue position
            got = min(qty, order["rem"])
            if got > 0:
                order["rem"] -= got
                log["fills"] += 1
                log["qty"] += got
                log["through_qty"] += got
                log["rest_s"].append(t - order["t"])
                log["fill_t"].append(t)
                if order["rem"] <= 0:
                    order = None
            continue

        # at our price
        order["traded_since_join"] += qty
        order["touched"] = True
        if tier == "ZERO_QUEUE":
            got = min(qty, order["rem"])
        elif order["traded_since_join"] > order["q_ahead"]:
            over = order["traded_since_join"] - order["q_ahead"]
            got = min(over, qty, order["rem"])
        elif tier == "PLAUSIBLE":
            # pro-rata allocation against the displayed queue
            share = qty * (float(clip) / (order["q_ahead"] + float(clip)))
            got = min(share, order["rem"])
            log["plausible_qty"] += got
        else:
            got = 0.0
        if got > 0:
            order["rem"] -= got
            log["fills"] += 1
            log["qty"] += got
            if tier != "ZERO_QUEUE" and order["traded_since_join"] > order["q_ahead"]:
                log["depletion_qty"] += got
            log["rest_s"].append(t - order["t"])
            log["fill_t"].append(t)
            if order["rem"] <= 0:
                order = None
    return log


def analyse(exp, prints, bbo, dur_s, min_cov, label, adverse_h=(5, 15, 30)):
    rows = []
    for k, e in exp.items():
        s = e - dur_s
        ob = sorted([o for o in bbo.get(k, []) if s <= o[0] <= e])
        if len(ob) < 2 or (ob[-1][0] - ob[0][0]) < min_cov:
            continue
        seen, pr = set(), []
        for t, qty, px, bim, tid in prints.get(k, []):
            if not (s <= t <= e):
                continue
            if tid is not None and tid in seen:
                continue
            if tid is not None:
                seen.add(tid)
            pr.append((t, qty, px, bim))
        pr.sort()
        rec = {"window": k, "cov_s": round(ob[-1][0] - ob[0][0], 1),
               "prints": len(pr), "contracts": round(sum(x[1] for x in pr), 2)}
        # contracts resting at our candidate levels (the touch)
        two = [o for o in ob if o[1] is not None and o[3] is not None]
        rec["touch_levels_observed"] = len(two)
        for tier in TIERS:
            for clip in CLIPS:
                tq = 0.0
                agg = defaultdict(float)
                for side in ("buy", "sell"):
                    lg = simulate(ob, pr, side, clip, tier)
                    tq += lg["qty"]
                    for f in ("fills", "placed", "reprices", "through_qty",
                              "depletion_qty", "plausible_qty"):
                        agg[f] += lg[f]
                    agg["rest_s"] = agg.get("rest_s", 0)
                    for x in lg["rest_s"]:
                        rec.setdefault(f"_rest_{tier}_{clip}", []).append(x)
                    for x in lg["queue_ahead"]:
                        rec.setdefault("_qahead", []).append(x)
                rec[f"{tier}_q{clip}_qty"] = round(tq, 2)
                rec[f"{tier}_q{clip}_fills"] = int(agg["fills"])
                rec[f"{tier}_q{clip}_placed"] = int(agg["placed"])
                rec[f"{tier}_q{clip}_reprices"] = int(agg["reprices"])
                rec[f"{tier}_q{clip}_through_qty"] = round(agg["through_qty"], 2)
                rec[f"{tier}_q{clip}_depletion_qty"] = round(agg["depletion_qty"], 2)
        rows.append(rec)

    n = len(rows)
    tot_c = sum(r["contracts"] for r in rows)
    tot_p = sum(r["prints"] for r in rows)
    print(f"=== {label}")
    print(f"windows (cov >= {int(min_cov)} s) : {n}")
    print(f"observed flow offered     : {tot_p} prints / {round(tot_c,2)} contracts")
    if not n:
        return rows
    print()
    print(f"{'tier/clip':20s} {'fills':>6s} {'qty':>10s} {'%total':>8s} "
          f"{'through':>9s} {'depletion':>10s} {'win_any':>8s} {'rest_s_p50':>11s}")
    print("-" * 92)
    for tier in TIERS:
        for clip in CLIPS:
            q = sum(r[f"{tier}_q{clip}_qty"] for r in rows)
            fl = sum(r[f"{tier}_q{clip}_fills"] for r in rows)
            th = sum(r[f"{tier}_q{clip}_through_qty"] for r in rows)
            dp = sum(r[f"{tier}_q{clip}_depletion_qty"] for r in rows)
            wa = sum(1 for r in rows if r[f"{tier}_q{clip}_qty"] > 0)
            rest = [x for r in rows for x in r.get(f"_rest_{tier}_{clip}", [])]
            rp = pct(rest, 0.5)
            print(f"{tier+' q'+str(clip):20s} {fl:6d} {q:10.2f} "
                  f"{(q/tot_c*100 if tot_c else 0):7.2f}% {th:9.2f} {dp:10.2f} "
                  f"{wa:5d}/{n:<3d} {(f'{rp:.1f}' if rp is not None else '-'):>11s}")
    qa = [x for r in rows for x in r.get("_qahead", [])]
    print()
    print("queue_ahead at placement (displayed size at our level):")
    print("  " + "  ".join(f"p{int(q*100)}={pct(qa,q):.1f}" for q in (0.1, 0.25, 0.5, 0.75, 0.9))
          + f"  max={max(qa):.1f}" if qa else "  (none)")
    print()
    return rows


def main():
    tape_dir = sys.argv[1] if len(sys.argv) > 1 else "data/policy_d_port"
    asset = sys.argv[2] if len(sys.argv) > 2 else "BTC"
    dur = sys.argv[3] if len(sys.argv) > 3 else "05M"
    dur_s = 300.0 if dur == "05M" else 900.0
    exp, prints, bbo = load_legacy(tape_dir, asset, dur)
    rows = analyse(exp, prints, bbo, dur_s, 240.0,
                   f"LEGACY ~5s-sampled cohort | {asset}{dur} | {tape_dir}")
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    out = Path(__file__).with_name(f"p1b_tiered_{asset}{dur}_legacy.json")
    out.write_text(json.dumps(clean, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
