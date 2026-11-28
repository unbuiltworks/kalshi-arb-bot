"""Read-only second sample: markets whose liquidity programme pays NOW and whose
listing bid is inside 0.25-0.75 (the controller's band), up to N per type,
round-robin across events (live first). Book read only (book carries stats).
THEORETICAL estimates; nothing here is a payment."""
import json, sys, time, random, collections
sys.path.insert(0, "/tmp/claude-0/-home-user-kalshi-arb-bot/dfd6c478-98b1-5b09-ba6a-de4e42bef2ef/scratchpad/dev")
from src import pmus_sports_rewards as R
from fetch_events import get_json
from classify import *
N = 40; random.seed(11)
M = [m for m in load() if is_open(m)]
P = json.load(open("programs_raw.json"))["found"]
pm = R.build_program_map(P, {m["slug"]: m["event"] for m in M})
now = time.time()
def q(v):
    try: return float((v or {}).get("value"))
    except Exception: return None
pop = collections.Counter(); bytype = collections.defaultdict(lambda: collections.defaultdict(list))
for m in M:
    t = mtype(m)
    if t == "futures" or "cricket" in str(m["sportsMarketType"]): continue
    prog = pm.get(m["slug"])
    per = R.paying_period(prog, now, event_live=bool(m["live"])) if prog else None
    if not per: continue
    pop[(t, "paying_now")] += 1
    b, a = q(m["bestBidQuote"]), q(m["bestAskQuote"])
    if b is None or a is None or not (0.25 <= b <= 0.75): continue
    pop[(t, "paying_now_in_band")] += 1
    bytype[t][m["event"]].append(m)
json.dump({str(k): v for k, v in pop.items()}, open("paying_population.json", "w"))
print(dict(pop), file=sys.stderr)
sample = []
for t, evs in bytype.items():
    order = sorted(evs, key=lambda e: (not evs[e][0]["live"], ts(evs[e][0]["start"]) or 9e18))
    for e in order: random.shuffle(evs[e])
    picks = []
    while len(picks) < N and any(evs[e] for e in order):
        for e in order:
            if evs[e] and len(picks) < N: picks.append(evs[e].pop())
    sample += [(t, m) for m in picks]
print("sample", len(sample), file=sys.stderr)
g = R.SpacedGet(get_json, gap_s=0.5); out = open("paying_sample.jsonl", "w")
for i, (t, m) in enumerate(sample):
    slug = m["slug"]; tick = float(m["orderPriceMinTickSize"] or 0.01)
    row = {"type": t, "slug": slug, "league": m["league"], "event": m["event"], "live": m["live"], "start": m["start"], "title": m["title"], "smt": m["sportsMarketType"], "tick": tick, "t": time.time()}
    try: md = (g("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
    except Exception as exc:
        row["err"] = type(exc).__name__; out.write(json.dumps(row) + "\n"); continue
    st = md.get("stats") or {}
    row["stats"] = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in st.items() if k in ("sharesTraded", "notionalTraded", "openInterest", "lastTradePx", "lastTradeSetTime")}
    bids, asks = R.book_sides(md)
    per = R.paying_period(pm[slug], time.time(), event_live=bool(m["live"]))
    row["paying"] = per
    if bids and asks and per:
        bb, ba = bids[0][0], asks[0][0]
        row.update(bid=bb, ask=ba, spread_ticks=round((ba - bb) / tick, 2), bid_qty=bids[0][1], ask_qty=asks[0][1],
                   bid_depth3=sum(v for p, v in bids if p >= bb - 3 * tick - 1e-9), ask_depth3=sum(v for p, v in asks if p <= ba + 3 * tick + 1e-9))
        for qty in (1, 5):
            for d in (0, 1):
                y, n = bb - d * tick, (1.0 - ba) - d * tick
                if not (0 < y < 1 and 0 < n < 1): continue
                e = R.quote_estimate(md, per, y, n, float(qty), tick)
                cap = qty * (y + n)
                row["q%d_d%d" % (qty, d)] = {"share": e["share"], "usd_h": e["est_usd_per_hour"], "cap": round(cap, 4), "usd_h_per_usd": e["est_usd_per_hour"] / cap,
                                             "bid_q": e["bid_qualifies"], "ask_q": e["ask_qualifies"], "ms_ok": e["max_spread_ok"]}
    else:
        row["two_sided"] = False
    out.write(json.dumps(row) + "\n")
    if i % 40 == 0: out.flush(); print(i, file=sys.stderr, flush=True)
print("done", g.n, file=sys.stderr)
