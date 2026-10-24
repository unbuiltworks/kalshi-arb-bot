import csv,collections,statistics as st,json
from decimal import Decimal as D
exec(open("/tmp/claude-0/-home-user-kalshi-arb-bot/e2b83131-1eb4-52c2-b749-df2a8ca69b61/scratchpad/sweep.py").read().split("def report")[0])

# ---- 1. the mechanism: the gates define a BAND on moneyness -------------
print("THE GATES, solved for moneyness o-c  (m = 1-(o-tick), pair = c+m):")
print("  priority-one reachable   c+(1-o) <= 0.96   <=>  o >= c + 0.04   -> keep complement")
print("  pair ceiling             c+1-(o-0.01) <= 1.20  <=>  o >= c - 0.19 -> exit allowed")
print("  so the exit is permitted only inside the BAND  c-0.19 <= o < c+0.04")
print("  i.e. the 1.20 pair ceiling IS a -0.19/unit stop. The arm window is")
print("  what stops it from ever being reached in time.\n")

# ---- 2. paired significance on the 15m held-out set ----------------------
outc,seq=build("15"); T=780
def arm(lo,hi):
    out={}
    for w,rows in seq.items():
        r=walk(rows,outc[w],T,lo,hi)
        if r is not None: out[w]=r[0]
    return out
hold=arm(1e9,1e9); exist=arm(T-60,T-15); cand=arm(180,T)
ks=sorted(set(hold)&set(exist)&set(cand))
print(f"paired comparison on n={len(ks)} 15-min windows")
def pair(a,b,na,nb):
    d=[a[k]-b[k] for k in ks]
    sd=st.pstdev(d) or 1e-9
    t=st.mean(d)/(sd/len(d)**0.5)
    print(f"  {na} - {nb}: mean {st.mean(d):+.4f}  t={t:+.2f}  "
          f"better in {100*sum(1 for x in d if x>0)/len(d):.1f}% of windows")
pair(cand,exist,"ARM(G=180)","EXISTING  ")
pair(cand,hold ,"ARM(G=180)","HOLD      ")
pair(exist,hold,"EXISTING  ","HOLD      ")

# ---- 3. the two real live windows, real bid/ask -------------------------
SP="/tmp/claude-0/-home-user-kalshi-arb-bot/e2b83131-1eb4-52c2-b749-df2a8ca69b61/scratchpad"
print("\nTHE TWO LIVE WINDOWS under ARM(G=180), using the authenticated book")
def live(nm,held,q,c,fill_t,up_won,actual):
    tape=json.load(open(f"{SP}/tape_{nm}.json"))
    q=D(q); c=D(c); fired=None
    for r in tape:
        if r["t"]<fill_t or r["t"]>780: continue
        bid=D(r["bid"]); ask=D(r["ask"])
        o_bid = bid if held=="UP" else D(1)-ask      # what we can SELL our side at
        o_ask = ask if held=="UP" else D(1)-bid
        m = D(1)-o_bid                               # complement own price, crossing
        if c+(D(1)-o_ask)<=MAKER_CEIL: continue      # priority one reachable
        pair_=c+m
        if pair_>PAIR_CEIL: continue
        if r["t"]<180: continue
        fired=(r["t"],o_bid,pair_,float((o_bid-c-tfee(m))*q)); break
    won = (up_won if held=="UP" else not up_won)
    hold_r=float(((D(1) if won else D(0))-c)*q)
    if fired:
        print(f"  {nm}: EXIT at T+{fired[0]:.0f}  sell our side at {fired[1]}  "
              f"pair {fired[2]}  realized {fired[3]:+.4f}")
    else:
        print(f"  {nm}: no exit permitted; held -> {hold_r:+.4f}")
    print(f"        actual campaign result {actual:+.4f}   hold-to-settlement {hold_r:+.4f}")
    return fired[3] if fired else hold_r
a=live("0230z","DOWN","1.17","0.07",288.0,True ,-0.0468)
b=live("0300z","UP"  ,"1.79","0.20",331.0,False,-0.3580)
print(f"\n  campaign under ARM(G=180): {a+b:+.4f}   actual: {-0.0468-0.3580:+.4f}   "
      f"delta {a+b-(-0.4048):+.4f}")
