"""Read-only: for each named pre-game market with a paying reward period,
the THEORETICAL reward for a small two-sided quote and the trading loss that
would cancel it. Public gateway only; uses the venue's published scoring rule
as implemented in src/pmus_sports_rewards.quote_estimate. Nothing here is a
credited reward: estimates until the venue pays them.
usage (from a checkout of the sports code): breakeven.py <out.json> <slug> ..."""
import json, sys, time, urllib.request, datetime as dt
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
PAIR_CAP = 0.97          # owner's limit: the two resting bids must not sum above this
PULL_BUFFER_S = 300      # stop quoting this long before the start
SIZES = (2, 10, 25, 50)
def get_json(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f:
        return json.load(f)
def ts(s):
    return dt.datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00").replace("+00", "+00:00").replace("+00:00:00", "+00:00")).timestamp()
out_path, slugs = sys.argv[1], sys.argv[2:]
found = {}
for i in range(0, len(slugs), 40):
    f, _ = R.fetch_programs(get_json, slugs[i:i + 40]); found.update(f)
pm = R.build_program_map(found)
now = time.time(); rows = []
for s in slugs:
    p = pm.get(s)
    if not p: continue
    per = R.paying_period(p, now, event_live=False)
    if not per: continue
    try:
        start = ts(p["eventStartTime"])
        md = (get_json("/v1/markets/%s/book" % s) or {}).get("marketData") or {}
        bbo = (get_json("/v1/markets/%s/bbo" % s) or {}).get("marketData") or {}
    except Exception as e:
        print(s, "ERR", type(e).__name__, file=sys.stderr); continue
    bids, asks = R.book_sides(md)
    if not bids or not asks: continue
    bb, ba = bids[0][0], asks[0][0]
    t = 0.01 if abs(round(bb * 100) - bb * 100) < 1e-6 and abs(round(ba * 100) - ba * 100) < 1e-6 else 0.0025
    hrs = max(0.0, (start - PULL_BUFFER_S - now) / 3600.0)
    row = {"slug": s, "league": s.split("-")[1], "period": per["period"], "pool_usd": per["pool"],
           "pool_usd_per_hour": round(per["pool_rate_usd_s"] * 3600, 3), "target": per["targetSize"], "df": per["df"],
           "max_spread": per["maxSpread"], "start": p["eventStartTime"], "hours_left": round(hrs, 2),
           "bid": bb, "ask": ba, "tick": t, "bid_touch": bids[0][1], "ask_touch": asks[0][1],
           "bid_top3": sum(q for _, q in bids[:3]), "ask_top3": sum(q for _, q in asks[:3]),
           "traded_total": float(bbo.get("sharesTraded") or 0), "open_interest": float(bbo.get("openInterest") or 0),
           "sizes": {}}
    for q in SIZES:
        touch = R.quote_estimate(md, per, bb, 1 - ba, float(q), t)
        # best placement whose two bids sum to no more than the pair cap
        best = None
        for i in range(0, 6):
            for j in range(0, 6):
                y, n = bb - i * t, (1 - ba) - j * t
                if y <= 0 or n <= 0 or y + n > PAIR_CAP + 1e-9: continue
                e = R.quote_estimate(md, per, y, n, float(q), t)
                if best is None or e["est_usd_per_hour"] > best[0]:
                    best = (e["est_usd_per_hour"], i, j, round(y + n, 4))
        capped = best or (0.0, None, None, None)
        def pack(rate, pair):
            period = rate * hrs
            return {"usd_per_hour": round(rate, 5), "period_usd": round(period, 4), "pair_cost": pair,
                    "capital_usd": round(q * pair, 2) if pair else None,
                    "cents_per_contract_hour": round(100 * rate / q, 4),
                    # one side filled in full and the price then moves against it: how far before the period's reward is gone
                    "breakeven_move_cents_one_full_fill": round(100 * period / q, 3),
                    # how many contracts may be filled one tick wrong per hour
                    "breakeven_contracts_per_hour_at_one_tick": round(rate / t, 2)}
        row["sizes"][str(q)] = {"at_touch": pack(touch["est_usd_per_hour"], round(bb + 1 - ba, 4)),
                                "within_pair_cap": dict(pack(capped[0], capped[3]), yes_ticks_back=capped[1], no_ticks_back=capped[2])}
    r50 = row["sizes"]["50"]["within_pair_cap"]["usd_per_hour"] / 50.0
    row["min_size_for_1usd_alone_remaining"] = round(1.0 / (r50 * hrs)) if r50 * hrs > 0 else None
    row["min_size_for_1usd_alone_full_6h"] = round(1.0 / (r50 * 6.0)) if r50 > 0 else None
    rows.append(row); time.sleep(0.25)
json.dump({"t": now, "pair_cap": PAIR_CAP, "pull_buffer_s": PULL_BUFFER_S, "rows": rows}, open(out_path, "w"), indent=1)
rows.sort(key=lambda r: -r["sizes"]["10"]["within_pair_cap"]["cents_per_contract_hour"])
print("%-40s %-6s %5s %6s %5s %-11s %14s %8s | %s" % ("market", "period", "$/h", "target", "hrs", "bid/ask", "touch b/a", "traded", "Q=10: touch $/h | capped $/h (y,n back; pair) | c/contract-h | b/e move c"))
for r in rows:
    a, c = r["sizes"]["10"]["at_touch"], r["sizes"]["10"]["within_pair_cap"]
    print("%-40s %-6s %5.1f %6d %5.2f %.2f/%.2f %7d/%-7d %7d | %.4f | %.4f (%s,%s; %s) | %.3f | %.2f | min Q rem %s, 6h %s" % (
        r["slug"][:40], r["period"], r["pool_usd_per_hour"], r["target"], r["hours_left"], r["bid"], r["ask"], r["bid_touch"], r["ask_touch"],
        r["traded_total"], a["usd_per_hour"], c["usd_per_hour"], c["yes_ticks_back"], c["no_ticks_back"], c["pair_cost"],
        c["cents_per_contract_hour"], c["breakeven_move_cents_one_full_fill"], r["min_size_for_1usd_alone_remaining"], r["min_size_for_1usd_alone_full_6h"]))
