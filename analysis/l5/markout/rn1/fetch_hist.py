"""Step 3. Public 1-minute price history per sampled token: clob /prices-history?market=<token>&startTs&endTs&fidelity=1,
from 20 min before the trader's first sampled fill of that token to 30 min after its last. <= 4 requests/second.
Stored per token: [[t, p], ...]"""
import concurrent.futures as cf, gzip, json
from common import *
S = json.load(open(SAMPLE)); os.makedirs(HIST_DIR, exist_ok=True)
span = {}
for f in S["fills"]:
    t = S["markets"][f["c"]]["tokens"][f["tok"]]; a, b = span.get(t, (9e18, 0)); span[t] = (min(a, f["ts"]), max(b, f["ts"]))
def work(t):
    p = os.path.join(HIST_DIR, t + ".json.gz")
    if os.path.exists(p): return None
    a, b = span[t]
    d = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1" % (t, a - 1200, b + 1800))
    if not d or "history" not in d: return "FAILED " + t[:12]
    json.dump([[h["t"], h["p"]] for h in d["history"]], gzip.open(p, "wt"))
    return None
with cf.ThreadPoolExecutor(4) as ex:
    bad = [x for x in ex.map(work, sorted(span)) if x]
print("tokens", len(span), "failed", len(bad), bad[:5])
