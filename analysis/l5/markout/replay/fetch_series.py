"""Step 1: list closed+open international events for the relevant series whose
slug date is 2026-11-12..2026-11-19; keep a slim record per event."""
import json, os, re, sys
from http_util import get, HERE
G = "https://gamma-api.polymarket.com"
SERIES = {"setkameua": 12329, "setkamemd": 12330, "setkamecz": 12331,
          "atp": 10365, "atp-doubles": 11632, "wta": 10366, "wta-doubles": 11633,
          "itf": 11634, "wtt": 12951, "wttmen": 11198, "wttwom": 11199,
          "cs2": 10310, "dota2": 10309, "test": 10661, "ttelite": 12324}
KEEP = ("slug", "question", "outcomes", "outcomePrices", "clobTokenIds", "closed",
        "sportsMarketType", "gameStartTime", "closedTime", "umaResolutionStatus",
        "endDate", "volumeNum", "negRisk")
DATE = re.compile(r"(2026-11-\d\d)")
out = {}

for name, sid in SERIES.items():
    n = 0
    # two passes (default order, then explicit id order) and merge by slug:
    # offset paging without an explicit order is not guaranteed stable.
    for closed, extra in (("true", ""), ("false", ""),
                          ("true", "&order=id&ascending=true"),
                          ("false", "&order=id&ascending=true")):
        off = 0
        while True:
            ev = get(f"{G}/events?series_id={sid}&closed={closed}"
                     f"&end_date_min=2026-11-12T00:00:00Z&end_date_max=2026-11-30T00:00:00Z"
                     f"&limit=50&offset={off}{extra}") or []
            for e in ev:
                m = DATE.search(e.get("slug") or "")
                if not m or not ("2026-11-12" <= m.group(1) <= "2026-11-19"):
                    continue
                out[e["slug"]] = {"series": name, "slug": e["slug"], "title": e.get("title"),
                                  "closed": e.get("closed"), "endDate": e.get("endDate"),
                                  "startTime": e.get("startTime"),
                                  "markets": [{k: x.get(k) for k in KEEP} for x in e.get("markets") or []]}
                n += 1
            if len(ev) < 50:
                break
            off += 50
    print(name, n, file=sys.stderr)
# Pass B (added after pass A proved incomplete): page by event creation day
# (start_date window), with and without the closed flag, and merge by slug.
DAYS = ["2026-11-%02d" % d for d in range(8, 21)]
for name, sid in SERIES.items():
    n = 0
    for i in range(len(DAYS) - 1):
        for cl in ("", "&closed=true", "&closed=false"):
            off = 0
            while True:
                ev = get(f"{G}/events?series_id={sid}{cl}"
                         f"&start_date_min={DAYS[i]}T00:00:00Z&start_date_max={DAYS[i+1]}T00:00:00Z"
                         f"&limit=50&offset={off}&order=id&ascending=true") or []
                for e in ev:
                    m = DATE.search(e.get("slug") or "")
                    if not m or not ("2026-11-12" <= m.group(1) <= "2026-11-19"):
                        continue
                    if e["slug"] not in out:
                        n += 1
                    out[e["slug"]] = {"series": name, "slug": e["slug"], "title": e.get("title"),
                                      "closed": e.get("closed"), "endDate": e.get("endDate"),
                                      "startTime": e.get("startTime"),
                                      "markets": [{k: x.get(k) for k in KEEP} for x in e.get("markets") or []]}
                if len(ev) < 50:
                    break
                off += 50
    print(name, "new in pass B:", n, file=sys.stderr)
json.dump(out, open(os.path.join(HERE, "intl_events.json"), "w"))
print(len(out))
