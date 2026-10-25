import json,sys,statistics as st,collections
rows=[json.loads(l) for l in open(sys.argv[1])]
def num(v):
    try: return float(v)
    except Exception: return None
by=collections.defaultdict(list)
for r in rows: by[r["series"]].append(r)
for s,rs in sorted(by.items()):
    print("="*70); print(s, "snapshot-market rows:", len(rs))
    cur=[r for r in rs if (r["secs_to_close"] or 1e9) <= 900 and (r["secs_to_close"] or -1) > 0]
    print(" in-window rows (0<ttc<=900s):", len(cur))
    def stats(name, vals, fmt="%.2f"):
        vals=[v for v in vals if v is not None]
        if not vals: print("  %-22s n=0"%name); return
        vals=sorted(vals)
        print(("  %-22s n=%d  med="+fmt+"  mean="+fmt+"  p10="+fmt+"  p90="+fmt+"  max="+fmt)%(
            name,len(vals),st.median(vals),sum(vals)/len(vals),
            vals[int(.1*(len(vals)-1))],vals[int(.9*(len(vals)-1))],vals[-1]))
    stats("trades/6min", [r["trades_6m"] for r in cur], "%.0f")
    print("  truncated(>=500 cap):", sum(1 for r in cur if r["trades_truncated"]), "/", len(cur))
    print("  tape_n==30 (Policy D ready):", sum(1 for r in cur if r["tape_n"]==30), "/", len(cur))
    stats("yes_bid", [num(r["yes_bid"]) for r in cur], "%.4f")
    stats("yes_bid_qty", [num(r["yes_bid_qty"]) for r in cur], "%.0f")
    stats("no_bid_qty", [num(r["no_bid_qty"]) for r in cur], "%.0f")
    stats("yes_depth<=5c", [num(r["yes_depth_5c"]) for r in cur], "%.0f")
    stats("no_depth<=5c", [num(r["no_depth_5c"]) for r in cur], "%.0f")
    stats("book levels (yes)", [r["yes_levels"] for r in cur], "%.0f")
    stats("volume_fp (window)", [num(r["volume_fp"]) for r in cur], "%.0f")
    stats("open_interest_fp", [num(r["open_interest_fp"]) for r in cur], "%.0f")
    # spread in cents
    sp=[]
    for r in cur:
        yb,ya=num(r["yes_bid"]),num(r["yes_ask"])
        if yb and ya and ya>yb: sp.append((ya-yb)*100)
    stats("yes spread (cents)", sp, "%.2f")
    c=collections.Counter(r["contra_outcome"] for r in cur)
    print("  contra signal:", dict(c))
    print("  derived settle known:", sum(1 for r in cur if r["derived_settle"]), "/", len(cur))
