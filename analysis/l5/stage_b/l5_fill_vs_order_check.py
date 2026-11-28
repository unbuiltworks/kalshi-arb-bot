import gzip, json, collections

def rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except Exception: continue
            pay = d.get("payload")
            if isinstance(pay, list):
                for r in pay: yield r
            elif isinstance(d, dict) and d.get("slug"): yield d

seen = set()
byw = collections.defaultdict(lambda: {"rows": 0, "tx": set(), "ts": set(),
                                       "px": set()})
for p in ("data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz",
          "data/l5/ops/activity_20260901.jsonl"):
    try:
        for r in rows(p):
            k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
                 r.get("size"), r.get("side"))
            if k in seen: continue
            seen.add(k)
            s = str(r.get("slug") or "")
            if not s.startswith("btc-updown-15m-") or r.get("type") != "TRADE":
                continue
            w = byw[s]
            w["rows"] += 1
            w["tx"].add(r.get("transactionHash"))
            w["ts"].add(int(r["timestamp"]))
            w["px"].add((int(r["timestamp"]), str(r.get("outcome")),
                         round(float(r.get("price") or 0), 4)))
    except FileNotFoundError:
        pass

def q(vals, name):
    v = sorted(vals)
    print("   %-34s p10=%d median=%d p90=%d mean=%.1f"
          % (name, v[len(v)//10], v[len(v)//2], v[len(v)*9//10],
             sum(v)/len(v)))

print("BTC15m windows: %d" % len(byw))
q([w["rows"] for w in byw.values()], "fill ROWS per window")
q([len(w["tx"]) for w in byw.values()], "distinct transactionHash per window")
q([len(w["ts"]) for w in byw.values()], "distinct timestamps (seconds) per win")
q([len(w["px"]) for w in byw.values()], "distinct (t,outcome,price) per window")
tot_rows = sum(w["rows"] for w in byw.values())
tot_tx = sum(len(w["tx"]) for w in byw.values())
print("\n   rows/tx overall = %.2f  (how many fill rows share one tx)"
      % (tot_rows / max(1, tot_tx)))
