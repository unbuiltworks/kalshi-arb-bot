"""Read-only exact reconciliation for the Aug 29 Gemini q10 reward probe."""
from __future__ import annotations
import asyncio, json, time
from decimal import Decimal as D
from typing import Any, Mapping
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
                       'available_usd':str(await _gemini_available_usd()),'orders':{},'settled_position':None,'computed':{}}
    for status in ('filled','cancelled'):
        r=await private_rest_request('POST','/v1/prediction-markets/orders/history',params={
            'status':status,'symbol':SYMBOL,'limit':100,'offset':0,'from':now-3*24*3600*1000,'to':now+1000},timeout=5.0)
        if r.status_code!=200: continue
        for row in (r.json().get('orders') or []):
            if isinstance(row,Mapping) and str(row.get('clientOrderId') or '') in IDS:
                keep={k:row.get(k) for k in ('clientOrderId','symbol','price','quantity','filledQuantity','averageExecutionPrice','eventOutcome','status','fee','fees','createdAt','updatedAt') if k in row}
                out['orders'][keep['clientOrderId']]=keep

    # Gemini's historical settled-position endpoint is the authoritative recovery
    # source after the fire-once settlement WebSocket event has passed.
    settled=await private_rest_request('POST','/v1/prediction-markets/positions/settled',timeout=8.0)
    out['settled_status']=settled.status_code
    if settled.status_code==200:
        for row in (settled.json().get('positions') or []):
            if isinstance(row,Mapping) and str(row.get('instrumentSymbol') or '').upper()==SYMBOL:
                out['settled_position']={k:row.get(k) for k in (
                    'instrumentSymbol','position','positionQuantity','outcome','payout','resolutionSide',
                    'settledAt','costBasis','realizedPnl','netProfit','contractMetadata') if k in row}
                break

    y=out['orders'].get(YES_ID) or {}
    qty=D(str(y.get('filledQuantity') or 0)); px=D(str(y.get('averageExecutionPrice') or y.get('price') or 0))
    pos=out.get('settled_position') or {}
    if pos:
        payout=D(str(pos.get('payout') or 0))
        cost=D(str(pos.get('costBasis') or qty*px))
        rpnl=D(str(pos.get('realizedPnl') or 0))
        net_raw=pos.get('netProfit')
        net=D(str(net_raw)) if net_raw is not None else payout-cost+rpnl
        out['computed']={'yes_fill_qty':str(qty),'yes_fill_price':str(px),'settlement_result':str(pos.get('resolutionSide') or '').upper(),
                         'settlement_payout':str(payout),'settled_cost_basis':str(cost),'settled_realized_pnl':str(rpnl),
                         'trade_net_profit':str(net),'realized':True}
    else:
        out['computed']={'yes_fill_qty':str(qty),'yes_fill_price':str(px),'realized':False}
    print('Q10_EXACT_RECONCILIATION='+json.dumps(out,separators=(',',':'),default=str))

if __name__=='__main__': asyncio.run(main())
