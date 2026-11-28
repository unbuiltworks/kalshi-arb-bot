import json, sys, time, urllib.request, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, '.')
from src import pmus_sports_two_sided as M
from src import pmus_spring_gateway_candidates as GW
G = "https://gateway.polymarket.us"
def get(url, t=15):
    req = urllib.request.Request(url, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.load(r)
ALLOW = {"cfb", "cs2", "nba", "nfl", "wta"}
d = get(G + GW.LIVE_EVENTS_PATH + "&limit=500&offset=0")
evs = d if isinstance(d, list) else d.get("events") or []
live = [e for e in evs if e.get("live") is True and not e.get("ended")]
rows = []
for e in live:
    es = e.get("eventState") or {}
    mks = [m for m in e.get("markets") or [] if isinstance(m, dict)]
    aec = [m for m in mks if str(m.get("slug", "")).startswith("aec-")]
    rows.append({"event": e["slug"], "league": e["slug"].split("-")[0], "state": es,
                 "n_markets": len(mks), "aec": [m["slug"] for m in aec],
                 "prefixes": sorted({str(m.get("slug","")).split("-")[0] for m in mks})})
def num(x):
    try: return float((x or {}).get("value"))
    except Exception: return None
def book(slug):
    try:
        md = get(G + "/v1/markets/%s/book" % slug)["marketData"]
        b = sorted(((float(r["px"]["value"]), float(r["qty"])) for r in md.get("bids") or []), reverse=True)
        a = sorted(((float(r["px"]["value"]), float(r["qty"])) for r in md.get("offers") or []))
        return {"state": md.get("state"), "bid": b[0] if b else None, "ask": a[0] if a else None,
                "bid3": sum(q for p, q in b[:3]), "ask3": sum(q for p, q in a[:3])}
    except Exception as ex:
        return {"err": type(ex).__name__}
def intl(slug):
    s = slug[4:]
    try:
        r = get("https://gamma-api.polymarket.com/markets?slug=" + s)
        if not r: return {"found": False}
        m = r[0]; toks = json.loads(m.get("clobTokenIds") or "[]")
        out = {"found": True, "active": m.get("active"), "closed": m.get("closed"),
               "accepting": m.get("acceptingOrders")}
        if toks:
            bk = get("https://clob.polymarket.com/book?token_id=" + toks[0])
            bb = max((float(x["price"]) for x in bk.get("bids") or []), default=None)
            ba = min((float(x["price"]) for x in bk.get("asks") or []), default=None)
            out.update(ibid=bb, iask=ba)
        return out
    except Exception as ex:
        return {"found": None, "err": type(ex).__name__}
slugs = [s for r in rows for s in r["aec"]]
with ThreadPoolExecutor(6) as ex:
    books = dict(zip(slugs, ex.map(book, slugs)))
    intls = dict(zip(slugs, ex.map(intl, slugs)))
out = []
for r in rows:
    for s in r["aec"]:
        sport = s.split("-")[1]
        bk, it = books[s], intls[s]
        st = dict(r["state"]); 
        try: late = M.is_late(st)
        except Exception as ex: late = "err:" + type(ex).__name__
        out.append({"slug": s, "sport": sport, "period": st.get("period"), "type": st.get("type"),
                    "late": late, "book": bk, "intl": it})
json.dump({"t": time.time(), "live_events": len(live), "rows": rows, "markets": out},
          open(sys.argv[1], "w"))
print("live events", len(live))
print("events with no aec market:", sum(1 for r in rows if not r["aec"]),
      collections.Counter(r["league"] for r in rows if not r["aec"]).most_common(12))
print("aec markets:", len(out))
