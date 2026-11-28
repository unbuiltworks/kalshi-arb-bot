import gzip, json, collections, statistics as st

def rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            pay = d.get("payload")
            if isinstance(pay, list):
                for r in pay:
                    yield r
            elif isinstance(d, dict) and d.get("slug"):
                yield d

seen, byw = set(), collections.defaultdict(list)
fam5 = collections.Counter()
for p in ("data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz",
          "data/l5/ops/activity_20260901.jsonl"):
    try:
        for r in rows(p):
            k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
                 r.get("size"), r.get("side"))
            if k in seen:
                continue
            seen.add(k)
            s = str(r.get("slug") or "")
            if s.startswith("btc-updown-5m-"):
                fam5["%s %s" % (r.get("type"), r.get("side"))] += 1
            if not s.startswith("btc-updown-15m-") or r.get("type") != "TRADE":
                continue
            try:
                ws = int(s.rsplit("-", 1)[1])
            except Exception:
                continue
            byw[s].append((int(r["timestamp"]) - ws, str(r.get("outcome")),
                           float(r.get("price") or 0),
                           float(r.get("size") or 0),
                           float(r.get("usdcSize") or 0)))
    except FileNotFoundError:
        pass

print("btc5m by (type, side) -- the family that DOES sell:")
for k, v in fam5.most_common(6):
    print("   %-18s %6d" % (k, v))

print("\nBTC15m trade windows: %d, trades: %d"
      % (len(byw), sum(len(v) for v in byw.values())))

n_act, first_px, first_sz, first_t, both, pair_rate = [], [], [], [], 0, []
seq_shape = collections.Counter()
for s, acts in byw.items():
    acts.sort()
    n_act.append(len(acts))
    t0, o0, p0, z0, _ = acts[0]
    first_px.append(p0); first_sz.append(z0); first_t.append(t0)
    ups = sum(a[3] for a in acts if a[1] == "Up")
    dns = sum(a[3] for a in acts if a[1] == "Down")
    if ups > 0 and dns > 0:
        both += 1
        pair_rate.append(min(ups, dns) / max(ups, dns))
    # shape of the first four actions, as same(S)/opposite(O) of the opener
    shape = "".join("S" if a[1] == o0 else "O" for a in acts[:4])
    seq_shape[shape] += 1

def q(v, name, pct=True):
    v = sorted(v)
    f = (lambda x: "%.3f" % x) if pct else (lambda x: "%.1f" % x)
    print("   %-22s n=%d  p10=%s  median=%s  p90=%s  mean=%s"
          % (name, len(v), f(v[len(v)//10]), f(v[len(v)//2]),
             f(v[len(v)*9//10]), f(sum(v)/len(v))))

print("\nOPENING DECISION (first trade of each window):")
q(first_px, "price"); q(first_sz, "size"); q(first_t, "t_in_window_s", False)
print("\nACTIONS PER WINDOW:"); q(n_act, "count", False)
print("\nTWO-SIDED BEHAVIOUR:")
print("   windows with BOTH outcomes bought: %d / %d  (%.1f%%)"
      % (both, len(byw), 100.0 * both / max(1, len(byw))))
q(pair_rate, "min/max side ratio")
print("\nFIRST FOUR ACTIONS, S=same side as opener, O=opposite:")
for k, v in seq_shape.most_common(10):
    print("   %-6s %6d  (%.1f%%)" % (k or "(1 action)", v,
                                     100.0 * v / max(1, len(byw))))
