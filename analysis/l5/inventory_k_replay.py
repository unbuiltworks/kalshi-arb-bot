"""Bounded inventory controller replay, parameterised on the residual cap k.

WHY THIS FILE EXISTS. `grok_inventory_policy_replay.py` is a SINGLE-POSITION
state machine: FLAT -> RESTING -> HELD -> MATCHED, then `break`. It cannot
represent k>1 at all, so it cannot answer "does raising k pay".  This file
keeps that file's loader, its fee/rebate schedule and -- critically -- its
CONSERVATIVE FILL PROXY, and replaces only the position model with a real
two-sided inventory.

FILL PROXY (unchanged, and it is a proxy, never a fill):
    a resting buy on side s at price p fills when cross(s,bid,ask) <= p,
    i.e. when the book has TRADED THROUGH our level.  For UP, cross is `ask`
    and touch is `bid`, so a quote placed at touch-depth can never fill on
    the tick it was placed; it fills only after the book moves down onto it.
    There is no queue model and no partial fill.  Nothing here is a fill, a
    realized profit, or a counterfactual saving.  Everything is MODELLED.

SETTLEMENT uses the convention verified against the venue's own
L5_WINDOW_ACCOUNTING for cpc-btc-updown-15m-2026-11-05-0245z: AVERAGE COST
BASIS PER SIDE.  matched_pnl = matched_qty * (1 - avg_up - avg_dn);
residual_pnl = residual_qty * ((1 if residual side won else 0) - avg_cost).
Those two lines reproduced that window's -$0.2900 exactly.

REBATES.  fee(theta,C,p) = round2(theta*C*p*(1-p)).  At C=1 the maker term is
0.0125*0.25 = $0.003 and rounds to ZERO at every price, so clip 1 earns no
rebate and pays no maker fee.  That is not an assumption: Campaign E's venue
record reported actual_fees_usd $0 across 6 maker orders.  Rebates are
therefore reported as a SEPARATE column and are never folded into net.
"""
import json, glob, os, sys, datetime as dt, statistics
from decimal import Decimal as D, ROUND_HALF_EVEN

S = sys.argv[1]
TICK = D("0.01")
ENTRY_CUT   = D(480)   # ts_entry_cutoff_s   -- legs that INCREASE |residual|
COMPLETE_CUT= D(600)   # ts_complete_cutoff_s -- legs that REDUCE |residual|
TAKER = D("0.0695"); MREB = D("0.0125")

def cents(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
def fee(th, C, p): return cents(th * D(C) * p * (D(1) - p))
def touch(s, b, a): return b if s == "UP" else D(1) - a
def cross(s, b, a): return a if s == "UP" else D(1) - b
def other(s): return "DOWN" if s == "UP" else "UP"

def wstart(slug):
    d = slug.split("-15m-")[1]
    return dt.datetime.fromisoformat("%s-%s-%sT%s:%s:00+00:00" % (
        d[0:4], d[5:7], d[8:10], d[11:13], d[13:15])).timestamp()

def load():
    out = []
    for p in sorted(glob.glob(os.path.join(S, "tape", "*.jsonl"))):
        slug = os.path.basename(p)[:-6]
        bb = [json.loads(l) for l in open(p)]
        bb = [r for r in bb if r.get("kind") == "bbo" and r.get("two_sided")]
        if len(bb) < 10: continue
        last = bb[-1]; bid = D(last["bid"]); ask = D(last["ask"])
        win = "UP" if bid >= D("0.60") else ("DOWN" if ask <= D("0.40") else None)
        if win is None: continue
        w0 = wstart(slug)
        t = [(D(int(dt.datetime.fromisoformat(r["t"]).timestamp() - w0)),
              D(r["bid"]), D(r["ask"])) for r in bb]
        mids = [float((b + a) / 2) for _, b, a in t]
        out.append(dict(slug=slug, win=win, ticks=t,
                        vol=statistics.pstdev(mids) if len(mids) > 2 else 0.0))
    return out


def sim(w, *, k, clip, depth, band_lo, band_hi, comp_cap, requote,
        max_subs, max_replace, allow_taker):
    """One window under a bounded inventory controller.

    k           max |up_qty - dn_qty| in contracts
    clip        contracts per fill
    requote     "stable" = place once per side and leave it; "chase" = follow
                the touch every time it moves (models the current engine's
                cancel/replace behaviour, and its churn)
    allow_taker completion may cross the spread.  NOT live-authorized; this
                exists only to price what the authorization is costing us.
    """
    ticks = w["ticks"]; win = w["win"]
    qty = {"UP": D(0), "DOWN": D(0)}          # filled contracts
    cost = {"UP": D(0), "DOWN": D(0)}         # cumulative cash paid
    rest = {"UP": None, "DOWN": None}         # resting quote price or None
    fees = D(0); rebate = D(0)
    subs = 0; repl = 0; fills = 0
    ref = {"RESIDUAL_ABOVE_CAP": 0, "NO_BAND": 0, "PAIR_ABOVE_CEILING": 0,
           "PAST_ENTRY_CUT": 0, "PAST_COMPLETE_CUT": 0, "SUB_BUDGET": 0}
    peak = D(0)

    def resid(): return qty["UP"] - qty["DOWN"]
    def avg(s):  return (cost[s] / qty[s]) if qty[s] > 0 else None

    for el, bid, ask in ticks:
        # ---- 1. fills on anything already resting -------------------------
        for s in ("UP", "DOWN"):
            p = rest[s]
            if p is None: continue
            if cross(s, bid, ask) <= p:
                qty[s] += D(clip); cost[s] += p * D(clip); fills += 1
                f = fee(MREB, clip, p)          # maker: a credit
                fees -= f; rebate += f
                rest[s] = None
                peak = max(peak, cost["UP"] + cost["DOWN"])

        # ---- 2. optional taker completion of a residual -------------------
        if allow_taker and el <= COMPLETE_CUT and resid() != 0:
            short = other("UP" if resid() > 0 else "DOWN")
            a_held = avg(other(short))
            if a_held is not None:
                xp = cross(short, bid, ask)
                if a_held + xp <= comp_cap:
                    f = fee(TAKER, clip, xp)
                    qty[short] += D(clip); cost[short] += xp * D(clip)
                    fees += f; fills += 1
                    peak = max(peak, cost["UP"] + cost["DOWN"])

        # ---- 3. (re)quote each side, inventory-aware ----------------------
        for s in ("UP", "DOWN"):
            want = touch(s, bid, ask) - depth * TICK
            reduces = (resid() > 0 and s == "DOWN") or (resid() < 0 and s == "UP")

            # would this fill breach the cap?
            after = resid() + (D(clip) if s == "UP" else -D(clip))
            if abs(after) > D(k):
                if rest[s] is not None: rest[s] = None   # pull it
                ref["RESIDUAL_ABOVE_CAP"] += 1; continue

            # clock: a reducing leg lives to COMPLETE_CUT, an increasing one
            # only to ENTRY_CUT.
            if reduces:
                if el > COMPLETE_CUT: ref["PAST_COMPLETE_CUT"] += 1; continue
            else:
                if el > ENTRY_CUT: ref["PAST_ENTRY_CUT"] += 1; continue

            # a reducing leg must respect the pair ceiling against the side
            # we are actually long.
            if reduces:
                a_held = avg(other(s))
                if a_held is not None and a_held + want > comp_cap:
                    ref["PAIR_ABOVE_CEILING"] += 1; continue
            else:
                if not (band_lo <= want <= band_hi):
                    ref["NO_BAND"] += 1; continue

            if rest[s] == want: continue                 # already there
            if rest[s] is not None:
                if requote == "stable": continue         # leave it alone
                if repl >= max_replace: continue
                repl += 1
            if subs >= max_subs: ref["SUB_BUDGET"] += 1; continue
            subs += 1; rest[s] = want

    # ---- settlement, average cost basis per side --------------------------
    matched = min(qty["UP"], qty["DOWN"])
    a_up = avg("UP") or D(0); a_dn = avg("DOWN") or D(0)
    m_pnl = matched * (D(1) - a_up - a_dn)
    r_qty = abs(resid()); r_side = "UP" if resid() > 0 else "DOWN"
    r_pnl = D(0)
    if r_qty > 0:
        a = avg(r_side)
        r_pnl = r_qty * ((D(1) if r_side == win else D(0)) - a)
    pair_cost = (a_up + a_dn) if matched > 0 else None
    return dict(slug=w["slug"], matched=matched, m_pnl=m_pnl,
                r_qty=r_qty, r_side=(r_side if r_qty else None), r_pnl=r_pnl,
                net=m_pnl + r_pnl, fees=fees, rebate=rebate,
                subs=subs, repl=repl, fills=fills, peak=peak,
                pair_cost=pair_cost, ref=ref,
                contracts=qty["UP"] + qty["DOWN"])
