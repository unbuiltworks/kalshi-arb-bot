"""One-shot fee-aware NFL cross-venue shadow profitability scanner.

Consumes the strict NFL identity layer, clusters rematches by kickoff time, and
normalizes every leg to the cost of a $1 winning payout.  Positive observations are
research only: REST snapshot timing, depth, account capacity, and complete rule
fingerprints are not sufficient for execution in this probe.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Iterable, Mapping

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata, fetch_series_fee_metadata
from src.nfl_identity_probe import (
    NflIdentity,
    canonical_team,
    fetch_gemini,
    fetch_kalshi,
    fetch_sportsbooks,
    normalize_gemini,
    normalize_kalshi,
    normalize_sportsbooks,
)
from src.nfl_rule_certifications import certification

D=Decimal
ONE=D("1")
START_TOLERANCE_MS=5*60*1000
KALSHI_SERIES=("KXNFLGAME","KXNFLSPREAD","KXNFLTOTAL")


@dataclass(frozen=True)
class Quote:
    venue: str
    market_id: str
    yes_cost: Decimal
    no_cost: Decimal
    fee_series: str | None = None
    source: str = "rest_snapshot"


def _d(value: Any) -> Decimal | None:
    try:
        out=D(str(value))
    except (InvalidOperation,TypeError,ValueError):
        return None
    return out if out.is_finite() else None


def gemini_quotes(events: Iterable[Mapping[str,Any]]) -> dict[tuple[str,str],Quote]:
    out={}
    for event in events:
        for c in event.get("contracts") or []:
            if not isinstance(c,Mapping): continue
            symbol=str(c.get("instrumentSymbol") or "")
            prices=c.get("prices") if isinstance(c.get("prices"),Mapping) else {}
            buy=prices.get("buy") if isinstance(prices.get("buy"),Mapping) else {}
            yes,no=_d(buy.get("yes")),_d(buy.get("no"))
            if not symbol or yes is None or no is None or not (D("0")<yes<ONE and D("0")<no<ONE): continue
            out[("gemini",symbol)]=Quote("gemini",symbol,yes,no)
    return out


def kalshi_quotes(events: Iterable[Mapping[str,Any]]) -> dict[tuple[str,str],Quote]:
    out={}
    for event in events:
        series=str(event.get("series_ticker") or "")
        for m in event.get("markets") or []:
            if not isinstance(m,Mapping): continue
            ticker=str(m.get("ticker") or "")
            yes,no=_d(m.get("yes_ask_dollars")),_d(m.get("no_ask_dollars"))
            if not ticker or yes is None or no is None or not (D("0")<yes<ONE and D("0")<no<ONE): continue
            inferred_series=series or ticker.split("-",1)[0]
            out[("kalshi",ticker)]=Quote("kalshi",ticker,yes,no,inferred_series)
    return out


def sportsbook_quotes(events: Iterable[Mapping[str,Any]]) -> dict[tuple[str,str],Quote]:
    out={}
    for event in events:
        event_id=str(event.get("id") or "")
        if not event_id: continue
        for book in event.get("bookmakers") or []:
            if not isinstance(book,Mapping): continue
            b=str(book.get("key") or book.get("title") or "").strip().lower()
            venue=f"sportsbook:{b}"
            for market in book.get("markets") or []:
                if not isinstance(market,Mapping): continue
                typ=str(market.get("key") or "")
                outcomes=[x for x in market.get("outcomes") or [] if isinstance(x,Mapping)]
                if typ in {"h2h","spreads"}:
                    # Only two-way shapes can be represented as one YES leg + one
                    # complementary NO leg. A 3-way/draw market is skipped.
                    if len(outcomes)!=2: continue
                    prices=[_d(x.get("price")) for x in outcomes]
                    if any(p is None or p<=0 for p in prices): continue
                    for idx,row in enumerate(outcomes):
                        if canonical_team(row.get("name")) is None: continue
                        yes=ONE/prices[idx]  # type: ignore[operator]
                        no=ONE/prices[1-idx] # type: ignore[operator]
                        mid=f"{event_id}:{'h2h' if typ=='h2h' else 'spread'}:{idx}"
                        out[(venue,mid)]=Quote(venue,mid,yes,no,source="odds_api_decimal_normalized")
                elif typ=="totals":
                    over=next(((idx,x) for idx,x in enumerate(outcomes) if str(x.get("name") or "").lower()=="over"),None)
                    under=next(((idx,x) for idx,x in enumerate(outcomes) if str(x.get("name") or "").lower()=="under"),None)
                    if over is None or under is None: continue
                    op,up=_d(over[1].get("price")),_d(under[1].get("price"))
                    if op is None or up is None or op<=0 or up<=0: continue
                    mid=f"{event_id}:total:{over[0]}"
                    out[(venue,mid)]=Quote(venue,mid,ONE/op,ONE/up,source="odds_api_decimal_normalized")
    return out


def _clusters(rows: list[NflIdentity], tolerance_ms: int=START_TOLERANCE_MS) -> list[list[NflIdentity]]:
    by_base: dict[str,list[NflIdentity]]=defaultdict(list)
    for row in rows: by_base[row.proposition_key].append(row)
    clusters=[]
    for members in by_base.values():
        ordered=sorted(members,key=lambda x:x.start_ms)
        current: list[NflIdentity]=[]
        anchor: int|None=None
        for row in ordered:
            if anchor is None or row.start_ms-anchor<=tolerance_ms:
                current.append(row)
                if anchor is None: anchor=row.start_ms
            else:
                if current: clusters.append(current)
                current=[row]; anchor=row.start_ms
        if current: clusters.append(current)
    return clusters


def _fee(quote: Quote, outcome: str, price: Decimal, kalshi_meta: Mapping[str,KalshiSeriesFeeMetadata]) -> Decimal | None:
    if quote.venue=="gemini": return gemini_predictions_taker_fee(price,ONE)
    if quote.venue=="kalshi":
        meta=kalshi_meta.get(str(quote.fee_series or ""))
        if meta is None or meta.formula_status!="MAPPED_QUADRATIC_TAKER": return None
        return meta.taker_fee_fn()(price,ONE)
    if quote.venue.startswith("sportsbook:"): return D("0")
    return None


def _leg_cost(quote: Quote,outcome: str,kalshi_meta: Mapping[str,KalshiSeriesFeeMetadata]) -> tuple[Decimal,Decimal] | None:
    price=quote.yes_cost if outcome=="YES" else quote.no_cost
    fee=_fee(quote,outcome,price,kalshi_meta)
    return None if fee is None else (price,fee)


def scan(rows: list[NflIdentity],quotes: Mapping[tuple[str,str],Quote],kalshi_meta: Mapping[str,KalshiSeriesFeeMetadata]) -> dict[str,Any]:
    positives=[]; evaluated=0; clusters_used=0
    for cluster in _clusters(rows):
        venue_classes={"prediction" if x.venue in {"gemini","kalshi"} else "sportsbook" for x in cluster}
        if len({x.venue for x in cluster})<2: continue
        # Require at least one prediction venue; pure sportsbook arbitrage is a
        # separate future lane with different limit/account mechanics.
        if not any(x.venue in {"gemini","kalshi"} for x in cluster): continue
        if max(x.start_ms for x in cluster)-min(x.start_ms for x in cluster)>START_TOLERANCE_MS: continue
        clusters_used+=1
        for i,a in enumerate(cluster):
            qa=quotes.get((a.venue,a.market_id))
            if qa is None: continue
            for b in cluster[i+1:]:
                if a.venue==b.venue: continue
                qb=quotes.get((b.venue,b.market_id))
                if qb is None: continue
                for yes_row,yes_q,no_row,no_q in ((a,qa,b,qb),(b,qb,a,qa)):
                    y=_leg_cost(yes_q,"YES",kalshi_meta); n=_leg_cost(no_q,"NO",kalshi_meta)
                    if y is None or n is None: continue
                    evaluated+=1
                    raw=y[0]+n[0]; fees=y[1]+n[1]; net=raw+fees; profit=ONE-net
                    if profit<=0: continue
                    pair=tuple(sorted(("gemini" if yes_row.venue=="gemini" else "kalshi" if yes_row.venue=="kalshi" else yes_row.venue,
                                       "gemini" if no_row.venue=="gemini" else "kalshi" if no_row.venue=="kalshi" else no_row.venue)))
                    cert=certification(pair[0],pair[1],yes_row.family)
                    positives.append({
                        "proposition":yes_row.proposition_key,
                        "kickoff_utc":datetime.fromtimestamp(yes_row.start_ms/1000,tz=timezone.utc).isoformat(),
                        "family":yes_row.family,
                        "yes_venue":yes_row.venue,"yes_market":yes_row.market_id,"yes_cost":str(y[0]),"yes_fee":str(y[1]),
                        "no_venue":no_row.venue,"no_market":no_row.market_id,"no_cost":str(n[0]),"no_fee":str(n[1]),
                        "raw_cost":str(raw),"fees":str(fees),"net_cost":str(net),"shadow_profit":str(profit),
                        "shadow_roi":str(profit/net),
                        "rule_status":cert.status.value,"rule_blockers":list(cert.blockers),"rule_executable":cert.executable,
                        "execution_status":"SHADOW_ONLY_NO_DEPTH_NO_FRESHNESS_CERTIFICATION",
                    })
    positives.sort(key=lambda x:D(x["shadow_profit"]),reverse=True)
    return {
        "clusters_evaluated":clusters_used,"directional_pairs_evaluated":evaluated,"positive_observations":len(positives),
        "positive_by_family":dict(Counter(x["family"] for x in positives)),
        "positive_by_pair":dict(Counter(" + ".join(sorted((x["yes_venue"],x["no_venue"]))) for x in positives)),
        "max_shadow_profit":str(max((D(x["shadow_profit"]) for x in positives),default=D("0"))),
        "top_positive":positives[:50],
    }


async def run()->dict[str,Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        g_events,k_events,s_result,fee_rows=await asyncio.gather(
            fetch_gemini(http),fetch_kalshi(http),fetch_sportsbooks(http,__import__("os").environ["THE_ODDS_API_KEY"]),
            asyncio.gather(*(fetch_series_fee_metadata(http,s) for s in KALSHI_SERIES),return_exceptions=True),
        )
    s_events,quota=s_result
    meta={row.series_ticker:row for row in fee_rows if isinstance(row,KalshiSeriesFeeMetadata)}
    identities=normalize_gemini(g_events)+normalize_kalshi(k_events)+normalize_sportsbooks(s_events)
    quotes={**gemini_quotes(g_events),**kalshi_quotes(k_events),**sportsbook_quotes(s_events)}
    result=scan(identities,quotes,meta)
    return {
        "status":"NFL_SHADOW_PRICE_PROBE","observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "identity_rows":len(identities),"quote_rows":len(quotes),"kalshi_fee_series":{k:v.as_dict() for k,v in meta.items()},
        "odds_api_quota":quota,**result,
        "read_only":True,"order_entry":False,"realized_pnl":"0",
        "warning":"Positive observations are not executable arbitrage: snapshot timing/depth and complete settlement-rule certification are absent.",
    }


def main()->None:
    print(json.dumps(asyncio.run(run()),separators=(",",":"),sort_keys=True,default=str),flush=True)


if __name__=="__main__": main()
