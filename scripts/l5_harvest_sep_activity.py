import json, sys, time, urllib.request, datetime as dt
ADDR="0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE="https://data-api.polymarket.com"; UA={"User-Agent":"Mozilla/5.0"}
DAYS={"2026-09-23":(1790121600,1790208000),
      "2026-09-24":(1790208000,1790294400),
      "2026-09-25":(1790294400,1790380800)}
def get(url,tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=45) as fh:
                return json.loads(fh.read().decode())
        except Exception:
            if i==tries-1: return None
            time.sleep(1.5*(i+1))
def harvest(kind,s,e):
    seen={}; off=0; miss=0
    while off<=200000:
        b=get("%s/activity?user=%s&limit=500&offset=%d&type=%s&start=%d&end=%d"
              %(BASE,ADDR,off,kind,s,e))
        if b is None:
            miss+=1
            if miss>3: print("  stall",kind,off,file=sys.stderr); break
            off+=500; continue
        if not b: break
        for r in b:
            seen[(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),
                  r.get("size"),r.get("price"),r.get("type"))]=r
        if len(b)<500: break
        off+=500
    return [r for r in seen.values() if s<=(r.get("timestamp") or 0)<e]
out={}
for day,(s,e) in DAYS.items():
    out[day]={}
    for kind in ("TRADE","REDEEM","REWARD","MAKER_REBATE","TAKER_REBATE"):
        rs=harvest(kind,s,e); out[day][kind]=rs
        print("%s %-13s %6d"%(day,kind,len(rs)))
json.dump(out,open("data/l5/raw/sep/l5_sep23_25_daily.json","w"))
print("saved")
