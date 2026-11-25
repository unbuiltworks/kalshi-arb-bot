"""Read-only: today's Champions League three-way markets on PMUS: reward
programme, book, spread, depth, and whether the international venue lists
the same event (public endpoints only). THEORETICAL reward estimates."""
import json, sys, time, urllib.request, datetime as dt
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"; GA = "https://gamma-api.polymarket.com"
def get(base, path):
    r = urllib.request.Request(base + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f: return json.load(f)
now = time.time()
evs = []
for off in range(0, 3000, 200):
    d = get(G, "/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off)
    e = d.get("events") or []; evs += e
    if len(e) < 200: break
ucl = [e for e in evs if str(e.get("slug","")).startswith("ucl-")]
print("UCL events listed:", len(ucl))
out = []
for e in ucl:
    es = e["slug"]
    try: full = get(G, "/v1/events/slug/%s" % es); full = full.get("event", full)
    except Exception as x: print("event read failed", es, x); continue
    mk = [m for m in (full.get("markets") or []) if str(m.get("slug","")).startswith("atc-") and m.get("sportsMarketType") == "soccer_team_full_time_winner"]
    try:
        ig = get(GA, "/events?slug=%s" % es)
        intl = (len(ig) == 1 and not ig[0].get("closed"))
        intl_n = len([x for x in ig[0].get("markets") or [] if x.get("sportsMarketType") == "moneyline"]) if intl else 0
    except Exception as x:
        intl, intl_n = "error:%s" % type(x).__name__, 0
    out.append({"event": es, "start": full.get("startTime") or e.get("startDate"), "live": bool(e.get("live")), "intl_event": intl, "intl_moneylines": intl_n, "markets": []})
    slugs = [m["slug"] for m in mk]
    f, _ = R.fetch_programs(lambda p: get(G, p), slugs); pm = R.build_program_map(f)
    for m in mk:
        s = m["slug"]; prog = pm.get(s)
        md = (get(G, "/v1/markets/%s/book" % s) or {}).get("marketData") or {}
        bids, asks = R.book_sides(md)
        row = {"slug": s, "tick": m.get("orderPriceMinTickSize")}
        if bids and asks:
            row.update(bid=bids[0][0], ask=asks[0][0], bq=round(bids[0][1],1), aq=round(asks[0][1],1))
        if prog:
            row["periods"] = [(p["period"], p["pool"], p["target"], p["maxSpread"]) for p in prog.get("periods") or []]
            for live in (False, True):
                per = R.paying_period(prog, now if not live else now + 1, event_live=live)
                if per and bids and asks:
                    t = float(m.get("orderPriceMinTickSize") or 0.01)
                    for q in (2, 20):
                        est = R.quote_estimate(md, per, bids[0][0] - t, 1 - asks[0][0] - t, float(q), t)
                        row["%s_q%d" % ("live" if live else "now", q)] = (per["period"], per["pool"], round(est["est_usd_per_hour"], 3), est["share"])
        out[-1]["markets"].append(row)
        time.sleep(0.3)
json.dump(out, open(sys.argv[1], "w"), default=str, indent=1)
for ev in out:
    print(ev["event"], ev["start"], "live" if ev["live"] else "", "intl:", ev["intl_event"], ev["intl_moneylines"])
    for r in ev["markets"]:
        print("   ", r["slug"][len(ev["event"])+5:] if r["slug"].startswith("atc-"+ev["event"]) else r["slug"], "bid", r.get("bid"), "ask", r.get("ask"), "touch", r.get("bq"), r.get("aq"), "| now", r.get("now_q2"), r.get("now_q20"), "| live-model", r.get("live_q2"), "| periods", r.get("periods"))
