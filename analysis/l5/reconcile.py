"""Reproduce the authoritative artefact exactly, then isolate each delta."""
import gzip, json
from decimal import Decimal as D
from collections import Counter

RAW="data/l5/raw/activity.jsonl"; GAP="data/l5/raw/activity_gap.jsonl.gz"
SER="btc-updown-15m"
WALLET="0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
INCENTIVE={"MAKER_REBATE","TAKER_REBATE","REWARD"}
KNOWN={"TRADE","REDEEM"}|INCENTIVE

def rows():
    for path in (RAW,GAP):
        op=gzip.open if path.endswith(".gz") else open
        try:
            with op(path,"rt") as f:
                for ln in f:
                    ln=ln.strip()
                    if not ln: continue
                    try: d=json.loads(ln)
                    except ValueError: continue
                    for r in (d.get("payload") or []): yield r
        except FileNotFoundError: pass

def run(label, rid_fn, use_usdc, slug_field, substring):
    seen=set(); buy=D(0); red=D(0); sell=D(0); nrows=0; dup=0
    for r in rows():
        k=rid_fn(r)
        if k in seen: dup+=1; continue
        seen.add(k)
        t=r.get("type")
        if t not in KNOWN: continue
        if r.get("proxyWallet") not in (None,WALLET): continue
        if t in INCENTIVE: continue
        slug=r.get(slug_field) or ""
        ok = (SER in slug) if substring else slug.startswith(SER+"-")
        if not ok: continue
        nrows+=1
        if use_usdc: amt=D(str(r.get("usdcSize") or 0))
        else:
            amt=D(str(r.get("size") or 0))*D(str(r.get("price") or 0)) if t=="TRADE" else D(str(r.get("size") or 0))
        if t=="TRADE":
            if r.get("side")=="BUY": buy+=amt
            else: sell+=amt
        else: red+=amt
    print("%-52s rows=%7d  buy=%14.2f  redeem=%14.2f  net=%+12.2f"
          % (label,nrows,buy,red,red+sell-buy))
    return nrows,buy,red

THEIRS=lambda r:(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),r.get("size"),r.get("type"),r.get("side"))
MINE  =lambda r:(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),r.get("size"),r.get("price"),r.get("type"))

print("TARGET (artefact): series_rows=686395  cash_deployed_all=6070392.86  redeem=6415918.50  net=+345525.64\n")
run("AUTHORITATIVE: their row_id, usdcSize, slug, substring", THEIRS, True,  "slug", True)
run("their row_id, but size*price",                            THEIRS, False, "slug", True)
run("MY row_id (price in, side out), usdcSize",                MINE,  True,  "slug", True)
run("MY row_id, size*price, eventSlug, startswith",            MINE,  False, "eventSlug", False)
