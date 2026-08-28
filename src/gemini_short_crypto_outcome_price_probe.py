"""Read-only consistency check for Gemini YES/NO outcome pricing.

Gemini short-duration crypto exposes one contract instrument symbol (typically
ending in -UP) while authenticated orders carry an explicit YES/NO outcome.
This probe compares the public contract's documented outcome prices with the
single-symbol order book transform used by the l5 model.

No credentials, orders, fills, or P&L.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import re
from typing import Any, Mapping

import httpx

REST = "https://api.gemini.com"
PAT = re.compile(r"^(BTC|ETH)(05M|15M)\d{10}$")


def d(v: Any) -> D | None:
    try:
        return D(str(v)) if v is not None else None
    except Exception:
        return None


def top(book: Mapping[str, Any]) -> tuple[D | None, D | None]:
    bids=[]; asks=[]
    for row in book.get("bids") or []:
        if isinstance(row, Mapping):
            p=d(row.get("price")); q=d(row.get("amount") or row.get("quantity"))
            if p is not None and q is not None and q>0: bids.append(p)
    for row in book.get("asks") or []:
        if isinstance(row, Mapping):
            p=d(row.get("price")); q=d(row.get("amount") or row.get("quantity"))
            if p is not None and q is not None and q>0: asks.append(p)
    return (max(bids) if bids else None, min(asks) if asks else None)


def get_nested(prices: Mapping[str, Any], a: str, b: str) -> D | None:
    x=prices.get(a)
    return d(x.get(b)) if isinstance(x, Mapping) else None


async def main() -> None:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent":"arb-gemini-outcome-price-probe/1.0"}) as http:
        r=await http.get(f"{REST}/v1/prediction-markets/events", params=[("status","active"),("category","Crypto"),("limit","500")])
        r.raise_for_status(); payload=r.json(); rows=payload.get("data") or []
        out=[]
        for ev in rows:
            if not isinstance(ev, Mapping): continue
            ticker=str(ev.get("ticker") or "").upper()
            m=PAT.match(ticker)
            if not m: continue
            for c in ev.get("contracts") or []:
                if not isinstance(c, Mapping): continue
                symbol=str(c.get("instrumentSymbol") or "")
                if not symbol: continue
                br=await http.get(f"{REST}/v1/book/{symbol}", params={"limit_bids":20,"limit_asks":20}, headers={"Cache-Control":"no-cache"})
                if br.status_code != 200: continue
                bid,ask=top(br.json() if isinstance(br.json(), Mapping) else {})
                prices=c.get("prices") if isinstance(c.get("prices"), Mapping) else {}
                buy_yes=get_nested(prices,"buy","yes")
                buy_no=get_nested(prices,"buy","no")
                sell_yes=get_nested(prices,"sell","yes")
                sell_no=get_nested(prices,"sell","no")
                derived_no_bid=(D("1")-ask) if ask is not None else None
                derived_no_ask=(D("1")-bid) if bid is not None else None
                out.append({
                    "ticker":ticker,"asset":m.group(1),"horizon":"5m" if m.group(2)=="05M" else "15m","symbol":symbol,
                    "price_increment":str(c.get("priceIncrement") or ""),
                    "book_yes_bid":str(bid) if bid is not None else None,"book_yes_ask":str(ask) if ask is not None else None,
                    "derived_no_bid":str(derived_no_bid) if derived_no_bid is not None else None,"derived_no_ask":str(derived_no_ask) if derived_no_ask is not None else None,
                    "reported_buy_yes":str(buy_yes) if buy_yes is not None else None,"reported_buy_no":str(buy_no) if buy_no is not None else None,
                    "reported_sell_yes":str(sell_yes) if sell_yes is not None else None,"reported_sell_no":str(sell_no) if sell_no is not None else None,
                    "buy_no_matches_derived_no_ask": bool(buy_no is not None and derived_no_ask is not None and buy_no==derived_no_ask),
                    "sell_no_matches_derived_no_bid": bool(sell_no is not None and derived_no_bid is not None and sell_no==derived_no_bid),
                })
        summary={"type":"GEMINI_SHORT_CRYPTO_OUTCOME_PRICE_PROBE","observed_at":datetime.now(timezone.utc).isoformat(),"rows":len(out),"rows_detail":out,"read_only":True,"order_entry":False,"realized_pnl":None}
        print(json.dumps(summary,indent=2,sort_keys=True))

if __name__ == "__main__": asyncio.run(main())
