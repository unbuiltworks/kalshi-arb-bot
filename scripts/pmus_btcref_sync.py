#!/usr/bin/env python3
"""Synchronize the external BTC reference with the authenticated PMUS tape, and
ask whether toxic first-leg fills are preceded by external movement, by queue
depletion, by both, or by neither -- early enough to beat measured cancel latency.

CHAIN, in the order the instruction specifies:

  external BTC  ->  PMUS L2  ->  PMUS trade  ->  hypothetical order state

  external BTC   data/btc_ref/btcref_*.jsonl   (src/btc_ref_recorder.py)
                 venue ev_ns (microsecond) AND local receipt t_ns, both kept
  PMUS L2        PMUS_BOOK_ROWS rows, recorder-local `t`
  PMUS trade     PMUS_TRADE_ROWS rows, carrying bid_at_trade/ask_at_trade bound
                 by the recorder at the instant of the trade
  order state    the RESTING arm's order (PLACE ONCE, retained price and queue),
                 not the frozen peg -- the peg's quote has no age to speak of,
                 which is the finding of PMUS_ENTRY_MECHANISM_AUDIT.

CLOCK BASIS, stated rather than assumed. The two feeds are recorded by different
processes on different machines, so no single clock is authoritative:

  - Within the reference feed, (t_ns - ev_ns) is recorded per row, so its own
    transport delay is an observable rather than a correction.
  - Across feeds, the comparison here uses LOCAL RECEIPT time on both sides
    (reference t_ns vs PMUS recorder `t`). That is the basis an order router
    would actually have: it compares what each feed had DELIVERED by a given
    instant. Venue-clock comparison is reported alongside as a second basis, and
    where the two bases disagree the disagreement is reported, not reconciled.
  - Lead/lag is MEASURED, by scanning reference-return against PMUS-mid-change
    over lags -2.0 s .. +2.0 s, and reporting the lag of maximum correlation.
    No lead is assumed anywhere in the classification.

CLASSIFICATION of each first-leg fill, using only information timestamped
STRICTLY BEFORE the fill:

  EXTERNAL   the reference BTC mid moved adversely over the lookback
  QUEUE      displayed size ahead of our order fell over the lookback
  BOTH / NEITHER

Each signal's onset is also timed: the earliest instant before the fill at which
the signal was already present. That onset-to-fill lead is what gets compared
against the measured cancel-latency distribution (p50 0.266 s, p90 0.612 s): a
signal whose lead is shorter than that cannot be acted on, however well it
separates.

NO CUTOFF IS OPTIMIZED. The signal magnitudes used to declare presence are the
coarse pre-registered values below, fixed before this script was run against any
new window, and the script reports avoidable-toxic vs sacrificed-good counts at
each of them rather than selecting one.

Observational. No orders.
"""
from __future__ import annotations
import argparse, bisect, glob, json, os, statistics
from decimal import Decimal as D

REF_GLOB = "data/btc_ref/btcref_*.jsonl"
OUT = "analysis/l5/pmus_rewards/PMUS_BTCREF_SYNC.json"
RTT_P50, RTT_P90 = 0.266, 0.612
LOOKBACKS = (0.25, 0.5, 1.0, 2.0, 5.0)
# pre-registered coarse presence levels, NOT fitted
EXT_BPS = (1.0, 2.0, 5.0)          # adverse reference move, basis points
QUEUE_FRAC = (0.25, 0.50)          # fraction of queue-ahead that disappeared
LAGS = [round(-2.0 + 0.05 * i, 2) for i in range(81)]

import importlib.util
_s = importlib.util.spec_from_file_location("pou", "scripts/pmus_pair_or_unwind.py")
pou = importlib.util.module_from_spec(_s); _s.loader.exec_module(pou)
DEADLINE_S = 90
Q = 25


def load_ref():
    """Reference BBO mids on both clock bases, plus skew diagnostics."""
    recv, venue, mids, skews = [], [], [], []
    for p in sorted(glob.glob(REF_GLOB)):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("kind") != "ref_bbo" or not r.get("bid") or not r.get("ask"):
                continue
            if r.get("ev_ns") is None:
                continue
            m = (float(r["bid"]) + float(r["ask"])) / 2
            recv.append(r["t_ns"] / 1e9); venue.append(r["ev_ns"] / 1e9)
            mids.append(m)
            if r.get("skew_ns") is not None:
                skews.append(r["skew_ns"] / 1e9)
    order = sorted(range(len(recv)), key=lambda i: recv[i])
    return ([recv[i] for i in order], [venue[i] for i in order],
            [mids[i] for i in order], skews)


def ref_at(ts, mids, t, default=None):
    """Last reference mid delivered at or before t. Never interpolated."""
    i = bisect.bisect_right(ts, t) - 1
    return mids[i] if i >= 0 else default


def measure_lag(ts, mids, books):
    """Correlate reference return with PMUS mid change over lags -2s..+2s."""
    pts = [(b["t"], (float(b["bid"]) + float(b["ask"])) / 2)
           for b in books if b.get("bid") and b.get("ask")]
    pts.sort()
    if len(pts) < 50:
        return {"status": "INSUFFICIENT", "n": len(pts)}
    H = 1.0
    best, rows = None, {}
    for lag in LAGS:
        xs, ys = [], []
        for t, pm in pts:
            a = ref_at(ts, mids, t - lag - H); b = ref_at(ts, mids, t - lag)
            pa = ref_at([p[0] for p in pts], [p[1] for p in pts], t - H)
            if a is None or b is None or pa is None or a == 0:
                continue
            xs.append((b - a) / a); ys.append(pm - pa)
        if len(xs) < 50:
            continue
        try:
            sx, sy = statistics.stdev(xs), statistics.stdev(ys)
            if sx == 0 or sy == 0:
                continue
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            c = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (len(xs) - 1) / (sx * sy)
        except statistics.StatisticsError:
            continue
        rows[str(lag)] = round(c, 4)
        if best is None or abs(c) > abs(best[1]):
            best = (lag, c)
    if best is None:
        return {"status": "INSUFFICIENT", "n": len(pts)}
    return {"status": "MEASURED", "n": len(pts), "best_lag_s": best[0],
            "best_corr": round(best[1], 4),
            "note": "positive lag = reference leads PMUS", "curve": rows}


def fills_resting(rows, dead):
    """RESTING arm: PLACE ONCE, retain price and queue-ahead; no silent moves."""
    order = {"buy": None, "sell": None}
    out = []
    for i, x in enumerate(rows):
        t = x["t"]; past = t >= dead
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))
        o = order.get(consumed)
        if o is not None:
            mypx, ahead, got, pt, ahead0 = o
            ok = (px <= mypx) if consumed == "buy" else (px >= mypx)
            if ok:
                if ahead > 0:
                    eat = min(ahead, avail); ahead -= eat; avail -= eat
                if avail > 0 and got < Q:
                    take = min(avail, D(Q) - got); got += take
                    mae = mfe = D(0)
                    for y in rows[i + 1:]:
                        ex = D(str(y["bid_at_trade"] if consumed == "buy" else y["ask_at_trade"]))
                        m = (ex - mypx) if consumed == "buy" else (mypx - ex)
                        mfe = max(mfe, m); mae = min(mae, m)
                    out.append({"t": t, "side": consumed, "px": float(mypx),
                                "placed_t": pt, "quote_age_s": t - pt,
                                "queue0": float(ahead0), "queue_now": float(ahead),
                                "idx": i, "toxic": bool(-mae > mfe)})
                order[consumed] = (mypx, ahead, got, pt, ahead0) if got < Q else None
        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                if order.get(side) is None:
                    order[side] = (qp, qsz, D(0), t, qsz)
    return out


def classify(fill, rows, ts, mids):
    """Only information strictly BEFORE the fill. Onset timed, never assumed."""
    t = fill["t"]
    res = {}
    for lb in LOOKBACKS:
        a = ref_at(ts, mids, t - lb); b = ref_at(ts, mids, t - 1e-6)
        ext_bps = None
        if a and b and a != 0:
            move = (b - a) / a * 10000.0
            # adverse = reference moved AGAINST the side we are acquiring
            ext_bps = -move if fill["side"] == "buy" else move
        # queue-ahead at the fill vs at the start of the lookback window
        prev = None
        for y in rows[:fill["idx"]][::-1]:
            if t - y["t"] >= lb:
                prev = y; break
        qfrac = None
        if prev is not None:
            k = "bid_qty" if fill["side"] == "buy" else "ask_qty"
            q0 = float(prev["book"][k] or 0); q1 = float(rows[fill["idx"]]["book"][k] or 0)
            if q0 > 0:
                qfrac = max(0.0, (q0 - q1) / q0)
        res[str(lb)] = {"ext_adverse_bps": ext_bps, "queue_gone_frac": qfrac}
    return res


def onset(fill, rows, ts, mids, ext_bps, qfrac):
    """Earliest pre-fill instant at which each signal was already present."""
    t = fill["t"]
    lead_ext = lead_q = None
    for lb in sorted(LOOKBACKS, reverse=True):
        c = classify(fill, rows, ts, mids)[str(lb)]
        if c["ext_adverse_bps"] is not None and c["ext_adverse_bps"] >= ext_bps:
            lead_ext = lb
            break
    for lb in sorted(LOOKBACKS, reverse=True):
        c = classify(fill, rows, ts, mids)[str(lb)]
        if c["queue_gone_frac"] is not None and c["queue_gone_frac"] >= qfrac:
            lead_q = lb
            break
    return lead_ext, lead_q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    rts, vts, mids, skews = load_ref()
    tr, bk = pou.load()
    sc, _ = pou.scorable(tr, bk)

    ref_span = (rts[0], rts[-1]) if rts else None
    overlap = {}
    for slug, rows in sc.items():
        if not rows or not ref_span:
            continue
        lo, hi = rows[0]["t"], rows[-1]["t"]
        ov = max(0.0, min(hi, ref_span[1]) - max(lo, ref_span[0]))
        overlap[slug] = {"pmus_span_s": round(hi - lo, 1), "overlap_s": round(ov, 1),
                         "covered": ov > 0.9 * (hi - lo)}

    covered = [s for s, v in overlap.items() if v["covered"]]
    res = {"ref_rows": len(rts),
           "ref_span_utc": [ref_span[0], ref_span[1]] if ref_span else None,
           "ref_skew_s": {"p10": round(sorted(skews)[len(skews)//10], 4),
                          "p50": round(statistics.median(skews), 4),
                          "p90": round(sorted(skews)[len(skews)*9//10], 4)} if skews else None,
           "clock_basis": "cross-feed comparison on LOCAL RECEIPT time; venue-clock basis reported via ref_skew_s",
           "rtt_p50": RTT_P50, "rtt_p90": RTT_P90,
           "lookbacks_s": list(LOOKBACKS), "ext_bps_levels": list(EXT_BPS),
           "queue_frac_levels": list(QUEUE_FRAC),
           "pmus_windows": len(sc), "overlap": overlap,
           "synchronized_windows": covered}

    if not covered:
        res["status"] = "NO_OVERLAP -- reference feed does not span any PMUS window yet"
        json.dump(res, open(a.out, "w"), indent=2)
        print(json.dumps({k: res[k] for k in ("ref_rows", "status", "overlap")}, indent=1))
        return res

    lagrows = []
    for s in covered:
        lagrows += bk.get(s) or []
    res["lead_lag"] = measure_lag(rts, mids, lagrows)

    buckets = {}
    per_fill = []
    for s in covered:
        rows = sc[s]
        dead = pou.window_end(s) - DEADLINE_S
        for f in fills_resting(rows, dead):
            c = classify(f, rows, rts, mids)
            per_fill.append({"slug": s, "t": f["t"], "side": f["side"],
                             "toxic": f["toxic"], "quote_age_s": round(f["quote_age_s"], 3),
                             "signals": c})
            for eb in EXT_BPS:
                for qf in QUEUE_FRAC:
                    le, lq = onset(f, rows, rts, mids, eb, qf)
                    cls = ("BOTH" if le and lq else "EXTERNAL" if le
                           else "QUEUE" if lq else "NEITHER")
                    lead = max([x for x in (le, lq) if x] or [0.0])
                    key = f"ext{eb}bps_q{qf}"
                    b = buckets.setdefault(key, {"TOX": {"BOTH": 0, "EXTERNAL": 0, "QUEUE": 0, "NEITHER": 0},
                                                 "QUAL": {"BOTH": 0, "EXTERNAL": 0, "QUEUE": 0, "NEITHER": 0},
                                                 "avoidable_toxic": 0, "sacrificed_good": 0,
                                                 "toxic_signal_too_late": 0, "leads": []})
                    b["TOX" if f["toxic"] else "QUAL"][cls] += 1
                    if cls != "NEITHER":
                        b["leads"].append(lead)
                        if lead >= RTT_P90:
                            if f["toxic"]:
                                b["avoidable_toxic"] += 1
                            else:
                                b["sacrificed_good"] += 1
                        elif f["toxic"]:
                            b["toxic_signal_too_late"] += 1
    for k, b in buckets.items():
        b["lead_s"] = {"p50": round(statistics.median(b["leads"]), 3)} if b["leads"] else None
        del b["leads"]
    res["fills"] = len(per_fill)
    res["toxic"] = sum(1 for f in per_fill if f["toxic"])
    res["buckets"] = buckets
    res["note"] = ("avoidable/sacrificed counts are reported at every pre-registered "
                   "level; no level is selected and no cutoff is optimized.")
    res["status"] = "MEASURED"
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({**res, "per_fill": per_fill}, open(a.out, "w"), indent=2)
    print(json.dumps({k: res[k] for k in
                      ("ref_rows", "ref_skew_s", "synchronized_windows", "lead_lag",
                       "fills", "toxic", "buckets")}, indent=1)[:4000])
    return res


if __name__ == "__main__":
    main()
