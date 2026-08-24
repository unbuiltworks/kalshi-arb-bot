"""Read-only NFL Gemini/Kalshi maker architecture comparison.

Uses the already-certified strict *identity matcher* for NFL normal-event contracts,
but remains non-executable because the full venue rule fingerprint is still gated.
No credentials or order-entry code are used. Kalshi prices come from nested event
quotes, avoiding per-market orderbook calls during broad screening.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
import json
import logging
import time
from typing import Any, Mapping

import httpx

# Installs the verified case-aligned exact NFL matcher patch.
import src.sports_nfl_gk_price_scan_verified  # noqa: F401
import src.sports_nfl_gk_price_scan as core

D = Decimal
ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MAKER_RATE = D("0.0175")
FLOOR = D("0.03")
log = logging.getLogger("kalshi_arb.sports_nfl_gk_maker_shadow")


def ceil_cent(x: D) -> D:
    return (x * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def maker_fee(venue: str, p: D, *, multiplier: D | None = None, fee_type: str = "") -> D | None:
    if venue == "gemini":
        return ceil_cent(MAKER_RATE * p * (ONE - p))
    if venue == "kalshi" and multiplier is not None:
        if fee_type != "quadratic_with_maker_fees":
            return ZERO
        return ceil_cent(MAKER_RATE * multiplier * p * (ONE - p))
    return None


def taker_fee(venue: str, p: D, *, multiplier: D | None = None) -> D | None:
    if venue == "gemini":
        return core.gemini_one_contract_fee(p)
    if venue == "kalshi" and multiplier is not None:
        return core.conservative_kalshi_fee(1, p, multiplier)
    return None


def nested_kalshi_book(m: Mapping[str, Any]) -> dict[str, D] | None:
    yb = core._d(m.get("yes_bid_dollars")); ya = core._d(m.get("yes_ask_dollars"))
    nb = core._d(m.get("no_bid_dollars")); na = core._d(m.get("no_ask_dollars"))
    ybs = core._d(m.get("yes_bid_size_fp")); yas = core._d(m.get("yes_ask_size_fp"))
    if None in (yb, ya, nb, na, ybs, yas): return None
    assert yb is not None and ya is not None and nb is not None and na is not None
    assert ybs is not None and yas is not None
    if not (ZERO <= yb < ya <= ONE and ZERO <= nb < na <= ONE): return None
    if ybs <= ZERO or yas <= ZERO: return None
    if abs(na - (ONE-yb)) > D("0.0001") or abs(nb - (ONE-ya)) > D("0.0001"): return None
    return {"yes_bid":yb,"yes_ask":ya,"no_bid":nb,"no_ask":na,"yes_bid_size":ybs,"yes_ask_size":yas}


def gemini_book(c: Mapping[str, Any]) -> dict[str, D] | None:
    p = c.get("prices") if isinstance(c.get("prices"), Mapping) else {}
    yb = core._d(p.get("bestBid")); ya = core._d(p.get("bestAsk"))
    if yb is None or ya is None or not (ZERO <= yb < ya <= ONE): return None
    return {"yes_bid":yb,"yes_ask":ya,"no_bid":ONE-ya,"no_ask":ONE-yb}


def kalshi_tick(m: Mapping[str, Any]) -> D:
    ranges = m.get("price_ranges")
    if isinstance(ranges, list):
        for r in ranges:
            if isinstance(r, Mapping):
                step = core._d(r.get("step"))
                if step is not None and ZERO < step <= D("0.10"): return step
    return CENT


def gemini_tick(c: Mapping[str, Any]) -> tuple[D,str]:
    step = core._d(c.get("priceIncrement"))
    if step is not None and ZERO < step <= D("0.10"): return step,"contract.priceIncrement"
    return CENT,"conservative_default_0.01"


def all_in(venue: str, p: D, kind: str, *, multiplier: D | None = None, fee_type: str = "") -> D | None:
    f = maker_fee(venue,p,multiplier=multiplier,fee_type=fee_type) if kind == "maker" else taker_fee(venue,p,multiplier=multiplier)
    return None if f is None else p + f


def safe_maker(*, maker_venue: str, bid: D, ask: D, tick: D, hedge_venue: str, hedge_ask: D,
               multiplier: D, fee_type: str) -> dict[str,Any] | None:
    hedge_cost = all_in(hedge_venue, hedge_ask, "taker", multiplier=multiplier if hedge_venue=="kalshi" else None)
    if hedge_cost is None or tick <= ZERO or bid >= ask: return None
    upper = ask - tick
    if upper < bid: return None
    best = None; p = bid; guard = 0
    while p <= upper + D("0.0000001") and guard < 200:
        mc = all_in(maker_venue,p,"maker",multiplier=multiplier if maker_venue=="kalshi" else None,
                    fee_type=fee_type if maker_venue=="kalshi" else "")
        if mc is None: return None
        profit = ONE - mc - hedge_cost
        if profit >= FLOOR:
            best = {"maker_price":p,"maker_all_in":mc,"hedge_ask":hedge_ask,"hedge_all_in":hedge_cost,
                    "fallback_profit":profit,"near_touch":p>=upper,"ticks_improved":int(((p-bid)/tick).to_integral_value())}
        p += tick; guard += 1
    if best is None: return None
    join = all_in(maker_venue,bid,"maker",multiplier=multiplier if maker_venue=="kalshi" else None,
                  fee_type=fee_type if maker_venue=="kalshi" else "")
    best.update({"bid":bid,"ask":ask,"tick":tick,"join_bid_profit":None if join is None else ONE-join-hedge_cost})
    return best


def s(v: Any) -> Any:
    if isinstance(v,D): return str(v)
    if isinstance(v,Mapping): return {str(k):s(x) for k,x in v.items()}
    if isinstance(v,list): return [s(x) for x in v]
    return v


async def run_once() -> dict[str,Any]:
    t0=time.monotonic(); health=Counter()
    async with httpx.AsyncClient(timeout=12.0) as http:
        gb,gms = await core._get(http,f"{core.GEMINI}/v1/prediction-markets/events",health,
            params={"status":"active","sport":"american_football","limit":500,"offset":0})
        gevents=[dict(x) for x in ((gb or {}).get("data") or (gb or {}).get("events") or []) if isinstance(x,Mapping)]
        grows,gcontracts=core._gemini_rows(gevents); gindex=core._gemini_match_index(grows)
        byseries={}; kms={}
        for series in core.SERIES_FAMILY:
            body,received=await core._get(http,f"{core.KALSHI}/events",health,
                params={"limit":200,"status":"open","series_ticker":series,"with_nested_markets":"true"})
            byseries[series]=[dict(x) for x in ((body or {}).get("events") or []) if isinstance(x,Mapping)]
            kms[series]=received
        krows,match_stats=core._kalshi_rows(byseries,gindex)
        fee_meta={}
        for series in core.SERIES_FAMILY:
            body,_=await core._get(http,f"{core.KALSHI}/series/{series}",health)
            meta=(body or {}).get("series") if isinstance((body or {}).get("series"),Mapping) else {}
            mult=core._d(meta.get("fee_multiplier")); typ=str(meta.get("fee_type") or "")
            if mult is not None and typ in {"quadratic","quadratic_with_maker_fees"}: fee_meta[series]=(mult,typ)

    rawk={}
    for series,events in byseries.items():
        for e in events:
            for m in e.get("markets") or []:
                if isinstance(m,Mapping) and m.get("ticker"): rawk[str(m["ticker"])]=(series,m)
    gbk=defaultdict(list); kbk=defaultdict(list)
    for r in grows: gbk[r.contract_key].append(r)
    for r in krows: kbk[r.side.contract_key].append(r)
    keys=sorted(set(gbk)&set(kbk))

    taker=[]; makers=[]; dual=[]; rejects=Counter(); priced=Counter()
    for key in keys:
        for gr in gbk[key]:
            gc=gcontracts.get(gr.venue_market_id)
            if not isinstance(gc,Mapping): rejects["missing_gemini_contract"]+=1; continue
            gbook=gemini_book(gc)
            if gbook is None: rejects["missing_gemini_book"]+=1; continue
            gtick,gts=gemini_tick(gc)
            for kr in kbk[key]:
                raw=rawk.get(kr.side.venue_market_id)
                if raw is None: rejects["missing_kalshi_market"]+=1; continue
                series,km=raw; kbook=nested_kalshi_book(km); meta=fee_meta.get(series)
                if kbook is None: rejects["invalid_kalshi_book"]+=1; continue
                if meta is None: rejects["missing_fee_meta"]+=1; continue
                mult,fee_type=meta; ktick=kalshi_tick(km); ident=gr.identity; priced[ident.market_family]+=1
                gy=all_in("gemini",gbook["yes_ask"],"taker"); gn=all_in("gemini",gbook["no_ask"],"taker")
                ky=all_in("kalshi",kbook["yes_ask"],"taker",multiplier=mult); kn=all_in("kalshi",kbook["no_ask"],"taker",multiplier=mult)
                if None not in (gy,gn,ky,kn):
                    for direction,profit in (("G_Y_K_N",ONE-gy-kn),("K_Y_G_N",ONE-ky-gn)):
                        if profit>ZERO: taker.append({"key":key,"family":ident.market_family,"direction":direction,"profit":profit,"g":gr.venue_market_id,"k":kr.side.venue_market_id})
                specs=(("G_Y_K_N","gemini","yes",gbook,gtick,"kalshi","no",kbook),
                       ("G_N_K_Y","gemini","no",gbook,gtick,"kalshi","yes",kbook),
                       ("K_Y_G_N","kalshi","yes",kbook,ktick,"gemini","no",gbook),
                       ("K_N_G_Y","kalshi","no",kbook,ktick,"gemini","yes",gbook))
                plans={}
                for name,mv,ms,mb,tick,hv,hs,hb in specs:
                    p=safe_maker(maker_venue=mv,bid=mb[f"{ms}_bid"],ask=mb[f"{ms}_ask"],tick=tick,
                                 hedge_venue=hv,hedge_ask=hb[f"{hs}_ask"],multiplier=mult,fee_type=fee_type)
                    if p:
                        row={"key":key,"family":ident.market_family,"subject":ident.subject,"line":None if ident.line is None else str(ident.line),
                             "g":gr.venue_market_id,"k":kr.side.venue_market_id,"maker_venue":mv,"maker_side":ms.upper(),
                             "hedge_venue":hv,"hedge_side":hs.upper(),"gemini_tick_source":gts,**p}
                        plans[name]=row; makers.append(row)
                for orient,a,b in (("G_Y__K_N","G_Y_K_N","K_N_G_Y"),("K_Y__G_N","K_Y_G_N","G_N_K_Y")):
                    if a in plans and b in plans:
                        pa,pb=plans[a],plans[b]
                        dual.append({"key":key,"family":ident.market_family,"orientation":orient,
                            "fallback_floor":min(pa["fallback_profit"],pb["fallback_profit"]),
                            "both_fill_profit":ONE-pa["maker_all_in"]-pb["maker_all_in"],
                            "both_near_touch":bool(pa["near_touch"] and pb["near_touch"]),"a":pa,"b":pb})

    tf=[r for r in taker if r["profit"]>=FLOOR]
    mf=[r for r in makers if r["fallback_profit"]>=FLOOR]; mn=[r for r in mf if r["near_touch"]]
    df=[r for r in dual if r["fallback_floor"]>=FLOOR]; dn=[r for r in df if r["both_near_touch"]]
    taker.sort(key=lambda r:r["profit"],reverse=True)
    mf.sort(key=lambda r:(r["near_touch"],r["ticks_improved"],r["fallback_profit"]),reverse=True)
    df.sort(key=lambda r:(r["both_near_touch"],r["both_fill_profit"]),reverse=True)
    out={"status":"SPORTS_NFL_GK_MAKER_ARCH_SHADOW","gemini_events":len(gevents),"gemini_nfl_contracts":len(grows),
         "kalshi_events":{k:len(v) for k,v in byseries.items()},"kalshi_candidates":len(krows),"exact_counterpart_keys":len(keys),
         "match_stats":dict(match_stats),"priced_instances":sum(priced.values()),"priced_by_family":dict(priced),"rejects":dict(rejects),
         "taker_taker_positive":len(taker),"taker_taker_floor_3c":len(tf),
         "maker_hedge_floor_3c":len(mf),"maker_hedge_near_touch_floor_3c":len(mn),
         "dual_maker_fallback_floor_3c":len(df),"dual_maker_both_near_touch_floor_3c":len(dn),
         "top_taker":taker[:10],"top_maker":mf[:25],"top_dual":df[:10],"min_after_fee_floor":"0.03",
         "quote_sources":{"gemini":"active_event_bestBid_bestAsk","kalshi":"nested_event_bid_ask_sizes"},
         "gemini_received_ms":gms,"kalshi_received_ms":kms,"fetch_health":dict(health),
         "rule_gate":"NFL_GEMINI_KALSHI_FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
         "execution_eligible":False,"read_only":True,"order_entry":False,"cycle_ms":round((time.monotonic()-t0)*1000,3)}
    log.warning("SPORTS_NFL_GK_MAKER_ARCH_SHADOW %s",json.dumps(s(out),separators=(",",":"),default=str))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())

if __name__ == "__main__": main()
