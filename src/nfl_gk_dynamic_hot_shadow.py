"""Dynamic read-only NFL Gemini<->Kalshi hot-book shadow scanner.

Cold discovery uses exact NFL game/team/family/line identities from the current
public Gemini and Kalshi catalogs. The cohort is selected by nearest kickoff only —
never by stale REST price positivity. Hot evaluation then comes exclusively from
live Gemini bookTicker and Kalshi orderbook_delta streams.

No order transport is imported. Complete NFL lifecycle rules remain CONDITIONAL,
so even live after-fee q=1 positives are economic evidence only.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import logging
import time
from typing import Any

import httpx

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.gemini_kalshi_execution_shadow import ShadowCandidate, TopOfBook, evaluate_pair, gemini_predictions_taker_fee
from src.gemini_kalshi_mlb_prop_hot_shadow import GeminiPropBooks
from src.nfl_identity_probe import NflIdentity, fetch_gemini, fetch_kalshi, normalize_gemini, normalize_kalshi
from src.nfl_rule_certifications import certification

D=Decimal
ONE=D("1")
logger=logging.getLogger("kalshi_arb.nfl_gk_dynamic_hot_shadow")


def _ceil_centicent(value:Decimal)->Decimal:
    return (value*D("10000")).to_integral_value(rounding=ROUND_CEILING)/D("10000")


def nfl_kalshi_taker_fee(price:Decimal, quantity:Decimal)->Decimal:
    if quantity<0 or price<0 or price>1: raise ValueError("invalid fee input")
    return _ceil_centicent(D("0.07")*quantity*price*(ONE-price))


@dataclass(frozen=True)
class HotPair:
    key:str
    family:str
    kickoff_ms:int
    gemini_symbol:str
    kalshi_ticker:str
    rule_status:str
    rule_blockers:tuple[str,...]


@dataclass
class PairState:
    pair:HotPair
    triggers:int=0
    both_books:int=0
    q1_depth:int=0
    strict_evaluations:int=0
    positives:int=0
    opens:int=0
    closes:int=0
    open_mono:float|None=None
    total_positive_ms:float=0.0
    longest_positive_ms:float=0.0
    max_profit:Decimal=D("0")
    rechecks_started:int=0
    rechecks_survived:int=0
    min_skew_ms:int|None=None
    max_skew_ms:int=0
    last_direction:str|None=None

    def state(self,positive:bool,now:float)->None:
        if positive and self.open_mono is None:
            self.open_mono=now; self.opens+=1
        elif not positive and self.open_mono is not None:
            dt=(now-self.open_mono)*1000
            self.total_positive_ms+=dt; self.longest_positive_ms=max(self.longest_positive_ms,dt)
            self.open_mono=None; self.closes+=1

    def finish(self,now:float)->None:
        if self.open_mono is not None:
            dt=(now-self.open_mono)*1000
            self.total_positive_ms+=dt; self.longest_positive_ms=max(self.longest_positive_ms,dt)
            self.open_mono=None

    def as_dict(self)->dict[str,Any]:
        p=self.pair
        return {
            "key":p.key,"family":p.family,
            "kickoff_utc":datetime.fromtimestamp(p.kickoff_ms/1000,tz=timezone.utc).isoformat(),
            "gemini":p.gemini_symbol,"kalshi":p.kalshi_ticker,
            "rule_status":p.rule_status,"rule_blockers":list(p.rule_blockers),
            "triggers":self.triggers,"both_books":self.both_books,"q1_depth":self.q1_depth,
            "strict_evaluations":self.strict_evaluations,"positive_observations":self.positives,
            "opens":self.opens,"closes":self.closes,
            "total_positive_ms":round(self.total_positive_ms,3),"longest_positive_ms":round(self.longest_positive_ms,3),
            "max_profit":str(self.max_profit),"rechecks_started":self.rechecks_started,
            "rechecks_survived":self.rechecks_survived,
            "recheck_survival_rate":self.rechecks_survived/self.rechecks_started if self.rechecks_started else None,
            "min_skew_ms":self.min_skew_ms,"max_skew_ms":self.max_skew_ms,"last_direction":self.last_direction,
        }


def discover_pairs(grows:list[NflIdentity],krows:list[NflIdentity],*,max_pairs:int,now_ms:int|None=None,max_start_skew_ms:int=300000)->tuple[list[HotPair],dict[str,int]]:
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    grouped:dict[str,dict[str,list[NflIdentity]]]=defaultdict(lambda:defaultdict(list))
    for row in grows+krows:
        grouped[row.proposition_key][row.venue].append(row)
    pairs:list[HotPair]=[]
    diag={"groups":len(grouped),"ambiguous":0,"start_skew_blocked":0,"past_blocked":0}
    for key,venues in grouped.items():
        gs={(x.market_id,x.start_ms):x for x in venues.get("gemini",[])}
        ks={(x.market_id,x.start_ms):x for x in venues.get("kalshi",[])}
        if len(gs)!=1 or len(ks)!=1:
            if gs and ks: diag["ambiguous"]+=1
            continue
        g=next(iter(gs.values())); k=next(iter(ks.values()))
        if abs(g.start_ms-k.start_ms)>max_start_skew_ms:
            diag["start_skew_blocked"]+=1; continue
        kickoff=min(g.start_ms,k.start_ms)
        if kickoff<now_ms:
            diag["past_blocked"]+=1; continue
        cert=certification("gemini","kalshi",g.family)
        pairs.append(HotPair(key,g.family,kickoff,g.market_id,k.market_id,cert.status.value,tuple(cert.blockers)))
    pairs.sort(key=lambda p:(p.kickoff_ms,p.family,p.key))
    return pairs[:max(1,max_pairs)],{**diag,"eligible":len(pairs),"selected":min(len(pairs),max(1,max_pairs))}


def kalshi_tob(q:Any,ticker:str)->TopOfBook:
    return TopOfBook("kalshi",ticker,int(q.received_ms),ONE-D(str(q.no_ask)),D(str(q.no_depth)),D(str(q.yes_ask)),D(str(q.yes_depth)))


def best(g:TopOfBook,k:TopOfBook,*,max_skew_ms:int)->ShadowCandidate|None:
    rows=evaluate_pair(g,k,gemini_fee=gemini_predictions_taker_fee,kalshi_fee=nfl_kalshi_taker_fee,max_quote_skew_ms=max_skew_ms,max_quantity=ONE)
    return max(rows,key=lambda x:x.locked_profit) if rows else None


def same_limits(direction:str,eg:TopOfBook,ek:TopOfBook,g:TopOfBook,k:TopOfBook)->bool:
    if direction=="GEMINI_YES_KALSHI_NO":
        return g.yes_ask<=eg.yes_ask and g.yes_ask_size>=ONE and k.no_ask<=ek.no_ask and k.no_ask_size>=ONE
    if direction=="KALSHI_YES_GEMINI_NO":
        return k.yes_ask<=ek.yes_ask and k.yes_ask_size>=ONE and g.no_ask<=eg.no_ask and g.no_ask_size>=ONE
    return False


class ShardedKalshiBooks:
    def __init__(self,tickers:list[str],*,shard_size:int=40)->None:
        self.shards=[EventingKalshiCrossBooks(tickers[i:i+shard_size]) for i in range(0,len(tickers),shard_size)]
        self.by_ticker={t:shard for shard in self.shards for t in shard.tickers}
        self.updates:asyncio.Queue[BookUpdate]=asyncio.Queue(maxsize=50000)
        self.dropped_updates=0
        self._forwarders:list[asyncio.Task]=[]

    async def _forward(self,shard:EventingKalshiCrossBooks)->None:
        while True:
            row=await shard.updates.get()
            try:self.updates.put_nowait(row)
            except asyncio.QueueFull:
                self.dropped_updates+=1

    async def run(self)->None:
        self._forwarders=[asyncio.create_task(self._forward(s)) for s in self.shards]
        tasks=[asyncio.create_task(s.run()) for s in self.shards]
        try: await asyncio.gather(*tasks)
        finally:
            for t in tasks+self._forwarders:t.cancel()
            await asyncio.gather(*(tasks+self._forwarders),return_exceptions=True)

    async def quote(self,ticker:str,*,max_age_ms:int)->Any|None:
        shard=self.by_ticker.get(ticker)
        return None if shard is None else await shard.quote(ticker,max_age_ms=max_age_ms)

    @property
    def initialized_count(self)->int:
        return sum(s.initialized_count for s in self.shards)

    @property
    def stream_drops(self)->int:
        return self.dropped_updates+sum(s.dropped_updates for s in self.shards)

    async def close(self)->None:
        await asyncio.gather(*(s.close() for s in self.shards),return_exceptions=True)


async def _next_update(g:GeminiPropBooks,k:ShardedKalshiBooks)->BookUpdate:
    gt=asyncio.create_task(g.updates.get()); kt=asyncio.create_task(k.updates.get())
    done,pending=await asyncio.wait((gt,kt),return_when=asyncio.FIRST_COMPLETED)
    for t in pending:t.cancel()
    await asyncio.gather(*pending,return_exceptions=True)
    return next(iter(done)).result()


async def run(*,seconds:float,max_pairs:int)->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        ge,ke=await asyncio.gather(fetch_gemini(http),fetch_kalshi(http))
    pairs,discovery=discover_pairs(normalize_gemini(ge),normalize_kalshi(ke),max_pairs=max_pairs)
    if not pairs:
        raise RuntimeError("no future exact NFL Gemini/Kalshi pairs")
    gemini=GeminiPropBooks([p.gemini_symbol for p in pairs])
    kalshi=ShardedKalshiBooks([p.kalshi_ticker for p in pairs])
    states=[PairState(p) for p in pairs]
    by_marker:dict[tuple[str,str],list[int]]=defaultdict(list)
    for i,p in enumerate(pairs):
        by_marker[("gemini",p.gemini_symbol)].append(i); by_marker[("kalshi",p.kalshi_ticker)].append(i)
    rechecks:dict[int,asyncio.Task]={}

    async def recheck(i:int,direction:str,eg:TopOfBook,ek:TopOfBook)->None:
        s=states[i]; s.rechecks_started+=1
        await asyncio.sleep(.050)
        p=s.pair; g2=gemini.quote(p.gemini_symbol,max_age_ms=100); kq2=await kalshi.quote(p.kalshi_ticker,max_age_ms=100)
        if g2 is None or kq2 is None:return
        k2=kalshi_tob(kq2,p.kalshi_ticker)
        if abs(g2.ts_ms-k2.ts_ms)>50 or not same_limits(direction,eg,ek,g2,k2):return
        c=best(g2,k2,max_skew_ms=50)
        if c is not None and c.direction==direction and c.locked_profit>0:s.rechecks_survived+=1

    tasks=[asyncio.create_task(gemini.run()),asyncio.create_task(kalshi.run())]
    started=time.monotonic(); deadline=started+max(10.0,seconds)
    logger.info("NFL_GK_DYNAMIC_HOT_START %s",json.dumps({
        "selected_pairs":len(pairs),"discovery":discovery,"gemini_symbols":len(gemini.symbols),
        "kalshi_tickers":len(kalshi.by_ticker),"kalshi_shards":len(kalshi.shards),"seconds":seconds,
        "book_age_ms":100,"quote_skew_ms":50,"revalidate_ms":50,"quantity":"1",
        "rule_gate":"CONDITIONAL_NOT_EXECUTABLE","read_only":True,"order_entry":False,
    },separators=(",",":")))
    try:
        while time.monotonic()<deadline:
            try: trigger=await asyncio.wait_for(_next_update(gemini,kalshi),timeout=1.0)
            except asyncio.TimeoutError: continue
            for i in by_marker.get((trigger.venue,trigger.market_id),[]):
                s=states[i]; p=s.pair; s.triggers+=1
                g=gemini.quote(p.gemini_symbol,max_age_ms=100); kq=await kalshi.quote(p.kalshi_ticker,max_age_ms=100)
                if g is None or kq is None:
                    s.state(False,time.monotonic()); continue
                k=kalshi_tob(kq,p.kalshi_ticker); s.both_books+=1
                skew=abs(g.ts_ms-k.ts_ms); s.min_skew_ms=skew if s.min_skew_ms is None else min(s.min_skew_ms,skew); s.max_skew_ms=max(s.max_skew_ms,skew)
                if skew>50:
                    s.state(False,time.monotonic()); continue
                s.strict_evaluations+=1
                c=best(g,k,max_skew_ms=50)
                if c is None:
                    s.state(False,time.monotonic()); continue
                # evaluate_pair already enforces depth on the exact two needed legs.
                s.q1_depth+=1; s.positives+=1; s.max_profit=max(s.max_profit,c.locked_profit); s.last_direction=c.direction
                s.state(True,time.monotonic())
                if i not in rechecks or rechecks[i].done(): rechecks[i]=asyncio.create_task(recheck(i,c.direction,g,k))
    finally:
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if rechecks: await asyncio.gather(*rechecks.values(),return_exceptions=True)
        await kalshi.close()
    now=time.monotonic()
    for s in states:s.finish(now)
    rows=[s.as_dict() for s in states]
    positive=[r for r in rows if r["positive_observations"]>0]
    result={
        "status":"NFL_GK_DYNAMIC_HOT_SUMMARY","runtime_s":round(time.monotonic()-started,3),
        "discovery":discovery,"selected_pairs":len(pairs),"gemini_connected_symbols":len(gemini.connected_symbols),
        "gemini_book_updates":gemini.book_updates,"gemini_dropped_updates":gemini.dropped_updates,
        "kalshi_initialized_books":kalshi.initialized_count,"kalshi_dropped_updates":kalshi.stream_drops,
        "pairs_with_live_after_fee_positive":len(positive),"positive_observations":sum(s.positives for s in states),
        "rechecks_started":sum(s.rechecks_started for s in states),"rechecks_survived":sum(s.rechecks_survived for s in states),
        "max_live_profit":str(max((s.max_profit for s in states),default=D("0"))),
        "top_positive":sorted(positive,key=lambda r:D(r["max_profit"]),reverse=True)[:50],
        "near_term_pairs":[s.as_dict() for s in states[:30]],
        "classification":"SHADOW_ONLY_RULES_CONDITIONAL_NO_ORDER_ENTRY","realized_pnl":"0","read_only":True,"order_entry":False,
    }
    logger.info("NFL_GK_DYNAMIC_HOT_SUMMARY %s",json.dumps(result,separators=(",",":"),default=str))
    return result


def main()->None:
    p=argparse.ArgumentParser(); p.add_argument("--seconds",type=float,default=180.0); p.add_argument("--max-pairs",type=int,default=120)
    a=p.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(seconds=a.seconds,max_pairs=a.max_pairs))


if __name__=="__main__": main()
