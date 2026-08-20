"""Read-only reconciliation for one ambiguous Gemini/Kalshi crypto attempt."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import os
import time
from src.auth import KalshiClient
from src.gemini_prediction_auth import private_rest_request

GID=os.getenv("RECON_GEMINI_CLIENT_ID","").strip(); KID=os.getenv("RECON_KALSHI_CLIENT_ID","").strip()
GSYM=os.getenv("RECON_GEMINI_SYMBOL","").strip(); KTICK=os.getenv("RECON_KALSHI_TICKER","").strip()
KALSHI_PROD_REST="https://external-api.kalshi.com/trade-api/v2"

async def main():
    if not all([GID,KID,GSYM,KTICK]): raise RuntimeError("all RECON_* identifiers required")
    now=int(time.time()*1000); gm=[]; gh=[]
    qs=[("active","/v1/prediction-markets/orders/active",{"symbol":GSYM,"limit":100,"offset":0}),
        ("filled","/v1/prediction-markets/orders/history",{"status":"filled","symbol":GSYM,"limit":100,"from":now-86400000,"to":now+1000}),
        ("cancelled","/v1/prediction-markets/orders/history",{"status":"cancelled","symbol":GSYM,"limit":100,"from":now-86400000,"to":now+1000})]
    for label,path,params in qs:
        r=await private_rest_request("POST",path,params=params,timeout=10.0); gh.append([label,r.status_code])
        if r.status_code==200:
            for x in r.json().get("orders",[]):
                if str(x.get("clientOrderId") or "")==GID:
                    gm.append({k:x.get(k) for k in ["clientOrderId","status","filledQuantity","avgExecutionPrice","eventOutcome","symbol"]})
    k=KalshiClient(base_url=KALSHI_PROD_REST)
    try:
        r=await k.get("/portfolio/orders",params={"ticker":KTICK,"limit":100}); km=[]
        if r.status_code==200:
            for x in r.json().get("orders",[]):
                if str(x.get("client_order_id") or "")==KID:
                    km.append({q:x.get(q) for q in ["client_order_id","status","fill_count_fp","taker_fill_cost_dollars","taker_fees_dollars","ticker"]})
        print("CRYPTO_ATTEMPT_RECONCILIATION "+json.dumps({"mode":"READ_ONLY_RECONCILIATION","checked_at_utc":datetime.now(timezone.utc).isoformat(),"gemini_http":gh,"gemini_found":bool(gm),"gemini_matches":gm,"kalshi_http_status":r.status_code,"kalshi_found":bool(km),"kalshi_matches":km,"order_entry":False},separators=(",",":")),flush=True)
    finally: await k.close()

if __name__=="__main__": asyncio.run(main())
