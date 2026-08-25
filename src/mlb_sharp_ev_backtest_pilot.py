"""Quota-bounded, read-only MLB sharp-line EV historical pilot.

Purpose: make the Aug. 28 Evidence-B pull real without lookahead or order entry.
For each settled Kalshi KXMLBGAME market, use one Odds API sharp-book snapshot
~60m before first pitch and another ~5m before first pitch, then align the entry
snapshot to the latest Kalshi 1-minute bid/ask candle ending at or before the
sportsbook snapshot timestamp.

This pilot is deliberately not a final decision table: it reports gross outcome
P&L because historical Kalshi fee attribution is not yet attached. It does report
n, timestamp alignment, multiplicative/power devig, and sharp closing-line value.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
import statistics
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ODDS_URL = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXMLBGAME"
SHARP_BOOKS = "pinnacle,betonlineag,bovada,lowvig,betus"
ET = ZoneInfo("America/New_York")
TICKER_RE = re.compile(r"^KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})(\d{4})")
MONTH = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

ALIASES = {
    "arizona":"diamondbacks", "atlanta":"braves", "baltimore":"orioles", "boston":"red sox",
    "chicago c":"cubs", "chicago cubs":"cubs", "chicago w":"white sox", "chicago white sox":"white sox",
    "cincinnati":"reds", "cleveland":"guardians", "colorado":"rockies", "detroit":"tigers",
    "houston":"astros", "kansas city":"royals", "la a":"angels", "los angeles a":"angels",
    "la d":"dodgers", "los angeles d":"dodgers", "miami":"marlins", "milwaukee":"brewers",
    "minnesota":"twins", "new york m":"mets", "new york y":"yankees", "athletics":"athletics",
    "oakland":"athletics", "philadelphia":"phillies", "pittsburgh":"pirates", "san diego":"padres",
    "san francisco":"giants", "seattle":"mariners", "st. louis":"cardinals", "st louis":"cardinals",
    "tampa bay":"rays", "texas":"rangers", "toronto":"blue jays", "washington":"nationals",
}


def parse_start(ticker: str) -> datetime | None:
    m = TICKER_RE.match(ticker)
    if not m: return None
    yy, mon, dd, hhmm = m.groups()
    month = MONTH.get(mon)
    if not month: return None
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    try:
        return datetime(2000 + int(yy), month, int(dd), hh, mm, tzinfo=ET).astimezone(timezone.utc)
    except ValueError:
        return None


def split_title(title: str) -> tuple[str,str] | None:
    raw = title.replace(" Winner?", "").replace(" winner?", "").strip()
    parts = re.split(r"\s+vs\s+", raw, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def name_matches(kalshi_name: str, odds_name: str) -> bool:
    k = re.sub(r"\s+", " ", kalshi_name.strip().lower())
    o = odds_name.strip().lower()
    alias = ALIASES.get(k)
    if alias: return alias in o
    return k in o or o in k


def power_devig(q_yes: float, q_no: float) -> tuple[float,float] | None:
    if not (0 < q_yes < 1 and 0 < q_no < 1): return None
    lo, hi = 0.01, 20.0
    for _ in range(100):
        mid = (lo + hi) / 2
        s = q_yes ** mid + q_no ** mid
        if s > 1: lo = mid
        else: hi = mid
    k = (lo + hi) / 2
    py, pn = q_yes ** k, q_no ** k
    s = py + pn
    return (py/s, pn/s) if s > 0 else None


def fair_for_event(event: dict[str,Any], kalshi_yes_name: str) -> dict[str,Any] | None:
    mult, power, books = [], [], []
    for book in event.get("bookmakers") or []:
        h2h = next((m for m in (book.get("markets") or []) if m.get("key") == "h2h"), None)
        if not h2h: continue
        outcomes = h2h.get("outcomes") or []
        if len(outcomes) != 2: continue
        probs=[]
        yes_idx=None
        for i,row in enumerate(outcomes):
            try: dec=float(row.get("price")); q=1.0/dec
            except (TypeError,ValueError,ZeroDivisionError): q=0.0
            probs.append(q)
            if name_matches(kalshi_yes_name, str(row.get("name") or "")): yes_idx=i
        if yes_idx is None or len(probs)!=2 or min(probs)<=0: continue
        s=sum(probs)
        if s<=1: continue
        p_mult=probs[yes_idx]/s
        p_pow=power_devig(probs[yes_idx], probs[1-yes_idx])
        if p_pow is None: continue
        mult.append(p_mult); power.append(p_pow[0]); books.append(str(book.get("key") or book.get("title") or ""))
    if not mult: return None
    return {"multiplicative":statistics.median(mult),"power":statistics.median(power),"book_count":len(mult),"books":sorted(set(books))}


def get_events_for_snapshot(http:httpx.Client, key:str, requested:datetime, cache:dict[str,Any], quota_floor:int) -> tuple[dict[str,Any] | None,dict[str,str]]:
    req = requested.replace(second=0,microsecond=0).isoformat().replace("+00:00","Z")
    if req in cache: return cache[req], {"cached":"true"}
    r=http.get(ODDS_URL,params={"apiKey":key,"bookmakers":SHARP_BOOKS,"markets":"h2h","oddsFormat":"decimal","dateFormat":"iso","date":req})
    quota={"remaining":r.headers.get("x-requests-remaining",""),"used":r.headers.get("x-requests-used",""),"last_cost":r.headers.get("x-requests-last","")}
    if r.status_code != 200:
        cache[req]={"error":r.text[:300],"http_status":r.status_code}; return cache[req],quota
    payload=r.json(); cache[req]=payload
    try: remaining=int(quota["remaining"])
    except Exception: remaining=quota_floor+1
    if remaining <= quota_floor: cache["__quota_stop__"]=True
    return payload,quota


def find_event(payload:dict[str,Any], pair:tuple[str,str], start:datetime) -> dict[str,Any] | None:
    for e in payload.get("data") or []:
        try: c=datetime.fromisoformat(str(e.get("commence_time")).replace("Z","+00:00")).astimezone(timezone.utc)
        except Exception: continue
        if abs((c-start).total_seconds()) > 15*60: continue
        names=[str(e.get("away_team") or ""),str(e.get("home_team") or "")]
        if all(any(name_matches(k,n) for n in names) for k in pair): return e
    return None


def latest_kalshi_candle(http:httpx.Client,ticker:str,snapshot_iso:str) -> tuple[dict[str,Any] | None,int | None]:
    snap=int(datetime.fromisoformat(snapshot_iso.replace("Z","+00:00")).timestamp())
    r=http.get(f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks",params={"start_ts":snap-300,"end_ts":snap+60,"period_interval":1})
    if r.status_code != 200: return None,None
    eligible=[]
    for row in (r.json() or {}).get("candlesticks") or []:
        try: ts=int(row.get("end_period_ts"))
        except Exception: continue
        if ts <= snap: eligible.append((ts,row))
    if not eligible: return None,None
    ts,row=max(eligible,key=lambda x:x[0]); return row,snap-ts


def close_price(side:Any) -> float | None:
    if not isinstance(side,dict): return None
    for k in ("close_dollars","close"):
        if side.get(k) is not None:
            try: return float(side[k])
            except Exception: return None
    return None


def settled_markets(http:httpx.Client, max_games:int) -> list[dict[str,Any]]:
    out=[]; cursor=""
    for _ in range(12):
        params={"series_ticker":SERIES,"limit":1000}
        if cursor: params["cursor"]=cursor
        r=http.get(f"{KALSHI_BASE}/historical/markets",params=params); r.raise_for_status(); body=r.json()
        for m in body.get("markets") or []:
            t=str(m.get("ticker") or "")
            if t.startswith("KXMLBGAME-26APR") and str(m.get("status") or "").lower() in {"settled","finalized"}:
                if parse_start(t): out.append(dict(m))
        cursor=str(body.get("cursor") or "")
        if not cursor or len(out)>=max_games: break
    out.sort(key=lambda m: parse_start(str(m.get("ticker"))) or datetime.max.replace(tzinfo=timezone.utc))
    # One binary market per event; defensively dedupe event ticker.
    seen=set(); uniq=[]
    for m in out:
        ev=str(m.get("event_ticker") or "")
        if ev and ev not in seen: seen.add(ev); uniq.append(m)
        if len(uniq)>=max_games: break
    return uniq


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--max-games",type=int,default=30); ap.add_argument("--quota-floor",type=int,default=3000); args=ap.parse_args()
    key=os.getenv("THE_ODDS_API_KEY","").strip()
    if not key: raise SystemExit("THE_ODDS_API_KEY is required")
    started=time.time(); cache:dict[str,Any]={}; rows=[]; last_quota={}
    with httpx.Client(timeout=30.0,follow_redirects=True) as http:
        markets=settled_markets(http,max(1,args.max_games))
        for m in markets:
            if cache.get("__quota_stop__"): break
            ticker=str(m.get("ticker") or ""); start=parse_start(ticker); title=split_title(str(m.get("title") or "")); yes_name=str(m.get("yes_sub_title") or "").strip()
            if not start or not title or not yes_name: continue
            entry_payload,q=get_events_for_snapshot(http,key,start-timedelta(minutes=60),cache,args.quota_floor); last_quota=q if q.get("remaining") else last_quota
            if not entry_payload or entry_payload.get("error") or cache.get("__quota_stop__"): break
            close_payload,q=get_events_for_snapshot(http,key,start-timedelta(minutes=5),cache,args.quota_floor); last_quota=q if q.get("remaining") else last_quota
            if not close_payload or close_payload.get("error"): continue
            e1=find_event(entry_payload,title,start); e2=find_event(close_payload,title,start)
            if not e1 or not e2: continue
            f1=fair_for_event(e1,yes_name); f2=fair_for_event(e2,yes_name)
            if not f1 or not f2: continue
            snap=str(entry_payload.get("timestamp") or ""); candle,lag=latest_kalshi_candle(http,ticker,snap)
            if candle is None or lag is None or lag>120: continue
            yes_ask=close_price(candle.get("yes_ask")); yes_bid=close_price(candle.get("yes_bid"))
            if yes_ask is None or yes_bid is None: continue
            result=str(m.get("result") or "").lower()
            for side,price,won in (("YES",yes_ask,result=="yes"),("NO",1.0-yes_bid,result=="no")):
                entry_mult=f1["multiplicative"] if side=="YES" else 1-f1["multiplicative"]
                entry_pow=f1["power"] if side=="YES" else 1-f1["power"]
                close_mult=f2["multiplicative"] if side=="YES" else 1-f2["multiplicative"]
                close_pow=f2["power"] if side=="YES" else 1-f2["power"]
                rows.append({"ticker":ticker,"side":side,"price":price,"won":won,"entry_edge_mult":entry_mult-price,"entry_edge_power":entry_pow-price,"clv_mult":close_mult-price,"clv_power":close_pow-price,"entry_fair_mult":entry_mult,"entry_fair_power":entry_pow,"close_fair_mult":close_mult,"close_fair_power":close_pow,"alignment_lag_s":lag,"entry_books":f1["books"],"close_books":f2["books"],"gross_pnl":(1-price if won else -price)})
    def summarize(method:str) -> dict[str,Any]:
        edge=f"entry_edge_{method}"; clv=f"clv_{method}"; sel=[r for r in rows if r[edge]>=0.03]
        return {"n_all":len(rows),"n_ge_3c":len(sel),"avg_edge_c":round(100*statistics.mean([r[edge] for r in sel]),4) if sel else None,"avg_clv_c":round(100*statistics.mean([r[clv] for r in sel]),4) if sel else None,"gross_roi_on_cost":round(sum(r["gross_pnl"] for r in sel)/sum(r["price"] for r in sel),6) if sel and sum(r["price"] for r in sel)>0 else None,"gross_pnl_per_contract":round(statistics.mean([r["gross_pnl"] for r in sel]),6) if sel else None,"wins":sum(1 for r in sel if r["won"])}
    inter=[r for r in rows if r["entry_edge_mult"]>=0.03 and r["entry_edge_power"]>=0.03]
    out={"status":"MLB_SHARP_EV_BACKTEST_PILOT","decision_grade":False,"reason_not_decision_grade":"pilot sample + historical Kalshi fee attribution pending","read_only":True,"order_entry":False,"kalshi_markets_requested":args.max_games,"side_observations":len(rows),"unique_games_matched":len({r["ticker"] for r in rows}),"snapshot_requests_paid":len([k for k in cache if not k.startswith("__")]),"quota":last_quota,"multiplicative":summarize("mult"),"power":summarize("power"),"intersection_ge_3c":{"n":len(inter),"avg_clv_mult_c":round(100*statistics.mean([r["clv_mult"] for r in inter]),4) if inter else None,"avg_clv_power_c":round(100*statistics.mean([r["clv_power"] for r in inter]),4) if inter else None,"gross_pnl_per_contract":round(statistics.mean([r["gross_pnl"] for r in inter]),6) if inter else None},"alignment":{"max_lag_s":max((r["alignment_lag_s"] for r in rows),default=None),"median_lag_s":statistics.median([r["alignment_lag_s"] for r in rows]) if rows else None},"sharp_books_config":SHARP_BOOKS.split(","),"elapsed_s":round(time.time()-started,2)}
    print(json.dumps(out,sort_keys=True,default=str))

if __name__=="__main__": main()
