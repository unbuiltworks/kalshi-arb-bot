"""Read-only: where could a SMALL resting size earn a real share of a
liquidity reward pool? Public gateway only. For every sports market on the
venue's slate (live or starting within the next 30 h): its reward
programme, which period pays NOW, and the THEORETICAL reward rate for Q
contracts joined at both touches under the venue's published scoring rule
(src/pmus_sports_rewards.quote_estimate). Estimates only: nothing is paid
until the venue credits it, and a day's rewards under $1 are not paid."""
import json, sys, time, urllib.request, collections
sys.path.insert(0, ".")
from src import pmus_sports_rewards as R
G = "https://gateway.polymarket.us"
def get_json(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=25) as f:
        return json.load(f)
now = time.time()
import datetime as dt
def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return None
events = []
for off in range(0, 3000, 200):
    d = get_json("/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off)
    evs = d.get("events") or []
    events += evs
    if len(evs) < 200: break
    time.sleep(0.3)
rows = {}
for e in events:
    st = ts(e.get("startDate"))
    live = bool(e.get("live"))
    if not live and (st is None or not (now - 4 * 3600 <= st <= now + 30 * 3600)):
        continue
    for m in e.get("markets") or []:
        slug = str(m.get("slug") or "")
        if slug.startswith(("aec-", "atc-")):
            rows[slug] = {"slug": slug, "live": live, "start": e.get("startDate"), "league": slug.split("-")[1]}
slugs = sorted(rows)
print("markets considered", len(slugs), "| live", sum(r["live"] for r in rows.values()), file=sys.stderr)
found = {}
for i in range(0, len(slugs), 60):
    f, diag = R.fetch_programs(get_json, slugs[i:i + 60])
    found.update(f)
pm = R.build_program_map(found)
out = []
for slug in slugs:
    prog = pm.get(slug)
    if not prog: continue
    r = rows[slug]
    per = R.paying_period(prog, now, event_live=r["live"])
    r["periods"] = [(p["period"], p["pool"], p["target"], p["maxSpread"]) for p in prog.get("periods") or []]
    if not per:
        r["paying_now"] = None; out.append(r); continue
    try:
        md = (get_json("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
        time.sleep(0.25)
    except Exception as exc:
        r["book_err"] = type(exc).__name__; out.append(r); continue
    bids, asks = R.book_sides(md)
    if not bids or not asks:
        r["paying_now"] = per["period"]; r["no_two_sided_book"] = True; out.append(r); continue
    bb, ba = bids[0][0], asks[0][0]
    tick = 0.01 if abs(round(bb * 100) - bb * 100) < 1e-6 and abs(round(ba * 100) - ba * 100) < 1e-6 else 0.0025
    r.update(paying_now=per["period"], pool=per["pool"], target=per["targetSize"], max_spread=per["maxSpread"],
             pool_usd_per_hour=round(per["pool_rate_usd_s"] * 3600, 4), bid=bb, ask=ba, spread=round(ba - bb, 4),
             bid_qty=bids[0][1], ask_qty=asks[0][1], tick=tick)
    for q in (10, 50, 200):
        est = R.quote_estimate(md, per, bb, 1.0 - ba, float(q), tick)
        r["q%d" % q] = {"share": est["share"], "usd_per_hour": est["est_usd_per_hour"],
                        "capital_usd": round(q * (bb + 1.0 - ba), 2), "max_spread_ok": est["max_spread_ok"]}
    out.append(r)
json.dump({"t": now, "markets_considered": len(slugs), "with_programme": len(out), "rows": out}, open(sys.argv[1], "w"), default=str)
print("with programme", len(out), "| paying now", sum(1 for r in out if r.get("paying_now")), file=sys.stderr)
