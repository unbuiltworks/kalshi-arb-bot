"""READ-ONLY market ranking across the PMUS recorder universe.

Observable execution-quality features only. No orders, no execution imports,
no recorder capacity or admission change. Nothing here is a strategy change and
nothing here is realized P&L.
"""
from __future__ import annotations
import gzip, json, collections, statistics as st
from datetime import datetime, timezone
from decimal import Decimal as D

REPO = "/home/user/kalshi-arb-bot/"
U = [json.loads(l) for l in gzip.open(
    REPO + "analysis/pmus/universe/live_funnel_rows.jsonl.gz", "rt") if l.strip()]
S = [json.loads(l) for l in gzip.open(
    REPO + "analysis/l5/streak_shadow/live_rows.jsonl.gz", "rt") if l.strip()]

def dec(v):
    try:
        return D(str(v))
    except Exception:
        return None

print("=" * 78)
print("PMUS UNIVERSE -- EXECUTION-QUALITY RANKING   (READ-ONLY, no admission change)")
print("=" * 78)
print(f"live markets in snapshot : {len(U)}")
seat = [r for r in U if r.get("has_recorder_seat")]
print(f"WITH a recorder seat     : {len(seat)}   ({len(seat)/len(U):.2%})")
print(f"without a seat           : {len(U)-len(seat)}  <- invisible, not refused")

print("\n--- WHY MARKETS FALL OUT (funnel buckets) ---")
for k, v in collections.Counter(r.get("bucket") for r in U).most_common():
    print(f"  {str(k):22s} {v:6d}  {v/len(U):6.2%}")

print("\n--- FEATURE AVAILABILITY (can we even rank on it?) ---")
def avail(field, rows, label=None):
    have = sum(1 for r in rows if r.get(field) not in (None, "", "None"))
    print(f"  {label or field:26s} {have:6d}/{len(rows):6d}  {have/max(len(rows),1):7.2%}")
for f in ("best_bid", "best_ask", "spread", "depth", "two_sided",
          "recent_flow", "min_qty", "end_date", "tick"):
    avail(f, U)
print("  -- same fields, SEAT-ONLY subset --")
for f in ("depth", "two_sided", "recent_flow"):
    avail(f, seat)

print("\n--- SEATED UNIVERSE: the only markets with execution-quality evidence ---")
def num(r, f):
    v = dec(r.get(f))
    return float(v) if v is not None else None
sp = [num(r, "spread") for r in seat if num(r, "spread") is not None]
dp = [num(r, "depth") for r in seat if num(r, "depth") is not None]
if sp:
    print(f"  spread   n={len(sp):3d}  p25 {st.quantiles(sp,n=4)[0]:.4f}  p50 {st.median(sp):.4f}  p75 {st.quantiles(sp,n=4)[2]:.4f}")
if dp:
    print(f"  depth    n={len(dp):3d}  p25 {st.quantiles(dp,n=4)[0]:.2f}  p50 {st.median(dp):.2f}  p75 {st.quantiles(dp,n=4)[2]:.2f}")
ts = collections.Counter(str(r.get("two_sided")) for r in seat)
print(f"  two_sided: {dict(ts)}")

print("\n--- CATEGORY CLUSTER (all live markets / seated) ---")
cat_seat = collections.Counter(r.get("category") for r in seat)
for k, v in collections.Counter(r.get("category") for r in U).most_common():
    print(f"  {str(k):12s} live {v:6d}   seated {cat_seat.get(k,0):4d}   "
          f"eligible_now {sum(1 for r in U if r.get('category')==k and r.get('bucket')=='ELIGIBLE_NOW'):3d}")

print("\n--- SETTLEMENT HORIZON (seated markets) ---")
now = datetime(2026, 9, 13, 23, 0, tzinfo=timezone.utc)
hs = []
for r in seat:
    try:
        d = datetime.fromisoformat(str(r["end_date"]).replace("Z", "+00:00"))
        hs.append((d - now).total_seconds() / 3600.0)
    except Exception:
        pass
if hs:
    hs.sort()
    print(f"  hours to end_date  n={len(hs)}  min {hs[0]:.1f}  p50 {st.median(hs):.1f}  max {hs[-1]:.1f}")
    print(f"  settling within 6h : {sum(1 for h in hs if h <= 6)}")
    print(f"  settling within 24h: {sum(1 for h in hs if h <= 24)}")

print("\n" + "=" * 78)
print("PER-MARKET EXECUTION HISTORY  (what the engine actually did)")
print("=" * 78)
by = collections.defaultdict(list)
for r in S:
    by[r.get("slug")].append(r)
print(f"{'market':42s} {'dec':>4s} {'OPEN':>4s} {'EXT':>4s} {'CMP':>4s} "
      f"{'fills':>5s} {'conv':>6s}")
rowsum = []
for slug, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
    acts = collections.Counter(r.get("acted") for r in rs)
    comp = [r for r in rs if r.get("acted") == "complete"]
    fil = [r for r in comp if r.get("fill_outcome") == "filled"]
    conv = len(fil) / len(comp) if comp else None
    rowsum.append((slug, len(rs), acts.get("open",0), acts.get("extend",0),
                   len(comp), len(fil), conv))
for slug, n, o, e, c, f, conv in rowsum[:14]:
    cv = f"{conv:.1%}" if conv is not None else "  n/a"
    print(f"{slug[:42]:42s} {n:4d} {o:4d} {e:4d} {c:4d} {f:5d} {cv:>6s}")
tot_c = sum(r[4] for r in rowsum); tot_f = sum(r[5] for r in rowsum)
print(f"{'TOTAL':42s} {sum(r[1] for r in rowsum):4d} {sum(r[2] for r in rowsum):4d} "
      f"{sum(r[3] for r in rowsum):4d} {tot_c:4d} {tot_f:5d} "
      f"{tot_f/max(tot_c,1):5.1%}")
print(f"\n  markets with >=1 COMPLETE decision : {sum(1 for r in rowsum if r[4])}")
print(f"  markets with >=1 COMPLETE FILL     : {sum(1 for r in rowsum if r[5])}")

print("\n--- CAN THE FROZEN <=0.97 CRITERIA BE EVALUATED ON THIS HISTORY? ---")
keys = set()
for r in S:
    keys |= set(r.keys())
has_depth = [k for k in keys if "depth" in k.lower()]
has_age = [k for k in keys if "age" in k.lower() or "stale" in k.lower()]
print(f"  executable-depth field present : {has_depth or 'NO'}")
print(f"  quote-age field present        : {has_age or 'NO'}")
print("  => every historical row stops at 'no authenticated quote age' /")
print("     'observed depth not recorded'. NO hypothetical fill is assigned.")
print("     The frozen criteria are UNEVALUABLE on all pre-cutover data.")
