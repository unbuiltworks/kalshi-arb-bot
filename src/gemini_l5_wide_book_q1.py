from __future__ import annotations

import asyncio, json, os, time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

from src import gemini_l5_ladder_q1 as ladder
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok

ONE=D("1"); ZERO=D("0")
MIN_PRICE=D("0.01"); MAX_PRICE=D("0.80"); MIN_EDGE=D("0.10")
FIRST_WAIT=120.0; COMPLEMENT_WAIT=75.0; MIN_TTE=120.0
LIVE_ENV="GEMINI_L5_WIDE_BOOK_LIVE_Q1"; SID_ENV="GEMINI_L5_WIDE_BOOK_SESSION_ID"; ARM_ENV="GEMINI_L5_WIDE_BOOK_ARM"; STATE_ENV="GEMINI_L5_WIDE_BOOK_STATE_FILE"


def emit(stage:str,**kw:Any)->None:
    print("GEMINI_L5_WIDE_BOOK_Q1 "+json.dumps({"stage":stage,"observed_at_utc":datetime.now(timezone.utc).isoformat(),**kw},separators=(",",":"),default=str),flush=True)


def armed()->bool:
    sid=os.getenv(SID_ENV,"").strip()
    return os.getenv(LIVE_ENV,"").strip().upper()=="YES" and bool(sid) and os.getenv(ARM_ENV,"").strip()==sid


def _state(payload:Mapping[str,Any])->None:
    p=Path(os.getenv(STATE_ENV,"/state/gemini-l5-wide-book-q1.json")); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(".tmp")
    with t.open("w") as f:
        json.dump(dict(payload),f,sort_keys=True,default=str); f.flush(); os.fsync(f.fileno())
    os.replace(t,p)


def edge(y:D,n:D)->D:
    return ONE-y-n-gemini_predictions_maker_fee(y,ONE)-gemini_predictions_maker_fee(n,ONE)


def safe(y:D,n:D)->bool:
    return MIN_PRICE<=y<=MAX_PRICE and MIN_PRICE<=n<=MAX_PRICE and edge(y,n)>=MIN_EDGE


def summary(specs:list[dict[str,Any]],fs:Mapping[str,D])->dict[str,Any]:
    yq=nq=yc=nc=fees=ZERO; detail=[]
    for s in specs:
        q=D(str(fs.get(s["id"],ZERO)))
        if q<=0: continue
        c=q*s["price"]; f=gemini_predictions_maker_fee(s["price"],q); fees+=f
        if s["side"]=="YES": yq+=q; yc+=c
        else: nq+=q; nc+=c
        detail.append({"side":s["side"],"price":str(s["price"]),"qty":str(q),"fee":str(f)})
    return {"fills":detail,"yes_qty":str(yq),"no_qty":str(nq),"matched_qty":str(min(yq,nq)),"residual_qty":str(abs(yq-nq)),"maker_turnover":str(yc+nc),"maker_fees":str(fees)}


async def run()->None:
    if not armed(): emit("DISARMED",order_entry=False); return
    if not await _gemini_terms_ok(): raise RuntimeError("terms not accepted")
    if await _gemini_available_usd()<D("4"): raise RuntimeError("insufficient bounded test balance")
    event=await discover_eth5m()
    if event is None or event.seconds_to_expiry<MIN_TTE:
        emit("STOP",reason="NO_ETH5_WITH_ENOUGH_TTE",order_entry=False); return
    bid,bs,ask,asz=await ladder.fresh_top_retry(event.symbol)
    y=D(str(bid)); n=(ONE-D(str(ask))).quantize(D("0.01"))
    e=edge(y,n)
    if not safe(y,n):
        emit("STOP",reason="WIDE_BOOK_EDGE_BELOW_FLOOR",yes=str(y),no=str(n),edge=str(e),yes_queue_hint=str(bs),no_queue_hint=str(asz),order_entry=False); return
    sid=os.getenv(SID_ENV,"").strip()
    specs=[
        {"side":"YES","price":y,"id":ladder.cid(sid,"YES",0),"row":None},
        {"side":"NO","price":n,"id":ladder.cid(sid,"NO",0),"row":None},
    ]
    st={"terminal":False,"sid":sid,"symbol":event.symbol,"event":event.ticker,"orders":[{k:(str(v) if k=="price" else v) for k,v in s.items() if k!="row"} for s in specs]}; _state(st)
    emit("START",symbol=event.symbol,event_ticker=event.ticker,yes=str(y),no=str(n),edge=str(e),max_same_side_race_qty=1,max_new_residual_notional=str(MAX_PRICE+D("0.01")),yes_queue_hint=str(bs),no_queue_hint=str(asz),order_entry=True,realized_pnl=None)
    maker=ladder.base.GeminiMakerSession(); await maker.connect()
    try:
        posted=[]
        for s in specs:
            p={"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(s["price"]),"quantity":"1","eventOutcome":s["side"],"clientOrderId":s["id"]}
            a=await maker.request("order.place",p,timeout=4.0)
            if int(a.get("status",0))!=200:
                await asyncio.gather(*(ladder.cancel(maker,x,event.symbol) for x in posted),return_exceptions=True)
                await asyncio.sleep(.2); fs=await ladder.fills(specs,event.symbol); sm=summary(specs,fs)
                st.update({"terminal":True,"status":"POST_REJECT",**sm}); _state(st); emit("COMPLETE",status="POST_REJECT",**sm,order_entry=False,realized_pnl=None); return
            s["row"]=await ladder.base._find_row(s["id"],event.symbol)
            if s["row"] is None: raise RuntimeError("accepted exact wide-book order not discoverable")
            posted.append(s)
        emit("PAIR_POSTED",n_orders=2,order_entry=True)
        ids={s["id"]:s["side"] for s in specs}; first=None
        dl=min(time.monotonic()+FIRST_WAIT,time.monotonic()+max(1,event.seconds_to_expiry-30))
        while time.monotonic()<dl and first is None:
            try: x=await asyncio.wait_for(maker.events.get(),timeout=.4)
            except asyncio.TimeoutError: continue
            if not isinstance(x,Mapping): continue
            i=str(x.get("c") or ""); q=D(str(x.get("Z") or "0")); status=str(x.get("X") or "").upper()
            if i in ids and q>0 and status in {"FILLED","PARTIALLY_FILLED"}:
                first=ids[i]; emit("FIRST_FILL",side=first,client_id=i,reported_qty=str(q),action="KEEP_COMPLEMENT_RESTING",order_entry=True)
        if first is None:
            await asyncio.gather(*(ladder.cancel(maker,s,event.symbol) for s in specs),return_exceptions=True); await asyncio.sleep(.25)
            fs=await ladder.fills(specs,event.symbol); sm=summary(specs,fs); st.update({"terminal":True,"status":"NO_FILL_TIMEOUT",**sm}); _state(st); emit("COMPLETE",status="NO_FILL_TIMEOUT",**sm,order_entry=False,realized_pnl=None); return
        other="NO" if first=="YES" else "YES"
        dl=min(time.monotonic()+COMPLEMENT_WAIT,time.monotonic()+max(1,event.seconds_to_expiry-25)); seen=False
        while time.monotonic()<dl and not seen:
            try: x=await asyncio.wait_for(maker.events.get(),timeout=.4)
            except asyncio.TimeoutError: continue
            if not isinstance(x,Mapping): continue
            i=str(x.get("c") or ""); q=D(str(x.get("Z") or "0")); status=str(x.get("X") or "").upper()
            if ids.get(i)==other and q>0 and status in {"FILLED","PARTIALLY_FILLED"}:
                seen=True; emit("COMPLEMENT_FILL",side=other,client_id=i,reported_qty=str(q),action="CANCEL_REMAINDER",order_entry=True)
        await asyncio.gather(*(ladder.cancel(maker,s,event.symbol) for s in specs),return_exceptions=True); await asyncio.sleep(.3)
        fs=await ladder.fills(specs,event.symbol); sm=summary(specs,fs); yq=D(sm["yes_qty"]); nq=D(sm["no_qty"])
        status="MATCHED" if yq>0 and nq>0 else "ONE_SIDED_RESIDUAL" if yq>0 or nq>0 else "NO_AUTHORITATIVE_FILL"
        st.update({"terminal":True,"status":status,**sm}); _state(st); emit("COMPLETE",status=status,**sm,settlement_required=(yq>0 or nq>0),order_entry=False,realized_pnl=None)
    finally:
        await maker.close()


def main()->None: asyncio.run(run())
if __name__=="__main__": main()
