"""Fetch public gamma market metadata for every condition in an activity file. READ-ONLY, <=4 requests/second.
Gamma hides closed markets unless closed=true, so each batch is asked twice. Cache is a JSON dict
condition_id -> trimmed market. usage: fetch_gamma_markets.py <activity.jsonl.gz> <cache.json>"""
import gzip, json, os, sys, time, urllib.request
act, cache_p = sys.argv[1], sys.argv[2]
conds = set()
for l in gzip.open(act, "rt"):
    r = json.loads(l)
    if r.get("condition_id"): conds.add(r["condition_id"])
cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
todo = sorted(c for c in conds if c not in cache)
print("conditions", len(conds), "to fetch", len(todo), flush=True)
KEEP = ("slug", "conditionId", "outcomes", "outcomePrices", "clobTokenIds", "gameStartTime", "closed", "closedTime",
        "sportsMarketType", "umaResolutionStatus", "endDate", "feeSchedule", "feesEnabled", "negRisk", "line")
def get(url):
    for a in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research-client"}), timeout=40) as f:
                return json.load(f)
        except Exception as e:
            print("retry", a, type(e).__name__, flush=True); time.sleep(2 * (a + 1))
    return None
B = 40
import concurrent.futures as cf, threading
lock = threading.Lock()
def work(batch):                       # 4 workers, each request followed by a 1 s pause: <= 4 requests/second in total
    q = "&".join("condition_ids=" + c for c in batch); got = {}
    for extra in ("&closed=true", "&closed=false"):
        d = get("https://gamma-api.polymarket.com/markets?limit=100&" + q + extra)
        for m in d or []:
            got[m["conditionId"]] = {k: m.get(k) for k in KEEP}
        time.sleep(1.0)
    return got
with cf.ThreadPoolExecutor(4) as ex:
    for n, got in enumerate(ex.map(work, [todo[i:i + B] for i in range(0, len(todo), B)])):
        cache.update(got)
        if n % 50 == 0:
            json.dump(cache, open(cache_p, "w")); print("batches", n, "cached", len(cache), flush=True)
json.dump(cache, open(cache_p, "w"))
print("DONE cached", len(cache), "missing", sum(1 for c in conds if c not in cache))
