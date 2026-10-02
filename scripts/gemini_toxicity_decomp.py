#!/usr/bin/env python3
"""Pre-fill toxicity decomposition: is adverse selection avoidable before a
fill, or intrinsic across observable states?

Replays the tape with the SAME fill rule as gemini_twosided_sim (Book, tick_of
and the rebate formula are imported from it so the two cannot drift), and for
every defensible fill records ONLY features observable STRICTLY BEFORE the
fill, then the forward 1s/5s/15s markout.

NO POST-FILL FEATURE MAY CLASSIFY A FILL. Every bucket key is computed from
state at or before the trade that filled us; the markout is the outcome and is
never fed back into a key.

Markout convention, per contract: a maker BUY at price p earns mid(t+h) - p;
a maker SELL earns p - mid(t+h). Negative = adverse.
"""
from __future__ import annotations
import json, sys, os, math, statistics, collections, bisect
from decimal import Decimal as D
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())
from scripts.gemini_twosided_sim import Book, rebate, TICK   # noqa: E402

OUT = "analysis/gemini/TOXICITY_DECOMP.json"
CANDLES = "data/btc15m/coinbase_1m.jsonl"
HORIZONS = (1, 5, 15)


def load_underlying():
    by = {}
    if not os.path.exists(CANDLES):
        return by
    for line in open(CANDLES):
        c = json.loads(line)
        by[int(c[0])] = float(c[4])
    return by


def ret_bp(by, ts, back_s=300):
    """Underlying move over the prior `back_s`, in bp. None if unsynchronized."""
    if not by:
        return None
    a = (int(ts) - back_s) // 60 * 60
    b = int(ts) // 60 * 60
    pa, pb = by.get(a), by.get(b)
    if not pa or not pb:
        return None
    return (pb - pa) / pa * 1e4


def main(tape, q=25, withdraw_s=90):
    und = load_underlying()
    frames = []
    meta = {}
    for line in open(tape):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "symbols":
            meta.update(r.get("meta") or {})
        elif r["kind"] == "frame" and r["f"].get("type") == "l2_updates":
            frames.append((datetime.fromisoformat(r["t"]), r["f"]))
    by_sym = collections.defaultdict(list)
    for ts, f in frames:
        if f.get("symbol"):
            by_sym[f["symbol"]].append((ts, f))

    fills = []
    for sym, seq in by_sym.items():
        exp = None
        m = meta.get(sym) or {}
        if m.get("event_expiry"):
            exp = datetime.fromisoformat(m["event_expiry"].replace("Z", "+00:00"))
        book = Book()
        inv = D(0)
        my = {"buy": None, "sell": None}
        mids = []                       # (ts, mid) for forward markouts
        flow = collections.deque()      # (ts, signed qty) recent trade flow
        midhist = collections.deque()   # (ts, mid) for recent price move
        withdrawn = False
        n_fill_in_window = 0

        for ts, f in seq:
            book.apply(f.get("changes") or [])
            bb, ba = book.bb(), book.ba()
            if bb is not None and ba is not None:
                mid = (bb + ba) / 2
                mids.append((ts, mid))
                midhist.append((ts, mid))
                while midhist and (ts - midhist[0][0]).total_seconds() > 30:
                    midhist.popleft()
            if exp and not withdrawn and (exp - ts).total_seconds() <= withdraw_s:
                withdrawn = True
                my = {"buy": None, "sell": None}

            for t in (f.get("trades") or []):
                try:
                    tpx = D(str(t["price"]))
                    tq = D(str(t.get("quantity") or t.get("amount")))
                except Exception:
                    continue
                side = str(t.get("side") or "").lower()
                if side == "sell":
                    consumed = "buy"
                elif side == "buy":
                    consumed = "sell"
                else:
                    continue
                flow.append((ts, float(tq) * (1 if side == "buy" else -1)))
                while flow and (ts - flow[0][0]).total_seconds() > 30:
                    flow.popleft()
                st = my.get(consumed)
                if not st:
                    continue
                mypx, ahead, got = st
                if consumed == "buy" and tpx > mypx:
                    continue
                if consumed == "sell" and tpx < mypx:
                    continue
                avail = tq
                # ---- PRE-FILL SNAPSHOT, taken before any state is mutated
                pre = {
                    "symbol": sym, "t": ts.isoformat(),
                    "side": "buy" if consumed == "buy" else "sell",
                    "px": float(mypx),
                    "spread": float(ba - bb) if (bb is not None and ba is not None) else None,
                    "queue_ahead": float(ahead),
                    "tte_s": (exp - ts).total_seconds() if exp else None,
                    "inv_before": float(inv),
                    "is_first_fill": n_fill_in_window == 0,
                    "mid_move_30s": (float(midhist[-1][1] - midhist[0][1])
                                     if len(midhist) > 1 else None),
                    "net_flow_30s": sum(x[1] for x in flow),
                    "under_ret_bp_300s": ret_bp(und, ts.timestamp()),
                }
                if ahead > 0:
                    eat = min(ahead, avail)
                    ahead -= eat
                    avail -= eat
                if avail > 0 and got < q:
                    take = min(avail, D(q) - got)
                    got += take
                    pre["qty"] = float(take)
                    # inventory-increasing or -reducing, from inv BEFORE the fill
                    signed = take if consumed == "buy" else -take
                    pre["reducing"] = bool(inv != 0 and (inv > 0) != (signed > 0))
                    inv += signed
                    n_fill_in_window += 1
                    fills.append((pre, ts, mypx, consumed, mids))
                my[consumed] = (mypx, ahead, got) if got < q else None

            if not withdrawn and bb is not None and ba is not None and ba > bb:
                skew = int(min(3, abs(inv) / max(D(1), D(q))))
                if inv > 0:
                    pb, pa = bb - skew * TICK, ba - min(skew, 1) * TICK
                elif inv < 0:
                    pb, pa = bb + min(skew, 1) * TICK, ba + skew * TICK
                else:
                    pb, pa = bb, ba
                for s_, px_ in (("buy", pb), ("sell", pa)):
                    cur = my.get(s_)
                    if cur is None or cur[0] != px_:
                        my[s_] = (px_, book.size_at(s_, px_), D(0))

    # ---- forward markouts
    recs = []
    for pre, ts, px, consumed, mids in fills:
        times = [m[0] for m in mids]
        row = dict(pre)
        for h in HORIZONS:
            i = bisect.bisect_left(times, ts)
            fut = None
            for j in range(i, len(mids)):
                if (mids[j][0] - ts).total_seconds() >= h:
                    fut = mids[j][1]
                    break
            row[f"mo{h}"] = (float(fut - px) if consumed == "buy"
                             else float(px - fut)) if fut is not None else None
        recs.append(row)
    print(f"defensible fills instrumented: {len(recs)}  (q={q}, withdraw={withdraw_s})")

    def bucket(name, keyfn):
        g = collections.defaultdict(list)
        for r in recs:
            k = keyfn(r)
            if k is None:
                continue
            g[k].append(r)
        print(f"\n--- {name}")
        print(f"{'bucket':>22s} {'n':>5s} " +
              " ".join(f"{'mo'+str(h)+'s':>10s} {'sd':>7s}" for h in HORIZONS))
        out = {}
        for k in sorted(g, key=lambda x: (str(type(x)), x)):
            rs = g[k]
            cells = []
            rec = {"n": len(rs)}
            for h in HORIZONS:
                v = [r[f"mo{h}"] for r in rs if r[f"mo{h}"] is not None]
                if len(v) >= 2:
                    mu = statistics.mean(v)
                    sd = statistics.stdev(v)
                    se = sd / math.sqrt(len(v))
                    cells.append(f"{mu:>+10.4f} {se:>7.4f}")
                    rec[f"mo{h}_mean"] = mu
                    rec[f"mo{h}_se"] = se
                    rec[f"mo{h}_n"] = len(v)
                else:
                    cells.append(f"{'-':>10s} {'-':>7s}")
            print(f"{str(k):>22s} {len(rs):>5d} " + " ".join(cells))
            out[str(k)] = rec
        return out

    def band(x, edges):
        if x is None:
            return None
        for e in edges:
            if x < e:
                return f"<{e}"
        return f">={edges[-1]}"

    res = {
        "tape": tape, "q": q, "withdraw_s": withdraw_s,
        "fills": len(recs),
        "note": "every bucket key is computed from state strictly before the "
                "fill; markout is the outcome and never feeds a key",
        "buckets": {
            "spread": bucket("spread", lambda r: band(r["spread"], [0.02, 0.05, 0.10, 0.20])),
            "queue_ahead": bucket("queue ahead", lambda r: band(r["queue_ahead"], [1, 25, 100, 500])),
            "tte_s": bucket("time to expiry (s)", lambda r: band(r["tte_s"], [60, 120, 180, 240])),
            "side": bucket("side", lambda r: r["side"]),
            "inv_before": bucket("inventory before", lambda r: band(abs(r["inv_before"]), [1, 10, 25])),
            "reducing": bucket("inventory-reducing", lambda r: r.get("reducing")),
            "first_fill": bucket("first fill in window", lambda r: r["is_first_fill"]),
            "mid_move_30s": bucket("contract mid move 30s", lambda r: band(r["mid_move_30s"], [-0.02, 0.0, 0.02])),
            "net_flow_30s": bucket("net trade flow 30s", lambda r: band(r["net_flow_30s"], [-100, 0, 100])),
            "under_ret_bp": bucket("underlying 300s ret bp", lambda r: band(r["under_ret_bp_300s"], [-10, 0, 10])),
        },
        "rows": recs,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2)
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "data/gemini_shadow/merged/canonical_5m_tape.jsonl"))
