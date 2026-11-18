import json, collections, statistics
D="data/l5/competitor_0x8dxd/"
t_in=[]; px=[]; sz=[]; both=collections.defaultdict(set)
per_window=collections.defaultdict(list)
nbuy=nsell=0
with open(D+"trades.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except Exception: continue
        s=r.get("slug") or ""
        if "updown-5m" not in s: continue
        try: w=int(s.split("-")[-1])
        except Exception: continue
        t=r["timestamp"]-w
        if not (0<=t<=300): continue
        if (r.get("side") or "").upper()=="BUY": nbuy+=1
        else: nsell+=1; continue
        t_in.append(t); px.append(float(r["price"])); sz.append(float(r["size"]))
        both[s].add(r["outcome"])
        per_window[s].append((t,r["outcome"],float(r["price"]),float(r["size"])))
def pct(a,q): a=sorted(a); return a[int(q*(len(a)-1))]
print(f"0x8dxd BUY prints inside a 5m window: {len(t_in):,}   (sells {nsell:,})")
print(f"\nTIME IN WINDOW (s)  p10 {pct(t_in,.10):5.0f}  p50 {pct(t_in,.50):5.0f}  p90 {pct(t_in,.90):5.0f}  max {max(t_in):.0f}")
print(f"  share at t > 270s (our no_new_entry_after_s): {100.0*sum(1 for t in t_in if t>270)/len(t_in):5.1f}%")
print(f"  share at t > 240s                           : {100.0*sum(1 for t in t_in if t>240)/len(t_in):5.1f}%")
print(f"\nPRICE               p10 {pct(px,.10):5.3f}  p50 {pct(px,.50):5.3f}  p90 {pct(px,.90):5.3f}  max {max(px):.3f}")
print(f"  share above 0.90 (our band_hi)              : {100.0*sum(1 for p in px if p>0.90)/len(px):5.1f}%")
print(f"  share in our band 0.51-0.90                 : {100.0*sum(1 for p in px if 0.51<=p<=0.90)/len(px):5.1f}%")
print(f"  share at/below 0.10 (our 8c straddle rung)   : {100.0*sum(1 for p in px if p<=0.10)/len(px):5.1f}%")
print(f"\nSIZE (shares)       p10 {pct(sz,.10):7.2f}  p50 {pct(sz,.50):7.2f}  p90 {pct(sz,.90):7.2f}")
print(f"  median notional $                           : {pct([a*b for a,b in zip(px,sz)],.5):7.2f}")
one=sum(1 for v in both.values() if len(v)==1)
print(f"\nWINDOWS: {len(both):,}   one-sided only {one:,} ({100.0*one/len(both):.1f}%)   two-sided {len(both)-one:,}")
# entries per window and whether later buys are same-side extends
ext=comp=0
for s,rows in per_window.items():
    rows.sort()
    seen=set()
    for t,o,p,q in rows:
        if o in seen: ext+=1
        elif seen: comp+=1
        seen.add(o)
print(f"ACTION SHAPE: first-on-side {len(both):,}+, same-side EXTEND {ext:,}, opposite-side COMPLETE {comp:,}")
