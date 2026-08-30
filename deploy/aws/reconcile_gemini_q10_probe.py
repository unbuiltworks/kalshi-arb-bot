"""Read-only exact reconciliation for the Aug 29 Gemini q10 reward probe."""
from __future__ import annotations
import asyncio, json, time
from decimal import Decimal as D
from typing import Any, Mapping
import httpx
from src.gemini_prediction_auth import private_rest_request
from src.gemini_kalshi_live_service import _gemini_available_usd

SYMBOL='GEMI-BTC05M2608291955-UP'
TICKER='BTC05M2608291955'
YES_ID='gem-q10-reward-usr-q10lr-2026082-yes'
NO_ID='gem-q10-reward-usr-q10lr-2026082-no'
IDS={YES_ID,NO_ID}

async def main():
    now=int(time.time()*1000)
    out:dict[str,Any]={'classification':'READ_ONLY_EXACT_Q10_PROBE_RECONCILIATION','symbol':SYMBOL,'ticker':TICKER,
                       'available_usd':str(await _gemini_available_usd()),'orders':{},'event':None,'computed':{}}
    for status in ('filled','cancelled'):
        r=await private_rest_request('POST','/v1/prediction-markets/orders/history',params={
            'status':status,'symbol':SYMBOL,'limit':100,'offset':0,'from':now-3*24*3600*1000,'to':now+1000},timeout=5.0)
        if r.status_code!=200: continue
        for row in (r.json().get('orders') or []):
            if isinstance(row,Mapping) and str(row.get('clientOrderId') or '') in IDS:
                keep={k:row.get(k) for k in ('clientOrderId','symbol','price','quantity','filledQuantity','averageExecutionPrice','eventOutcome','status','fee','fees','createdAt','updatedAt') if k in row}
                out['orders'][keep['clientOrderId']]=keep
    async with httpx.AsyncClient(timeout=8.0,follow_redirects=True,headers={'User-Agent':'arb-q10-reconcile/1.0'}) as http:
        for status in ('settled','active'):
            r=await http.get('https://api.gemini.com/v1/prediction-markets/events',params={'status':status,'category':'Crypto','limit':500,'offset':0})
            if r.status_code!=200: continue
            for row in (r.json().get('data') or []):
                if isinstance(row,Mapping) and str(row.get('ticker') or '').upper()==TICKER:
                    out['event']={k:row.get(k) for k in ('ticker','title','status','marketState','result','resolution','settlementValue','settledAt','expiryDate') if k in row}
                    break
    y=out['orders'].get(YES_ID) or {}
    qty=D(str(y.get('filledQuantity') or 0)); px=D(str(y.get('averageExecutionPrice') or y.get('price') or 0))
    fee_raw=y.get('fee')
    if fee_raw is None and isinstance(y.get('fees'),(str,int,float)): fee_raw=y.get('fees')
    try: fee=D(str(fee_raw or 0))
    except Exception: fee=D('0')
    result=str((out.get('event') or {}).get('result') or (out.get('event') or {}).get('resolution') or '').upper()
    payout=qty if result=='YES' else D('0') if result=='NO' else None
    if payout is not None:
        out['computed']={'yes_fill_qty':str(qty),'yes_fill_price':str(px),'entry_principal':str(qty*px),
                         'reported_order_fee':str(fee),'settlement_result':result,'settlement_payout':str(payout),
                         'trade_pnl_before_rebates':str(payout-qty*px-fee),'realized':True}
    else:
        out['computed']={'yes_fill_qty':str(qty),'yes_fill_price':str(px),'reported_order_fee':str(fee),'realized':False}
    print('Q10_EXACT_RECONCILIATION='+json.dumps(out,separators=(',',':'),default=str))

if __name__=='__main__': asyncio.run(main())
