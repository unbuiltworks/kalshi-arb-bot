"""P3: tiered maker capture + adverse selection on the FRESH event-level cohort.

Runs ONLY on lane_recorder.py hi-fi tapes (every @depth diff, every @trade).
It is not run on, and never pooled with, the legacy ~5 s-sampled tapes.

QUEUE MODEL
-----------
The event-level book makes real queue tracking possible. We rest a clip at a
level and maintain `q_ahead` as the venue tells us the level changed:

  * a TRADE at our level consumes the queue ahead of us first; only the
    overflow reaches our clip.
  * a level-size DECREASE not explained by a trade is a CANCEL. Whether that
    cancel sat ahead of us or behind us is UNOBSERVABLE, so it is an explicit
    assumption, reported as two separate tiers and never blended:
      DEFENSIBLE  cancels are assumed BEHIND us -- they do not advance us.
                  Our queue only shrinks on observed traded volume.
      PLAUSIBLE   cancels advance us PRO RATA: q_ahead falls by
                  cancel * q_ahead/level_size. Structural, not a fitted knob.
  * a level-size INCREASE joins BEHIND us and never advances us, in every
    tier.
  * a print strictly THROUGH our price fills us regardless of queue, in every
    tier, and is reported separately so the queue-free component is visible.

  ZERO_QUEUE  q_ahead == 0 always. Physically unreachable; a ceiling only.

Repricing is CANCEL + NEW: on a touch move the order is cancelled and the new
one joins the BACK of the new level. No pegging, no zero latency, no
touch = fill. Every fill requires an actual print.

ADVERSE SELECTION is measured at FIXED horizons (5 / 15 / 30 s) against the
book mid. The horizons are fixed in advance and no threshold is fitted.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

CLIPS = (1, 5, 10, 25)
TIERS = ("DEFENSIBLE", "PLAUSIBLE", "ZERO_QUEUE")
HORIZONS = (5.0, 15.0, 30.0)


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def load(glob_pat):
    by_market = defaultdict(list)
    windows = {}
    for f in sorted(Path(".").glob(glob_pat)):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "window":
                windows[r["market"]] = r
            elif k in ("depth_update", "depth_snapshot", "trade"):
                if r.get("market"):
                    by_market[r["market"]].append(r)
    return by_market, windows


def apply_diff(book, rows):
    """Apply a diff, returning {price: (before, after)} for touched levels."""
    touched = {}
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        touched[p] = (book.get(p, 0.0), s)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s
    return touched


def mid(bids, asks):
    if not bids or not asks:
        return None
    return (max(bids) + min(asks)) / 2.0


def run_side(rows, side, clip, tier, expiry, dur_s):
    """Simulate one resting clip on one side across a window's event stream."""
    is_bid = side == "buy"
    passive = True if is_bid else False
    bids, asks = {}, {}
    seeded = False
    desync = True
    last_u = None
    order = None
    fills = []
    log = {"placed": 0, "reprices": 0, "through_qty": 0.0, "queue_ahead": [],
           "eligible_qty": 0.0, "at_level_qty": 0.0}
    marks = []           # (t, mid) for adverse-selection lookup

    def inwin(t):
        return expiry is None or (expiry - dur_s) <= t <= expiry

    def place(t):
        nonlocal order
        book = bids if is_bid else asks
        if not book:
            return
        px = max(book) if is_bid else min(book)
        order = {"px": px, "rem": float(clip),
                 "q_ahead": 0.0 if tier == "ZERO_QUEUE" else book[px],
                 "t": t}
        log["placed"] += 1
        log["queue_ahead"].append(order["q_ahead"])

    for r in rows:
        k, t = r["kind"], r.get("t")
        if k == "depth_snapshot":
            # Seed ONCE, and thereafter only to RESYNC a broken chain. The
            # depth20 snapshot stream and the depth diff stream are
            # independent, so re-seeding on every snapshot and then comparing
            # the next diff's U against this snapshot's lastUpdateId marks the
            # chain desynced constantly -- which silently zeroed every tier.
            if not seeded or desync:
                bids, asks = {}, {}
                apply_diff(bids, r.get("bids"))
                apply_diff(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded, desync = True, False
            continue

        if k == "depth_update":
            if not seeded:
                continue
            U, u = r.get("U"), r.get("u")
            # Standard reconstruction: drop diffs fully behind the seed, then
            # chain strictly on u -> U.
            if u is not None and last_u is not None and u <= last_u:
                continue
            if U is not None and last_u is not None and U != last_u:
                desync = True
            book = bids if is_bid else asks
            rows_side = r.get("b") if is_bid else r.get("a")
            touched = apply_diff(book, rows_side)
            apply_diff(asks if is_bid else bids,
                       r.get("a") if is_bid else r.get("b"))
            last_u = u
            m = mid(bids, asks)
            if m is not None and t is not None:
                marks.append((t, m))

            if order is not None and not desync:
                # a decrease at our level with no trade is a CANCEL
                if order["px"] in touched:
                    before, after = touched[order["px"]]
                    if after < before:
                        cancel = before - after
                        if tier == "PLAUSIBLE" and before > 0:
                            order["q_ahead"] = max(
                                0.0,
                                order["q_ahead"] - cancel * (order["q_ahead"] / before))
                        # DEFENSIBLE: cancels assumed behind us -> no advance
                        if after == 0.0:
                            # our level was wiped out entirely
                            order = None
            # touch moved -> CANCEL + NEW at the back of the new level
            if order is not None and not desync:
                bk = bids if is_bid else asks
                if bk:
                    touch = max(bk) if is_bid else min(bk)
                    if touch != order["px"]:
                        log["reprices"] += 1
                        place(t)
            if order is None and seeded and not desync and inwin(t or 0):
                place(t)
            continue

        if k != "trade":
            continue
        px, qty = float(r["price"]), float(r["qty"])
        bim = r.get("buyer_is_maker")
        if t is not None and inwin(t):
            if bim is passive:
                log["eligible_qty"] += qty
        if order is None or desync or bim is not passive:
            continue
        P = order["px"]
        through = (px < P) if is_bid else (px > P)
        at = (px == P)
        if at:
            log["at_level_qty"] += qty
        if through:
            got = min(qty, order["rem"])
            if got > 0:
                order["rem"] -= got
                log["through_qty"] += got
                fills.append({"t": t, "px": P, "qty": got, "basis": "through",
                              "rest_s": (t - order["t"]) if t and order["t"] else None,
                              "side": side})
                if order["rem"] <= 0:
                    order = None
            continue
        if not at:
            continue
        take = min(qty, order["q_ahead"])
        order["q_ahead"] -= take
        over = qty - take
        if over > 0:
            got = min(over, order["rem"])
            if got > 0:
                order["rem"] -= got
                fills.append({"t": t, "px": P, "qty": got, "basis": "depletion",
                              "rest_s": (t - order["t"]) if t and order["t"] else None,
                              "side": side})
                if order["rem"] <= 0:
                    order = None
    return fills, log, marks


def adverse(fills, marks, side):
    """Mark move after each fill at fixed horizons, signed so POSITIVE = the
    mark moved AGAINST the maker (toxic)."""
    out = defaultdict(list)
    if not marks:
        return out
    ms = sorted(marks)
    ts = [x[0] for x in ms]
    import bisect
    for f in fills:
        t0 = f["t"]
        if t0 is None:
            continue
        i0 = bisect.bisect_right(ts, t0) - 1
        if i0 < 0:
            continue
        m0 = ms[i0][1]
        for h in HORIZONS:
            j = bisect.bisect_right(ts, t0 + h) - 1
            if j < 0 or ts[j] <= t0:
                continue
            m1 = ms[j][1]
            # a BUY maker is hurt when the mark FALLS; a SELL maker when it RISES
            adv = (m0 - m1) if side == "buy" else (m1 - m0)
            out[h].append(adv)
    return out


def main():
    glob_pat = sys.argv[1] if len(sys.argv) > 1 else "data/gemini_hifi/hifi_*.jsonl"
    asset = sys.argv[2] if len(sys.argv) > 2 else "BTC"
    dur_s = 300.0
    by_market, windows = load(glob_pat)

    rows = []
    for m, evs in by_market.items():
        if f"{asset}05M" not in m:
            continue
        meta = windows.get(m) or {}
        expiry = meta.get("expiry_t")
        evs.sort(key=lambda r: (r.get("t") or 0))
        inw = [e for e in evs
               if e.get("t") and expiry and (expiry - dur_s) <= e["t"] <= expiry]
        tr = [e for e in inw if e["kind"] == "trade"]
        bk = [e for e in inw if e["kind"] in ("depth_update", "depth_snapshot")]
        if not bk:
            continue
        cov = max(e["t"] for e in bk) - min(e["t"] for e in bk)
        rec = {"market": m, "window": meta.get("window"), "cov_s": round(cov, 1),
               "book_events_in": len(bk), "trades_in": len(tr),
               "contracts_in": round(sum(float(e["qty"]) for e in tr), 2),
               "book_events_per_min": round(len(bk) / (cov / 60.0), 1) if cov > 0 else None}
        for tier in TIERS:
            for clip in CLIPS:
                tot = 0.0
                nf = 0
                thr = 0.0
                rest = []
                qa = []
                elig = 0.0
                atl = 0.0
                adv_acc = defaultdict(list)
                for side in ("buy", "sell"):
                    fl, lg, mk = run_side(evs, side, clip, tier, expiry, dur_s)
                    fl = [f for f in fl if f["t"] and expiry
                          and (expiry - dur_s) <= f["t"] <= expiry]
                    tot += sum(f["qty"] for f in fl)
                    nf += len(fl)
                    thr += sum(f["qty"] for f in fl if f["basis"] == "through")
                    rest += [f["rest_s"] for f in fl if f["rest_s"] is not None]
                    qa += lg["queue_ahead"]
                    # eligible is per-side passive flow; summing the two
                    # sides counts each print once (a print is passive on
                    # exactly one side), so this is a true per-window total
                    elig += lg["eligible_qty"]
                    atl += lg["at_level_qty"]
                    for h, v in adverse(fl, mk, side).items():
                        adv_acc[h] += v
                rec[f"{tier}_q{clip}_qty"] = round(tot, 2)
                rec[f"{tier}_q{clip}_fills"] = nf
                rec[f"{tier}_q{clip}_through_qty"] = round(thr, 2)
                rec[f"{tier}_q{clip}_rest_s"] = rest
                rec[f"{tier}_q{clip}_adv"] = {str(h): v for h, v in adv_acc.items()}
                if tier == "DEFENSIBLE" and clip == 1:
                    rec["eligible_qty"] = round(elig, 2)
                    rec["at_level_qty"] = round(atl, 2)
                    rec["queue_ahead"] = qa
        rows.append(rec)

    rows.sort(key=lambda r: r["window"] or "")
    clean = [{k: v for k, v in r.items()
              if not (k.endswith("_rest_s") or k.endswith("_adv") or k == "queue_ahead")}
             for r in rows]
    out = Path("analysis/gemini/microstructure/p3_hifi_capture.json")
    out.write_text(json.dumps(clean, indent=1))

    n = len(rows)
    print(f"=== FRESH event-level cohort | {asset}05M | {glob_pat}")
    print(f"windows with in-window book events : {n}")
    if not n:
        print("no windows yet"); return
    wc = [r for r in rows if r["cov_s"] >= 240]
    print(f"  with >= 240 s in-window coverage : {len(wc)}")
    tot_c = sum(r["contracts_in"] for r in wc)
    tot_t = sum(r["trades_in"] for r in wc)
    elig = sum(r.get("eligible_qty", 0) for r in wc)
    print(f"  trades / contracts (covered)     : {tot_t} / {round(tot_c,2)}")
    print(f"  passive-side eligible contracts  : {round(elig,2)}")
    print()
    print("OPPORTUNITY, per well-covered window:")
    for f in ("contracts_in", "trades_in", "book_events_per_min", "cov_s"):
        v = [r[f] for r in wc if r[f] is not None]
        print(f"  {f:22s} p50={pct(v,0.5)} p90={pct(v,0.9)} max={max(v) if v else None}")
    qa = [x for r in wc for x in r.get("queue_ahead", [])]
    if qa:
        print(f"  queue_ahead at placement  " + "  ".join(
            f"p{int(q*100)}={pct(qa,q):.1f}" for q in (0.1, 0.5, 0.9)) + f"  max={max(qa):.1f}")
    print()
    print(f"{'tier/clip':20s} {'fills':>6s} {'qty':>10s} {'%total':>8s} {'%elig':>7s} "
          f"{'through':>9s} {'win_any':>8s} {'rest_s_p50':>11s}")
    print("-" * 90)
    for tier in TIERS:
        for clip in CLIPS:
            q = sum(r[f"{tier}_q{clip}_qty"] for r in wc)
            fl = sum(r[f"{tier}_q{clip}_fills"] for r in wc)
            th = sum(r[f"{tier}_q{clip}_through_qty"] for r in wc)
            wa = sum(1 for r in wc if r[f"{tier}_q{clip}_qty"] > 0)
            rest = [x for r in wc for x in r[f"{tier}_q{clip}_rest_s"]]
            rp = pct(rest, 0.5)
            print(f"{tier+' q'+str(clip):20s} {fl:6d} {q:10.2f} "
                  f"{(q/tot_c*100 if tot_c else 0):7.2f}% "
                  f"{(q/elig*100 if elig else 0):6.2f}% {th:9.2f} "
                  f"{wa:5d}/{len(wc):<3d} {(f'{rp:.1f}' if rp is not None else '-'):>11s}")
    print()
    print("ADVERSE SELECTION after DEFENSIBLE fills (positive = mark moved AGAINST maker):")
    for clip in CLIPS:
        acc = defaultdict(list)
        for r in wc:
            for h, v in r[f"DEFENSIBLE_q{clip}_adv"].items():
                acc[h] += v
        parts = []
        for h in HORIZONS:
            v = acc.get(str(h), [])
            parts.append(f"{int(h)}s n={len(v)} p50="
                         f"{(f'{pct(v,0.5):+.4f}' if v else '-')}")
        print(f"  q{clip:<3d} " + "  ".join(parts))
    print()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
