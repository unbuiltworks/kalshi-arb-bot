#!/usr/bin/env python3
"""SNAPSHOT + PUBLISHED: what a small resting quote at the touch would score
in the liquidity-incentive pools of ~20 high-pool rewarded markets.

READ-ONLY. Public GETs of /v1/markets/{slug}/book on
https://gateway.polymarket.us (User-Agent "research-client", <= 2 req/s).

Scoring (venue doc copy, data/pmus/sports_incentives_2026-09-10/
doc_incentives_liquidity.md): per second, per side,
    score = DF ^ (ticks from best) x size, counted only for orders inside the
    range where cumulative raw size from best first reaches Target Size;
    each side is normalised to 1.0 per snapshot if Target Size is met.
So a side's pool share is our_score / total_score on that side, and each
side carries half the period pool (sides are normalised independently).

Two pool readings are reported because the public API does not say which
applies and the repo's earlier quote of polymarket.us/rewards (Sept 2026)
says the pool is "shared across the program's markets":
  PER_MARKET : rewardPool is paid per market        (upper bound)
  SHARED     : rewardPool / M, M = open markets in the program (census)
Book assumed static as observed; duty 100%; no fills. Rewards < $1.00 per
period are not paid (venue doc) -- flagged.

    python3 analysis/l5/pivot_pmus/incentive_share_estimate.py
"""
import datetime as dt
import gzip
import json
import os
import time
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
GW = "https://gateway.polymarket.us"
SIZES = (10, 50)
N_PICK = 20
ROUNDS, SPACING_S = 4, 60.0
_last = [0.0]


def get(path):
    w = 0.55 - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()
    req = urllib.request.Request(GW + path, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ts(s):
    if not s:
        return None
    s = s.replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def current(r, now):
    """Is this program period paying right now, and its length in hours."""
    tp, per = r["tp"], r["tp"]["period"]
    es, st = ts(r.get("eventStartTime")), ts(tp.get("start"))
    if st and st > now:
        return None
    if per in ("daily", "daily_event"):
        return 24.0
    if not es:
        return None
    if per == "day_of":
        return 6.0 if es - dt.timedelta(hours=6) <= now < es else None
    if per == "live":   # live = start..settlement; 3 h assumed for the rate only
        return 3.0 if es <= now < es + dt.timedelta(hours=4) else None
    if per == "early":
        end = es - dt.timedelta(hours=6)
        return max(1.0, (end - st).total_seconds() / 3600) if (st and now < end) else None
    return None


def lv(levels):
    out = []
    for l in levels or []:
        p = l.get("px")
        out.append((float(p.get("value") if isinstance(p, dict) else p), float(l.get("qty") or 0)))
    return out


def side_share(levels, best_first, tick, df, target, q):
    """Our Q at the best price on this side. Returns dict."""
    if not levels:
        return {"qualifies": False, "reason": "empty side", "share": 0.0}
    levels = sorted(levels, key=lambda x: -x[0] if best_first == "bid" else x[0])
    best = levels[0][0]
    cum, s_oth, used = q, 0.0, 0   # our order sits at best (behind the queue: same price, same score)
    for p, sz in levels:
        if cum >= target and used > 0:
            break
        k = round(abs(p - best) / tick)
        s_oth += (df ** k) * sz
        cum += sz
        used += 1
    qual = cum >= target
    return {"qualifies": qual, "best": best, "touch_qty": levels[0][1], "levels_in_range": used,
            "size_in_range_incl_ours": round(cum, 2), "others_score": round(s_oth, 2),
            "share": (q / (q + s_oth)) if qual else 0.0}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cen = json.load(gzip.open(os.path.join(HERE, "raw", "incentives_census.json.gz")))
    ev = json.load(gzip.open(os.path.join(HERE, "raw", "all_events_slim.json.gz")))
    tick = {m["slug"]: (m.get("tick") or 0.01) for e in ev["events"] for m in e["markets"]}
    M = Counter(r["tp"]["programId"] for r in cen["rows"])
    cands = []
    for r in cen["rows"]:
        if r.get("instrumentState") != "INSTRUMENT_STATE_OPEN":
            continue
        hrs = current(r, now)
        if not hrs:
            continue
        pool = float(r["tp"]["rewardPool"])
        cands.append({"slug": r["marketSlug"], "category": r["event_category"], "programId": r["tp"]["programId"],
                      "period": r["tp"]["period"], "pool": pool, "M": M[r["tp"]["programId"]], "period_hours": hrs,
                      "df": float(r["tp"]["discountFactor"]), "target": float(r["tp"]["targetSize"]),
                      "maxSpread": r["tp"].get("maxSpread"), "eventStartTime": r.get("eventStartTime"),
                      "rate_per_market_hr": pool / hrs, "rate_shared_hr": pool / hrs / M[r["tp"]["programId"]]})
    # pick: best shared-rate markets, at most 2 per program, then best per-market-rate, until N_PICK
    pick, per_prog = [], defaultdict(int)
    for key in ("rate_shared_hr", "rate_per_market_hr"):
        for c in sorted(cands, key=lambda c: -c[key]):
            if len(pick) >= (N_PICK // 2 if key == "rate_shared_hr" else N_PICK):
                break
            if per_prog[c["programId"]] >= 2 or c in pick:
                continue
            pick.append(c); per_prog[c["programId"]] += 1
    res = []
    for c in pick:
        c["tick"] = float(tick.get(c["slug"]) or 0.01)
        c["rounds"] = []
    for rnd in range(ROUNDS):
        t0 = time.time()
        for c in pick:
            row = {"round": rnd, "t": dt.datetime.now(dt.timezone.utc).isoformat()}
            try:
                md = get("/v1/markets/%s/book" % c["slug"]).get("marketData") or {}
            except Exception as exc:   # noqa: BLE001
                row["error"] = str(exc)[:120]; c["rounds"].append(row); continue
            bids, offs = lv(md.get("bids")), lv(md.get("offers"))
            row["state"] = md.get("state")
            row["bid"] = max((p for p, _ in bids), default=None)
            row["ask"] = min((p for p, _ in offs), default=None)
            row["spread"] = round(row["ask"] - row["bid"], 4) if (row["bid"] is not None and row["ask"] is not None) else None
            row["est"] = {}
            for q in SIZES:
                sb = side_share(bids, "bid", c["tick"], c["df"], c["target"], q)
                sa = side_share(offs, "ask", c["tick"], c["df"], c["target"], q)
                row["est"][q] = {"bid_side": sb, "ask_side": sa,
                                 "usd_per_period_both_sides_PER_MARKET": round(c["pool"] / 2.0 * (sb["share"] + sa["share"]), 3),
                                 "capital_both_sides_usd": round(q * ((row["bid"] or 0) + (1 - (row["ask"] or 1))), 2)}
            c["rounds"].append(row)
        print("round", rnd, "%.0fs" % (time.time() - t0), flush=True)
        if rnd < ROUNDS - 1:
            time.sleep(max(0.0, SPACING_S - (time.time() - t0)))
    for c in pick:
        ok = [r for r in c["rounds"] if "est" in r]
        c["spreads"] = [r["spread"] for r in ok]
        c["summary"] = {}
        for q in SIZES:
            v = [r["est"][q]["usd_per_period_both_sides_PER_MARKET"] for r in ok]
            sh = [(round(r["est"][q]["bid_side"]["share"], 3), round(r["est"][q]["ask_side"]["share"], 3)) for r in ok]
            m = sum(v) / len(v) if v else 0.0
            c["summary"][q] = {"shares_bid_ask_by_round": sh,
                               "mean_usd_per_period_PER_MARKET": round(m, 3),
                               "min_usd_per_period_PER_MARKET": round(min(v), 3) if v else None,
                               "mean_usd_per_period_SHARED": round(m / c["M"], 4),
                               "capital_both_sides_usd": ok[-1]["est"][q]["capital_both_sides_usd"] if ok else None}
        res.append(c)
    out = {"label": "SNAPSHOT books x PUBLISHED pools; static-book, 100%-duty, no-fill estimate; NOT realized, NOT credited",
           "taken": now.isoformat(), "candidates_paying_now": len(cands), "markets": res}
    json.dump(out, open(os.path.join(HERE, "raw", "incentive_share_books.json"), "w"), indent=1)
    print("%-46s %-11s %6s %3s %4s %6s %-16s | Q=10 shares(bid,ask) by round | Q10 mean$/per PM  SH | Q50 mean$/per PM" % (
        "slug", "period", "pool", "M", "DF", "target", "spreads"))
    for c in res:
        s10, s50 = c["summary"][10], c["summary"][50]
        print("%-46s %-11s %6.0f %3d %4.2f %6.0f %-16s | %s | %8.2f %7.3f | %8.2f" % (
            c["slug"][:46], c["period"], c["pool"], c["M"], c["df"], c["target"], c["spreads"],
            s10["shares_bid_ask_by_round"], s10["mean_usd_per_period_PER_MARKET"], s10["mean_usd_per_period_SHARED"],
            s50["mean_usd_per_period_PER_MARKET"]))


if __name__ == "__main__":
    main()
