"""Live eligibility funnel using the bot's own international resolver."""
import json, sys, time, urllib.request, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, '.')
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV
from src import pmus_spring_gateway_candidates as GW
G = "https://gateway.polymarket.us"
def get(url, t=15):
    req = urllib.request.Request(url, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.load(r)
d = get(G + GW.LIVE_EVENTS_PATH + "&limit=500&offset=0")
evs = d if isinstance(d, list) else d.get("events") or []
live = [e for e in evs if e.get("live") is True and not e.get("ended")]
items = []
for e in live:
    for m in e.get("markets") or []:
        if str(m.get("slug", "")).startswith("aec-"):
            items.append((m["slug"], e.get("eventState") or {}, e.get("startTime")))
def one(it):
    slug, st, start = it
    o = {"slug": slug, "sport": slug.split("-")[1], "period": st.get("period"), "start": start}
    try: o["late"] = M.is_late(dict(st))
    except Exception as ex: o["late"] = "err"
    try:
        md = get(G + "/v1/markets/%s/book" % slug)["marketData"]
        b = sorted(((float(r["px"]["value"]), float(r["qty"])) for r in md.get("bids") or []), reverse=True)
        a = sorted(((float(r["px"]["value"]), float(r["qty"])) for r in md.get("offers") or []))
        o.update(bid=b[0] if b else None, ask=a[0] if a else None)
    except Exception as ex:
        o["book_err"] = type(ex).__name__
    try:
        m = get(G + "/v1/market/slug/" + slug)["market"]
        po = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else m.get("outcomes")
        o["names"] = po
        tok = XV.resolve_token(XV.gamma_get, slug, po, XV.yes_names(m))
        o["tok"] = bool(tok)
        if tok:
            bk = get("https://clob.polymarket.com/book?token_id=" + tok)
            bb = max(((float(x["price"]), float(x["size"])) for x in bk.get("bids") or []), default=None)
            ba = min(((float(x["price"]), float(x["size"])) for x in bk.get("asks") or []), default=None)
            o.update(ibid=bb, iask=ba)
    except Exception as ex:
        o["intl_err"] = type(ex).__name__
    return o
with ThreadPoolExecutor(5) as ex:
    out = list(ex.map(one, items))
json.dump({"t": time.time(), "live_events": len(live), "markets": out}, open(sys.argv[1], "w"))
ALLOW = set(sys.argv[2].split(","))
def ts(o): return bool(o.get("bid") and o.get("ask"))
steps = [("two-sided book", ts),
         ("price 0.25-0.75", lambda o: ts(o) and 0.25 <= o["bid"][0] <= 0.75),
         ("spread <= 6c", lambda o: ts(o) and o["ask"][0] - o["bid"][0] <= 0.06 + 1e-9),
         ("not final phase", lambda o: not o.get("late")),
         ("intl match, live 2-sided", lambda o: bool(o.get("tok") and o.get("ibid") and o.get("iask") and o["iask"][0] > o["ibid"][0])),
         ("allowlist " + sys.argv[2], lambda o: o["sport"] in ALLOW)]
print(time.strftime("%H:%MZ", time.gmtime()), "live events", len(live), "moneyline markets", len(out))
cur = out
for name, f in steps:
    nxt = [o for o in cur if f(o)]
    print("  %-34s %3d -> %3d  lost %s" % (name, len(cur), len(nxt), dict(collections.Counter(o["sport"] for o in cur if not f(o)).most_common(7))))
    cur = nxt
for o in sorted(cur, key=lambda o: o["sport"]):
    print("  ELIGIBLE %-42s %-6s us %.3f/%.3f (%d x %d)  intl %.3f/%.3f (%d x %d)  %s" % (o["slug"], o["period"], o["bid"][0], o["ask"][0], o["bid"][1], o["ask"][1], o["ibid"][0], o["iask"][0], o["ibid"][1], o["iask"][1], o.get("names")))
