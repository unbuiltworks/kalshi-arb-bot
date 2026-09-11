"""Read-only exact reconciliation for the Aug 30 Gemini q10 V4 live canary."""
from __future__ import annotations
import asyncio, json, time
from decimal import Decimal as D
from typing import Any, Mapping
from src.gemini_prediction_auth import private_rest_request
from src.gemini_kalshi_live_service import _gemini_available_usd

SYMBOL='GEMI-BTC05M2608301245-UP'
TICKER='BTC05M2608301245'
PREFIX='q10p-usr-q10v4-20260830-'

async def main():
    now=int(time.time()*1000)
    out:dict[str,Any]={'classification':'READ_ONLY_EXACT_Q10_V4_RECONCILIATION','symbol':SYMBOL,'ticker':TICKER,
                       'available_usd':str(await _gemini_available_usd()),'orders':[],'settled_position':None}
    for status in ('filled','cancelled'):
        r=await private_rest_request('POST','/v1/prediction-markets/orders/history',params={
            'status':status,'symbol':SYMBOL,'limit':100,'offset':0,'from':now-2*24*3600*1000,'to':now+1000},timeout=8.0)
        if r.status_code!=200:
            out.setdefault('history_errors',[]).append({'status':status,'http':r.status_code}); continue
        for row in (r.json().get('orders') or []):
            if isinstance(row,Mapping) and str(row.get('clientOrderId') or '').startswith(PREFIX):
                keep={k:row.get(k) for k in ('clientOrderId','symbol','price','quantity','filledQuantity','averageExecutionPrice','eventOutcome','status','fee','fees','createdAt','updatedAt') if k in row}
                out['orders'].append(keep)
    settled=await private_rest_request('POST','/v1/prediction-markets/positions/settled',timeout=8.0)
    out['settled_status']=settled.status_code
    if settled.status_code==200:
        for row in (settled.json().get('positions') or []):
            if isinstance(row,Mapping) and str(row.get('instrumentSymbol') or '').upper()==SYMBOL:
                out['settled_position']={k:row.get(k) for k in (
                    'instrumentSymbol','position','positionQuantity','outcome','payout','resolutionSide','settledAt','costBasis','realizedPnl','netProfit','contractMetadata') if k in row}
                break
    fills=[]
    for row in out['orders']:
        q=D(str(row.get('filledQuantity') or 0)); px=D(str(row.get('averageExecutionPrice') or row.get('price') or 0))
        if q>0: fills.append({'cid':row.get('clientOrderId'),'outcome':row.get('eventOutcome'),'qty':str(q),'price':str(px)})
    out['computed']={'fills':fills,'fill_count':len(fills),'one_leg_only':len(fills)==1}
    if out['settled_position']:
        p=out['settled_position']; out['computed'].update({'realized':True,'resolution':str(p.get('resolutionSide') or '').upper(),'net_profit':str(p.get('netProfit')),'cost_basis':str(p.get('costBasis')),'payout':str(p.get('payout'))})
    else:
        out['computed']['realized']=False
    print('Q10_V4_EXACT_RECONCILIATION='+json.dumps(out,separators=(',',':'),default=str))

if __name__=='__main__': asyncio.run(main())
