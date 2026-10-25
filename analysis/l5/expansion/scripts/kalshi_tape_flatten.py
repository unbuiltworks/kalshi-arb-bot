"""Flatten the canonical L5 Kalshi 5m tape into one row per (snapshot, market).
Read-only. No orders. No fill claims."""
import json, glob, os, sys, datetime as dt
from decimal import Decimal as D

TAPE = sys.argv[1]
OUT = sys.argv[2]

def ts(v):
    if not v: return None
    try: return dt.datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
    except Exception: return None

def best(rows, hi=True):
    vals=[]
    for lv in rows or []:
        try: p=D(str(lv[0])); q=D(str(lv[1]))
        except Exception: continue
        if D(0)<p<D(1) and q>0: vals.append((p,q))
    if not vals: return (None,None)
    vals.sort(key=lambda x:x[0], reverse=hi)
    return vals[0]

def depth_within(rows, touch, band):
    """Summed qty at levels within `band` of the touch price."""
    tot=D(0)
    for lv in rows or []:
        try: p=D(str(lv[0])); q=D(str(lv[1]))
        except Exception: continue
        if touch is not None and abs(p-touch)<=band: tot+=q
    return tot

rows=[]
for path in sorted(glob.glob(os.path.join(TAPE,"tape_*.jsonl"))):
    with open(path) as f:
        for line in f:
            try: snap=json.loads(line)
            except Exception: continue
            cts=snap.get("captured_ts")
            for series, blk in (snap.get("series") or {}).items():
                mkts=blk.get("markets") or []
                # index strikes by open_ts so we can derive settlement from the
                # next window's strike (next open index == this window's close index)
                by_open={}
                for m in mkts:
                    mr=m.get("market_raw") or {}
                    o=ts(mr.get("open_time"))
                    if o is not None and mr.get("floor_strike") is not None:
                        by_open[round(o)]=float(mr["floor_strike"])
                for m in mkts:
                    mr=m.get("market_raw") or {}
                    bk=(m.get("orderbook_raw") or {}).get("orderbook_fp") or {}
                    trades=(m.get("trades_raw") or {}).get("trades") or []
                    close=ts(mr.get("close_time")); open_=ts(mr.get("open_time"))
                    ybid,ybq=best(bk.get("yes_dollars"))
                    nbid,nbq=best(bk.get("no_dollars"))
                    strike=mr.get("floor_strike")
                    nxt=by_open.get(round(close)) if close is not None else None
                    settle=None
                    if strike is not None and nxt is not None:
                        settle = "YES" if float(nxt)>=float(strike) else "NO"
                    # tape imbalance exactly as Policy D: share-weighted, last 30 prints
                    pr=[]
                    for t in trades:
                        side=str(t.get("taker_side") or "").lower()
                        if side not in ("yes","no"): continue
                        try:
                            p=D(str(t.get("yes_price_dollars") or 0)); q=D(str(t.get("count_fp") or 0))
                        except Exception: continue
                        if not (D(0)<p<D(1)) or q<=0: continue
                        pr.append((ts(t.get("created_time")) or 0, str(t.get("trade_id")), side, p, q))
                    pr.sort()
                    seen=set(); ded=[]
                    for x in pr:
                        if x[1] in seen: continue
                        seen.add(x[1]); ded.append(x)
                    last30=ded[-30:]
                    yq=sum((x[4] for x in last30 if x[2]=="yes"), D(0))
                    nq=sum((x[4] for x in last30 if x[2]=="no"), D(0))
                    tot=yq+nq
                    imb = float((yq-nq)/tot) if tot>0 else None
                    contra=None
                    if imb is not None and len(last30)==30:
                        if imb>0.10: contra="NO"
                        elif imb<-0.10: contra="YES"
                    rows.append({
                        "series":series,"ticker":mr.get("ticker"),"captured_ts":cts,
                        "open_ts":open_,"close_ts":close,
                        "secs_to_close": (close-cts) if (close and cts) else None,
                        "strike":strike,"next_strike":nxt,"derived_settle":settle,
                        "status":mr.get("status"),
                        "yes_bid":str(ybid) if ybid is not None else None,
                        "yes_bid_qty":str(ybq) if ybq is not None else None,
                        "no_bid":str(nbid) if nbid is not None else None,
                        "no_bid_qty":str(nbq) if nbq is not None else None,
                        "yes_ask":mr.get("yes_ask_dollars"),"no_ask":mr.get("no_ask_dollars"),
                        "last_price":mr.get("last_price_dollars"),
                        "volume_fp":mr.get("volume_fp"),"volume_24h_fp":mr.get("volume_24h_fp"),
                        "open_interest_fp":mr.get("open_interest_fp"),
                        "yes_levels":len(bk.get("yes_dollars") or []),
                        "no_levels":len(bk.get("no_dollars") or []),
                        "yes_depth_5c":str(depth_within(bk.get("yes_dollars"),ybid,D("0.05"))),
                        "no_depth_5c":str(depth_within(bk.get("no_dollars"),nbid,D("0.05"))),
                        "trades_6m":len(ded),"trades_truncated":len(trades)>=500,
                        "tape_n":len(last30),"tape_imbalance":imb,"contra_outcome":contra,
                    })
with open(OUT,"w") as f:
    for r in rows: f.write(json.dumps(r,default=str)+"\n")
print("rows:",len(rows))
