import json, sys, itertools
from decimal import Decimal as D
sys.path.insert(0,'.')
from replay import Cfg, run_window, summarise

ws=json.load(open('windows.json')); ws.sort(key=lambda w:w["close"])
TRAIN=[w for w in ws if w["series"]!="KXBTC15M"]      # ETH / SOL / XRP / DOGE
TEST =[w for w in ws if w["series"]=="KXBTC15M"]      # the market we actually trade
POL=[("A","maker-open  cheapest-first"),("B","TAKER-open  cheapest-first"),
     ("D","maker-open  L5 band"),      ("C","TAKER-open  L5 band")]
GRID=[dict(entry_pair_cap=D(e), lam=D(l), complete_pair_cap=D(c))
      for e,l,c in itertools.product(("0.94","0.96","0.98"),("0.5","1.0","2.0"),
                                     ("0.96","0.98"))]
print("TRAIN (non-BTC) %d windows   TEST (BTC15m) %d windows   grid %d"
      % (len(TRAIN),len(TEST),len(GRID)))
HDR=("pol","design","trd","clean","strnd","matched","net","per_win","roc%","maxDD","advR","fees")
FMT="%-3s %-27s %4s %5s %5s %8s %9s %9s %8s %7s %6s %7s"
ROW="%-3s %-27s %4d %5d %5d %8.2f %+9.4f %+9.4f %+8.2f %7.3f %6.3f %7.3f"
best={}
for pol,_ in POL:
    sc=sorted(((summarise([run_window(w,pol,Cfg(**g)) for w in TRAIN],pol)["net"],g)
               for g in GRID), key=lambda x:-x[0])
    best[pol]=sc[0][1]
    print("  train-optimal %s: entry<=%s lam=%s complete<=%s  train net %+.4f"
          % (pol,sc[0][1]["entry_pair_cap"],sc[0][1]["lam"],
             sc[0][1]["complete_pair_cap"],sc[0][0]))

def block(title,cfgf):
    print("\n"+"="*108); print(title); print("="*108)
    print(FMT % HDR); out={}
    for pol,desc in POL:
        s=summarise([run_window(w,pol,cfgf(pol)) for w in TEST],pol); out[pol]=s
        print(ROW % (pol,desc,s["traded"],s["clean"],s["stranded_windows"],s["matched"],
                     s["net"],s["per_window"],s["roc"],s["max_dd"],s["adv_rate"],s["fees"]))
    return out

r1=block("TEST = BTC15m, held out. Each policy at its OWN train-selected config.",
         lambda p: Cfg(**best[p]))
r2=block("TEST = BTC15m. SHARED config (A's train-optimal) -- no tuning advantage.",
         lambda p: Cfg(**best["A"]))
for nm,r in (("own-config",r1),("shared-config",r2)):
    print("\n2x2 DECOMPOSITION on held-out BTC15m (%s), net P&L:" % nm)
    print("                      cheapest-first     L5 band       SELECTION effect")
    print("  maker-open          %+9.4f       %+9.4f     %+9.4f"
          % (r["A"]["net"],r["D"]["net"],r["D"]["net"]-r["A"]["net"]))
    print("  TAKER-open          %+9.4f       %+9.4f     %+9.4f"
          % (r["B"]["net"],r["C"]["net"],r["C"]["net"]-r["B"]["net"]))
    print("  EXECUTION effect    %+9.4f       %+9.4f"
          % (r["B"]["net"]-r["A"]["net"],r["C"]["net"]-r["D"]["net"]))
json.dump({k:{kk:str(vv) for kk,vv in v.items() if kk!="label"} for k,v in r2.items()},
          open('btc_test_shared.json','w'))
json.dump({k:{kk:str(vv) for kk,vv in v.items()} for k,v in best.items()},
          open('train_configs.json','w'))
