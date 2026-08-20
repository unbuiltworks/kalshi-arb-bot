"""NFL shadow pricing v2: expose gross gaps without inventing unknown Kalshi fees.

The v1 scanner correctly skipped any route whose fee formula was unmapped.  This
version keeps that fail-closed behavior for *net* economics, but also reports raw
complementary price sums below $1 as research-only fee headroom.  An unmapped-fee
candidate can never be called profitable or executable here.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata, fetch_series_fee_metadata
from src.nfl_identity_probe import (
    NflIdentity, fetch_gemini, fetch_kalshi, fetch_sportsbooks,
    normalize_gemini, normalize_kalshi, normalize_sportsbooks,
)
from src.nfl_rule_certifications import certification
from src.nfl_shadow_price_probe import (
    ONE, KALSHI_SERIES, Quote, _clusters, _fee,
    gemini_quotes, kalshi_quotes, sportsbook_quotes,
)

D=Decimal


def _pair_cert(a:NflIdentity,b:NflIdentity):
    def base(v:str)->str:
        return v if v in {"gemini","kalshi"} else v
    return certification(*sorted((base(a.venue),base(b.venue))),a.family)


def scan_v2(
    rows:list[NflIdentity],
    quotes:Mapping[tuple[str,str],Quote],
    kalshi_meta:Mapping[str,KalshiSeriesFeeMetadata],
)->dict[str,Any]:
    fully_priced=[]
    gross_unmapped=[]
    directions_seen=0
    fee_priced_directions=0
    clusters_used=0
    for cluster in _clusters(rows):
        if len({x.venue for x in cluster})<2: continue
        if not any(x.venue in {"gemini","kalshi"} for x in cluster): continue
        if max(x.start_ms for x in cluster)-min(x.start_ms for x in cluster)>5*60*1000: continue
        clusters_used+=1
        for i,a in enumerate(cluster):
            qa=quotes.get((a.venue,a.market_id))
            if qa is None: continue
            for b in cluster[i+1:]:
                if a.venue==b.venue: continue
                qb=quotes.get((b.venue,b.market_id))
                if qb is None: continue
                for yes_row,yes_q,no_row,no_q in ((a,qa,b,qb),(b,qb,a,qa)):
                    directions_seen+=1
                    yp=yes_q.yes_cost; np=no_q.no_cost
                    raw=yp+np
                    yf=_fee(yes_q,"YES",yp,kalshi_meta)
                    nf=_fee(no_q,"NO",np,kalshi_meta)
                    cert=_pair_cert(yes_row,no_row)
                    common={
                        "proposition":yes_row.proposition_key,
                        "kickoff_utc":datetime.fromtimestamp(yes_row.start_ms/1000,tz=timezone.utc).isoformat(),
                        "family":yes_row.family,
                        "yes_venue":yes_row.venue,"yes_market":yes_row.market_id,"yes_cost":str(yp),
                        "no_venue":no_row.venue,"no_market":no_row.market_id,"no_cost":str(np),
                        "raw_cost":str(raw),
                        "rule_status":cert.status.value,"rule_blockers":list(cert.blockers),"rule_executable":cert.executable,
                    }
                    if yf is None or nf is None:
                        if raw<ONE:
                            missing=[]
                            if yf is None: missing.append(yes_row.venue)
                            if nf is None: missing.append(no_row.venue)
                            gross_unmapped.append({
                                **common,
                                "gross_fee_headroom":str(ONE-raw),
                                "fee_status":"UNMAPPED_DO_NOT_CALCULATE_NET_PROFIT",
                                "unmapped_fee_venues":sorted(set(missing)),
                                "net_profit":None,
                                "execution_status":"SHADOW_GROSS_ONLY_FEE_UNMAPPED_RULES_UNCERTIFIED_NO_DEPTH_NO_FRESHNESS",
                            })
                        continue
                    fee_priced_directions+=1
                    fees=yf+nf; net=raw+fees; profit=ONE-net
                    if profit<=0: continue
                    fully_priced.append({
                        **common,
                        "yes_fee":str(yf),"no_fee":str(nf),"fees":str(fees),"net_cost":str(net),
                        "shadow_profit":str(profit),"shadow_roi":str(profit/net),
                        "fee_status":"FULLY_PRICED_FOR_SHADOW",
                        "execution_status":"SHADOW_ONLY_RULES_UNCERTIFIED_NO_DEPTH_NO_FRESHNESS",
                    })
    fully_priced.sort(key=lambda x:D(x["shadow_profit"]),reverse=True)
    gross_unmapped.sort(key=lambda x:D(x["gross_fee_headroom"]),reverse=True)
    return {
        "clusters_evaluated":clusters_used,
        "directional_pairs_seen":directions_seen,
        "fee_priced_directions":fee_priced_directions,
        "fully_fee_priced_positive_observations":len(fully_priced),
        "fee_unmapped_gross_positive_observations":len(gross_unmapped),
        "gross_unmapped_by_family":dict(Counter(x["family"] for x in gross_unmapped)),
        "gross_unmapped_by_pair":dict(Counter(" + ".join(sorted((x["yes_venue"],x["no_venue"]))) for x in gross_unmapped)),
        "max_gross_fee_headroom":str(max((D(x["gross_fee_headroom"]) for x in gross_unmapped),default=D("0"))),
        "max_fully_priced_shadow_profit":str(max((D(x["shadow_profit"]) for x in fully_priced),default=D("0"))),
        "top_fully_priced":fully_priced[:40],
        "top_fee_unmapped_gross":gross_unmapped[:60],
    }


async def run()->dict[str,Any]:
    key=os.environ.get("THE_ODDS_API_KEY","").strip()
    if not key: raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        g_events,k_events,s_result,fee_rows=await asyncio.gather(
            fetch_gemini(http),fetch_kalshi(http),fetch_sportsbooks(http,key),
            asyncio.gather(*(fetch_series_fee_metadata(http,s) for s in KALSHI_SERIES),return_exceptions=True),
        )
    s_events,quota=s_result
    meta={row.series_ticker:row for row in fee_rows if isinstance(row,KalshiSeriesFeeMetadata)}
    identities=normalize_gemini(g_events)+normalize_kalshi(k_events)+normalize_sportsbooks(s_events)
    quotes={**gemini_quotes(g_events),**kalshi_quotes(k_events),**sportsbook_quotes(s_events)}
    return {
        "status":"NFL_SHADOW_PRICE_PROBE_V2","observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "identity_rows":len(identities),"quote_rows":len(quotes),
        "kalshi_fee_series":{k:v.as_dict() for k,v in meta.items()},"odds_api_quota":quota,
        **scan_v2(identities,quotes,meta),
        "read_only":True,"order_entry":False,"realized_pnl":"0",
        "warning":"Gross fee headroom is not net profit. Unknown Kalshi fee formulas, rules, depth and quote freshness block execution.",
    }


def main()->None:
    print(json.dumps(asyncio.run(run()),separators=(",",":"),sort_keys=True,default=str),flush=True)


if __name__=="__main__": main()
