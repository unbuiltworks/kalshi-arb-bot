"""Read-only: per market type, sample up to N open markets spread across
leagues (live first, then soonest start; per event the line nearest 0.50
first = 'main', then one random other line = 'ladder'), read the public
book + bbo, and compute THEORETICAL reward estimates with R.quote_estimate.
Nothing here is a payment."""
import json, sys, time, random, collections
sys.path.insert(0, "/tmp/claude-0/-home-user-kalshi-arb-bot/dfd6c478-98b1-5b09-ba6a-de4e42bef2ef/scratchpad/dev")
from src import pmus_sports_rewards as R
from fetch_events import get_json
from classify import *
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
random.seed(7)
M = [m for m in load() if is_open(m)]
P = json.load(open("programs_raw.json"))["found"]
pm = R.build_program_map(P, {m["slug"]: m["event"] for m in M})
now = time.time()
def q(v):
    try: return float((v or {}).get("value"))
    except Exception: return None
def mid(m):
    b, a = q(m["bestBidQuote"]), q(m["bestAskQuote"])
    return None if b is None or a is None else (a + b) / 2
bytype = collections.defaultdict(list)
for m in M:
    t = mtype(m)
    if t == "futures" or "cricket" in str(m["sportsMarketType"]): continue
    st = ts(m["start"]) or 9e18
    if not m["live"] and st > now + 30 * 3600: continue
    bytype[t].append(m)
sample = []
for t, ms in bytype.items():
    evs = collections.defaultdict(list)
    for m in ms: evs[m["event"]].append(m)
    lg = collections.defaultdict(list)
    for e, lst in evs.items():
        lst.sort(key=lambda m: (mid(m) is None, abs((mid(m) or 0) - 0.5)))
        lg[lst[0]["league"]].append(lst)
    for k in lg: lg[k].sort(key=lambda lst: (not lst[0]["live"], ts(lst[0]["start"]) or 9e18))
    picks = []; rnd = 0
    while len(picks) < N and any(lg.values()):
        for k in sorted(lg):
            if not lg[k] or len(picks) >= N: continue
            lst = lg[k].pop(0)
            picks.append((lst[0], "main"))
            if len(lst) > 1 and len(picks) < N and rnd % 2 == 0:
                picks.append((random.choice(lst[1:]), "ladder"))
        rnd += 1
    sample += [(t, m, role) for m, role in picks]
print("sample", len(sample), collections.Counter(t for t, _, _ in sample), file=sys.stderr)
g = R.SpacedGet(get_json, gap_s=0.5)
out = open("book_sample.jsonl", "w")
for i, (t, m, role) in enumerate(sample):
    slug = m["slug"]; tick = float(m["orderPriceMinTickSize"] or 0.01)
    row = {"type": t, "role": role, "slug": slug, "league": m["league"], "event": m["event"], "live": m["live"], "start": m["start"],
           "title": m["title"], "smt": m["sportsMarketType"], "tick": tick, "min_qty": m["minimumTradeQty"], "t": time.time()}
    try:
        md = (g("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
        bbo = (g("/v1/markets/%s/bbo" % slug) or {}).get("marketData") or {}
    except Exception as exc:
        row["err"] = "%s:%s" % (type(exc).__name__, str(exc)[:80]); out.write(json.dumps(row) + "\n"); continue
    row["bbo"] = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in bbo.items() if k in
                  ("currentPx", "lastTradePx", "sharesTraded", "openInterest", "bestAsk", "bestBid", "askDepth", "bidDepth", "state", "bidShares", "askShares")}
    row["book_keys"] = sorted(md.keys())
    bids, asks = R.book_sides(md)
    row["n_bid_levels"], row["n_ask_levels"] = len(bids), len(asks)
    row["bids5"], row["asks5"] = bids[:5], asks[:5]
    prog = pm.get(slug)
    row["programme"] = [(p["programId"], p["period"], p["pool"], p["target"], p["maxSpread"], p["df"], p["status"], p["M_event"]) for p in (prog or {}).get("periods") or []]
    per = R.paying_period(prog, time.time(), event_live=bool(m["live"])) if prog else None
    row["paying"] = per
    if bids and asks:
        bb, ba = bids[0][0], asks[0][0]
        row.update(bid=bb, ask=ba, spread_ticks=round((ba - bb) / tick, 2), bid_qty=bids[0][1], ask_qty=asks[0][1],
                   bid_depth3=sum(qv for p, qv in bids if p >= bb - 3 * tick - 1e-9), ask_depth3=sum(qv for p, qv in asks if p <= ba + 3 * tick + 1e-9),
                   two_sided=True)
        if per:
            for qty in (1, 5):
                for d in (0, 1):
                    y, n = bb - d * tick, (1.0 - ba) - d * tick
                    if not (0 < y < 1 and 0 < n < 1): continue
                    e = R.quote_estimate(md, per, y, n, float(qty), tick)
                    cap = qty * (y + n)
                    row["q%d_d%d" % (qty, d)] = {"share": e["share"], "usd_h": e["est_usd_per_hour"], "cap": round(cap, 4),
                                                 "usd_h_per_usd": e["est_usd_per_hour"] / cap if cap > 0 else None,
                                                 "bid_q": e["bid_qualifies"], "ask_q": e["ask_qualifies"], "ms_ok": e["max_spread_ok"]}
    else:
        row["two_sided"] = False
        row["bid"] = bids[0][0] if bids else None; row["ask"] = asks[0][0] if asks else None
        row["bid_qty"] = bids[0][1] if bids else None; row["ask_qty"] = asks[0][1] if asks else None
    out.write(json.dumps(row) + "\n")
    if i % 40 == 0: out.flush(); print(i, file=sys.stderr, flush=True)
print("done", g.n, file=sys.stderr)
