"""Audit the raw activity for duplicate snapshots / overlapping pages."""
import gzip, json
from collections import Counter, defaultdict

RAW="data/l5/raw/activity.jsonl"; GAP="data/l5/raw/activity_gap.jsonl.gz"
SER="btc-updown-15m-"

def pages(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path,"rt") as f:
        for ln in f:
            ln=ln.strip()
            if not ln: continue
            try: d=json.loads(ln)
            except ValueError: continue
            yield d

off_count=Counter(); pagecount=0; rows_seen=0
for src in (RAW,GAP):
    for d in pages(src):
        pagecount+=1
        p=d.get("payload") or []
        rows_seen+=len(p)
        off=(d.get("params") or {}).get("offset")
        off_count[(src,off)]+=1

print("pages:",pagecount," rows_seen:",rows_seen)
dupe_pages={k:v for k,v in off_count.items() if v>1}
print("offsets fetched more than once:",len(dupe_pages))
if dupe_pages:
    print("  examples:",list(dupe_pages.items())[:5])

# dedup under several keys, series only
keys={
 "txhash+asset+ts+size+px+type":lambda r:(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),r.get("size"),r.get("price"),r.get("type")),
 "txhash+asset+type":lambda r:(r.get("transactionHash"),r.get("asset"),r.get("type")),
 "txhash only":lambda r:(r.get("transactionHash"),),
 "txhash+asset+ts+size+px+type+side+outcome":lambda r:(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),r.get("size"),r.get("price"),r.get("type"),r.get("side"),r.get("outcome")),
}
seen={k:set() for k in keys}
series_rows=0
tot_rows=0
type_ser=Counter()
for src in (RAW,GAP):
    for d in pages(src):
        for r in (d.get("payload") or []):
            tot_rows+=1
            slug=r.get("eventSlug") or r.get("slug")
            if not isinstance(slug,str) or not slug.startswith(SER): continue
            series_rows+=1
            type_ser[str(r.get("type"))]+=1
            for k,fn in keys.items():
                seen[k].add(fn(r))
print()
print("total payload rows:",tot_rows)
print("btc15m series rows (pre-dedup):",series_rows)
print("btc15m type census (pre-dedup):",dict(type_ser))
print()
for k in keys:
    print("  unique under %-46s : %d" % (k, len(seen[k])))
