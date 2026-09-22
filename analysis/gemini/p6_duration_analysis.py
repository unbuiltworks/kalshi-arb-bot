"""Duration-normalized habitat + a CAUSAL selector, from the public tape only.

TAPE-ONLY PAIRING MODEL, and its licence. The venue publishes one ladder and
mirrors it, which `residual_timeline.calibrate` proved against 56 confirmed
maker fills: our YES buy at p appears as a SELL-aggressor print at p, and our
NO buy at q appears as a BUY-aggressor print at 1-q. So on tape alone:

    a SELL print at p   =>  a YES contract was buyable at p
    a BUY  print at b   =>  a NO  contract was buyable at 1-b

A matched pair therefore needs one of each, and its cost is p + (1-b). Pairing
them CHRONOLOGICALLY (the complement must come at or after the first leg) is
what makes this causal rather than a sorted-best fantasy.

NO FUTURE LEAKAGE IN THE GATES. Every gate feature is computed from prints
strictly BEFORE the decision timestamp. A gate that reads the market's own
final print count is measuring the answer.
"""
import json, sys, statistics
from collections import Counter, defaultdict
from fractions import Fraction as F

TICK = F(1, 100)


def prints_of(m):
    """(t, side, price, qty) sorted ascending. Tape comes newest-first."""
    return sorted(m.get("_prints") or [], key=lambda x: x[0])


def pair_capacity(pr, *, max_pair_cost=None):
    """Chronological greedy pairing: how many matched pairs the tape supports.

    Walks forward holding at most one unpaired leg. When the opposite leg
    arrives the pair closes at the sum of the two prices. `max_pair_cost`
    refuses a pair that would cost more than the cap -- the leg keeps waiting.
    Whatever is still unpaired at the end is RESIDUAL, priced at what it cost.
    """
    pend_yes = []   # prices of YES legs bought and not yet completed
    pend_no = []
    pairs = []
    for t, side, px, qty in pr:
        if side == "sell":                      # a YES contract was buyable
            p = px
            if pend_no and (max_pair_cost is None
                            or p + pend_no[0] <= max_pair_cost):
                pairs.append((t, p + pend_no.pop(0)))
            else:
                pend_yes.append(p)
        elif side == "buy":                     # a NO contract was buyable
            q = F(1) - px
            if pend_yes and (max_pair_cost is None
                             or q + pend_yes[0] <= max_pair_cost):
                pairs.append((t, q + pend_yes.pop(0)))
            else:
                pend_no.append(q)
    resid = pend_yes + pend_no
    return pairs, resid


def summarise(ms, label):
    live = [m for m in ms if m["n_trades"] > 0]
    if not live:
        print(f"{label:>10} -- no contract had a single print")
        return None
    def med(f, src=live):
        v = [f(m) for m in src if f(m) is not None]
        return statistics.median(v) if v else None
    npairs, ncost, nres, nrescost, matched_ok = [], [], [], [], 0
    for m in live:
        pr = prints_of(m)
        pairs, resid = pair_capacity(pr)
        npairs.append(len(pairs))
        nres.append(len(resid))
        if pairs:
            ncost.append(float(sum(c for _, c in pairs) / len(pairs)))
            if sum(c for _, c in pairs) / len(pairs) <= 1:
                matched_ok += 1
        if resid:
            nrescost.append(float(sum(resid) / len(resid)))
    row = {
        "label": label, "n": len(ms), "traded": len(live),
        "life_min": med(lambda m: m.get("life_min")),
        "prints": med(lambda m: m["n_trades"]),
        "usd": med(lambda m: m["notional"]),
        "prints_per_min": med(lambda m: m["trades_per_min"]),
        "usd_per_min": med(lambda m: m["usd_per_min"]),
        "two_sided": med(lambda m: m["two_sided_frac"]),
        "gap_s": med(lambda m: m["median_gap_s"]),
        "levels": med(lambda m: m["distinct_prices"]),
        "reversals": med(lambda m: m["reversals_through_50"]),
        "amplitude": med(lambda m: m["amplitude"]),
        "pairs_per_mkt": statistics.median(npairs) if npairs else 0,
        "pairs_total": sum(npairs),
        "median_pair_cost": statistics.median(ncost) if ncost else None,
        "pct_pair_under_1": (matched_ok / len(live)) if live else 0,
        "residual_per_mkt": statistics.median(nres) if nres else 0,
        "median_residual_cost": statistics.median(nrescost) if nrescost else None,
    }
    return row


def print_rows(rows, title):
    print(f"\n=== {title} ===")
    hdr = (f"{'lane':>10} {'n':>4} {'traded':>7} {'life':>6} {'prints':>7} {'$':>8} "
           f"{'pr/min':>7} {'$/min':>7} {'2side':>6} {'gap_s':>7} {'lvls':>5} "
           f"{'revs':>5} {'amp':>5} {'pairs/mkt':>10} {'pairCost':>9} {'pair<=1':>8} "
           f"{'resid/mkt':>10} {'residCost':>10}")
    print(hdr)
    for r in rows:
        if not r:
            continue
        f = lambda v, d=2: ("-" if v is None else f"{v:.{d}f}")
        print(f"{r['label']:>10} {r['n']:>4} {r['traded']:>7} {f(r['life_min'],0):>6} "
              f"{f(r['prints'],0):>7} {f(r['usd'],0):>8} {f(r['prints_per_min'],3):>7} "
              f"{f(r['usd_per_min'],2):>7} {f(r['two_sided'],2):>6} {f(r['gap_s'],0):>7} "
              f"{f(r['levels'],0):>5} {f(r['reversals'],0):>5} {f(r['amplitude'],2):>5} "
              f"{f(r['pairs_per_mkt'],1):>10} {f(r['median_pair_cost'],4):>9} "
              f"{f(r['pct_pair_under_1'],2):>8} {f(r['residual_per_mkt'],1):>10} "
              f"{f(r['median_residual_cost'],4):>10}")


# --------------------------------------------------------------------------
# the selector
# --------------------------------------------------------------------------
# NO FUTURE LEAKAGE, and this is the whole design constraint. At the moment we
# must decide whether to participate in market k, market k has not traded yet
# -- it has no prints at all. So a gate may only read the PRECEDING contract in
# the same series. Reading market k's own realized flow would be reading the
# answer, and would make every gate look brilliant.

def chain(ms):
    """Contracts of one series in start order, so 'the previous one' exists."""
    return sorted([m for m in ms if m.get("start")], key=lambda m: m["start"])


def evaluate(ms, gate, label, *, max_pair_cost=None):
    """Walk a duration's contracts in time order, gating on the PREVIOUS one."""
    seq = chain(ms)
    entered = skipped = 0
    pairs_tot = 0
    pair_cost_sum = F(0)
    resid_tot = 0
    resid_cost_sum = F(0)
    for i, m in enumerate(seq):
        prev = seq[i - 1] if i > 0 else None
        if not gate(prev):
            skipped += 1
            continue
        entered += 1
        pairs, resid = pair_capacity(prints_of(m), max_pair_cost=max_pair_cost)
        pairs_tot += len(pairs)
        pair_cost_sum += sum(c for _, c in pairs)
        resid_tot += len(resid)
        resid_cost_sum += sum(resid)
    # A matched pair pays exactly $1. Residual pays $1 only if its leg wins,
    # and we do NOT know the outcome here, so residual is carried at COST and
    # its P&L is reported as a range rather than guessed.
    matched_pnl = F(pairs_tot) - pair_cost_sum
    return {
        "label": label, "entered": entered, "skipped": skipped,
        "pairs": pairs_tot,
        "pair_cost": (float(pair_cost_sum / pairs_tot) if pairs_tot else None),
        "matched_pnl": float(matched_pnl),
        "residual": resid_tot,
        "residual_cost": (float(resid_cost_sum / resid_tot) if resid_tot else None),
        "residual_cost_total": float(resid_cost_sum),
    }


def gate_all(prev):
    return True


def gate_flow(threshold):
    def g(prev):
        if prev is None:
            return False
        r = prev.get("trades_per_min")
        return r is not None and r >= threshold
    return g


def gate_oscillation(min_pairs):
    def g(prev):
        if prev is None:
            return False
        pairs, _ = pair_capacity(prints_of(prev))
        return len(pairs) >= min_pairs
    return g


def gate_both(threshold, min_pairs):
    f, o = gate_flow(threshold), gate_oscillation(min_pairs)
    def g(prev):
        return f(prev) and o(prev)
    return g


def print_gates(rows, title):
    print(f"\n=== {title} ===")
    print(f"{'gate':>34} {'entered':>8} {'skipped':>8} {'pairs':>7} {'pairCost':>9} "
          f"{'matched$':>9} {'residual':>9} {'residCost':>10} {'resid$ at risk':>15}")
    for r in rows:
        f = lambda v, d=4: ("-" if v is None else f"{v:.{d}f}")
        print(f"{r['label']:>34} {r['entered']:>8} {r['skipped']:>8} {r['pairs']:>7} "
              f"{f(r['pair_cost']):>9} {f(r['matched_pnl'],2):>9} {r['residual']:>9} "
              f"{f(r['residual_cost']):>10} {f(r['residual_cost_total'],2):>15}")


# --------------------------------------------------------------------------
# item 1 + 2: pair-cost and residual-quality OPPORTUNITY bands
# --------------------------------------------------------------------------
PAIR_BANDS = ("<0.95", "0.95-1.00", ">1.00")
RES_BANDS = ("<0.35", "0.35-0.50", "0.50-0.65", "0.65-0.80", ">0.80")


def pair_band(c):
    return "<0.95" if c < F(95, 100) else ("0.95-1.00" if c <= F(1) else ">1.00")


def res_band(u):
    return ("<0.35" if u < F(35, 100) else "0.35-0.50" if u < F(1, 2)
            else "0.50-0.65" if u < F(65, 100) else "0.65-0.80"
            if u < F(4, 5) else ">0.80")


def opportunity(ms, label):
    """How often the tape PERMITS a cheap pair, and where residual would land.

    P1 is the reason this exists: raw flow without attractive matching
    economics is not a habitat. A market that prints constantly but only ever
    permits a pair above $1 is a machine for losing money slowly.
    """
    pb, rb = Counter(), Counter()
    pcost = []
    for m in ms:
        pairs, resid = pair_capacity(prints_of(m))
        for _, c in pairs:
            pb[pair_band(c)] += 1
            pcost.append(float(c))
        for u in resid:
            rb[res_band(u)] += 1
    tp, tr = sum(pb.values()) or 1, sum(rb.values()) or 1
    return {"label": label, "pairs": sum(pb.values()), "resid": sum(rb.values()),
            "pair_bands": {k: pb[k] / tp for k in PAIR_BANDS},
            "res_bands": {k: rb[k] / tr for k in RES_BANDS},
            "median_pair": statistics.median(pcost) if pcost else None}


def print_opportunity(rows):
    print("\n=== item 1: PAIR-COST opportunity (share of tape-supported pairs) ===")
    print(f"{'lane':>10} {'pairs':>7} {'medPair':>8} " + " ".join(f"{b:>11}" for b in PAIR_BANDS))
    for r in rows:
        f = "-" if r["median_pair"] is None else f"{r['median_pair']:.4f}"
        print(f"{r['label']:>10} {r['pairs']:>7} {f:>8} "
              + " ".join(f"{r['pair_bands'][b]:>11.1%}" for b in PAIR_BANDS))
    print("\n=== item 2: RESIDUAL-QUALITY opportunity (share of leftover legs) ===")
    print(f"{'lane':>10} {'resid':>7} " + " ".join(f"{b:>11}" for b in RES_BANDS))
    for r in rows:
        print(f"{r['label']:>10} {r['resid']:>7} "
              + " ".join(f"{r['res_bands'][b]:>11.1%}" for b in RES_BANDS))


# --------------------------------------------------------------------------
# hourly: EVENT-level, never summed as independent capacity
# --------------------------------------------------------------------------
# The ladder is NESTED (>=86,100 contains >=86,200 ...), so the eight strikes
# are one BTC direction bet. Per-strike matched economics are real and
# separable -- a matched pair inside ONE strike pays $1 whatever happens. The
# RESIDUAL is not separable and is therefore aggregated to the event.

def event_of(sym):
    """GEMI-BTC2609221800-HI86100 -> BTC2609221800"""
    s = str(sym)
    return s.split("-")[1] if "-" in s else s


def strike_of(sym):
    s = str(sym)
    p = s.split("-")
    return p[2] if len(p) > 2 else None


def hourly_event_view(ms, *, select=None, label="ALL STRIKES"):
    """Per-strike matched economics; residual summed to the EVENT.

    `select(strikes_state) -> set of symbols` is a CAUSAL chooser: it sees
    only each strike's FIRST observed print (time and price) and must decide
    before any later print is used. Pairs are then counted only from prints
    at or after that first-print cutoff.
    """
    by_ev = defaultdict(list)
    for m in ms:
        by_ev[event_of(m["symbol"])].append(m)
    n_ev = 0
    pairs_tot = 0
    pair_cost_sum = F(0)
    ev_resid_legs = []
    ev_resid_cost = []
    strikes_used = []
    for ev, arm in by_ev.items():
        traded = [m for m in arm if m["n_trades"] > 0]
        if not traded:
            continue
        n_ev += 1
        chosen = traded
        if select is not None:
            state = []
            for m in traded:
                pr = prints_of(m)
                if pr:
                    state.append({"symbol": m["symbol"], "t0": pr[0][0],
                                  "p0": F(pr[0][2])})
            keep = select(state)
            chosen = [m for m in traded if m["symbol"] in keep]
        strikes_used.append(len(chosen))
        ev_legs = 0
        ev_cost = F(0)
        for m in chosen:
            pr = prints_of(m)
            pairs, resid = pair_capacity(pr)
            pairs_tot += len(pairs)
            pair_cost_sum += sum(c for _, c in pairs)
            ev_legs += len(resid)
            ev_cost += sum(resid)
        ev_resid_legs.append(ev_legs)
        ev_resid_cost.append(float(ev_cost))
    return {
        "label": label, "events": n_ev,
        "median_strikes_used": (statistics.median(strikes_used)
                                if strikes_used else 0),
        "pairs": pairs_tot,
        "pair_cost": (float(pair_cost_sum / pairs_tot) if pairs_tot else None),
        "matched_pnl": float(F(pairs_tot) - pair_cost_sum),
        "resid_legs_per_event": (statistics.median(ev_resid_legs)
                                 if ev_resid_legs else 0),
        "resid_cost_per_event": (statistics.median(ev_resid_cost)
                                 if ev_resid_cost else 0),
        "resid_cost_total": sum(ev_resid_cost),
    }


def select_nearest_money(k):
    """Causal: keep the k strikes whose FIRST observed price is nearest $0.50.

    Uses only each strike's first print -- information that exists at the
    moment it arrives. Everything counted afterwards is strictly later.
    """
    def sel(state):
        ranked = sorted(state, key=lambda s: abs(s["p0"] - F(1, 2)))
        return {s["symbol"] for s in ranked[:k]}
    return sel


def print_hourly(rows):
    print("\n=== hourly: per-strike MATCHED economics, EVENT-level RESIDUAL ===")
    print("    (strikes are NESTED, so residual is never summed as independent capacity)")
    print(f"{'selection':>26} {'events':>7} {'strikes':>8} {'pairs':>7} {'pairCost':>9} "
          f"{'matched$':>9} {'residLegs/ev':>13} {'residCost/ev':>13} {'residCost tot':>14}")
    for r in rows:
        f = "-" if r["pair_cost"] is None else f"{r['pair_cost']:.4f}"
        print(f"{r['label']:>26} {r['events']:>7} {r['median_strikes_used']:>8.1f} "
              f"{r['pairs']:>7} {f:>9} {r['matched_pnl']:>9.2f} "
              f"{r['resid_legs_per_event']:>13.1f} {r['resid_cost_per_event']:>13.2f} "
              f"{r['resid_cost_total']:>14.2f}")
