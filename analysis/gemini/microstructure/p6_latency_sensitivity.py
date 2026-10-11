"""P6: SEPARATE counterfactual latency / early-queue diagnostic.

THIS DOES NOT ALTER OR INVALIDATE THE FROZEN DECISION-GATE ANALYSIS.
The back-of-queue result in DECISION_GATE.md stands as committed. This asks a
different question on the same event-level tape:

    If a maker could arrive EARLIER at a newly formed touch, would defensible
    capture and adverse selection materially change?

The latencies simulated (25/50/100/250/500 ms) are COUNTERFACTUAL SENSITIVITY
POINTS ONLY. Nothing here claims our system can achieve them.

NEW-TOUCH TAXONOMY -- the structural heart of the question
---------------------------------------------------------
A price can become the touch two ways, and they are NOT equivalent:

  NEW_PRICE  the price was ABSENT from the book and appears as the new touch.
             Its first observable size is the size that was posted. Early
             arrival can only matter here.
  PROMOTED   the price was ALREADY resting deeper in the book and becomes the
             touch when better levels vanish. Its queue was built long before
             we could have arrived, so no achievable latency helps.

Reported separately. Pooling them would hide the answer.

QUEUE DISCIPLINE -- unchanged from the frozen analysis
-----------------------------------------------------
  * No touch = fill. Every fill requires an actual print.
  * Queue ahead is DISPLAYED size, which remains a PROXY, never proven queue
    position. Hidden/iceberg/refreshing size is unobservable.
  * A trade at our price consumes the queue ahead first; only overflow
    reaches our clip.
  * A print strictly THROUGH our price fills us regardless of queue, and is
    decomposed out separately.
  * A size DECREASE is only credited as volume ahead of us when the sequence
    data supports it being a TRADE. A cancellation is NOT treated as volume
    ahead of us: under DEFENSIBLE it does not advance us at all.
  * Fixed horizons 5/15/30 s for post-fill move, identical to the frozen run.
  * No threshold fitting anywhere.
"""
from __future__ import annotations

import bisect
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SYM = re.compile(r"^GEMI-((?:BTC|ETH)05M\d{10})-UP$")
CLIPS = (1, 5, 10, 25)
LATENCIES_MS = (25, 50, 100, 250, 500)
PROBE_MS = (0, 50, 100, 250, 500, 1000)
HORIZONS = (5.0, 15.0, 30.0)
# fixed DESCRIPTIVE reference levels for "before the large queue forms".
# These are reporting buckets, not fitted thresholds.
QUEUE_REFS = (10.0, 100.0, 1000.0)


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def load(asset="BTC"):
    ev = defaultdict(list)
    win = {}
    for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "window":
                win[r["market"]] = r
            elif k in ("depth_update", "depth_snapshot", "trade"):
                m = r.get("market") or ""
                h = SYM.match(m)
                if h and h.group(1).startswith(asset):
                    ev[m].append(r)
    for m in ev:
        ev[m].sort(key=lambda r: (r.get("t") or 0))
    return ev, win


def apply_side(book, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
        else:
            book[p] = s


def analyse_market(rows, meta, side):
    """Return new-touch records for one market and one side."""
    is_bid = side == "buy"
    passive = True if is_bid else False
    expiry = (meta or {}).get("expiry_t")
    start = (expiry - 300.0) if expiry else None

    bids, asks = {}, {}
    seeded, desync, last_u = False, True, None
    prev_touch = None
    # timeline of (t, book_side_snapshot_of_our_price) is rebuilt lazily;
    # instead we record a full event log and replay per new-touch event.
    log = []     # (t, kind, payload) with book state captured as needed
    touches = []
    marks = []

    # First pass: build a replayable list of (t, side_levels_after, trades)
    for r in rows:
        k, t = r["kind"], r.get("t")
        if k == "depth_snapshot":
            if not seeded or desync:
                bids, asks = {}, {}
                apply_side(bids, r.get("bids"))
                apply_side(asks, r.get("asks"))
                last_u = r.get("lastUpdateId")
                seeded, desync = True, False
                prev_touch = (max(bids) if is_bid and bids
                              else min(asks) if (not is_bid) and asks else None)
            continue
        if k == "depth_update":
            if not seeded:
                continue
            U, u = r.get("U"), r.get("u")
            if u is not None and last_u is not None and u <= last_u:
                continue
            if U is not None and last_u is not None and U != last_u:
                desync = True
            before_has = dict(bids if is_bid else asks)
            apply_side(bids, r.get("b"))
            apply_side(asks, r.get("a"))
            last_u = u
            if bids and asks:
                marks.append((t, (max(bids) + min(asks)) / 2.0))
            book = bids if is_bid else asks
            touch = (max(book) if is_bid else min(book)) if book else None
            log.append(("book", t, touch, dict(book), desync))
            if (touch is not None and touch != prev_touch and not desync
                    and (start is None or (start <= t <= expiry))):
                kind = "NEW_PRICE" if touch not in before_has else "PROMOTED"
                touches.append({"t": t, "px": touch, "side": side,
                                "formation": kind,
                                "size_first": book[touch],
                                "idx": len(log) - 1})
            prev_touch = touch
            continue
        if k == "trade":
            try:
                log.append(("trade", t, float(r["price"]), float(r["qty"]),
                            r.get("buyer_is_maker")))
            except Exception:
                pass
    return touches, log, marks, passive, expiry, start


def probe_sizes(log, tr, px):
    """Displayed size at `px` at t0 + each PROBE_MS, plus a decomposition of
    how the level changed, as far as the feed permits."""
    t0 = tr["t"]
    out = {"size_at_%dms" % 0: tr["size_first"]}
    adds = cancels = execs = 0.0
    last = tr["size_first"]
    probe_i = 1
    targets = [t0 + p / 1000.0 for p in PROBE_MS[1:]]
    cur = tr["size_first"]
    ti = 0
    for rec in log[tr["idx"] + 1:]:
        t = rec[1]
        while ti < len(targets) and t > targets[ti]:
            out["size_at_%dms" % PROBE_MS[ti + 1]] = cur
            ti += 1
        if ti >= len(targets) and t > targets[-1]:
            break
        if rec[0] == "book":
            book = rec[3]
            new = book.get(px, 0.0)
            if new > cur:
                adds += new - cur
            cur = new
        else:
            _, _, p, q, bim = rec
            if p == px:
                execs += q
    while ti < len(targets):
        out["size_at_%dms" % PROBE_MS[ti + 1]] = cur
        ti += 1
    # cancellations = total decrease not explained by executions
    total_dec = max(0.0, tr["size_first"] + adds - cur)
    cancels = max(0.0, total_dec - execs)
    out.update({"adds": round(adds, 2), "cancels": round(cancels, 2),
                "execs": round(execs, 2)})
    return out


def simulate_arrival(log, tr, lat_ms, clip, passive, expiry, start):
    """Arrive at t0+lat, join the BACK of the then-displayed queue, FIFO."""
    t0 = tr["t"]
    t_arr = t0 + lat_ms / 1000.0
    px = tr["px"]
    is_bid = tr["side"] == "buy"
    # displayed size at px at arrival
    cur = tr["size_first"]
    i = tr["idx"] + 1
    while i < len(log) and log[i][1] <= t_arr:
        if log[i][0] == "book":
            cur = log[i][3].get(px, 0.0)
        i += 1
    if cur <= 0.0:
        return None          # level gone before we could arrive
    q_ahead = cur
    rem = float(clip)
    fills = []
    flow_after = 0.0
    # walk forward from arrival until the level stops being the touch
    while i < len(log):
        rec = log[i]
        t = rec[1]
        if expiry is not None and t > expiry:
            break
        if rec[0] == "book":
            book = rec[3]
            if not book:
                break
            touch = max(book) if is_bid else min(book)
            if touch != px:
                break        # touch moved: our order is stale -> stop (no reprice here)
            new = book.get(px, 0.0)
            if new < q_ahead:
                # decrease: only trades advance us, and trades are handled
                # below. A cancellation does NOT advance us under DEFENSIBLE.
                pass
            i += 1
            continue
        _, _, p, q, bim = rec
        if bim is not passive:
            i += 1
            continue
        flow_after += q
        through = (p < px) if is_bid else (p > px)
        at = (p == px)
        if through:
            got = min(q, rem)
            if got > 0:
                rem -= got
                fills.append({"t": t, "qty": got, "basis": "through"})
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
                    fills.append({"t": t, "qty": got, "basis": "same_price"})
                    if rem <= 0:
                        break
        i += 1
    return {"q_ahead_at_arrival": q_ahead if not fills else cur,
            "q_ahead_initial": cur, "fills": fills,
            "flow_after": flow_after}


def adverse_at(marks, t0, side):
    if not marks:
        return {}
    ts = [m[0] for m in marks]
    i0 = bisect.bisect_right(ts, t0) - 1
    if i0 < 0:
        return {}
    m0 = marks[i0][1]
    out = {}
    for h in HORIZONS:
        j = bisect.bisect_right(ts, t0 + h) - 1
        if j < 0 or ts[j] <= t0:
            continue
        m1 = marks[j][1]
        out[h] = (m0 - m1) if side == "buy" else (m1 - m0)
    return out


def main():
    asset = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    ev, win = load(asset)

    all_touch = []
    per_lat = {(l, c): {"n": 0, "fills": 0, "qty": 0.0, "through": 0.0,
                        "same": 0.0, "flow": 0.0, "qa": [], "wins": set(),
                        "adv": defaultdict(list)}
               for l in LATENCIES_MS for c in CLIPS}

    for m, rows in ev.items():
        meta = win.get(m) or {}
        for side in ("buy", "sell"):
            touches, log, marks, passive, expiry, start = analyse_market(rows, meta, side)
            for tr in touches:
                pr = probe_sizes(log, tr, tr["px"])
                rec = {"market": m, "window": meta.get("window"),
                       "side": side, "t": tr["t"], "px": tr["px"],
                       "formation": tr["formation"], **pr}
                all_touch.append(rec)
                for lat in LATENCIES_MS:
                    for clip in CLIPS:
                        s = simulate_arrival(log, tr, lat, clip, passive,
                                             expiry, start)
                        if s is None:
                            continue
                        a = per_lat[(lat, clip)]
                        a["n"] += 1
                        a["qa"].append(s["q_ahead_initial"])
                        a["flow"] += s["flow_after"]
                        for f in s["fills"]:
                            a["fills"] += 1
                            a["qty"] += f["qty"]
                            if f["basis"] == "through":
                                a["through"] += f["qty"]
                            else:
                                a["same"] += f["qty"]
                            a["wins"].add(meta.get("window"))
                            for h, v in adverse_at(marks, f["t"], side).items():
                                a["adv"][h].append(v)

    out = Path("analysis/gemini/microstructure/p6_latency_touches.json")
    out.write_text(json.dumps(all_touch, indent=1))

    # ---------- STRUCTURAL QUESTION ----------
    print("=" * 78)
    print("P6 LATENCY / EARLY-QUEUE DIAGNOSTIC -- SEPARATE FROM THE FROZEN")
    print("DECISION GATE, WHICH IS UNCHANGED. Latencies are COUNTERFACTUAL.")
    print("=" * 78)
    nt = [r for r in all_touch if r["formation"] == "NEW_PRICE"]
    pm = [r for r in all_touch if r["formation"] == "PROMOTED"]
    print(f"\nnew-touch formation events: {len(all_touch)}"
          f"   NEW_PRICE={len(nt)}  PROMOTED={len(pm)}")
    print("\n--- STRUCTURAL: is the giant queue ALREADY THERE at first sight? ---")
    for label, grp in (("NEW_PRICE", nt), ("PROMOTED", pm)):
        if not grp:
            continue
        print(f"\n{label}  (n={len(grp)})")
        print("  displayed size at the level, by probe offset:")
        for p in PROBE_MS:
            v = [r.get("size_at_%dms" % p) for r in grp
                 if r.get("size_at_%dms" % p) is not None]
            if v:
                print(f"    +{p:4d} ms  p10={pct(v,.1):9.1f} p50={pct(v,.5):9.1f} "
                      f"p90={pct(v,.9):10.1f} max={max(v):10.1f}")
        g0 = [r["size_at_0ms"] for r in grp]
        g1 = [r.get("size_at_1000ms") for r in grp if r.get("size_at_1000ms")]
        print(f"  size already >= {QUEUE_REFS[1]:.0f} at FIRST observation : "
              f"{sum(1 for x in g0 if x >= QUEUE_REFS[1])}/{len(g0)} "
              f"({sum(1 for x in g0 if x >= QUEUE_REFS[1])/len(g0)*100:.1f}%)")
        print(f"  size already >= {QUEUE_REFS[2]:.0f} at FIRST observation: "
              f"{sum(1 for x in g0 if x >= QUEUE_REFS[2])}/{len(g0)} "
              f"({sum(1 for x in g0 if x >= QUEUE_REFS[2])/len(g0)*100:.1f}%)")
        if g1:
            ratios = [r["size_at_0ms"] / r["size_at_1000ms"]
                      for r in grp if r.get("size_at_1000ms")]
            print(f"  size_at_0ms / size_at_1000ms : p10={pct(ratios,.1):.3f} "
                  f"p50={pct(ratios,.5):.3f} p90={pct(ratios,.9):.3f}")
            print("    (p50 ~1.0 => the queue is ALREADY FORMED at first sight;")
            print("     p50 << 1.0 => it builds over the following second)")
        adds = [r["adds"] for r in grp]
        can = [r["cancels"] for r in grp]
        exe = [r["execs"] for r in grp]
        print(f"  within +1000 ms: adds p50={pct(adds,.5):.1f}  "
              f"cancels p50={pct(can,.5):.1f}  execs p50={pct(exe,.5):.1f}")

    # ---------- LATENCY CURVE ----------
    print("\n--- LATENCY CURVE (all formation types pooled for capture) ---")
    hdr = (f"{'lat':>5s} {'clip':>5s} {'elig':>6s} {'qa_p10':>8s} {'qa_p50':>9s} "
           f"{'qa_p90':>10s} {'<=10':>6s} {'<=100':>6s} {'fills':>6s} "
           f"{'qty':>8s} {'cap%':>7s} {'thru':>7s} {'same':>7s} {'wins':>5s}")
    print(hdr); print("-" * len(hdr))
    for lat in LATENCIES_MS:
        for clip in CLIPS:
            a = per_lat[(lat, clip)]
            if not a["n"]:
                continue
            qa = a["qa"]
            f10 = sum(1 for x in qa if x <= QUEUE_REFS[0]) / len(qa) * 100
            f100 = sum(1 for x in qa if x <= QUEUE_REFS[1]) / len(qa) * 100
            cap = (a["qty"] / a["flow"] * 100) if a["flow"] else 0.0
            print(f"{lat:5d} {clip:5d} {a['n']:6d} {pct(qa,.1):8.1f} "
                  f"{pct(qa,.5):9.1f} {pct(qa,.9):10.1f} {f10:5.1f}% {f100:5.1f}% "
                  f"{a['fills']:6d} {a['qty']:8.2f} {cap:6.2f}% "
                  f"{a['through']:7.2f} {a['same']:7.2f} {len(a['wins']):5d}")

    print("\n--- POST-FILL MOVE at the FROZEN 5/15/30 s horizons "
          "(positive = AGAINST maker) ---")
    for lat in LATENCIES_MS:
        parts = []
        for clip in CLIPS:
            a = per_lat[(lat, clip)]
            seg = []
            for h in HORIZONS:
                v = a["adv"].get(h, [])
                seg.append(f"{int(h)}s:" + (f"{pct(v,.5):+.4f}(n={len(v)})"
                                            if v else "-"))
            parts.append(f"q{clip} " + " ".join(seg))
        print(f"  {lat:3d} ms  " + "  |  ".join(parts))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
