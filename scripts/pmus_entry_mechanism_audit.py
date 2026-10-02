#!/usr/bin/env python3
"""Entry-mechanism audit + RESTING diagnostic arm, on the authenticated PMUS tape.

THE FROZEN BASELINE IS NOT TOUCHED. scripts/pmus_pair_or_unwind.py and every
frozen output stay exactly as they are. This file only observes what the frozen
entry mechanism does, and runs a SEPARATE arm with different entry mechanics.

WHAT PROMPTED IT. The pre-fill cancellability study (4cfc293f) found
quote_staleness identically 0.0000 at every horizon. The cause is this block in
the frozen arm:

    if cur is None or cur[0] != qp:
        entry[side] = (qp, qsz, D(0))

On every book event, if the touch has moved, the hypothetical entry is RELOCATED
to the new touch and its queue-ahead is RESET to the new touch size -- with no
cancellation, no latency, and no loss of anything. That is not a resting order.
It is a zero-latency peg that follows the touch, so it can never be stale, and
staleness measured on it is a tautology rather than a market observation.

ARM A -- PEG_AUDIT: measures what that peg actually does.
  replaces           how many times the entry price was moved
  quote_lifetime_s   how long a price survived before being moved
  age_at_fill_s      how long the filling quote had existed when it filled
  impossible_fills   fills whose quote was younger than the measured cancel
                     latency p50 (0.266 s) -- a real order could not yet have
                     been resting there, so these fills are an artifact of the
                     peg, not obtainable liquidity.
  chase_fraction     share of relocations that moved the quote TOWARD the last
                     trade price, i.e. followed the market rather than waiting.

ARM B -- RESTING: a genuinely resting order.
  PLACE ONCE at the touch, keep that price and that queue-ahead. A price change
  is NOT a move: it is a CANCEL event plus a NEW order, which loses all prior
  queue position (queue-ahead resets to the new level's displayed size). The arm
  never silently follows the touch. Orders leave only by:
     EXPIRE  -- the pre-registered TTL elapsed
     FILL    -- evidenced queue consumption, same rules as the frozen arm
  and after an EXPIRE a new order may be placed at the then-current touch.

  TTLs are pre-registered coarse values {5 s, 30 s, WINDOW}, not tuned. WINDOW
  means place once and never re-place: the strictest resting behaviour.

FILL DISCIPLINE, identical to every prior study and not relaxed here: no
touch=fill; only a scorable trade whose `dir` says it consumed our side can
fill; queue-ahead is the displayed touch size from the preceding book row;
nothing rests beyond the touch.

TOXICITY LABEL. This arm runs no exit strategy, so FORCED cannot be its label.
It uses the stated proxy MAE > MFE over the remainder of the window on the
filled side -- declared here because it is a different label from the frozen
study's and the two counts are therefore not interchangeable.

Diagnostic only. No thresholds fitted. No orders.
"""
from __future__ import annotations
import json, os, bisect, collections, statistics
from decimal import Decimal as D

TAPE = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl"
OUT = "analysis/l5/pmus_rewards/PMUS_ENTRY_MECHANISM_AUDIT.json"
FRESH_MS = 2000
DEADLINE_S = 90
Q = 25
RTT_P50, RTT_P90 = 0.266, 0.612
TTLS = (("TTL5", 5.0), ("TTL30", 30.0), ("WINDOW", None))

import importlib.util
spec = importlib.util.spec_from_file_location("pou", "scripts/pmus_pair_or_unwind.py")
pou = importlib.util.module_from_spec(spec); spec.loader.exec_module(pou)


def excursions(rows, i, side, px):
    """MAE/MFE after a fill, from subsequent executable touches only."""
    mae = mfe = D(0)
    for x in rows[i + 1:]:
        ex = D(str(x["bid_at_trade"] if side == "buy" else x["ask_at_trade"]))
        m = (ex - px) if side == "buy" else (px - ex)
        mfe = max(mfe, m); mae = min(mae, m)
    return mae, mfe


def arm_peg(rows, dead):
    """ARM A: replicate the frozen peg's ENTRY mechanics and instrument them."""
    entry = {"buy": None, "sell": None}
    placed_at = {"buy": None, "sell": None}
    st = {"replaces": 0, "chase": 0, "chase_denom": 0,
          "lifetimes": [], "age_at_fill": [], "fills": 0, "impossible": 0,
          "toxic": 0}
    for i, x in enumerate(rows):
        t = x["t"]; past = t >= dead
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))
        if not past and entry.get(consumed):
            mypx, ahead, got = entry[consumed]
            ok = (px <= mypx) if consumed == "buy" else (px >= mypx)
            if ok:
                if ahead > 0:
                    eat = min(ahead, avail); ahead -= eat; avail -= eat
                if avail > 0 and got < Q:
                    take = min(avail, D(Q) - got); got += take
                    age = t - placed_at[consumed]
                    st["fills"] += 1; st["age_at_fill"].append(age)
                    if age < RTT_P50:
                        st["impossible"] += 1
                    mae, mfe = excursions(rows, i, consumed, mypx)
                    if -mae > mfe:
                        st["toxic"] += 1
                entry[consumed] = (mypx, ahead, got) if got < Q else None
        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                cur = entry.get(side)
                if cur is None or cur[0] != qp:
                    if cur is not None:
                        st["replaces"] += 1
                        st["lifetimes"].append(t - placed_at[side])
                        st["chase_denom"] += 1
                        moved_up = qp > cur[0]
                        if (moved_up and px > cur[0]) or ((not moved_up) and px < cur[0]):
                            st["chase"] += 1
                    entry[side] = (qp, qsz, D(0))
                    placed_at[side] = t
    return st


def arm_resting(rows, dead, ttl):
    """ARM B: PLACE ONCE -> retain price and queue -> EXPIRE -> new order."""
    order = {"buy": None, "sell": None}     # (px, ahead, got, placed_t)
    st = {"placed": 0, "expired": 0, "fills": 0, "toxic": 0,
          "age_at_fill": [], "lifetimes": []}
    for i, x in enumerate(rows):
        t = x["t"]; past = t >= dead
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))

        # EXPIRE first: an order past its TTL is gone before this trade.
        if ttl is not None:
            for side in ("buy", "sell"):
                o = order[side]
                if o is not None and t - o[3] >= ttl:
                    st["expired"] += 1; st["lifetimes"].append(t - o[3])
                    order[side] = None

        o = order.get(consumed)
        if o is not None:
            mypx, ahead, got, pt = o
            ok = (px <= mypx) if consumed == "buy" else (px >= mypx)
            if ok:
                if ahead > 0:
                    eat = min(ahead, avail); ahead -= eat; avail -= eat
                if avail > 0 and got < Q:
                    take = min(avail, D(Q) - got); got += take
                    st["fills"] += 1; st["age_at_fill"].append(t - pt)
                    mae, mfe = excursions(rows, i, consumed, mypx)
                    if -mae > mfe:
                        st["toxic"] += 1
                order[consumed] = (mypx, ahead, got, pt) if got < Q else None

        # PLACE only where NOTHING is resting. Never relocate a live order.
        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                if order.get(side) is None:
                    order[side] = (qp, qsz, D(0), t)
                    st["placed"] += 1
    return st


def q(v, p):
    if not v:
        return None
    v = sorted(v)
    return round(v[min(len(v) - 1, int(p * len(v)))], 4)


def main():
    tr, bk = pou.load()
    sc, dropped = pou.scorable(tr, bk)
    per = {}
    tot = {"peg": collections.Counter(), **{k: collections.Counter() for k, _ in TTLS}}
    lif = {"peg": [], **{k: [] for k, _ in TTLS}}
    agef = {"peg": [], **{k: [] for k, _ in TTLS}}
    for slug in sorted(sc):
        rows = sc[slug]
        if not rows:
            continue
        dead = pou.window_end(slug) - DEADLINE_S
        a = arm_peg(rows, dead)
        per.setdefault(slug, {})["PEG"] = {
            "replaces": a["replaces"], "fills": a["fills"], "toxic": a["toxic"],
            "impossible_fills": a["impossible"],
            "chase_fraction": round(a["chase"] / a["chase_denom"], 4) if a["chase_denom"] else None,
            "quote_lifetime_s": {"p10": q(a["lifetimes"], .1), "p50": q(a["lifetimes"], .5),
                                 "p90": q(a["lifetimes"], .9)},
            "age_at_fill_s": {"p10": q(a["age_at_fill"], .1), "p50": q(a["age_at_fill"], .5),
                              "p90": q(a["age_at_fill"], .9)}}
        for k in ("replaces", "fills", "toxic"):
            tot["peg"][k] += a[k]
        tot["peg"]["impossible"] += a["impossible"]
        tot["peg"]["chase"] += a["chase"]; tot["peg"]["chase_denom"] += a["chase_denom"]
        lif["peg"] += a["lifetimes"]; agef["peg"] += a["age_at_fill"]
        for name, ttl in TTLS:
            b = arm_resting(rows, dead, ttl)
            per[slug][name] = {kk: b[kk] for kk in ("placed", "expired", "fills", "toxic")}
            per[slug][name]["age_at_fill_s"] = {"p50": q(b["age_at_fill"], .5),
                                                "p90": q(b["age_at_fill"], .9)}
            for kk in ("placed", "expired", "fills", "toxic"):
                tot[name][kk] += b[kk]
            lif[name] += b["lifetimes"]; agef[name] += b["age_at_fill"]

    summ = {}
    p = tot["peg"]
    summ["PEG"] = {
        "replaces": p["replaces"], "fills": p["fills"], "toxic": p["toxic"],
        "toxic_rate": round(p["toxic"] / p["fills"], 4) if p["fills"] else None,
        "impossible_fills": p["impossible"],
        "impossible_rate": round(p["impossible"] / p["fills"], 4) if p["fills"] else None,
        "chase_fraction": round(p["chase"] / p["chase_denom"], 4) if p["chase_denom"] else None,
        "quote_lifetime_s": {"p10": q(lif["peg"], .1), "p50": q(lif["peg"], .5),
                             "p90": q(lif["peg"], .9)},
        "lifetime_below_rtt_p50": round(sum(1 for v in lif["peg"] if v < RTT_P50) / len(lif["peg"]), 4) if lif["peg"] else None,
        "age_at_fill_s": {"p50": q(agef["peg"], .5), "p90": q(agef["peg"], .9)}}
    for name, _ in TTLS:
        c = tot[name]
        summ[name] = {"placed": c["placed"], "expired": c["expired"],
                      "fills": c["fills"], "toxic": c["toxic"],
                      "toxic_rate": round(c["toxic"] / c["fills"], 4) if c["fills"] else None,
                      "age_at_fill_s": {"p50": q(agef[name], .5), "p90": q(agef[name], .9)}}
    res = {"tape": TAPE, "windows": len(per), "q": Q, "rtt_p50": RTT_P50,
           "rtt_p90": RTT_P90, "ttls": {k: v for k, v in TTLS},
           "toxicity_label": "MAE > MFE over remainder of window (NOT the frozen FORCED label)",
           "summary": summ, "per_window": per}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, default=str)
    print(json.dumps(summ, indent=1, default=str))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
