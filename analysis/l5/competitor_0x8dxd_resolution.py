"""Fetch AUTHORITATIVE venue resolution for each captured BTC5m window.

Why this exists: settlement cannot be read from 0x8dxd's redemption rows
alone. A window where the trader held only the LOSING side pays no
redemption, so a redemption-only definition of "settled" silently
excludes exactly the losing windows and manufactures a ~100% win rate.
The CLOB market record carries `winner` per token, which is independent
of whether the trader redeemed anything.

Read-only. Caches to resolutions.jsonl so it is resumable and never
re-fetches a condition already recorded.
"""
import json, os, sys, threading, urllib.request, queue

D = os.environ.get("CAP_DIR") or "/home/user/kalshi-arb-bot/data/l5/competitor_0x8dxd"
OUT = os.path.join(D, "resolutions.jsonl")
PREFIX = "btc-updown-5m-"

need = {}
for name in ("activity.jsonl", "trades.jsonl"):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        continue
    for line in open(p):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("slug") or "").startswith(PREFIX) and r.get("condition_id"):
            need[r["condition_id"]] = r["slug"]

have = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            have.add(json.loads(line)["condition_id"])
        except Exception:
            pass
todo = [(c, s) for c, s in need.items() if c not in have]
print("conditions needed %d   cached %d   to fetch %d"
      % (len(need), len(have & set(need)), len(todo)))
sys.stdout.flush()

lock = threading.Lock()
fh = open(OUT, "a")
q = queue.Queue()
for item in todo:
    q.put(item)
done = [0]


def work():
    while True:
        try:
            cond, slug = q.get_nowait()
        except queue.Empty:
            return
        rec = {"condition_id": cond, "slug": slug}
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    "https://clob.polymarket.com/markets/" + cond,
                    headers={"User-Agent": "research-client"})
                d = json.load(urllib.request.urlopen(req, timeout=30))
                rec["closed"] = d.get("closed")
                rec["winner"] = None
                for t in d.get("tokens") or []:
                    if t.get("winner"):
                        rec["winner"] = t.get("outcome")
                rec["tokens"] = [{"outcome": t.get("outcome"),
                                  "winner": t.get("winner"),
                                  "price": t.get("price")}
                                 for t in d.get("tokens") or []]
                break
            except Exception as exc:
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
        with lock:
            fh.write(json.dumps(rec) + "\n")
            done[0] += 1
            if done[0] % 200 == 0:
                fh.flush()
                print("  %d/%d" % (done[0], len(todo))); sys.stdout.flush()


ts = [threading.Thread(target=work) for _ in range(8)]
for t in ts:
    t.start()
for t in ts:
    t.join()
fh.close()
print("wrote %s" % OUT)
