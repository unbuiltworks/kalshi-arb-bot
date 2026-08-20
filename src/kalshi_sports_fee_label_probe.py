"""Compare current public Kalshi fee metadata across proven MLB fills and NFL series."""
from __future__ import annotations

import asyncio
import json

import httpx

from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata

SERIES=("KXMLBHR","KXMLBHIT","KXNFLGAME","KXNFLSPREAD","KXNFLTOTAL")


async def run():
    async with httpx.AsyncClient(timeout=20.0) as http:
        rows=await asyncio.gather(*(fetch_series_fee_metadata(http,s) for s in SERIES),return_exceptions=True)
    out={}
    for s,row in zip(SERIES,rows):
        if isinstance(row,Exception):
            out[s]={"error_type":type(row).__name__,"error":str(row)[:160]}
        else:
            out[s]=row.as_dict()
    return {"status":"KALSHI_SPORTS_FEE_LABEL_PROBE","series":out,"read_only":True,"order_entry":False}


def main():
    print(json.dumps(asyncio.run(run()),separators=(",",":"),sort_keys=True,default=str),flush=True)


if __name__=="__main__": main()
