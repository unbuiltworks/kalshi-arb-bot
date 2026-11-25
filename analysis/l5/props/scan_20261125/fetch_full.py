"""Read-only: for every sports event live or starting within 30 h whose
marketCounts.numMarkets exceeds the markets carried by the list, fetch
/v1/events/slug/{slug} (full market set). Slimmed rows -> markets_full.jsonl"""
import json, sys, time, datetime as dt
from fetch_events import get_json
d = json.load(open("events_raw.json")); now = d["t"]
def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return None
def slim(m, e, src):
    r = {k: m.get(k) for k in ("id", "slug", "marketType", "sportsMarketType", "sportsMarketTypeV2", "title", "subtitle",
         "question", "description", "active", "closed", "archived", "hidden", "ep3Status", "status", "comboEnabled",
         "orderPriceMinTickSize", "minimumTradeQty", "bestBidQuote", "bestAskQuote", "gameStartTime", "endDate",
         "feeCoefficient", "singlesRfqConfig", "eventSubcategory")}
    r["sides"] = [{k: s.get(k) for k in ("description", "long", "price", "tradable", "identifier", "marketSideType")}
                  | {"player": (s.get("player") or {}).get("name") if isinstance(s.get("player"), dict) else None}
                  for s in m.get("marketSides") or []]
    r["side_keys"] = sorted({k for s in m.get("marketSides") or [] for k in s})
    r.update(event=e["slug"], event_id=e["id"], live=bool(e.get("live")), start=e.get("startDate"),
             league=e["slug"].split("-")[0], series=e.get("seriesSlug"), src=src,
             numMarkets=(e.get("marketCounts") or {}).get("numMarkets"),
             groups={str(mid): g.get("title") for g in e.get("marketGroups") or [] for mid in g.get("marketIds") or []}.get(str(m.get("id"))))
    return r
win = [e for e in d["events"] if e.get("live") or (ts(e.get("startDate")) and now - 4 * 3600 <= ts(e["startDate"]) <= now + 30 * 3600)]
out = open("markets_full.jsonl", "w"); meta = []
n = 0
for e in win:
    nm = (e.get("marketCounts") or {}).get("numMarkets", 0)
    src, full = "list", e
    if nm > len(e.get("markets") or []):
        t0 = time.time()
        try:
            full = get_json("/v1/events/slug/" + e["slug"])["event"]; src = "event_slug"
        except Exception as exc:
            print("ERR", e["slug"], exc, file=sys.stderr); src = "list_fetch_failed"; full = e
        n += 1
        time.sleep(max(0.0, 0.5 - (time.time() - t0)))
    meta.append({"slug": full["slug"], "id": full["id"], "live": bool(full.get("live")), "start": full.get("startDate"),
                 "marketCounts": full.get("marketCounts"), "combos": full.get("combos"), "comboEnabled": full.get("comboEnabled"),
                 "groups": [(g.get("id"), g.get("title"), len(g.get("marketIds") or []), g.get("live")) for g in full.get("marketGroups") or []],
                 "eventState": full.get("eventState"), "period": full.get("period"), "n_markets": len(full.get("markets") or []), "src": src,
                 "tags": [t.get("slug") if isinstance(t, dict) else t for t in full.get("tags") or []][:8]})
    for m in full.get("markets") or []:
        out.write(json.dumps(slim(m, full, src)) + "\n")
    if n % 50 == 0: print(n, file=sys.stderr); out.flush()
json.dump({"t": now, "events": meta}, open("events_meta.json", "w"))
print("fetched", n, "events total", len(win), file=sys.stderr)
