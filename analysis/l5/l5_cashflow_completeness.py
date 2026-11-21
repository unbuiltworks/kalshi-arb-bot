"""Is +345,525.64 CASHFLOW-COMPLETE, not merely reproducible?

Reproducing the artefact proves the same inputs give the same output. It says
nothing about inputs that were never fetched. Five independent checks.
"""
import sys, gzip, json, datetime as dt
sys.path.insert(0, "/home/user/kalshi-arb-bot")
import importlib.util
from collections import Counter, defaultdict
from decimal import Decimal as D

spec = importlib.util.spec_from_file_location(
    "auth", "/home/user/kalshi-arb-bot/analysis/l5/l5_authoritative_reconstruction.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

# ---- CHECK 1: every row type present anywhere in the raw, series or not ----
seen=set(); types_all=Counter(); types_ser=Counter()
for r in A._raw_rows():
    k=A.row_id(r)
    if k in seen: continue
    seen.add(k)
    t=str(r.get("type"))
    types_all[t]+=1
    if A.SERIES in (r.get("slug") or ""): types_ser[t]+=1
print("CHECK 1 -- row types in the raw corpus (deduped)")
print("  all series :", dict(types_all))
print("  btc15m only:", dict(types_ser))
unknown=set(types_all)-A.KNOWN
print("  types NOT in {TRADE,REDEEM,incentives}:", unknown or "none")
print("  NOTE: a MERGE/SPLIT/CONVERSION type would appear here if present.")
print()

# ---- CHECK 2: time coverage, and gaps ----
trades, redeem, slug_of, inc, sells = A.load()
ws_list=sorted(x for x in (A.window_start(s) for s in slug_of.values()) if x)
print("CHECK 2 -- window coverage")
f=lambda x: dt.datetime.fromtimestamp(x,dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
print("  first window %s   last window %s" % (f(ws_list[0]), f(ws_list[-1])))
span_days=(ws_list[-1]-ws_list[0])/86400
print("  span %.1f days, %d windows traded" % (span_days, len(ws_list)))
print("  windows available in span (15m): %.0f  -> participation %.1f%%"
      % (span_days*96, 100.0*len(ws_list)/(span_days*96)))
gaps=[]
for a,b in zip(ws_list, ws_list[1:]):
    d=b-a
    if d > 6*3600: gaps.append((a,b,d/3600.0))
print("  gaps > 6h between traded windows:", len(gaps))
for a,b,h in gaps[:8]:
    print("     %s -> %s  (%.1f h)" % (f(a), f(b), h))
print()

# ---- CHECK 3: redeem_in >= matched pairs?  Unredeemed matched inventory ----
print("CHECK 3 -- is any MATCHED inventory unredeemed?")
bad=[]; unresolved=[]; tot_short=D(0)
for cid, rows in trades.items():
    _, s = A.walk(rows)
    rd = redeem.get(cid)
    if rd is None:
        unresolved.append((cid, s["spend"], s["matched"])); continue
    if rd + D("0.000001") < s["matched"]:
        bad.append((slug_of.get(cid), str(s["matched"]), str(rd)))
        tot_short += s["matched"] - rd
print("  resolved windows with redeem < matched pairs:", len(bad))
for b in bad[:8]: print("     slug=%s matched=%s redeem=%s" % b)
print("  total shortfall (pairs not redeemed): %s" % tot_short)
print("  unresolved windows (no REDEEM row at all):", len(unresolved))
us=sum(x[1] for x in unresolved); um=sum(x[2] for x in unresolved)
print("  their spend %.2f ; their matched pairs %.2f" % (us, um))
print("     -> booked as TOTAL LOSS of spend by the artefact.")
print("     -> but matched pairs redeem $1 regardless, so the floor on their")
print("        true value is %.2f, i.e. the worst case OVERSTATES loss by that." % um)
print()

# ---- CHECK 4: outstanding inventory per the positions endpoints ----
print("CHECK 4 -- independent positions files")
for path in ("data/l5/raw/positions_all.jsonl",
             "data/l5/raw/positions_redeemable.jsonl",
             "data/l5/raw/positions_closed.jsonl"):
    n=0; ser=0; sz=D(0); val=D(0); keys=set()
    try:
        for ln in open(path):
            ln=ln.strip()
            if not ln: continue
            try: d=json.loads(ln)
            except ValueError: continue
            for p in (d.get("payload") or []):
                n+=1; keys|=set(p.keys())
                if A.SERIES in (p.get("slug") or ""):
                    ser+=1
                    sz += D(str(p.get("size") or 0))
                    for vk in ("currentValue","value","initialValue"):
                        if p.get(vk) is not None:
                            val += D(str(p.get(vk))); break
    except FileNotFoundError:
        print("  %s : absent" % path); continue
    print("  %-44s rows=%6d btc15m=%6d size=%12.2f value=%12.2f"
          % (path.split("/")[-1], n, ser, sz, val))
print("  position fields:", sorted(keys)[:18])
print()

# ---- CHECK 5: non-series cashflows ----
print("CHECK 5 -- cashflows EXCLUDED from the btc15m figure")
other=Counter(); other_usd=defaultdict(lambda: D(0))
seen2=set()
for r in A._raw_rows():
    k=A.row_id(r)
    if k in seen2: continue
    seen2.add(k)
    slug=r.get("slug") or ""
    if A.SERIES in slug: continue
    t=str(r.get("type"))
    other[t]+=1
    other_usd[t]+=D(str(r.get("usdcSize") or 0))
print("  non-btc15m rows by type and usdcSize:")
for t in sorted(other):
    print("     %-14s n=%7d  usdc=%14.2f" % (t, other[t], other_usd[t]))
print("  -> these belong to OTHER series (5m etc). They do not affect a")
print("     btc15m-only figure, but they are not zero and are named here.")
print("  incentives inside btc15m:", {k:str(v) for k,v in inc.items()} or "NONE")
