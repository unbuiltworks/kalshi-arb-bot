"""SUPERSEDED AND KNOWN WRONG -- see
analysis/l5/CRITICAL_REVIEW_RECONCILIATION_20261024.md

Two defects, both material:
  1. the dedup key included `price`, so the 18 repeated fetch passes
     over the early offsets survived as 16,170 duplicate rows and
     +$327,737 of phantom spend;
  2. the action classifier tested `held_other > 0`, which calls
     buying the already-short side a complement.

Use analysis/l5/l5_authoritative_reconstruction.py instead: it
reproduces the pinned artefact to the cent and classifies on
|imbalance|.
"""

"""Split L5's realized BTC15m P&L into MATCHED and RESIDUAL components.

WHY IT MATTERS. If L5's residual carrying is a net cost that his matched
volume outruns, then our stranded legs are the same phenomenon and the fix is
to strand less. If his residual is itself a profit centre, the two strategies
differ in kind and copying his completion behaviour alone will not transfer.

CONVENTION, stated because the split is not unique: a matched pair redeems
exactly $1 whatever the outcome, so matched_pnl = matched * (1 - avg_up -
avg_dn) using each side's own average fill price. The residual is then priced
at the average price of the side actually left long, and redeems $1 per
contract only if that side won. Outcome is read from the REDEEM rows, not
guessed: redeem_total - matched tells us what the residual paid.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from decimal import Decimal as D

RAW = "data/l5/raw/activity.jsonl"
GAP = "data/l5/raw/activity_gap.jsonl.gz"
SERIES = "btc-updown-15m-"


def _rows(path):
    op = gzip.open if path.endswith(".gz") else open
    try:
        with op(path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for r in (d.get("payload") or ()):
                    yield r
    except FileNotFoundError:
        return


def main():
    up_q = defaultdict(lambda: D(0)); up_c = defaultdict(lambda: D(0))
    dn_q = defaultdict(lambda: D(0)); dn_c = defaultdict(lambda: D(0))
    redeem = defaultdict(lambda: D(0))
    seen = set()
    for r in list(_rows(RAW)) + list(_rows(GAP)):
        slug = r.get("eventSlug") or r.get("slug")
        if not isinstance(slug, str) or not slug.startswith(SERIES):
            continue
        key = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
               r.get("size"), r.get("price"), r.get("type"))
        if key in seen:
            continue
        seen.add(key)
        typ = str(r.get("type") or "")
        try:
            size = D(str(r.get("size")))
        except (TypeError, ArithmeticError, ValueError):
            continue
        if typ == "REDEEM":
            redeem[slug] += size
            continue
        if typ != "TRADE" or str(r.get("side") or "").upper() != "BUY":
            continue
        try:
            px = D(str(r.get("price")))
        except (TypeError, ArithmeticError, ValueError):
            continue
        if str(r.get("outcome") or "").lower().startswith("up"):
            up_q[slug] += size; up_c[slug] += size * px
        else:
            dn_q[slug] += size; dn_c[slug] += size * px

    slugs = set(up_q) | set(dn_q)
    tot_m_pnl = D(0); tot_r_pnl = D(0); tot_spend = D(0); tot_redeem = D(0)
    m_win = m_los = r_win = r_los = 0
    n_resolved = 0
    pair_costs = []
    resid_px = []
    for s in slugs:
        uq, dq = up_q[s], dn_q[s]
        uc, dc = up_c[s], dn_c[s]
        spend = uc + dc
        matched = min(uq, dq)
        resid = uq - dq
        avg_up = (uc / uq) if uq > 0 else D(0)
        avg_dn = (dc / dq) if dq > 0 else D(0)
        m_pnl = matched * (D(1) - avg_up - avg_dn)
        if matched > 0:
            pair_costs.append(float(avg_up + avg_dn))
        side_px = avg_up if resid > 0 else avg_dn
        r_cost = abs(resid) * side_px
        if abs(resid) > 0:
            resid_px.append(float(side_px))
        rd = redeem.get(s)
        if rd is None:
            continue
        n_resolved += 1
        # the residual's own redemption is whatever redeem left over matched
        r_redeem = rd - matched
        if r_redeem < 0:
            r_redeem = D(0)
        r_pnl = r_redeem - r_cost
        tot_m_pnl += m_pnl; tot_r_pnl += r_pnl
        tot_spend += spend; tot_redeem += rd
        if m_pnl > 0: m_win += 1
        elif m_pnl < 0: m_los += 1
        if r_pnl > 0: r_win += 1
        elif r_pnl < 0: r_los += 1

    print("=== L5 BTC15m P&L DECOMPOSITION (%d resolved windows) ===" % n_resolved)
    print("  total spend            : %12.2f" % tot_spend)
    print("  total redeem           : %12.2f" % tot_redeem)
    print("  realized net (r - s)   : %12.2f" % (tot_redeem - tot_spend))
    print()
    print("  MATCHED  component     : %12.2f   (windows +%d / -%d)"
          % (tot_m_pnl, m_win, m_los))
    print("  RESIDUAL component     : %12.2f   (windows +%d / -%d)"
          % (tot_r_pnl, r_win, r_los))
    print("  sum of components      : %12.2f" % (tot_m_pnl + tot_r_pnl))
    print()
    if pair_costs:
        pc = sorted(pair_costs)
        print("  pair cost (avg_up+avg_dn): median %.4f  p05 %.4f  p95 %.4f"
              % (pc[len(pc)//2], pc[int(len(pc)*0.05)], pc[int(len(pc)*0.95)]))
        print("  pair cost <= 0.96        : %.1f%% of windows"
              % (100.0*sum(1 for x in pc if x <= 0.96)/len(pc)))
        print("  pair cost <= 1.00        : %.1f%% of windows"
              % (100.0*sum(1 for x in pc if x <= 1.00)/len(pc)))
    if resid_px:
        rp = sorted(resid_px)
        print("  residual avg price       : median %.4f  p05 %.4f  p95 %.4f"
              % (rp[len(rp)//2], rp[int(len(rp)*0.05)], rp[int(len(rp)*0.95)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def oos_split():
    """Chronological out-of-sample check: are ~0.97 pair economics stable?"""
    import gzip, json
    from collections import defaultdict
    up_q=defaultdict(lambda:D(0)); up_c=defaultdict(lambda:D(0))
    dn_q=defaultdict(lambda:D(0)); dn_c=defaultdict(lambda:D(0))
    redeem=defaultdict(lambda:D(0)); tmin={}
    seen=set()
    for r in list(_rows(RAW))+list(_rows(GAP)):
        slug=r.get("eventSlug") or r.get("slug")
        if not isinstance(slug,str) or not slug.startswith(SERIES): continue
        key=(r.get("transactionHash"),r.get("asset"),r.get("timestamp"),
             r.get("size"),r.get("price"),r.get("type"))
        if key in seen: continue
        seen.add(key)
        typ=str(r.get("type") or "")
        try: size=D(str(r.get("size")))
        except Exception: continue
        try: ws=int(slug[len(SERIES):])
        except ValueError: continue
        tmin[slug]=ws
        if typ=="REDEEM": redeem[slug]+=size; continue
        if typ!="TRADE" or str(r.get("side") or "").upper()!="BUY": continue
        try: px=D(str(r.get("price")))
        except Exception: continue
        if str(r.get("outcome") or "").lower().startswith("up"):
            up_q[slug]+=size; up_c[slug]+=size*px
        else:
            dn_q[slug]+=size; dn_c[slug]+=size*px
    slugs=sorted(set(up_q)|set(dn_q), key=lambda s: tmin.get(s,0))
    cut=int(len(slugs)*0.7)
    for name, part in (("IN-SAMPLE  (first 70%)", slugs[:cut]),
                       ("OUT-OF-SAMPLE (last 30%)", slugs[cut:])):
        m_pnl=D(0); r_pnl=D(0); mq=D(0); rq=D(0); pcs=[]; n=0; wins=0
        for s in part:
            uq,dq,uc,dc=up_q[s],dn_q[s],up_c[s],dn_c[s]
            matched=min(uq,dq); resid=uq-dq
            au=(uc/uq) if uq>0 else D(0); ad=(dc/dq) if dq>0 else D(0)
            if matched>0: pcs.append(float(au+ad))
            mp=matched*(D(1)-au-ad)
            side=au if resid>0 else ad
            rc=abs(resid)*side
            rd=redeem.get(s)
            if rd is None: continue
            n+=1
            rr=max(D(0), rd-matched)
            m_pnl+=mp; r_pnl+=rr-rc; mq+=matched; rq+=abs(resid)
            if (rd-(uc+dc))>0: wins+=1
        pcs.sort()
        print("  %s  n=%d" % (name,n))
        print("     matched %+12.2f   residual %+12.2f   net %+12.2f  (%.3f/window)"
              % (m_pnl, r_pnl, m_pnl+r_pnl, float((m_pnl+r_pnl)/n) if n else 0))
        print("     median pair cost %.4f   residual/matched %.4f   window win rate %.1f%%"
              % (pcs[len(pcs)//2] if pcs else 0, float(rq/mq) if mq else 0,
                 100.0*wins/n if n else 0))


if __name__ == "__main__" and "--oos" in __import__("sys").argv:
    print("=== CHRONOLOGICAL OUT-OF-SAMPLE SPLIT ===")
    oos_split()
