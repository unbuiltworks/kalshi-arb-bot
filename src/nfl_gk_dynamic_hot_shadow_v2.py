"""Dynamic NFL G/K hot-book shadow v2 with diversified game coverage.

This scanner fixes two interpretation problems from v1:
1) a single near-term game with a deep alternate-line ladder cannot monopolize the
   cohort; selection is balanced across games and families;
2) q=1 depth and economic positivity are measured separately at 50ms/100ms/250ms
   receive-skew tiers.

Only the 50ms tier is execution-style evidence. Every tier remains rule-blocked and
read-only; no order transport is imported.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src.eventing_books import BookUpdate
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_mlb_prop_hot_shadow import GeminiPropBooks
from src.nfl_gk_dynamic_hot_shadow import (
    HotPair,
    ShardedKalshiBooks,
    best,
    discover_pairs,
    kalshi_tob,
    same_limits,
)
from src.nfl_identity_probe import fetch_gemini, fetch_kalshi, normalize_gemini, normalize_kalshi

D=Decimal
ONE=D("1")
logger=logging.getLogger("kalshi_arb.nfl_gk_dynamic_hot_shadow_v2")
FAMILIES=("moneyline","spread","game_total")


def game_bucket(pair:HotPair)->tuple[int,str]:
    parts=pair.key.split("|")
    teams=parts[2] if len(parts)>2 else pair.key
    return pair.kickoff_ms,teams


def diversify_pairs(pairs:list[HotPair],*,max_pairs:int,per_game_cap:int)->list[HotPair]:
    """Round-robin games and families; never let one strike ladder dominate."""
    by_game:dict[tuple[int,str],dict[str,deque[HotPair]]]=defaultdict(lambda:{f:deque() for f in FAMILIES})
    for pair in pairs:
        if pair.family in FAMILIES:
            by_game[game_bucket(pair)][pair.family].append(pair)
    games=sorted(by_game)
    chosen:list[HotPair]=[]
    used:dict[tuple[int,str],int]=defaultdict(int)
    made_progress=True
    while len(chosen)<max_pairs and made_progress:
        made_progress=False
        for game in games:
            if len(chosen)>=max_pairs: break
            if used[game]>=per_game_cap: continue
            families=by_game[game]
            for family in FAMILIES:
                if len(chosen)>=max_pairs or used[game]>=per_game_cap: break
                q=families[family]
                if not q: continue
                chosen.append(q.popleft()); used[game]+=1; made_progress=True
    return chosen


def any_q1_depth(g:TopOfBook,k:TopOfBook)->bool:
    return bool(
        (g.yes_ask_size>=ONE and k.no_ask_size>=ONE)
        or (k.yes_ask_size>=ONE and g.no_ask_size>=ONE)
    )


@dataclass
class Tier:
    skew_ms:int
    max_age_ms:int
    evaluations:int=0
    positives:int=0
    max_profit:Decimal=D("0")


@dataclass
class PairMetrics:
    pair:HotPair
    triggers:int=0
    both_books_5s:int=0
    q1_depth_5s:int=0
    min_skew_ms:int|None=None
    max_skew_ms:int=0
    tier50:Tier|None=None
    tier100:Tier|None=None
    tier250:Tier|None=None
    rechecks_started:int=0
    rechecks_survived:int=0

    def __post_init__(self)->None:
        self.tier50=Tier(50,100)
        self.tier100=Tier(100,500)
        self.tier250=Tier(250,1000)

    def as_dict(self)->dict[str,Any]:
        p=self.pair
        def td(t:Tier)->dict[str,Any]:
            return {"evaluations":t.evaluations,"positives":t.positives,"max_profit":str(t.max_profit)}
        return {
            "key":p.key,"family":p.family,"kickoff_ms":p.kickoff_ms,"gemini":p.gemini_symbol,"kalshi":p.kalshi_ticker,
            "triggers":self.triggers,"both_books_5s":self.both_books_5s,"q1_depth_5s":self.q1_depth_5s,
            "min_skew_ms":self.min_skew_ms,"max_skew_ms":self.max_skew_ms,
            "tier50":td(self.tier50),"tier100":td(self.tier100),"tier250":td(self.tier250),
            "rechecks_started":self.rechecks_started,"rechecks_survived":self.rechecks_survived,
            "rule_status":p.rule_status,"rule_blockers":list(p.rule_blockers),
        }


async def _next_update(g:GeminiPropBooks,k:ShardedKalshiBooks)->BookUpdate:
    gt=asyncio.create_task(g.updates.get()); kt=asyncio.create_task(k.updates.get())
    done,pending=await asyncio.wait((gt,kt),return_when=asyncio.FIRST_COMPLETED)
    for t in pending:t.cancel()
    await asyncio.gather(*pending,return_exceptions=True)
    return next(iter(done)).result()


async def run(*,seconds:float,max_pairs:int,per_game_cap:int)->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        ge,ke=await asyncio.gather(fetch_gemini(http),fetch_kalshi(http))
    all_pairs,disc=discover_pairs(normalize_gemini(ge),normalize_kalshi(ke),max_pairs=10000)
    pairs=diversify_pairs(all_pairs,max_pairs=max_pairs,per_game_cap=per_game_cap)
    if not pairs: raise RuntimeError("no diversified future NFL G/K pairs")
    gemini=GeminiPropBooks([p.gemini_symbol for p in pairs])
    kalshi=ShardedKalshiBooks([p.kalshi_ticker for p in pairs])
    metrics=[PairMetrics(p) for p in pairs]
    by_marker:dict[tuple[str,str],list[int]]=defaultdict(list)
    for i,p in enumerate(pairs):
        by_marker[("gemini",p.gemini_symbol)].append(i); by_marker[("kalshi",p.kalshi_ticker)].append(i)
    rechecks:dict[int,asyncio.Task]={}

    async def recheck(i:int,direction:str,eg:TopOfBook,ek:TopOfBook)->None:
        m=metrics[i]; m.rechecks_started+=1; await asyncio.sleep(.050); p=m.pair
        g2=gemini.quote(p.gemini_symbol,max_age_ms=100); kq2=await kalshi.quote(p.kalshi_ticker,max_age_ms=100)
        if g2 is None or kq2 is None:return
        k2=kalshi_tob(kq2,p.kalshi_ticker)
        if abs(g2.ts_ms-k2.ts_ms)>50 or not same_limits(direction,eg,ek,g2,k2):return
        c=best(g2,k2,max_skew_ms=50)
        if c is not None and c.direction==direction and c.locked_profit>0:m.rechecks_survived+=1

    tasks=[asyncio.create_task(gemini.run()),asyncio.create_task(kalshi.run())]
    start=time.monotonic(); deadline=start+max(10.0,seconds)
    logger.info("NFL_GK_DYNAMIC_V2_START %s",json.dumps({
        "all_exact_pairs":len(all_pairs),"selected_pairs":len(pairs),"games_selected":len({game_bucket(p) for p in pairs}),
        "per_game_cap":per_game_cap,"gemini_symbols":len(gemini.symbols),"kalshi_tickers":len(kalshi.by_ticker),
        "kalshi_shards":len(kalshi.shards),"seconds":seconds,
        "tiers":{"strict":{"age_ms":100,"skew_ms":50},"diagnostic100":{"age_ms":500,"skew_ms":100},"diagnostic250":{"age_ms":1000,"skew_ms":250}},
        "rule_gate":"CONDITIONAL_NOT_EXECUTABLE","read_only":True,"order_entry":False,
    },separators=(",",":")))
    try:
        while time.monotonic()<deadline:
            try: ev=await asyncio.wait_for(_next_update(gemini,kalshi),timeout=1.0)
            except asyncio.TimeoutError: continue
            for i in by_marker.get((ev.venue,ev.market_id),[]):
                m=metrics[i]; p=m.pair; m.triggers+=1
                g5=gemini.quote(p.gemini_symbol,max_age_ms=5000); kq5=await kalshi.quote(p.kalshi_ticker,max_age_ms=5000)
                if g5 is not None and kq5 is not None:
                    k5=kalshi_tob(kq5,p.kalshi_ticker); m.both_books_5s+=1
                    skew=abs(g5.ts_ms-k5.ts_ms); m.min_skew_ms=skew if m.min_skew_ms is None else min(m.min_skew_ms,skew); m.max_skew_ms=max(m.max_skew_ms,skew)
                    if any_q1_depth(g5,k5):m.q1_depth_5s+=1
                for tier in (m.tier250,m.tier100,m.tier50):
                    g=gemini.quote(p.gemini_symbol,max_age_ms=tier.max_age_ms); kq=await kalshi.quote(p.kalshi_ticker,max_age_ms=tier.max_age_ms)
                    if g is None or kq is None:continue
                    k=kalshi_tob(kq,p.kalshi_ticker)
                    if abs(g.ts_ms-k.ts_ms)>tier.skew_ms:continue
                    tier.evaluations+=1
                    c=best(g,k,max_skew_ms=tier.skew_ms)
                    if c is None:continue
                    tier.positives+=1; tier.max_profit=max(tier.max_profit,c.locked_profit)
                    if tier is m.tier50 and (i not in rechecks or rechecks[i].done()):
                        rechecks[i]=asyncio.create_task(recheck(i,c.direction,g,k))
    finally:
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if rechecks:await asyncio.gather(*rechecks.values(),return_exceptions=True)
        await kalshi.close()
    rows=[m.as_dict() for m in metrics]
    def count_positive(name:str)->int:
        return sum(1 for r in rows if r[name]["positives"]>0)
    result={
        "status":"NFL_GK_DYNAMIC_V2_SUMMARY","runtime_s":round(time.monotonic()-start,3),
        "discovery":disc,"selected_pairs":len(pairs),"games_selected":len({game_bucket(p) for p in pairs}),
        "gemini_book_updates":gemini.book_updates,"gemini_dropped_updates":gemini.dropped_updates,
        "kalshi_initialized_books":kalshi.initialized_count,"kalshi_dropped_updates":kalshi.stream_drops,
        "pairs_with_q1_depth_5s":sum(1 for m in metrics if m.q1_depth_5s>0),
        "pairs_positive_50ms":count_positive("tier50"),"pairs_positive_100ms":count_positive("tier100"),"pairs_positive_250ms":count_positive("tier250"),
        "positive_observations_50ms":sum(m.tier50.positives for m in metrics),
        "positive_observations_100ms":sum(m.tier100.positives for m in metrics),
        "positive_observations_250ms":sum(m.tier250.positives for m in metrics),
        "max_profit_50ms":str(max((m.tier50.max_profit for m in metrics),default=D("0"))),
        "max_profit_100ms":str(max((m.tier100.max_profit for m in metrics),default=D("0"))),
        "max_profit_250ms":str(max((m.tier250.max_profit for m in metrics),default=D("0"))),
        "rechecks_started":sum(m.rechecks_started for m in metrics),"rechecks_survived":sum(m.rechecks_survived for m in metrics),
        "top_by_250ms":sorted(rows,key=lambda r:D(r["tier250"]["max_profit"]),reverse=True)[:50],
        "classification":"SHADOW_ONLY_RULES_CONDITIONAL_NO_ORDER_ENTRY","realized_pnl":"0","read_only":True,"order_entry":False,
    }
    logger.info("NFL_GK_DYNAMIC_V2_SUMMARY %s",json.dumps(result,separators=(",",":"),default=str))
    return result


def main()->None:
    p=argparse.ArgumentParser(); p.add_argument("--seconds",type=float,default=180); p.add_argument("--max-pairs",type=int,default=180); p.add_argument("--per-game-cap",type=int,default=9)
    a=p.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(seconds=a.seconds,max_pairs=a.max_pairs,per_game_cap=a.per_game_cap))


if __name__=="__main__":main()
