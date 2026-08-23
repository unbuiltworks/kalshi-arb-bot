"""One-off fail-closed recovery for gkbasis-br0823131801-01.

Neutralize the orphaned Gemini SOL NO q=1 leg. Recovery order: sell NO; if no bid,
buy complementary YES on Gemini; if that cannot fill, try the intended Kalshi YES hedge.
Every recovery order is q=1 FOK and no new speculative paired trade is authorized.
"""
from __future__ import annotations
import asyncio, json, logging, time, uuid
from decimal import Decimal
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request
D=Decimal; logger=logging.getLogger("kalshi_arb.gemini_kalshi_basis_orphan_recovery")
TRADE_ID="gkbasis-br0823131801-01"; SYMBOL="GEMI-SOL15M2608231330-UP"; TICKER="KXSOL15M-26AUG230930-30"; OUTCOME="NO"
ORIGINAL_GEMINI_CLIENT_ID="solc2-br0823131801-01-g"; ORIGINAL_KALSHI_CLIENT_ID="solc2-br0823131801-01-k"
def _d(v):
    try:return D(str(v or "0"))
    except:return D("0")
async def _gemini_filled_orders():
    now=int(time.time()*1000); r=await private_rest_request("POST","/v1/prediction-markets/orders/history",params={"status":"filled","symbol":SYMBOL,"limit":100,"from":now-3600000,"to":now+1000},timeout=4.0)
    if r.status_code!=200: raise RuntimeError(f"gemini history status={r.status_code}")
    return list(r.json().get("orders",[]))
def _cid(r):return str(r.get("clientOrderId") or r.get("client_order_id") or "")
def _sym(r):return str(r.get("symbol") or "").upper()
def _out(r):return str(r.get("eventOutcome") or r.get("event_outcome") or r.get("outcome") or "").upper()
def _side(r):return str(r.get("side") or "").upper()
def _qty(r):return _d(r.get("filledQuantity") or r.get("filled_quantity") or r.get("executedQuantity") or r.get("quantity"))
def _net_no(rows):
    n=D("0")
    for r in rows:
        if _sym(r)!=SYMBOL: continue
        o=_out(r)
        if o and o!="NO": continue
        if _side(r)=="BUY": n+=_qty(r)
        elif _side(r)=="SELL": n-=_qty(r)
    return n
async def _kalshi_fill(k):
    r=await k.client.get("/portfolio/orders",params={"ticker":TICKER,"limit":100})
    if r.status_code!=200: raise RuntimeError(f"kalshi orders status={r.status_code}")
    for x in r.json().get("orders",[]):
        if str(x.get("client_order_id") or "")==ORIGINAL_KALSHI_CLIENT_ID:return _d(x.get("fill_count_fp") or x.get("fill_count"))
    return D("0")
async def main_async():
    k=KalshiV2Executor(); g=GeminiTradingSession()
    try:
        kfill=await _kalshi_fill(k); rows=await _gemini_filled_orders(); orig=[r for r in rows if _cid(r)==ORIGINAL_GEMINI_CLIENT_ID]; net=_net_no(rows)
        logger.critical("CRYPTO_BASIS_ORPHAN_RECONCILE %s",json.dumps({"trade_id":TRADE_ID,"kalshi_original_fill":str(kfill),"gemini_original_qty":str(sum((_qty(r) for r in orig),D('0'))),"gemini_net_no":str(net),"new_pair_trade_authorized":False},separators=(",",":")))
        if kfill>0 or net<=0:return
        if not orig or sum((_qty(r) for r in orig),D("0"))!=D("1") or net!=D("1"):raise RuntimeError("orphan exposure not exactly proven q1")
        await g.connect()
        sell={"symbol":SYMBOL,"side":"SELL","type":"LIMIT","timeInForce":"FOK","price":"0.01","quantity":"1","eventOutcome":"NO","clientOrderId":f"orphan-sell-no-{uuid.uuid4().hex[:14]}"}
        gr=await g.place_fok(sell,terminal_timeout=1.5); logger.critical("CRYPTO_BASIS_ORPHAN_GEMINI_SELL_NO %s",json.dumps(gr.as_dict(),default=str,separators=(",",":")))
        if gr.status=="FILLED" and _d(gr.filled_quantity)==D("1"): logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_VERIFIED mode=SELL_NO"); return
        yes={"symbol":SYMBOL,"side":"BUY","type":"LIMIT","timeInForce":"FOK","price":"0.99","quantity":"1","eventOutcome":"YES","clientOrderId":f"orphan-buy-yes-{uuid.uuid4().hex[:14]}"}
        gy=await g.place_fok(yes,terminal_timeout=1.5); logger.critical("CRYPTO_BASIS_ORPHAN_GEMINI_BUY_YES %s",json.dumps(gy.as_dict(),default=str,separators=(",",":")))
        if gy.status=="FILLED" and _d(gy.filled_quantity)==D("1"): logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_VERIFIED mode=BUY_COMPLEMENTARY_YES"); return
        hedge={"ticker":TICKER,"client_order_id":f"orphan-k-yes-{uuid.uuid4().hex[:14]}","side":"bid","count":"1.00","price":"0.99","time_in_force":"fill_or_kill","self_trade_prevention_type":"taker_at_cross","post_only":False,"cancel_order_on_pause":True,"reduce_only":False,"subaccount":0,"exchange_index":0}
        kr=await k.place(hedge); logger.critical("CRYPTO_BASIS_ORPHAN_KALSHI_BUY_YES %s",json.dumps(kr.as_dict(),default=str,separators=(",",":")))
        if kr.status!="FILLED" or _d(kr.filled_quantity)!=D("1"): raise RuntimeError("all orphan recovery paths unavailable")
        logger.critical("CRYPTO_BASIS_ORPHAN_RECOVERY_VERIFIED mode=KALSHI_YES_HEDGE")
    finally:
        await g.close(); await k.close()
def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s"); asyncio.run(main_async())
if __name__=="__main__":main()
