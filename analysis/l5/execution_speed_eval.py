#!/usr/bin/env python3
"""Execution-speed analysis of the L5 pairing engine, from existing tapes.

Measures the six stages asked for, and keeps OUR reaction time strictly
apart from QUEUE WAIT. The 20-34s between paired fills is a resting maker
order waiting for the market to come to it -- it is not latency, and
nothing here treats it as such.
"""
import json, glob, re, statistics as st, datetime as dt
from decimal import Decimal as D

TAPES = sorted(glob.glob("analysis/l5/residual_exit/preserved/btc15m_*.ndjson"))

def t_of(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def load(tape):
    out = []
    for ln in open(tape):
        d = json.loads(ln)
        try:
            b = json.loads(d["body"], strict=False)
        except Exception:
            b = {}
        out.append((t_of(d["ts"]), d.get("label"), b))
    return sorted(out, key=lambda r: r[0])

def q(vals, name, unit="s"):
    if not vals:
        print(f"  {name:46} n=0")
        return
    vals = sorted(vals)
    def pct(p):
        return vals[min(len(vals) - 1, int(p * (len(vals) - 1)))]
    print(f"  {name:46} n={len(vals):5d}  "
          f"min {vals[0]:7.3f}  p50 {pct(.5):7.3f}  p90 {pct(.9):7.3f}  "
          f"max {vals[-1]:8.3f} {unit}")

def main():
    stream_age, book_age, frame_age = [], [], []
    dec_gap, samp_gap, poll_gap = [], [], []
    presub = {}        # coid -> (t, slug, own_px, qty, queue_ahead, level_depth)
    acks, fills = {}, {}
    sub_decision = []  # decision-row ts for ORDER:SUBMITTED, by coid

    for tape in TAPES:
        rows = load(tape)
        last_dec = last_samp = None
        poll_last = {}
        for ts, lab, b in rows:
            if lab == "L5_LIVE_DECISION":
                sa = b.get("stream_age_s")
                if isinstance(sa, (int, float)):
                    stream_age.append(float(sa))
                if last_dec is not None and ts - last_dec > 0.5:
                    dec_gap.append(ts - last_dec)
                if last_dec is None or ts - last_dec > 0.5:
                    last_dec = ts
                o = b.get("order") or {}
                if o.get("submitted") is True and o.get("client_order_id"):
                    sub_decision.append((o["client_order_id"], ts))
            elif lab == "L5_RESIDUAL_BOOK_SAMPLE":
                ra = ((b.get("book") or {}).get("sync") or {}).get("row_age_s")
                if isinstance(ra, (int, float)):
                    book_age.append(float(ra))
                if last_samp is not None:
                    samp_gap.append(ts - last_samp)
                last_samp = ts
            elif lab == "L5_FILL_CALIB_PRESUBMIT":
                bk = b.get("book") or {}
                qu = b.get("queue") or {}
                for k, acc in (("stream_age_s", stream_age), ("age_s", None)):
                    v = bk.get(k)
                    if acc is not None and isinstance(v, (int, float)):
                        acc.append(float(v))
                fa = qu.get("frame_age_s")
                if isinstance(fa, (int, float)):
                    frame_age.append(float(fa))
                if b.get("coid") and b.get("t"):
                    presub[b["coid"]] = dict(
                        t=float(b["t"]), read_t=b.get("read_t"),
                        slug=b.get("slug"), own_px=b.get("own_px"),
                        qty=b.get("qty"),
                        queue_ahead=qu.get("queue_ahead"),
                        level_depth=qu.get("level_depth"),
                        pos=qu.get("our_position_within_level"))
            elif lab == "L5_LIVE_LIFECYCLE":
                oid, state = b.get("venue_order_id"), b.get("state")
                coid = b.get("coid")
                if oid:
                    k = poll_last.get(oid)
                    if k is not None and ts - k > 0.5:
                        poll_gap.append(ts - k)
                    poll_last[oid] = ts
                if state == "ORDER_STATE_NEW" and coid and coid not in acks:
                    acks[coid] = ts
                if (state == "ORDER_STATE_FILLED" and b.get("terminal")
                        and coid and coid not in fills):
                    fills[coid] = (ts, b.get("cum_qty"), b.get("avg_px"))

    print("=" * 78)
    print("1. MARKET-DATA LATENCY  (age of the book the decision actually used)")
    print("=" * 78)
    q(stream_age, "stream_age_s / book age at decision+presubmit")
    q(book_age, "residual-sample book row_age_s")
    q(frame_age, "queue ladder frame_age_s at presubmit")

    print()
    print("=" * 78)
    print("6. SCHEDULING / POLLING CADENCE  (gaps between our own observations)")
    print("=" * 78)
    q(dec_gap, "gap between L5_LIVE_DECISION evaluations")
    q(samp_gap, "gap between residual book samples (configured 5.0s)")
    q(poll_gap, "gap between order-state polls per order")

    print()
    print("=" * 78)
    print("3. SUBMISSION LATENCY  (presubmit -> first observed venue ack)")
    print("=" * 78)
    print("  venue ack time is NOT published to us; ORDER_STATE_NEW is the")
    print("  first time OUR POLL saw the order, so every figure below is an")
    print("  UPPER BOUND inflated by up to one poll interval.")
    sub_ack = []
    for coid, ps in presub.items():
        if coid in acks:
            sub_ack.append(acks[coid] - ps["t"])
    q(sub_ack, "presubmit -> ack OBSERVED (upper bound)")

    print()
    print("=" * 78)
    print("4. MAKER EXECUTION  (ack -> fill; this is QUEUE WAIT, not latency)")
    print("=" * 78)
    ack_fill, sub_fill = [], []
    print(f"  {'coid':28} {'own_px':>7} {'qty':>5} {'queue_ahead':>12} "
          f"{'lvl_depth':>10} {'sub->fill':>10}")
    for coid, ps in sorted(presub.items(), key=lambda kv: kv[1]["t"]):
        if coid not in fills:
            continue
        ft = fills[coid][0]
        sub_fill.append(ft - ps["t"])
        if coid in acks:
            ack_fill.append(ft - acks[coid])
        print(f"  {coid[:28]:28} {str(ps['own_px']):>7} {str(ps['qty']):>5} "
              f"{str(ps['queue_ahead']):>12} {str(ps['level_depth']):>10} "
              f"{ft - ps['t']:>10.2f}")
    q(ack_fill, "ack observed -> fill observed (queue wait)")
    q(sub_fill, "presubmit -> fill observed (total)")
    pos = {ps.get("pos") for ps in presub.values()}
    print(f"  our_position_within_level reported as: {pos}")

if __name__ == "__main__":
    main()
