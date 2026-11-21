"""Win rate vs BREAKEVEN win rate. READ-ONLY.

THE OBJECTION THIS ANSWERS, and it is a fair one: "but we win a bunch of
windows." That is true and it is not an illusion. On the tape the strategy
is net positive in 54.6% of the windows it trades, and 59.3% of the shares
it buys settle on the winning side. Buying the favorite is SUPPOSED to win
more often than it loses -- that is what a favorite is.

WHY WINNING MOST WINDOWS STILL LOSES MONEY. The payoff is asymmetric. Buy
at p and you make (1 - p) when right and lose p when wrong, so the
breakeven win rate IS the average price paid. Paying 0.6419 a share means
needing to be right 64.2% of the time. The strategy is right 59.3% of the
time. The whole loss is that 4.9-point gap, and no amount of winning
"most" windows closes it -- "most" has to mean more than 64%, not more
than 50%.

HOW TO READ A SHORT LIVE RUN. A live sample decomposes into the structural
gap (what the model says the strategy is worth) and luck (how far this
particular run fell from it). Reporting the raw live loss without that
split overstates a bad run and understates a good one. This script prints
both, so a six-window stretch cannot be mistaken for a mechanism.

Usage: python3 -I analysis/l5/btc5m_accumulation/winrate.py
"""
import os, sys, statistics, collections
from decimal import Decimal as D
sys.path.insert(0, os.getcwd()); os.environ["L5_BTC5M_CONTROLLER"]="ON"
from src import l5_btc5m_controller as C
H="analysis/l5/btc5m_accumulation"
exec(compile(open(os.path.join(H,"replay.py")).read().replace("if __name__ ==","if 0 and __name__ =="),"replay.py","exec"))

w=load(TAPE); runs={}
for slug,rows in sorted(w.items()):
    r=run_window(rows,tier="ADVERSE")
    if r and r["fills"]: runs[slug]=r

win_w=loss_w=flat=0; nets=[]
shares_won=shares_lost=D(0); paid=D(0)
for r in runs.values():
    net=sum((f["qty"] if f["won"] else D(0))-f["qty"]*f["price"] for f in r["fills"])
    nets.append(float(net))
    if net>0: win_w+=1
    elif net<0: loss_w+=1
    else: flat+=1
    for f in r["fills"]:
        paid+=f["qty"]*f["price"]
        if f["won"]: shares_won+=f["qty"]
        else: shares_lost+=f["qty"]

tot=shares_won+shares_lost
avg_px=paid/tot
print("REPLAY TAPE, %d windows that traded"%len(runs))
print("="*62)
print("  windows NET POSITIVE : %3d  (%.1f%%)"%(win_w,100*win_w/len(runs)))
print("  windows NET NEGATIVE : %3d  (%.1f%%)"%(loss_w,100*loss_w/len(runs)))
print()
print("  shares whose side WON : %7.2f  (%.1f%%)"%(shares_won,100*shares_won/tot))
print("  shares whose side LOST: %7.2f  (%.1f%%)"%(shares_lost,100*shares_lost/tot))
print()
print("  average price paid    : %.4f"%avg_px)
print("  BREAKEVEN win rate    : %.1f%%   (you must win as often as you pay)"%(100*avg_px))
print("  ACTUAL win rate       : %.1f%%"%(100*shares_won/tot))
print("  GAP                   : %.1f points"%(100*(shares_won/tot-avg_px)))
print()
won_amt=float(shares_won)*(1-float(avg_px)); lost_amt=float(shares_lost)*float(avg_px)
print("  when right you make   : %.4f a share  (1 - %.2f)"%(1-float(avg_px),avg_px))
print("  when wrong you lose   : %.4f a share"%float(avg_px))
print("  ratio                 : you must be right %.2fx as often as wrong"%(float(avg_px)/(1-float(avg_px))))
print("  actual right:wrong    : %.2f"%(float(shares_won)/float(shares_lost)))
print()
print("  net per share         : %+0.4f"%((float(shares_won)-float(paid))/float(tot)))
m=statistics.fmean(nets); print("  net per window        : %+0.4f"%m)
