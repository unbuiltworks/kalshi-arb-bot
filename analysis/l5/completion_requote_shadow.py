"""READ-ONLY demonstration of L5_COMPLETION_REQUOTE.

Drives the REAL `l5_two_sided_probe.completion_requote` -- the deployed
function, not a reimplementation -- over VENUE-CONFIRMED inventory against
REAL books. Submits nothing, cancels nothing, touches no venue.

REAL:          the four 2026-11-02 fills (qty, own cost, venue order id) and
               the harvested books of their windows.
RECONSTRUCTED: the resting complement price the engine would have had, taken
               from the engine's own first eligible quote in that window.
"""
import json,os,sys,glob,collections,datetime as dt
from decimal import Decimal as D
sys.path.insert(0,"/home/user/kalshi-arb-bot")
from src import l5_two_sided_probe as TS
S=sys.argv[1]
WINDOW=900.0; QTY=D(2)
# slug, held_side, held_qty, held_own_cost_usd, fill_utc, venue_order_id
FILLS=[("cpc-btc-updown-15m-2026-11-02-2215z","DOWN","1.92","0.7488","22:17:09","DFWP527012WE"),
       ("cpc-btc-updown-15m-2026-11-02-2230z","UP",  "2.04","0.9588","22:32:10","l5f-9b2fb36a"),
       ("cpc-btc-updown-15m-2026-11-02-2245z","DOWN","2.00","0.9000","22:45:35","l5f-98ed5dc6"),
       ("cpc-btc-updown-15m-2026-11-02-2300z","UP",  "1.68","0.3528","23:00:21","l5f-71bcdba3")]
def touch(s,b,a): return b if s=="UP" else D(1)-a
def wstart(slug):
    d=slug.split("-15m-")[1]
    return dt.datetime.fromisoformat("%s-%s-%sT%s:%s:00+00:00"%(d[0:4],d[5:7],d[8:10],d[11:13],d[13:15])).timestamp()
reasons=collections.Counter(); acts=[]; ceil_viol=[]; stale=0; total=0
print("%-6s %-6s %-9s %-9s %-9s %-10s %-9s %s"%("win","t+s","bid","ask","touch","ceiling","target","verdict"))
for slug,hs,hq,hc,futc,vid in FILLS:
    comp = "UP" if hs=="DOWN" else "DOWN"
    w0=wstart(slug); ft=dt.datetime.fromisoformat(slug.split("-15m-")[1][:10]+"T"+futc+"+00:00").timestamp()
    rows=[json.loads(l) for l in open(os.path.join(S,"tape",slug+".jsonl"))]
    bb=[r for r in rows if r.get("kind")=="bbo" and r.get("two_sided")
        and dt.datetime.fromisoformat(r["t"]).timestamp()>ft]
    resting=None; shown=0
    for r in bb:
        t=dt.datetime.fromisoformat(r["t"]).timestamp(); el=t-w0
        b,a=D(r["bid"]),D(r["ask"])
        tp=touch(comp,b,a)
        if resting is None:                 # first eligible quote = the resting one
            if D(hc)/D(hq)+tp<=TS.max_pair_cost(): resting=tp
            continue
        total+=1
        out=TS.completion_requote(
            side=comp, resting_own_px=str(resting), resting_qty=hq,
            resting_filled_qty="0", bid=str(b), ask=str(a), tick="0.01",
            min_qty="0.01", held_qty=hq, held_cost_usd=hc,
            deficient_qty="0", deficient_cost_usd="0",
            residual_budget_usd="1.50",
            now_s=t, window_start_s=w0, window_end_s=w0+WINDOW,
            book_age_s=0.1, gap_suspected=False,
            orders_uncertain=None, cancel_unresolved=None)
        rs = out.get("reason") or "ACT"
        reasons[rs]+=1
        if out.get("act"):
            tgt=D(str(out.get("target_own_px") or out.get("touch")))
            # THE SUBMISSION BOUNDARY, applied as the executor applies it.
            # eff is the whole book's cost per matched share after this order.
            eff=(D(hc)+tgt*D(hq))/D(hq)
            survives = eff<=D(1)            # completion_pair_rescue's hard line
            acts.append((slug[-5:],el,str(tgt),str(eff),survives))
            if not survives: ceil_viol.append((slug[-5:],str(eff)))
        if shown<3 or out.get("act"):
            print("%-6s %-6.0f %-9s %-9s %-9s %-10s %-9s %s"%(
                slug[-5:],el,b,a,tp,out.get("completion_ceiling"),
                out.get("target_own_px") or "-", rs))
            shown+=1
print()
print("decisions evaluated      : %d"%total)
print("ACT (would reprice)      : %d"%len(acts))
surv=[a for a in acts if a[4]]
print("would survive submission : %d  (eff <= 1.00, completion_pair_rescue)"%len(surv))
print("refused at submission    : %d  (eff > 1.00 = certain loss)"%len(ceil_viol))
print("NOTE: 0.96 is the OPENING cap. COMPLETION_CEILINGS_IN_FORCE deliberately")
print("      excludes pair_cap; completions answer to improves/within_budget and")
print("      the hard eff<=1.00 line at the submission boundary.")
print("\nrefusal reasons:")
for k,v in reasons.most_common(): print("   %-34s %5d"%(k,v))
if acts:
    print("\nthe reprices it would have made:")
    for w,el,tgt,eff,ok in acts[:12]:
        print("   %s t+%4.0fs target own %-7s eff %-7s %s"%(w,el,tgt,eff,"SURVIVES" if ok else "REFUSED eff>1.00"))
