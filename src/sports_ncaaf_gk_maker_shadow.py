"""Strict read-only NCAAF Gemini/Kalshi maker-vs-taker shadow scan.

Identity joins use exact local game date plus exact normalized full team names from
venue titles. No fuzzy team matching, auth, or order entry. Unknown/variant names
fail closed. Economics are q=1 discovery only; Gemini depth and full settlement-rule
fingerprints must be revalidated before any live path can use a row.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json, logging, re, time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx
from src.gemini_sports_adapter import normalize_events as normalize_gemini_events
from src.strict_mlb_prop_snapshot import conservative_kalshi_fee
from src.three_venue_pricing_core import gemini_one_contract_fee

D=Decimal; ONE=D("1"); ZERO=D("0"); CENT=D("0.01"); FLOOR=D("0.03"); MAKER_RATE=D("0.0175")
ET=ZoneInfo("America/New_York")
G="https://api.gemini.com"; K="https://external-api.kalshi.com/trade-api/v2"
SERIES={"KXNCAAFGAME":"moneyline","KXNCAAFSPREAD":"spread","KXNCAAFTOTAL":"game_total"}
DATE_RE=re.compile(r"^KXNCAAF(?:GAME|SPREAD|TOTAL)-(?P<date>\d{2}[A-Z]{3}\d{2})")
SPREAD_RE=re.compile(r"^(?P<team>.+?) wins by over (?P<line>\d+(?:\.5)) points$",re.I)
TOTAL_RE=re.compile(r"^Over (?P<line>\d+(?:\.5)) points scored$",re.I)
log=logging.getLogger("kalshi_arb.sports_ncaaf_gk_maker_shadow")


def d(v:Any)->D|None:
    try: x=D(str(v))
    except Exception:return None
    return x if x.is_finite() else None


def team(v:Any)->str:
    x=str(v or "").strip().lower().replace("’","'").replace("&"," and ")
    x=x.replace("'","")
    x=re.sub(r"[^a-z0-9]+"," ",x).strip()
    parts=x.split()
    # A trailing "St." in college team names means State; a leading "St." may mean Saint.
    if parts and parts[-1]=="st": parts[-1]="state"
    x=" ".join(parts)
    explicit={"university at albany":"albany"}
    return explicit.get(x,x)


def teams_from_title(title:Any)->tuple[str,str]|None:
    base=str(title or "").split(":",1)[0].strip()
    if " vs " not in base:return None
    a,b=base.split(" vs ",1); a=team(a); b=team(b)
    if not a or not b or a==b:return None
    return tuple(sorted((a,b)))


def kalshi_date(ticker:Any):
    m=DATE_RE.match(str(ticker or "").upper())
    if not m:return None
    try:return datetime.strptime(m.group("date"),"%y%b%d").date()
    except ValueError:return None


def half(x:D|None)->bool:
    return x is not None and (abs(x)*2)==(abs(x)*2).to_integral_value() and int(abs(x)*2)%2==1


def g_subject(family:str,label:str)->str|None:
    if family=="game_total":return "contest"
    if family=="moneyline":return team(label) or None
    if family=="spread":
        m=re.match(r"^(?P<t>.+?) wins by (?:more than|over) \d+(?:\.5) points$",label.strip(),re.I)
        return team(m.group("t")) if m else None
    return None


def gbook(c:Mapping[str,Any])->dict[str,D]|None:
    p=c.get("prices") if isinstance(c.get("prices"),Mapping) else {}
    yb=d(p.get("bestBid")); ya=d(p.get("bestAsk"))
    if yb is None or ya is None or not (ZERO<=yb<ya<=ONE):return None
    return {"yes_bid":yb,"yes_ask":ya,"no_bid":ONE-ya,"no_ask":ONE-yb}


def kbook(m:Mapping[str,Any])->dict[str,D]|None:
    yb=d(m.get("yes_bid_dollars"));ya=d(m.get("yes_ask_dollars"));nb=d(m.get("no_bid_dollars"));na=d(m.get("no_ask_dollars"))
    ybs=d(m.get("yes_bid_size_fp"));yas=d(m.get("yes_ask_size_fp"))
    if None in (yb,ya,nb,na,ybs,yas):return None
    assert yb is not None and ya is not None and nb is not None and na is not None and ybs is not None and yas is not None
    if not (ZERO<=yb<ya<=ONE and ZERO<=nb<na<=ONE) or ybs<=ZERO or yas<=ZERO:return None
    if abs(na-(ONE-yb))>D("0.0001") or abs(nb-(ONE-ya))>D("0.0001"):return None
    return {"yes_bid":yb,"yes_ask":ya,"no_bid":nb,"no_ask":na,"yes_bid_size":ybs,"yes_ask_size":yas}


def ktick(m:Mapping[str,Any])->D:
    for r in m.get("price_ranges") or []:
        if isinstance(r,Mapping):
            x=d(r.get("step"))
            if x is not None and ZERO<x<=D("0.10"):return x
    return CENT


def gtick(c:Mapping[str,Any])->tuple[D,str]:
    x=d(c.get("priceIncrement"))
    return (x,"contract.priceIncrement") if x is not None and ZERO<x<=D("0.10") else (CENT,"conservative_default_0.01")


def ceilcent(x:D)->D:return (x*100).to_integral_value(rounding=ROUND_CEILING)/100

def maker_fee(v:str,p:D,mult:D|None=None,typ:str="")->D|None:
    if v=="gemini":return ceilcent(MAKER_RATE*p*(ONE-p))
    if v=="kalshi" and mult is not None:return ceilcent(MAKER_RATE*mult*p*(ONE-p)) if typ=="quadratic_with_maker_fees" else ZERO
    return None

def taker_fee(v:str,p:D,mult:D|None=None)->D|None:
    if v=="gemini":return gemini_one_contract_fee(p)
    if v=="kalshi" and mult is not None:return conservative_kalshi_fee(1,p,mult)
    return None

def cost(v:str,p:D,kind:str,mult:D|None=None,typ:str="")->D|None:
    f=maker_fee(v,p,mult,typ) if kind=="maker" else taker_fee(v,p,mult)
    return None if f is None else p+f


def safe_maker(mv:str,side:str,mb:Mapping[str,D],tick:D,hv:str,hside:str,hb:Mapping[str,D],mult:D,typ:str)->dict[str,Any]|None:
    bid=mb[f"{side}_bid"];ask=mb[f"{side}_ask"];ha=hb[f"{hside}_ask"]
    hc=cost(hv,ha,"taker",mult if hv=="kalshi" else None)
    if hc is None or bid>=ask or tick<=ZERO:return None
    upper=ask-tick
    if upper<bid:return None
    best=None;p=bid;guard=0
    while p<=upper+D("0.0000001") and guard<200:
        mc=cost(mv,p,"maker",mult if mv=="kalshi" else None,typ if mv=="kalshi" else "")
        if mc is None:return None
        prof=ONE-mc-hc
        if prof>=FLOOR:best={"maker_price":p,"maker_all_in":mc,"hedge_ask":ha,"hedge_all_in":hc,"fallback_profit":prof,"near_touch":p>=upper,"ticks_improved":int(((p-bid)/tick).to_integral_value()),"bid":bid,"ask":ask,"tick":tick}
        p+=tick;guard+=1
    return best


def ser(v:Any)->Any:
    if isinstance(v,D):return str(v)
    if isinstance(v,Mapping):return {str(k):ser(x) for k,x in v.items()}
    if isinstance(v,list):return [ser(x) for x in v]
    return v


async def get(h:httpx.AsyncClient,url:str,health:Counter,**kwargs):
    for i in range(3):
        health["requests"]+=1
        try:
            r=await h.get(url,**kwargs)
            if r.status_code==429 and i<2:
                health["429"]+=1;await asyncio.sleep(.25*(i+1));continue
            r.raise_for_status();health["success"]+=1;return r.json()
        except Exception:
            health["failure"]+=1
            if i<2:await asyncio.sleep(.2*(i+1));continue
    return {}


async def run_once()->dict[str,Any]:
    t0=time.monotonic();health=Counter()
    async with httpx.AsyncClient(timeout=15.0) as h:
        gb=await get(h,f"{G}/v1/prediction-markets/events",health,params={"status":"active","sport":"american_football","limit":500,"offset":0})
        ge=[x for x in (gb.get("data") or gb.get("events") or []) if isinstance(x,Mapping) and "NCAAF" in str(x.get("ticker") or x.get("eventTicker") or "").upper()]
        ke={}
        for sname in SERIES:
            b=await get(h,f"{K}/events",health,params={"status":"open","series_ticker":sname,"limit":200,"with_nested_markets":"true"})
            ke[sname]=[x for x in b.get("events",[]) if isinstance(x,Mapping)]
        fees={}
        for sname in SERIES:
            b=await get(h,f"{K}/series/{sname}",health);meta=b.get("series") if isinstance(b.get("series"),Mapping) else {}
            mult=d(meta.get("fee_multiplier"));typ=str(meta.get("fee_type") or "")
            if mult is not None and typ in {"quadratic","quadratic_with_maker_fees"}:fees[sname]=(mult,typ)

    # Gemini exact semantic rows keyed by local date + exact normalized full names.
    gidx=defaultdict(list);gstats=Counter(); gcontracts={}
    for e in ge:
        pair=teams_from_title(e.get("title") or e.get("name"))
        if pair is None:gstats["bad_event_title"]+=1;continue
        rows=normalize_gemini_events([e])
        for c in e.get("contracts") or []:
            if isinstance(c,Mapping):
                sym=str(c.get("instrumentSymbol") or c.get("ticker") or "")
                if sym:gcontracts[sym]=c
        for r in rows:
            ident=r.identity
            if ident.event.league!="ncaaf" or ident.scope!="full_contest" or ident.market_family not in {"moneyline","spread","game_total"}:continue
            local=datetime.fromtimestamp(ident.event.scheduled_start_ms/1000,tz=timezone.utc).astimezone(ET).date()
            subject=g_subject(ident.market_family,r.contract_label)
            if subject is None:gstats["bad_subject"]+=1;continue
            line=ident.line
            if ident.market_family in {"spread","game_total"} and not half(line):gstats["bad_line"]+=1;continue
            key=(local,pair,ident.market_family,subject,None if line is None else str(line))
            gidx[key].append((r,gcontracts.get(r.instrument_symbol)));gstats[ident.market_family]+=1

    kidx=defaultdict(list);kstats=Counter()
    for sname,events in ke.items():
        family=SERIES[sname]
        for e in events:
            pair=teams_from_title(e.get("title"));date=kalshi_date(e.get("event_ticker"))
            if pair is None or date is None:kstats["bad_event"]+=1;continue
            for m in e.get("markets") or []:
                if not isinstance(m,Mapping) or str(m.get("status") or "").lower() not in {"active",""}:continue
                title=str(m.get("title") or "").strip();subject="contest";line=None
                if family=="moneyline":subject=team(m.get("yes_sub_title") or title.replace(" wins",""))
                elif family=="spread":
                    sm=SPREAD_RE.fullmatch(title)
                    if not sm:kstats["spread_parse"]+=1;continue
                    subject=team(sm.group("team"));line=d(sm.group("line"))
                else:
                    tm=TOTAL_RE.fullmatch(title)
                    if not tm:kstats["total_parse"]+=1;continue
                    line=d(tm.group("line"))
                if not subject or (family in {"spread","game_total"} and not half(line)):kstats["semantic_reject"]+=1;continue
                key=(date,pair,family,subject,None if line is None else str(line))
                kidx[key].append((sname,m));kstats[family]+=1

    keys=sorted(set(gidx)&set(kidx)); taker=[];makers=[];dual=[];rejects=Counter();priced=Counter()
    for key in keys:
        _,pair,family,subject,line=key
        for gr,gc in gidx[key]:
            if not isinstance(gc,Mapping):rejects["missing_g_contract"]+=1;continue
            gbk=gbook(gc)
            if gbk is None:rejects["missing_g_book"]+=1;continue
            gstep,gstep_src=gtick(gc)
            for sname,km in kidx[key]:
                kbk=kbook(km);meta=fees.get(sname)
                if kbk is None:rejects["bad_k_book"]+=1;continue
                if meta is None:rejects["missing_fee"]+=1;continue
                mult,typ=meta;kstep=ktick(km);priced[family]+=1
                gy=cost("gemini",gbk["yes_ask"],"taker");gn=cost("gemini",gbk["no_ask"],"taker")
                ky=cost("kalshi",kbk["yes_ask"],"taker",mult);kn=cost("kalshi",kbk["no_ask"],"taker",mult)
                if None not in (gy,gn,ky,kn):
                    for direction,p in (("G_Y_K_N",ONE-gy-kn),("K_Y_G_N",ONE-ky-gn)):
                        if p>ZERO:taker.append({"pair":pair,"family":family,"subject":subject,"line":line,"direction":direction,"profit":p,"g":gr.instrument_symbol,"k":km.get("ticker")})
                specs=(("G_Y_K_N","gemini","yes",gbk,gstep,"kalshi","no",kbk),("G_N_K_Y","gemini","no",gbk,gstep,"kalshi","yes",kbk),("K_Y_G_N","kalshi","yes",kbk,kstep,"gemini","no",gbk),("K_N_G_Y","kalshi","no",kbk,kstep,"gemini","yes",gbk))
                plans={}
                for name,mv,ms,mb,step,hv,hs,hb in specs:
                    p=safe_maker(mv,ms,mb,step,hv,hs,hb,mult,typ)
                    if p:
                        row={"pair":pair,"family":family,"subject":subject,"line":line,"g":gr.instrument_symbol,"k":km.get("ticker"),"maker_venue":mv,"maker_side":ms.upper(),"hedge_venue":hv,"hedge_side":hs.upper(),"gemini_tick_source":gstep_src,**p}
                        plans[name]=row;makers.append(row)
                for orient,a,b in (("G_Y__K_N","G_Y_K_N","K_N_G_Y"),("K_Y__G_N","K_Y_G_N","G_N_K_Y")):
                    if a in plans and b in plans:
                        pa,pb=plans[a],plans[b];dual.append({"pair":pair,"family":family,"subject":subject,"line":line,"orientation":orient,"fallback_floor":min(pa["fallback_profit"],pb["fallback_profit"]),"both_fill_profit":ONE-pa["maker_all_in"]-pb["maker_all_in"],"both_near_touch":pa["near_touch"] and pb["near_touch"],"a":pa,"b":pb})
    tf=[r for r in taker if r["profit"]>=FLOOR];mf=[r for r in makers if r["fallback_profit"]>=FLOOR];mn=[r for r in mf if r["near_touch"]];df=[r for r in dual if r["fallback_floor"]>=FLOOR];dn=[r for r in df if r["both_near_touch"]]
    taker.sort(key=lambda r:r["profit"],reverse=True);mf.sort(key=lambda r:(r["near_touch"],r["ticks_improved"],r["fallback_profit"]),reverse=True);df.sort(key=lambda r:(r["both_near_touch"],r["both_fill_profit"]),reverse=True)
    out={"status":"SPORTS_NCAAF_GK_MAKER_ARCH_SHADOW","gemini_ncaaf_events":len(ge),"kalshi_events":{k:len(v) for k,v in ke.items()},"gemini_semantic_rows":sum(gstats[f] for f in ("moneyline","spread","game_total")),"kalshi_semantic_rows":sum(kstats[f] for f in ("moneyline","spread","game_total")),"exact_counterpart_keys":len(keys),"priced_instances":sum(priced.values()),"priced_by_family":dict(priced),"gstats":dict(gstats),"kstats":dict(kstats),"rejects":dict(rejects),"taker_taker_positive":len(taker),"taker_taker_floor_3c":len(tf),"maker_hedge_floor_3c":len(mf),"maker_hedge_near_touch_floor_3c":len(mn),"dual_maker_fallback_floor_3c":len(df),"dual_maker_both_near_touch_floor_3c":len(dn),"top_taker":taker[:10],"top_maker":mf[:30],"top_dual":df[:10],"min_after_fee_floor":"0.03","identity_method":"EXACT_LOCAL_DATE_PLUS_NORMALIZED_FULL_TEAM_NAMES_NO_FUZZY","rule_gate":"NCAAF_GEMINI_KALSHI_FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION","execution_eligible":False,"read_only":True,"order_entry":False,"fetch_health":dict(health),"cycle_ms":round((time.monotonic()-t0)*1000,3)}
    log.warning("SPORTS_NCAAF_GK_MAKER_ARCH_SHADOW %s",json.dumps(ser(out),separators=(",",":"),default=str));return out

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s");asyncio.run(run_once())
