from __future__ import annotations

import asyncio, hashlib, json, os, time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok

ONE=D("1"); ZERO=D("0"); TICK=D("0.01")
OFFSETS=(D("0"),D("0.01"),D("0.02"))
MIN_PRICE=D("0.20"); MAX_PRICE=D("0.70"); MIN_EDGE=D("0.00")
FIRST_WAIT=120.0; COMPLEMENT_WAIT=75.0; MIN_TTE=120.0
LIVE_ENV="GEMINI_L5_LADDER_LIVE_Q1"; SID_ENV="GEMINI_L5_LADDER_SESSION_ID"; ARM_ENV="GEMINI_L5_LADDER_ARM"; STATE_ENV="GEMINI_L5_LADDER_STATE_FILE"

def emit(stage:str,**kw:Any)->None:
    print("GEMINI_L5_LADDER_Q1 "+json.dumps({"stage":stage,"observed_at_utc":datetime.now(timezone.utc).isoformat(),**kw},separators=(",",":"),default=str),flush=True)

def armed()->bool:
    sid=os.getenv(SID_ENV,"").strip(); return os.getenv(LIVE_ENV,"").strip().upper()=="YES" and bool(sid) and os.getenv(ARM_ENV,"").strip()==sid

def _state(payload:Mapping[str,Any])->None:
    p=Path(os.getenv(STATE_ENV,"/state/gemini-l5-ladder-q1.json")); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(".tmp")
    with t.open("w") as f: json.dump(dict(payload),f,sort_keys=True,default=str); f.flush(); os.fsync(f.fileno())
    os.replace(t,p)

def cid(sid:str,side:str,rung:int)->str:
    h=hashlib.sha256(f"{sid}:{side}:{rung}".encode()).hexdigest()[:22]; out=f"l5lad-{h}-{side[0].lower()}{rung}"; assert len(out)<=36; return out

def prices(join:D)->tuple[D,...]: return tuple((D(str(join))-x).quantize(TICK) for x in OFFSETS)

def edge(y:D,n:D)->D: return ONE-y-n-gemini_predictions_maker_fee(y,ONE)-gemini_predictions_maker_fee(n,ONE)

def safe(ys:tuple[D,...],ns:tuple[D,...])->bool:
    return len(set(ys))==3 and len(set(ns))==3 and all(MIN_PRICE<=x<=MAX_PRICE for x in (*ys,*ns)) and edge(ys[0],ns[0])>=MIN_EDGE

async def refresh(spec:dict[str,Any],symbol:str)->None: spec["row"]=await base._order_by_client(spec["id"],symbol) or spec.get("row")
async def cancel(maker:base.GeminiMakerSession,spec:dict[str,Any],symbol:str)->dict[str,Any]:
    await refresh(spec,symbol); return await base._cancel_maker(maker,row=spec["row"]) if spec.get("row") else {"ok":False,"reason":"EXACT_ROW_NOT_FOUND"}
async def fills(specs:list[dict[str,Any]],symbol:str)->dict[str,D]:
    out={}
    for s in specs: await refresh(s,symbol); out[s["id"]]=base._filled(s.get("row"))
    return out

def summary(specs:list[dict[str,Any]],fs:Mapping[str,D])->dict[str,Any]:
    yq=nq=yc=nc=fees=ZERO; detail=[]
    for s in specs:
        q=D(str(fs.get(s["id"],ZERO)))
        if q<=0: continue
        c=q*s["price"]; f=gemini_predictions_maker_fee(s["price"],q); fees+=f
        if s["side"]=="YES": yq+=q; yc+=c
        else: nq+=q; nc+=c
        detail.append({"side":s["side"],"rung":s["rung"],"price":str(s["price"]),"qty":str(q),"fee":str(f)})
    return {"fills":detail,"yes_qty":str(yq),"no_qty":str(nq),"matched_qty":str(min(yq,nq)),"residual_qty":str(abs(yq-nq)),"maker_turnover":str(yc+nc),"maker_fees":str(fees)}

async def run()->None:
    if not armed(): emit("DISARMED",order_entry=False); return
    if not await _gemini_terms_ok(): raise RuntimeError("terms not accepted")
    if await _gemini_available_usd()<D("8"): raise RuntimeError("insufficient bounded test balance")
    event=await discover_eth5m()
    if event is None or event.seconds_to_expiry<MIN_TTE: emit("STOP",reason="NO_ETH5_WITH_ENOUGH_TTE",order_entry=False); return
    bid,bs,ask,asz=await base.fresh_top(event.symbol); ys=prices(bid); ns=prices(ONE-ask)
    if not safe(ys,ns): emit("STOP",reason="UNSAFE_LADDER",yes=[str(x) for x in ys],no=[str(x) for x in ns],top_edge=str(edge(ys[0],ns[0])),order_entry=False); return
    sid=os.getenv(SID_ENV,"").strip()[:16]
    specs=[]
    for side,arr in (("YES",ys),("NO",ns)):
        for i,p in enumerate(arr): specs.append({"side":side,"rung":i,"price":p,"id":cid(sid,side,i),"row":None})
    st={"terminal":False,"sid":sid,"symbol":event.symbol,"event":event.ticker,"orders":[{k:(str(v) if k=="price" else v) for k,v in s.items() if k!="row"} for s in specs]}; _state(st)
    emit("START",symbol=event.symbol,event_ticker=event.ticker,yes=[str(x) for x in ys],no=[str(x) for x in ns],top_edge=str(edge(ys[0],ns[0])),max_same_side_race_qty=3,max_new_residual_notional="2.10",yes_queue_hint=str(bs),no_queue_hint=str(asz),order_entry=True,realized_pnl=None)
    maker=base.GeminiMakerSession(); await maker.connect()
    try:
        posted=[]
        for s in specs:
            p={"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(s["price"]),"quantity":"1","eventOutcome":s["side"],"clientOrderId":s["id"]}
            a=await maker.request("order.place",p,timeout=4.0)
            if int(a.get("status",0))!=200:
                await asyncio.gather(*(cancel(maker,x,event.symbol) for x in posted),return_exceptions=True); await asyncio.sleep(.2); fs=await fills(specs,event.symbol); sm=summary(specs,fs); st.update({"terminal":True,"status":"POST_REJECT",**sm}); _state(st); emit("COMPLETE",status="POST_REJECT",**sm,order_entry=False,realized_pnl=None); return
            s["row"]=await base._find_row(s["id"],event.symbol)
            if s["row"] is None: raise RuntimeError("accepted exact ladder order not discoverable")
            posted.append(s)
        emit("LADDER_POSTED",n_orders=6,order_entry=True)
        ids={s["id"]:s["side"] for s in specs}; first=None; dl=min(time.monotonic()+FIRST_WAIT,time.monotonic()+max(1,event.seconds_to_expiry-30))
        while time.monotonic()<dl and first is None:
            try: x=await asyncio.wait_for(maker.events.get(),timeout=.4)
            except asyncio.TimeoutError: continue
            if not isinstance(x,Mapping): continue
            i=str(x.get("c") or ""); q=D(str(x.get("Z") or "0")); status=str(x.get("X") or "").upper()
            if i in ids and q>0 and status in {"FILLED","PARTIALLY_FILLED"}: first=ids[i]; emit("FIRST_FILL",side=first,client_id=i,reported_qty=str(q),action="CANCEL_SAME_SIDE_KEEP_COMPLEMENT",order_entry=True)
        if first is None:
            await asyncio.gather(*(cancel(maker,s,event.symbol) for s in specs),return_exceptions=True); await asyncio.sleep(.25); fs=await fills(specs,event.symbol); sm=summary(specs,fs); st.update({"terminal":True,"status":"NO_FILL_TIMEOUT",**sm}); _state(st); emit("COMPLETE",status="NO_FILL_TIMEOUT",**sm,order_entry=False,realized_pnl=None); return
        same=[s for s in specs if s["side"]==first]; opp=[s for s in specs if s["side"]!=first]
        await asyncio.gather(*(cancel(maker,s,event.symbol) for s in same),return_exceptions=True); await asyncio.sleep(.15); mid=summary(specs,await fills(specs,event.symbol)); emit("SAME_SIDE_CANCELLED",first_side=first,**mid,order_entry=True,realized_pnl=None)
        other="NO" if first=="YES" else "YES"; oq=D(mid["no_qty"] if other=="NO" else mid["yes_qty"])
        if oq<=0:
            dl=min(time.monotonic()+COMPLEMENT_WAIT,time.monotonic()+max(1,event.seconds_to_expiry-25)); seen=False
            while time.monotonic()<dl and not seen:
                try: x=await asyncio.wait_for(maker.events.get(),timeout=.4)
                except asyncio.TimeoutError: continue
                if not isinstance(x,Mapping): continue
                i=str(x.get("c") or ""); q=D(str(x.get("Z") or "0")); status=str(x.get("X") or "").upper()
                if ids.get(i)==other and q>0 and status in {"FILLED","PARTIALLY_FILLED"}: seen=True; emit("COMPLEMENT_FILL",side=other,client_id=i,reported_qty=str(q),action="CANCEL_COMPLEMENT_SIBLINGS",order_entry=True)
        await asyncio.gather(*(cancel(maker,s,event.symbol) for s in opp),return_exceptions=True); await asyncio.gather(*(cancel(maker,s,event.symbol) for s in specs),return_exceptions=True); await asyncio.sleep(.3)
        fs=await fills(specs,event.symbol); sm=summary(specs,fs); y=D(sm["yes_qty"]); n=D(sm["no_qty"])
        status="MATCHED" if y>0 and n>0 and y==n else "MATCHED_WITH_RACE_RESIDUAL" if y>0 and n>0 else "ONE_SIDED_RESIDUAL" if y>0 or n>0 else "NO_AUTHORITATIVE_FILL"
        st.update({"terminal":True,"status":status,**sm}); _state(st); emit("COMPLETE",status=status,**sm,settlement_required=(y>0 or n>0),order_entry=False,realized_pnl=None)
    finally: await maker.close()

def main()->None: asyncio.run(run())
if __name__=="__main__": main()
