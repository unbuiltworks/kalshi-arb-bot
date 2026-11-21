"""Why pair completion fell from the earlier 54 settled markets to the later 54.

ORDER LEVEL, FROM THE SERVICE'S OWN LOG. Inputs (all read-only pulls of the
production log, stored beside this script's data):

  lifecycle.json          every order-state line per settled window
                          (client order id, side, state, cumulative fill,
                          average price, seconds from window start)
  refusals.json           per window: deployed SHA and how many lines named
                          each refusal reason
  replace_exhausted_lines.json
                          per window where REPLACE ran out: every decision
                          line that carried the refusal -- the second, which
                          leg was being re-placed (or which side was held),
                          and the pair cost the engine had planned
  residual.json           the venue ladder at each ~6.6 s sample while
                          unmatched inventory was held
  settled.json            the settled ledger row per window
  outcomes.json           the venue's settlement

WHAT IT MEASURES. A "first leg" is a fill that opens or adds to unmatched
inventory; it is "completed" by later fills on the other side (first in,
first out). For every first leg: was a completion order on the book when it
filled, for what share of the time it stayed unmatched, and when the last one
ended. Then, for legs that never completed, whether a completion RESTING at
the maker target would have filled.

WHAT IT DOES NOT CLAIM. A resting order is counted filled only when the
venue's touch traded THROUGH its price (strictly better), on a sample at
least 14 s after the first-leg fill and before T+600. A touch AT the price
is not a fill. Queue position is not modelled beyond that. No rebate is
assumed. Nothing here is realized P&L: the counterfactual is a replay.
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics as st
import sys
from decimal import Decimal as D

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent.parent / "data" / "l5" / "recent" / "wide_20261111"
FEE = D("0.095")              # venue feeCoefficient; charged on the modelled
                              # completion as if it paid the taker schedule,
                              # which a resting maker order does not
TERMINAL_S = 600.0
MIN_REST_S = 14.0


def load(name):
    return json.loads((DATA / name).read_text())


def orders(rows):
    by = collections.OrderedDict()
    for r in rows:
        o = by.setdefault(r["coid"], {"coid": r["coid"], "side": None,
                                      "t0": r["t"], "t1": r["t"],
                                      "fills": [], "cum": D(0)})
        side = r.get("side_tracked") or r.get("side")
        if side:
            o["side"] = side
        o["t1"] = r["t"]
        try:
            cum = D(str(r.get("cum_qty") or r.get("cum") or 0))
        except Exception:                                    # noqa: BLE001
            cum = D(0)
        if cum > o["cum"]:
            px = D(str(r.get("avg_px") or 0))
            own = px if o["side"] == "UP" else D(1) - px
            o["fills"].append((r["t"], cum - o["cum"], own))
            o["cum"] = cum
    return [o for o in by.values() if o["side"] in ("UP", "DOWN")]


def first_legs(rows):
    os_ = orders(rows)
    fills = sorted((t, o["side"], q, px, o["coid"])
                   for o in os_ for t, q, px in o["fills"])
    net, legs, unmatched = D(0), [], []
    for t, side, q, px, coid in fills:
        sgn = q if side == "UP" else -q
        if net == 0 or (net > 0) == (sgn > 0):
            legs.append(_leg(os_, t, side, q, px, coid))
            unmatched.append([q, legs[-1]])
        else:
            left = q
            for u in unmatched:
                if left <= 0:
                    break
                if u[0] <= 0:
                    continue
                k = min(left, u[0])
                u[0] -= k
                left -= k
                u[1]["completed_qty"] += k
                if u[0] <= 0:
                    u[1]["t_done"] = t
            if left > 0:
                legs.append(_leg(os_, t, side, left, px, coid))
                unmatched.append([left, legs[-1]])
        net += sgn
    return legs


def _leg(os_, t, side, q, px, coid):
    opp = "DOWN" if side == "UP" else "UP"
    mine = [o for o in os_ if o["side"] == opp and o["coid"] != coid]
    live = [o for o in mine if o["t0"] <= t + 3 and o["t1"] >= t - 3]
    return {"t": t, "side": side, "qty": q, "px": px, "coid": coid,
            "completed_qty": D(0), "t_done": None,
            "opp_live_at_fill": bool(live), "_opp": mine}


def coverage(leg):
    """Share of the unmatched time with a completion order on the book,
    and seconds from the fill to the end of the last such order."""
    end = leg["t_done"] if leg["t_done"] is not None else TERMINAL_S
    grid = [leg["t"] + 2 * i for i in range(int(max(0, end - leg["t"]) // 2))]
    if not grid:
        return None, None, 0.0
    on = sum(1 for g in grid
             if any(o["t0"] <= g <= o["t1"] for o in leg["_opp"]))
    ends = [o["t1"] for o in leg["_opp"] if o["t1"] >= leg["t"] - 3]
    last = (max(ends) - leg["t"]) if ends else None
    return on / len(grid), last, end - leg["t"]


def resting_fill(leg, ladder, target):
    """Would a completion resting at `target - paid` have traded? Through
    only; None when the recorded ladder stops before the terminal."""
    want = target - leg["px"]
    if want <= 0:
        return False
    rows = [s for s in ladder
            if leg["t"] + MIN_REST_S <= s["t"] <= TERMINAL_S]
    if not rows:
        return None
    for s in rows:
        try:
            bid = D(s["bids"][0][0]) if s.get("bids") else None
            ask = D(s["asks"][0][0]) if s.get("asks") else None
        except Exception:                                    # noqa: BLE001
            continue
        # holding UP: sell YES (buy DOWN at `want`) -> YES bid through 1-want
        if leg["side"] == "UP" and bid is not None and bid > D(1) - want:
            return True
        # holding DOWN: buy YES at `want` -> YES ask through want
        if leg["side"] == "DOWN" and ask is not None and ask < want:
            return True
    return False


def med(xs):
    xs = [float(x) for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def pct(a, b):
    return f"{100 * float(a) / float(b):.0f}%" if b else "n/a"


def main():
    life, ref = load("lifecycle.json"), load("refusals.json")
    ladders, settled = load("residual.json"), load("settled.json")
    lines = load("replace_exhausted_lines.json")
    slugs = sorted(s for s in settled
                   if "cpc-btc-updown-15m" in s and s in life
                   and (D(str(settled[s].get("up_qty") or 0))
                        + D(str(settled[s].get("dn_qty") or 0))) > 0)
    half = len(slugs) // 2
    groups = [("earlier", slugs[:half]), ("later", slugs[half:])]
    legs_by = {s: first_legs(life[s]) for s in slugs}
    print(f"settled windows with a fill: {len(slugs)}  "
          f"({slugs[0][-16:]} .. {slugs[-1][-16:]})")

    print("\n1. FIRST LEGS AND WHAT COMPLETED THEM")
    for tag, ss in groups:
        legs = [l for s in ss for l in legs_by[s]]
        q = sum(l["qty"] for l in legs)
        cq = sum(l["completed_qty"] for l in legs)
        stranded = [l for l in legs if l["completed_qty"] < l["qty"]]
        print(f"  {tag:8s} windows {len(ss):3d}  first legs {len(legs):3d}  "
              f"contracts {q}  completed {pct(cq, q)}  "
              f"stranded legs {len(stranded)}  "
              f"completion order live at the fill "
              f"{pct(sum(1 for l in legs if l['opp_live_at_fill']), len(legs))}")

    print("\n2. THE STRANDED LEGS: WAS A COMPLETION ORDER ON THE BOOK?")
    for tag, ss in groups:
        cov, last, dur, none_after = [], [], [], 0
        under10 = n = 0
        for s in ss:
            for l in legs_by[s]:
                if l["completed_qty"] >= l["qty"]:
                    continue
                c, la, d = coverage(l)
                if c is None:
                    continue
                n += 1
                cov.append(c)
                dur.append(d)
                under10 += c < 0.10
                if la is None:
                    none_after += 1
                else:
                    last.append(la)
        print(f"  {tag:8s} stranded {n:3d}  unmatched for median {med(dur)} s  "
              f"completion order live: median {100 * (med(cov) or 0) / 1:.0f}% "
              f"of that time, under 10% in {under10}  "
              f"none at all after the fill in {none_after}  "
              f"last one ended median {med(last)} s after the fill")

    print("\n3. REPLACE BUDGET EXHAUSTED: the completing leg, refused, "
          "line by line")
    blocked = {}                # slug -> [(leg, refusal lines, pair costs)]
    for s in slugs:
        for l in legs_by[s]:
            if l["completed_qty"] >= l["qty"]:
                continue
            opp = "DOWN" if l["side"] == "UP" else "UP"
            hits = [x for x in lines.get(s, []) if x["t"] >= l["t"]
                    and (x.get("leg") == opp
                         or x.get("held_side") == l["side"])]
            if hits:
                blocked.setdefault(s, []).append((l, hits))
    comp_lines = sum(ref[s]["counts"].get("COMPLETE_BUDGET_EXHAUSTED", 0)
                     for s in slugs if s in ref)
    print(f"  COMPLETE_BUDGET_EXHAUSTED lines, all {len(slugs)} windows: "
          f"{comp_lines}")
    for s in sorted(blocked):
        for l, hits in blocked[s]:
            pcs = sorted({x["pair_cost"] for x in hits if x.get("pair_cost")})
            print(f"    {s[-16:]}  sha {','.join(ref[s]['sha'])}  held "
                  f"{l['side']:4s} {l['qty']} @ {l['px']} from {l['t']:.0f} s"
                  f"  completing leg refused {len(hits):3d} times "
                  f"({hits[0]['t']:.0f}-{hits[-1]['t']:.0f} s)  planned pair "
                  f"{pcs[0]}..{pcs[-1]}  settled "
                  f"{D(str(settled[s].get('settlement_pnl') or 0)):+.4f}")
    for tag, ss in groups:
        hit = [s for s in ss if s in blocked]
        bq = sum(l["qty"] - l["completed_qty"] for s in hit
                 for l, _ in blocked[s])
        allq = sum(l["qty"] for s in ss for l in legs_by[s])
        donq = sum(l["completed_qty"] for s in ss for l in legs_by[s])
        pnl = sum(D(str(settled[s].get("settlement_pnl") or 0)) for s in hit)
        locked = sum((l["qty"] - l["completed_qty"])
                     * (D(1) - max(D(x["pair_cost"]) for x in hits
                                   if x.get("pair_cost")))
                     for s in hit for l, hits in blocked[s])
        print(f"  {tag:8s} windows {len(hit)}  contracts stranded behind the "
              f"refusal {bq}  completion {pct(donq, allq)} -> "
              f"{pct(donq + bq, allq)} had they completed  settled as it "
              f"happened {pnl:+.4f}  vs the planned pair locked "
              f"{locked:+.4f} (replay, no fill is assumed here)")

    print("\n4. COUNTERFACTUAL ON THE REAL STRANDED LEGS: A COMPLETION "
          "RESTING AT THE TARGET (through-only, >=14 s after the fill, "
          "before T+600)")
    for scope, keep in (("all windows", lambda s: True),
                        ("windows where REPLACE blocked the completion",
                         lambda s: s in blocked)):
        print(f"  -- {scope}")
        for tag, ss in groups + [("both", slugs)]:
            ss = [s for s in ss if keep(s)]
            allq = sum(l["qty"] for s in ss for l in legs_by[s])
            donq = sum(l["completed_qty"] for s in ss for l in legs_by[s])
            line = (f"  {tag:8s} completion now {pct(donq, allq)} of {allq}")
            for target in (D("0.96"), D("0.98"), D("1.00")):
                n = fills = 0
                gain_q, delta = D(0), D(0)
                for s in ss:
                    yes = settled[s].get("yes_value")
                    for l in legs_by[s]:
                        left = l["qty"] - l["completed_qty"]
                        if left <= 0:
                            continue
                        f = resting_fill(l, ladders.get(s, []), target)
                        if f is None:
                            continue
                        n += 1
                        if not f:
                            continue
                        fills += 1
                        gain_q += left
                        if yes is None:
                            continue
                        up_won = D(str(yes)) == 1
                        held_won = up_won == (l["side"] == "UP")
                        # as it settled: the unmatched leg alone
                        actual = left * ((D(1) if held_won else D(0))
                                         - l["px"])
                        want = target - l["px"]
                        fee = FEE * want * (D(1) - want) * left
                        delta += left * (D(1) - target) - fee - actual
                line += (f" | {target}: {fills}/{n} fill -> "
                         f"{pct(donq + gain_q, allq)}, settled effect "
                         f"{delta:+.2f}")
            print(line)
    print("\n5. WHAT STOOD BEHIND EACH STRANDED LEG (refusal lines logged in "
          "its window)")
    PRICE = ("PAIR_COST_ABOVE_CEILING", "COMPLEMENT_PAIR_ABOVE_CAP",
             "PAIR_ABOVE_REDEMPTION")
    for tag, ss in groups:
        kinds = collections.Counter()
        for s in ss:
            for l in legs_by[s]:
                if l["completed_qty"] >= l["qty"]:
                    continue
                cnt = (ref.get(s) or {}).get("counts", {})
                if s in blocked:
                    kinds["replace budget refused the completing leg"] += 1
                elif sum(cnt.get(k, 0) for k in PRICE) > 0:
                    kinds["touch priced the pair above the ceiling; "
                          "refused each tick, nothing rested"] += 1
                else:
                    kinds["neither logged"] += 1
        print(f"  {tag:8s} " + "; ".join(f"{v} {k}" for k, v in
                                         kinds.most_common()))

    print("\n6. MARKET CONDITIONS ON THE STRANDED LEGS (venue ladder)")
    for tag, ss in groups:
        n = at100 = at096 = 0
        mv60, mv300 = [], []
        for s in ss:
            lad = ladders.get(s, [])
            for l in legs_by[s]:
                if l["completed_qty"] >= l["qty"]:
                    continue
                rows = [x for x in lad if l["t"] <= x["t"] <= TERMINAL_S
                        and x.get("bids") and x.get("asks")]
                if not rows:
                    continue
                n += 1

                def pair(x):
                    bid, ask = D(x["bids"][0][0]), D(x["asks"][0][0])
                    other = (D(1) - bid) if l["side"] == "UP" else ask
                    return l["px"] + other

                def held_mid(x):
                    mid = (D(x["bids"][0][0]) + D(x["asks"][0][0])) / 2
                    return mid if l["side"] == "UP" else D(1) - mid

                best = min(pair(x) for x in rows)
                at100 += best <= D("1.00")
                at096 += best <= D("0.96")
                for horizon, acc in ((60, mv60), (300, mv300)):
                    later = [x for x in rows if x["t"] >= l["t"] + horizon]
                    if later:
                        acc.append(held_mid(later[0]) - held_mid(rows[0]))
        print(f"  {tag:8s} stranded legs with a ladder {n}  the other side "
              f"was OFFERED at a pair <= 1.00 in {pct(at100, n)}, <= 0.96 in "
              f"{pct(at096, n)}  held side moved median "
              f"{100 * (med(mv60) or 0) / 1:+.1f}c at 60 s, "
              f"{100 * (med(mv300) or 0) / 1:+.1f}c at 300 s")

    print("\n7. BY DEPLOYED SHA")
    by = collections.OrderedDict()
    for s in slugs:
        sha = ",".join((ref.get(s) or {}).get("sha") or ["?"])
        r = by.setdefault(sha, {"w": 0, "q": D(0), "cq": D(0), "blk": 0,
                                "first": s[-16:], "pnl": D(0)})
        r["w"] += 1
        r["q"] += sum(l["qty"] for l in legs_by[s])
        r["cq"] += sum(l["completed_qty"] for l in legs_by[s])
        r["blk"] += s in blocked
        r["pnl"] += D(str(settled[s].get("settlement_pnl") or 0))
    for sha, r in by.items():
        print(f"    {sha:22s} from {r['first']}  windows {r['w']:3d}  "
              f"first-leg contracts {r['q']:>7}  completed "
              f"{pct(r['cq'], r['q']):>4s}  budget-blocked windows "
              f"{r['blk']}  settled {r['pnl']:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
