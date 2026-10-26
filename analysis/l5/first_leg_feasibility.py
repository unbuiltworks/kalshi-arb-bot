#!/usr/bin/env python3
"""At the instant a first leg fills, was the pair still reachable?

No lookahead: for each venue-confirmed first-leg fill, take the FIRST book
observation at or after the fill and ask what the complement would cost
then. A pair needs complement_own <= CEILING - first_leg_own. If that is
already false at the fill, the pair was lost before any completion attempt
-- the leg filled because the market moved against it, which is what a
resting maker order is selected for.
"""
import json, glob, re, sys, datetime as dt
from decimal import Decimal as D
sys.path.insert(0, "src")

CEILING = D("0.96")

def own_buy(side, bid, ask, mode):
    bid, ask = D(str(bid)), D(str(ask))
    if side == "UP":
        return bid if mode == "MAKER" else ask
    return (D(1) - ask) if mode == "MAKER" else (D(1) - bid)

def t_of(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))

def books_of(tape):
    out = []
    for ln in open(tape):
        d = json.loads(ln)
        try:
            b = json.loads(d["body"], strict=False)
        except Exception:
            continue
        if d.get("label") == "L5_LIVE_DECISION":
            bk = b.get("book") or {}
            if bk.get("bid") and bk.get("ask"):
                out.append((d["ts"], D(str(bk["bid"])), D(str(bk["ask"]))))
        elif d.get("label") == "L5_RESIDUAL_BOOK_SAMPLE":
            row = ((b.get("book") or {}).get("row") or {})
            if row.get("bids") and row.get("asks"):
                out.append((d["ts"], D(str(row["bids"][0][0])),
                            D(str(row["asks"][0][0]))))
    return sorted(set(out))

def side_map(tape):
    """coid -> side. The Oct 24 lifecycle rows carry side_tracked: None, so
    the side comes from the decision that submitted that client_order_id."""
    m = {}
    for ln in open(tape):
        d = json.loads(ln)
        if d.get("label") != "L5_LIVE_DECISION":
            continue
        b = json.loads(d["body"], strict=False)
        o = b.get("order") or {}
        coid = o.get("client_order_id") or (b.get("intent") or {}).get(
            "client_order_id")
        if not coid:
            continue
        sd = ((b.get("intent") or {}).get("side")
              or (b.get("two_sided") or {}).get("leg_to_place")
              or b.get("side"))
        if sd in ("UP", "DOWN") and o.get("submitted") is True:
            m[coid] = sd
    return m


def fills_of(tape):
    sm = side_map(tape)
    out = []
    for ln in open(tape):
        d = json.loads(ln)
        if d.get("label") != "L5_LIVE_LIFECYCLE":
            continue
        b = json.loads(d["body"], strict=False)
        if b.get("terminal") and b.get("state") == "ORDER_STATE_FILLED":
            sd = (b.get("side_tracked") or b.get("side")
                  or sm.get(b.get("coid")))
            out.append((d["ts"], b.get("venue_order_id"),
                        D(str(b.get("avg_px"))), D(str(b.get("cum_qty"))),
                        sd))
    return sorted(set(out))

print(f"{'win':10} {'order':13} {'side':5} {'own_f':>6} {'qty':>5} "
      f"{'need<=':>7} {'comp_mk':>8} {'comp_tk':>8} {'pair_mk':>8} {'feasible':>9} {'outcome':8}")
print("-" * 104)
rows = []
for tape in sorted(glob.glob(
        "analysis/l5/residual_exit/preserved/btc15m_*.ndjson")):
    m = re.search(r'_(\d{8})_(\d{4})z\.ndjson$', tape)
    if not m:
        continue
    day, win = m.group(1), m.group(2)
    bks, fls = books_of(tape), fills_of(tape)
    if not fls:
        continue
    # pair up fills in time order: a fill whose opposite side already has
    # unmatched qty is a COMPLETION; otherwise it opened exposure.
    held = {"UP": D(0), "DOWN": D(0)}
    for ts, oid, px, qty, sd in fls:
        if sd not in ("UP", "DOWN"):
            continue
        f_own = px if sd == "UP" else (D(1) - px)
        opp = "UP" if sd == "DOWN" else "DOWN"
        completing = held[opp] > 0
        if completing:
            held[opp] = max(D(0), held[opp] - qty)
            continue
        held[sd] += qty
        t = t_of(ts)
        nxt = next((b for b in bks if t_of(b[0]) >= t), None)
        need = CEILING - f_own
        if nxt is None:
            print(f"{day[4:]}/{win}z {oid:13} {sd:5} {f_own:>6.3f} "
                  f"{qty:>5} {need:>7.3f} {'-':>8} {'-':>8} {'-':>8} "
                  f"{'NO BOOK':>9}")
            continue
        _, bid, ask = nxt
        cmk = own_buy(opp, bid, ask, "MAKER")
        ctk = own_buy(opp, bid, ask, "TAKER")
        feas = cmk <= need
        rows.append((f"{day[4:]}/{win}z", oid, sd, f_own, qty, need,
                     cmk, ctk, f_own + cmk, feas))
# outcome: did this window's settlement leave a residual on this side?
resid = {}
for tape in sorted(glob.glob(
        "analysis/l5/residual_exit/preserved/btc15m_*.ndjson")):
    m = re.search(r'_(\d{8})_(\d{4})z\.ndjson$', tape)
    if not m:
        continue
    for ln in open(tape):
        d = json.loads(ln)
        if d.get("label") == "L5_LIVE_SETTLED":
            b = json.loads(d["body"], strict=False)
            resid[f"{m.group(1)[4:]}/{m.group(2)}z"] = (
                b.get("residual_side"), D(str(b.get("residual_qty", "0"))))
for (w, oid, sd, f_own, qty, need, cmk, ctk, pmk, feas) in rows:
    rsd, rq = resid.get(w, (None, D(0)))
    outcome = "STRAND" if (rsd == sd and rq > 0) else ("PAIRED" if rsd is not None else "?")
    print(f"{w:10} {oid:13} {sd:5} {f_own:>6.3f} {qty:>5} {need:>7.3f} "
          f"{cmk:>8.3f} {ctk:>8.3f} {pmk:>8.3f} "
          f"{('YES' if feas else 'NO'):>9} {outcome:8}")
print("-" * 104)
fe = [(r, resid.get(r[0], (None, D(0)))) for r in rows]
print("\nfeasible-at-fill vs eventual outcome:")
import collections
c = collections.Counter()
for r, (rsd, rq) in fe:
    st = "STRAND" if (rsd == r[2] and rq > 0) else "PAIRED"
    c[("feasible" if r[9] else "infeasible", st)] += 1
for k in sorted(c):
    print(f"   complement {k[0]:11} at fill -> {k[1]}: {c[k]}")
