"""Read-only exact reconciliation for the approved Aug 29 q10 BTC5 V3 campaign."""
from __future__ import annotations
import asyncio, json, time
from decimal import Decimal as D
from typing import Any, Mapping
from src.gemini_prediction_auth import private_rest_request
from src.gemini_kalshi_live_service import _gemini_available_usd

SYMBOL='GEMI-BTC05M2608300230-UP'
TICKER='BTC05M2608300230'
PREFIX='q10p-usr-q10v3-20260829-'
START_AVAILABLE_USD=D('56.6055')


def side_from_client_id(cid: str) -> str:
    value=str(cid or '').lower()
    if '-yes-' in value:
        return 'YES'
    if '-no-' in value:
        return 'NO'
    return ''


async def main():
    now=int(time.time()*1000)
    available=D(str(await _gemini_available_usd()))
    out:dict[str,Any]={'classification':'READ_ONLY_EXACT_Q10_V3_RECONCILIATION','symbol':SYMBOL,'ticker':TICKER,
                       'available_usd':str(available),'start_available_usd':str(START_AVAILABLE_USD),
                       'observed_balance_delta':str(available-START_AVAILABLE_USD),'orders':{},'settled_position':None,'computed':{}}
    for status in ('filled','cancelled'):
        r=await private_rest_request('POST','/v1/prediction-markets/orders/history',params={
            'status':status,'symbol':SYMBOL,'limit':100,'offset':0,'from':now-2*3600*1000,'to':now+1000},timeout=5.0)
        if r.status_code!=200: continue
        for row in (r.json().get('orders') or []):
            if isinstance(row,Mapping) and str(row.get('clientOrderId') or '').startswith(PREFIX):
                keep={k:row.get(k) for k in ('clientOrderId','symbol','price','quantity','filledQuantity','averageExecutionPrice','eventOutcome','status','fee','fees','createdAt','updatedAt') if k in row}
                cid=str(keep.get('clientOrderId') or '')
                keep['inferredOutcome']=side_from_client_id(cid)
                out['orders'][cid]=keep

    settled=await private_rest_request('POST','/v1/prediction-markets/positions/settled',timeout=8.0)
    out['settled_status']=settled.status_code
    if settled.status_code==200:
        for row in (settled.json().get('positions') or []):
            if isinstance(row,Mapping) and str(row.get('instrumentSymbol') or '').upper()==SYMBOL:
                out['settled_position']={k:row.get(k) for k in (
                    'instrumentSymbol','position','positionQuantity','outcome','payout','resolutionSide','settledAt',
                    'costBasis','realizedPnl','netProfit','contractMetadata') if k in row}
                break

    yes_qty=no_qty=yes_cost=no_cost=D('0')
    for cid,row in out['orders'].items():
        qty=D(str(row.get('filledQuantity') or 0))
        px=D(str(row.get('averageExecutionPrice') or row.get('price') or 0))
        outcome=str(row.get('eventOutcome') or row.get('inferredOutcome') or side_from_client_id(cid)).upper()
        if outcome=='YES': yes_qty+=qty; yes_cost+=qty*px
        elif outcome=='NO': no_qty+=qty; no_cost+=qty*px
    matched=min(yes_qty,no_qty)
    gross_locked=matched-(yes_cost+no_cost) if yes_qty==no_qty==matched else None
    result=str((out.get('settled_position') or {}).get('resolutionSide') or '').upper()
    payout=(yes_qty if result=='YES' else no_qty if result=='NO' else None)
    computed={'yes_fill_qty':str(yes_qty),'no_fill_qty':str(no_qty),'yes_principal':str(yes_cost),'no_principal':str(no_cost),
              'matched_qty':str(matched),'residual_qty':str(abs(yes_qty-no_qty)),
              'gross_locked_pair_profit_before_fees_rebates':str(gross_locked) if gross_locked is not None else None,
              'observed_balance_delta':str(available-START_AVAILABLE_USD),'settlement_result':result}
    if payout is not None:
        computed.update({'settlement_payout_from_fills':str(payout),'trade_pnl_from_fill_principal_before_any_unreported_fees':str(payout-yes_cost-no_cost),'realized':True})
    else:
        computed['realized']=False
    pos=out.get('settled_position') or {}
    if pos:
        computed['gemini_settled_net_profit']=str(pos.get('netProfit'))
        computed['gemini_settled_cost_basis']=str(pos.get('costBasis'))
        computed['gemini_settled_payout']=str(pos.get('payout'))
    out['computed']=computed
    print('Q10_V3_EXACT_RECONCILIATION='+json.dumps(out,separators=(',',':'),default=str))

if __name__=='__main__': asyncio.run(main())
