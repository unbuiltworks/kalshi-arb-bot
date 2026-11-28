import json,urllib.request,time,sys,os
out=json.load(open('rebuild.json'))
slugs=sorted(out)
os.makedirs('api',exist_ok=True)
for s in slugs:
    fn='api/'+s+'.json'
    if os.path.exists(fn): continue
    for a in range(4):
        try:
            r=urllib.request.Request('https://gateway.polymarket.us/v1/market/slug/'+s,headers={'User-Agent':'research-client','Accept':'application/json'})
            b=urllib.request.urlopen(r,timeout=30).read(); open(fn,'wb').write(b); break
        except Exception as e:
            print(s,a,repr(e)[:200]); time.sleep(2+2*a)
    time.sleep(0.6)
print(len(os.listdir('api')))
