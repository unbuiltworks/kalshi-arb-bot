#!/usr/bin/env python3
"""L5's BTC15m inventory sequences vs the PMUS book and our real-runner
replay, window by window. Research only; reads files, places nothing.

L5 side (Polymarket international, wallet activity via l5-ops Data API v2):
every BUY fill per window in time order -> inventory path, matched pairs,
residual, and his TRADING P&L settled on the winner implied by his own
REDEEM rows (fallback: the PMUS winner for the same 15-minute window).
No rebates or rewards are included in trading P&L; MAKER_REBATE /
TAKER_REBATE / REWARD rows are tallied separately.

Matched state: each L5 fill is placed against the PMUS YES book at the same
second of the same window (cross-venue PROXY: his book was Polymarket
international, not PMUS). Measured: his price vs the PMUS own-side bid, and
the PMUS own-side mid markout 30 s / 120 s after his fill (adverse
selection proxy).

Ours: per-window replay P&L from fills380.json (real runner, recovery_0.98,
through-only fills). REPLAY, hypothetical fills, never realized.

    python3 l5_btc15m_matched_state.py ACTIVITY.jsonl... --books BOOKS_380 --ours FILLS380 [--out OUT.json]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import json
import statistics as st

UTC = dt.timezone.utc


def pmus_slug(epoch: int) -> str:
    return "cpc-btc-updown-15m-" + dt.datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d-%H%Mz")


def load_activity(paths):
    trades = collections.defaultdict(list)
    redeem = collections.defaultdict(float)
    cond_slug = {}
    incent = collections.Counter()
    for p in paths:
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            typ, slug = r.get("type"), str(r.get("slug") or "")
            if typ in ("MAKER_REBATE", "TAKER_REBATE", "REWARD"):
                incent[typ] += float(r.get("usdcSize") or 0)
                continue
            if not slug.startswith("btc-updown-15m-"):
                continue
            cond_slug[r.get("conditionId")] = slug
            if typ == "TRADE" and r.get("side") == "BUY":
                trades[slug].append(r)
            elif typ == "REDEEM":
                redeem[slug] += float(r.get("usdcSize") or 0)
    return trades, redeem, incent


def own_side(bid, ask, side):
    """(bid, ask, mid) of the side's own price: UP = YES, DOWN = 1 - YES."""
    b, a = float(bid), float(ask)
    return (b, a, (a + b) / 2) if side == "Up" else (1 - a, 1 - b, 1 - (a + b) / 2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("activity", nargs="+")
    ap.add_argument("--books", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    trades, redeem, incent = load_activity(a.activity)
    books = json.load(open(a.books))
    ours = {o["slug"]: o for o in json.load(open(a.ours))}

    win_rows, fill_rows = [], []
    for slug, fills in sorted(trades.items()):
        ws = int(slug.rsplit("-", 1)[1])
        ps = pmus_slug(ws)
        bk = books.get(ps)
        fills.sort(key=lambda r: (r["timestamp"], r.get("transactionHash", "")))
        q = {"Up": 0.0, "Down": 0.0}
        c = {"Up": 0.0, "Down": 0.0}
        first_opp_t = None
        first_side = fills[0]["outcome"]
        for r in fills:
            s = r["outcome"]
            q[s] += float(r["size"])
            c[s] += float(r["usdcSize"])
            t = r["timestamp"] - ws
            if first_opp_t is None and s != first_side:
                first_opp_t = t
            if bk:
                ts = [x[0] for x in bk["rows"]]

                def at(tt):
                    i = bisect.bisect_right(ts, tt) - 1
                    return bk["rows"][max(i, 0)] if i >= 0 else None
                now = at(t)
                if now and now[1] and now[2]:
                    b, ak, mid = own_side(now[1], now[2], s)
                    later = {}
                    for h in (30, 120):
                        x = at(t + h)
                        later[h] = own_side(x[1], x[2], s)[2] if x and x[1] and x[2] else None
                    px = float(r["price"])
                    fill_rows.append({"slug": slug, "t": t, "side": s, "price": px,
                                      "size": float(r["size"]),
                                      "vs_pmus_bid": round(px - b, 4),
                                      "markout_30": None if later[30] is None else round(later[30] - px, 4),
                                      "markout_120": None if later[120] is None else round(later[120] - px, 4)})
        # winner: his redemption tells which side paid; else PMUS winner
        winner = None
        if redeem.get(slug):
            # redeemed dollars equal the winning side's quantity
            ru = abs(redeem[slug] - q["Up"])
            rd = abs(redeem[slug] - q["Down"])
            winner = "Up" if ru < rd else "Down"
            wsrc = "L5_REDEEM"
        elif bk:
            winner = "Up" if bk["winner"] == "UP" else "Down"
            wsrc = "PMUS_PROXY"
        else:
            wsrc = None
        m = min(q["Up"], q["Down"])
        pair = (c["Up"] / q["Up"] + c["Down"] / q["Down"]) if m > 0 else None
        cost = c["Up"] + c["Down"]
        pnl = (q[winner] - cost) if winner else None
        matched_pnl = m * (1 - pair) if pair is not None else 0.0
        win_rows.append({"slug": slug, "pmus": ps, "has_book": bool(bk), "winner": winner, "winner_src": wsrc,
                         "fills": len(fills), "up": round(q["Up"], 4), "down": round(q["Down"], 4),
                         "cost": round(cost, 4), "matched": round(m, 4),
                         "pair_cost": None if pair is None else round(pair, 4),
                         "residual": round(abs(q["Up"] - q["Down"]), 4),
                         "t_first": fills[0]["timestamp"] - ws, "t_first_opposite": first_opp_t,
                         "pnl": None if pnl is None else round(pnl, 4),
                         "matched_pnl": round(matched_pnl, 4),
                         "residual_pnl": None if pnl is None else round(pnl - matched_pnl, 4),
                         "ours_pnl": (ours.get(ps) or {}).get("pnl")})

    def summ(rows, key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return {"n": len(v), "sum": round(sum(v), 4), "mean": round(st.mean(v), 4) if v else None,
                "median": round(st.median(v), 4) if v else None}
    settled = [r for r in win_rows if r["pnl"] is not None]
    both = [r for r in settled if r["matched"] > 0]
    vol = sum(r["up"] + r["down"] for r in settled)
    out = {
        "label": "L5 = Polymarket international venue fills (activity feed), trading P&L only; "
                 "ours = REPLAY hypothetical fills on PMUS books; matched-state is a CROSS-VENUE PROXY",
        "windows": len(win_rows), "settled_windows": len(settled),
        "winner_sources": collections.Counter(r["winner_src"] for r in win_rows),
        "l5_trading_pnl": summ(settled, "pnl"),
        "l5_matched_pnl": summ(settled, "matched_pnl"),
        "l5_residual_pnl": summ(settled, "residual_pnl"),
        "l5_contracts_bought": round(vol, 1),
        "l5_pnl_per_contract": round(sum(r["pnl"] for r in settled) / vol, 5) if vol else None,
        "l5_two_sided_share": round(len(both) / len(settled), 3) if settled else None,
        "l5_pair_cost_median": st.median([r["pair_cost"] for r in both]) if both else None,
        "l5_residual_over_bought_median": st.median([r["residual"] / (r["up"] + r["down"]) for r in settled]) if settled else None,
        "l5_fills_per_window_median": st.median([r["fills"] for r in settled]) if settled else None,
        "l5_t_first_opposite_median": st.median([r["t_first_opposite"] for r in settled if r["t_first_opposite"] is not None]) if settled else None,
        "incentive_rows_usd": dict(incent),
        "fills_vs_pmus": {
            "n": len(fill_rows),
            "price_minus_pmus_own_bid_median": st.median([f["vs_pmus_bid"] for f in fill_rows]) if fill_rows else None,
            "markout_30_mean": round(st.mean([f["markout_30"] for f in fill_rows if f["markout_30"] is not None]), 4) if fill_rows else None,
            "markout_120_mean": round(st.mean([f["markout_120"] for f in fill_rows if f["markout_120"] is not None]), 4) if fill_rows else None,
            "markout_30_size_weighted": round(sum(f["markout_30"] * f["size"] for f in fill_rows if f["markout_30"] is not None) /
                                              max(1e-9, sum(f["size"] for f in fill_rows if f["markout_30"] is not None)), 4) if fill_rows else None,
        },
        "same_windows_ours": summ([r for r in settled if r["ours_pnl"] is not None], "ours_pnl"),
        "same_windows_l5": summ([r for r in settled if r["ours_pnl"] is not None], "pnl"),
    }
    print(json.dumps(out, indent=1, default=str))
    if a.out:
        json.dump({"summary": out, "windows": win_rows, "fills": fill_rows}, open(a.out, "w"), default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
