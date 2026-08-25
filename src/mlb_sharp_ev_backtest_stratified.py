"""Read-only Apr-Aug stratified MLB sharp-line EV backtest slice.

Uses the fixed precommitted >=3c threshold. Apr-Jun is labeled development;
Jul-Aug is labeled out-of-sample. No threshold fitting occurs here. Historical
Odds API requests stop at a quota floor. No order entry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
import json
import os
import statistics
import time
from typing import Any

import httpx

from src.mlb_sharp_ev_backtest_pilot import (
    KALSHI_BASE, SERIES, SHARP_BOOKS, close_price, fair_for_event,
    find_event, get_events_for_snapshot, name_matches, parse_start, split_title,
)


def _fetch_pages(http:httpx.Client, url:str, params:dict[str,Any], source:str) -> list[dict[str,Any]]:
    out=[]; cursor=""
    for _ in range(12):
        p=dict(params)
        if cursor: p["cursor"]=cursor
        r=http.get(url,params=p); r.raise_for_status(); body=r.json()
        for row in body.get("markets") or []:
            m=dict(row); m["_source"]=source; out.append(m)
        cursor=str(body.get("cursor") or "")
        if not cursor: break
    return out


def all_2026_mlb(http:httpx.Client) -> list[dict[str,Any]]:
    hist=_fetch_pages(http,f"{KALSHI_BASE}/historical/markets",{"series_ticker":SERIES,"limit":1000},"historical")
    live=_fetch_pages(http,f"{KALSHI_BASE}/markets",{"series_ticker":SERIES,"status":"settled","limit":1000},"live")
    seen=set(); out=[]
    for m in hist+live:
        t=str(m.get("ticker") or ""); start=parse_start(t)
        ev=str(m.get("event_ticker") or "")
        if not start or start.year!=2026 or start.month not in {4,5,6,7,8}: continue
        if str(m.get("status") or "").lower() not in {"settled","finalized"}: continue
        if not ev or ev in seen: continue
        seen.add(ev); out.append(m)
    out.sort(key=lambda m: parse_start(str(m.get("ticker"))))
    return out


def stratified(markets:list[dict[str,Any]], per_month:int) -> list[dict[str,Any]]:
    by=defaultdict(list)
    for m in markets:
        start=parse_start(str(m.get("ticker") or ""))
        if start: by[start.month].append(m)
    # Deterministic evenly spaced picks, not cherry-picked by price/outcome.
    selected=[]
    for month in (4,5,6,7,8):
        rows=by.get(month,[])
        if len(rows)<=per_month: selected.extend(rows); continue
        if per_month<=1: selected.append(rows[len(rows)//2]); continue
        idxs=[round(i*(len(rows)-1)/(per_month-1)) for i in range(per_month)]
        selected.extend(rows[i] for i in sorted(set(idxs)))
    selected.sort(key=lambda m: parse_start(str(m.get("ticker"))))
    return selected


def latest_candle(http:httpx.Client, market:dict[str,Any], snapshot_iso:str) -> tuple[dict[str,Any] | None,int | None]:
    from datetime import datetime
    snap=int(datetime.fromisoformat(snapshot_iso.replace("Z","+00:00")).timestamp())
    ticker=str(market.get("ticker") or "")
    if market.get("_source")=="historical":
        url=f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks"
    else:
        url=f"{KALSHI_BASE}/series/{SERIES}/markets/{ticker}/candlesticks"
    r=http.get(url,params={"start_ts":snap-300,"end_ts":snap+60,"period_interval":1})
    if r.status_code!=200: return None,None
    eligible=[]
    for row in (r.json() or {}).get("candlesticks") or []:
        try: ts=int(row.get("end_period_ts"))
        except Exception: continue
        if ts<=snap: eligible.append((ts,row))
    if not eligible: return None,None
    ts,row=max(eligible,key=lambda x:x[0]); return row,snap-ts


def summary(rows:list[dict[str,Any]], method:str) -> dict[str,Any]:
    edge=f"entry_edge_{method}"; clv=f"clv_{method}"; sel=[r for r in rows if r[edge]>=0.03]
    cost=sum(r["price"] for r in sel)
    return {
        "n_all":len(rows), "n_ge_3c":len(sel),
        "avg_edge_c":round(100*statistics.mean([r[edge] for r in sel]),4) if sel else None,
        "avg_clv_c":round(100*statistics.mean([r[clv] for r in sel]),4) if sel else None,
        "gross_roi_on_cost":round(sum(r["gross_pnl"] for r in sel)/cost,6) if cost>0 else None,
        "gross_pnl_per_contract":round(statistics.mean([r["gross_pnl"] for r in sel]),6) if sel else None,
        "wins":sum(1 for r in sel if r["won"]),
    }


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--per-month",type=int,default=25); ap.add_argument("--quota-floor",type=int,default=900); args=ap.parse_args()
    key=os.getenv("THE_ODDS_API_KEY","").strip()
    if not key: raise SystemExit("THE_ODDS_API_KEY is required")
    started=time.time(); cache:dict[str,Any]={}; rows=[]; last_quota={}; market_counts={}
    with httpx.Client(timeout=30.0,follow_redirects=True) as http:
        universe=all_2026_mlb(http); markets=stratified(universe,max(1,args.per_month))
        market_counts={str(m):sum(1 for x in markets if (parse_start(str(x.get("ticker"))) or started).month==m) for m in (4,5,6,7,8)}
        for market in markets:
            if cache.get("__quota_stop__"): break
            ticker=str(market.get("ticker") or ""); start=parse_start(ticker); title=split_title(str(market.get("title") or "")); yes_name=str(market.get("yes_sub_title") or "").strip()
            if not start or not title or not yes_name: continue
            entry,q=get_events_for_snapshot(http,key,start-timedelta(minutes=60),cache,args.quota_floor); last_quota=q if q.get("remaining") else last_quota
            if not entry or entry.get("error") or cache.get("__quota_stop__"): break
            close,q=get_events_for_snapshot(http,key,start-timedelta(minutes=5),cache,args.quota_floor); last_quota=q if q.get("remaining") else last_quota
            if not close or close.get("error"): continue
            e1=find_event(entry,title,start); e2=find_event(close,title,start)
            if not e1 or not e2: continue
            f1=fair_for_event(e1,yes_name); f2=fair_for_event(e2,yes_name)
            if not f1 or not f2: continue
            snap=str(entry.get("timestamp") or ""); candle,lag=latest_candle(http,market,snap)
            if candle is None or lag is None or lag>120: continue
            ya=close_price(candle.get("yes_ask")); yb=close_price(candle.get("yes_bid"))
            if ya is None or yb is None: continue
            result=str(market.get("result") or "").lower(); month=start.month; split="oos" if month>=7 else "development"
            for side,price,won in (("YES",ya,result=="yes"),("NO",1-yb,result=="no")):
                em=f1["multiplicative"] if side=="YES" else 1-f1["multiplicative"]
                ep=f1["power"] if side=="YES" else 1-f1["power"]
                cm=f2["multiplicative"] if side=="YES" else 1-f2["multiplicative"]
                cp=f2["power"] if side=="YES" else 1-f2["power"]
                rows.append({"ticker":ticker,"month":month,"split":split,"side":side,"price":price,"won":won,"entry_edge_mult":em-price,"entry_edge_power":ep-price,"clv_mult":cm-price,"clv_power":cp-price,"alignment_lag_s":lag,"gross_pnl":1-price if won else -price})
    oos=[r for r in rows if r["split"]=="oos"]; dev=[r for r in rows if r["split"]=="development"]
    inter=[r for r in rows if r["entry_edge_mult"]>=0.03 and r["entry_edge_power"]>=0.03]
    inter_oos=[r for r in oos if r["entry_edge_mult"]>=0.03 and r["entry_edge_power"]>=0.03]
    out={
        "status":"MLB_SHARP_EV_BACKTEST_STRATIFIED", "decision_grade":False,
        "reason_not_decision_grade":"historical Kalshi fee attribution pending; quota-bounded stratified sample",
        "read_only":True,"order_entry":False,"threshold_precommitted_c":3.0,
        "universe_games":len(all_2026_mlb(httpx.Client(timeout=30.0))) if False else None,
        "requested_per_month":args.per_month,"requested_market_counts":market_counts,
        "unique_games_matched":len({r["ticker"] for r in rows}),"side_observations":len(rows),
        "paid_snapshot_requests":len([k for k in cache if not k.startswith("__")]),"quota":last_quota,
        "all":{"multiplicative":summary(rows,"mult"),"power":summary(rows,"power"),"intersection_ge_3c_n":len(inter)},
        "development_apr_jun":{"multiplicative":summary(dev,"mult"),"power":summary(dev,"power")},
        "oos_jul_aug":{"multiplicative":summary(oos,"mult"),"power":summary(oos,"power"),"intersection_ge_3c_n":len(inter_oos)},
        "matched_games_by_month":{str(m):len({r["ticker"] for r in rows if r["month"]==m}) for m in (4,5,6,7,8)},
        "alignment":{"median_lag_s":statistics.median([r["alignment_lag_s"] for r in rows]) if rows else None,"max_lag_s":max((r["alignment_lag_s"] for r in rows),default=None)},
        "sharp_books_config":SHARP_BOOKS.split(","),"elapsed_s":round(time.time()-started,2),
    }
    print(json.dumps(out,sort_keys=True,default=str))

if __name__=="__main__": main()
