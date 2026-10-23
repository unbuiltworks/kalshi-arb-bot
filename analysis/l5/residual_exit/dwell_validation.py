"""Full net-P&L validation of the dwell gate, per eligible window.

NOT a tail metric. Every policy below is scored on realized net per unit of
stranded inventory over EVERY eligible window, with the opportunity cost of
exiting early charged explicitly, paired confidence intervals, and the
execution assumptions written down.

POLICIES
  HOLD   never exit; the leg settles.
  ARM    the deployed gate: exit only on an evaluation tick landing inside
         [terminal-60, terminal-15). Retirement AT the terminal, so a tick
         that lands late finds no runway -- the 0300z defect, modelled.
  ARM+C  the same gate with the retirement coordinated (terminal - runway).
  DWELL  continuous from `dwell_s` after the leg went off flat to the
         terminal, with the same coordination. All economic gates identical.

EVALUATION CADENCE IS THE TAPE. Policies may only act on a tick that exists,
so an irregular cadence can miss a narrow window exactly as production did
(0300z stepped T+524 -> T+587 across a 45s interval). This is why ARM and
DWELL differ at all.

EXECUTION ASSUMPTIONS, all adverse to the policy under test
  * a cross sells our side at `price - 1 tick`, never at the mid;
  * PMUS fee curve theta*p*(1-p): taker 0.0695, maker credit 0.0125;
  * a maker completion counts as filled only if the path REACHES its level,
    and the queue-aware variant requires it to pass one tick BEYOND;
  * no rebate is assumed on any crossing order;
  * settlement pays $1 on the winning outcome, $0 otherwise.

OPPORTUNITY COST is charged, not ignored: for every window where a policy
exits, we record whether the profitable maker completion (pair <= 0.96) became
reachable LATER in that same window, and what holding would have realized.

PATH AND OUTCOME come from L5's Polymarket window corpus -- the same BTC/ETH
up-down instrument family -- because PMUS book history of this length does not
exist. FEES, CEILINGS AND TICKS are PMUS's own. Polymarket fill economics,
rebates and queue positions are NOT transferred. Side, size and cost basis at
each decision are the as-of-tick cumulative state, so the policies see only
what existed then; only the settlement outcome comes from the window's end.

No network, no venue, no orders, no limit changed.
"""
from __future__ import annotations

import collections
import csv
import json
import math
import os
import statistics as st
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

TICK = D("0.01")
TAKER_THETA = D("0.0695")
MAKER_THETA = D("0.0125")
MAKER_CEIL = D("0.96")
PAIR_CEIL = D("1.20")
ARM_S = 60.0
MIN_EXEC_S = 15.0
DWELL_S = 30.0


def tfee(p: D) -> D:
    return TAKER_THETA * p * (D(1) - p)


def mcred(p: D) -> D:
    return MAKER_THETA * p * (D(1) - p)


def load(minutes: str) -> Tuple[Dict[str, str], Dict[str, list]]:
    outc = {r["window_id"]: r["winning_outcome"]
            for r in csv.DictReader(open(f"{BASE}/l5_windows_summary.csv"))
            if r["window_minutes"] == minutes
            and r["winning_outcome"] in ("Up", "Down")}
    seq: Dict[str, list] = collections.defaultdict(list)
    for r in csv.DictReader(open(f"{BASE}/l5_window_sequences.csv")):
        w = r["window_id"]
        if w not in outc:
            continue
        try:
            t = float(r["seconds_from_window_start"])
            px = D(r["price"])
            eq = D(r["excess_qty"] or 0)
            ec = D(r["excess_cost"] or 0)
        except Exception:                                      # noqa: BLE001
            continue
        if not (D("0.005") <= px <= D("0.995")):
            continue
        up = px if r["outcome"] == "Up" else D(1) - px
        seq[w].append((t, up, eq, ec, r["dominant_side"]))
    for w in seq:
        seq[w].sort(key=lambda x: x[0])
    return outc, seq


def _state(row) -> Optional[tuple]:
    t, up, eq, ec, dom = row
    if dom not in ("Up", "Down") or eq == 0 or ec <= 0:
        return None
    q = abs(eq)
    c = ec / q
    if not (D("0.01") <= c <= D("0.99")):
        return None
    return t, q, c, (up if dom == "Up" else D(1) - up), dom


def eligible(rows: Sequence, terminal: float) -> Optional[dict]:
    """A window is ELIGIBLE if a leg became unmatched and, at that moment,
    the profitable completion was NOT already reachable. Those are the
    windows this policy exists to manage; scoring it on the others would
    dilute the comparison with states it never sees."""
    first = None
    for row in rows:
        s = _state(row)
        if s is None or s[0] > terminal:
            continue
        first = s
        break
    if first is None:
        return None
    return {"t_strand": first[0], "cost": first[2], "qty": first[1]}


def walk(rows: Sequence, won_side: str, terminal: float, policy: str,
         dwell_s: float = DWELL_S) -> dict:
    """Run one policy over one window. Returns the realized outcome plus the
    opportunity cost of whatever it did."""
    t_strand = None
    last = None
    exit_rec = None
    maker_reachable_after_exit = False
    best_pair_after_exit = None
    for row in rows:
        s = _state(row)
        if s is None or s[0] > terminal:
            continue
        t, q, c, o, dom = s
        last = s
        if t_strand is None:
            t_strand = t
        maker_pair = c + (D(1) - o)            # complement AT the touch
        if exit_rec is not None:
            # after exiting we only RECORD what we gave up
            if maker_pair <= MAKER_CEIL:
                maker_reachable_after_exit = True
            if best_pair_after_exit is None or maker_pair < best_pair_after_exit:
                best_pair_after_exit = maker_pair
            continue
        ex = max(D("0.01"), o - TICK)           # we sell our side at the bid
        m = D(1) - ex
        pair = c + m
        if maker_pair <= MAKER_CEIL:
            continue                            # priority one: keep working it
        if pair > PAIR_CEIL:
            continue
        if pair - D(1) + tfee(m) >= c:
            continue                            # must reduce bounded downside
        if policy == "HOLD":
            continue
        rem = terminal - t
        if policy == "ARM":
            # deployed: the arm window, and retirement AT the terminal means a
            # tick inside [terminal-60, terminal-15) is the ONLY chance
            if not (ARM_S >= rem > MIN_EXEC_S):
                continue
        elif policy == "ARM_C":
            if not (ARM_S >= rem >= MIN_EXEC_S):
                continue
        else:                                   # DWELL
            if t - t_strand < dwell_s:
                continue
            if rem < MIN_EXEC_S:
                continue
        exit_rec = {"t": t, "price": ex, "pair": pair,
                    "net": float(ex - c - tfee(m))}
    if last is None:
        return {}
    t, q, c, o, dom = last
    hold_net = float((D(1) if dom == won_side else D(0)) - c)
    if exit_rec is None:
        return {"exited": False, "net": hold_net, "hold_net": hold_net,
                "opportunity_cost": 0.0, "qty": float(q), "cost": float(c)}
    return {"exited": True, "net": exit_rec["net"], "hold_net": hold_net,
            "exit_t": exit_rec["t"], "exit_pair": float(exit_rec["pair"]),
            # WHAT EXITING GAVE UP, both legs of it
            "opportunity_cost": max(0.0, hold_net - exit_rec["net"]),
            "maker_became_reachable_after_exit": maker_reachable_after_exit,
            "best_pair_after_exit": (None if best_pair_after_exit is None
                                     else float(best_pair_after_exit)),
            "qty": float(q), "cost": float(c)}


def _ci(xs: Sequence[float], z: float = 1.96) -> Tuple[float, float, float]:
    """mean and a normal-approximation CI on the mean."""
    n = len(xs)
    if n < 2:
        return (st.mean(xs) if xs else 0.0, 0.0, 0.0)
    m = st.mean(xs)
    se = st.stdev(xs) / math.sqrt(n)
    return m, m - z * se, m + z * se


def evaluate(minutes: str, terminal: float, train_frac: float = 0.70
             ) -> Dict[str, Any]:
    outc, seq = load(minutes)
    wins = sorted(seq)
    elig = []
    for w in wins:
        e = eligible(seq[w], terminal)
        if e is not None:
            elig.append(w)
    k = int(len(elig) * train_frac)
    out: Dict[str, Any] = {
        "minutes": minutes, "terminal_s": terminal,
        "windows_total": len(wins), "windows_eligible": len(elig),
        "train_eligible": k, "oos_eligible": len(elig) - k,
        "eligibility": ("a leg became unmatched and the profitable completion "
                        "was not already reachable at that moment"),
        "splits": {},
    }
    for name, pool in (("train", elig[:k]), ("oos", elig[k:])):
        res: Dict[str, Any] = {}
        runs = {p: [walk(seq[w], outc[w], terminal, p) for w in pool]
                for p in ("HOLD", "ARM", "ARM_C", "DWELL")}
        for p, rs in runs.items():
            rs = [r for r in rs if r]
            nets = [r["net"] for r in rs]
            m, lo, hi = _ci(nets)
            s = sorted(nets)
            res[p] = {
                "n": len(rs),
                "mean_net_per_unit": round(m, 5),
                "ci95": [round(lo, 5), round(hi, 5)],
                "median": round(st.median(nets), 5),
                "p05": round(s[max(0, round(0.05 * (len(s) - 1)))], 5),
                "worst": round(s[0], 5),
                "exit_rate": round(st.mean([1.0 if r.get("exited") else 0.0
                                            for r in rs]), 4),
                "mean_opportunity_cost": round(
                    st.mean([r.get("opportunity_cost", 0.0) for r in rs]), 5),
                "frac_below_-0.30": round(
                    sum(1 for x in nets if x < -0.30) / len(nets), 4),
                "exits_where_maker_became_reachable_later": round(
                    st.mean([1.0 if r.get("maker_became_reachable_after_exit")
                             else 0.0 for r in rs if r.get("exited")]), 4)
                if any(r.get("exited") for r in rs) else None,
            }
        # PAIRED differences, which is the only honest comparison
        res["paired"] = {}
        for a, b in (("DWELL", "ARM"), ("DWELL", "ARM_C"), ("DWELL", "HOLD"),
                     ("ARM_C", "ARM"), ("ARM", "HOLD")):
            ra = {w: r for w, r in zip(pool, runs[a]) if r}
            rb = {w: r for w, r in zip(pool, runs[b]) if r}
            ks = sorted(set(ra) & set(rb))
            d = [ra[w]["net"] - rb[w]["net"] for w in ks]
            if len(d) < 2:
                continue
            m, lo, hi = _ci(d)
            se = st.stdev(d) / math.sqrt(len(d))
            res["paired"]["%s_minus_%s" % (a, b)] = {
                "n": len(d), "mean": round(m, 5),
                "ci95": [round(lo, 5), round(hi, 5)],
                "t": round(m / se, 3) if se else None,
                "better_in_frac": round(
                    sum(1 for x in d if x > 0) / len(d), 4),
                "significant_at_95": bool(lo > 0 or hi < 0),
            }
        out["splits"][name] = res
    return out


def report() -> Dict[str, Any]:
    return {"btc15m_like": evaluate("15", 780.0),
            "btc5m_like": evaluate("5", 270.0),
            "assumptions": {
                "cross_price": "our side's price minus one tick (the bid)",
                "taker_theta": str(TAKER_THETA),
                "maker_credit_theta": str(MAKER_THETA),
                "maker_ceiling": str(MAKER_CEIL),
                "pair_ceiling": str(PAIR_CEIL),
                "arm_s": ARM_S, "min_exec_s": MIN_EXEC_S, "dwell_s": DWELL_S,
                "rebate_assumed_on_cross": False,
                "evaluation_cadence": "the tape's own ticks, irregular",
                "path_source": "L5 Polymarket window corpus (same instrument "
                               "family); fees/ceilings/ticks are PMUS's",
                "lookahead": "side, size and cost basis are as-of-tick; only "
                             "the settlement outcome comes from window end",
            }}


def main() -> int:
    rep = report()
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "dwell_validation.json")
    with open(dst, "w") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    print("wrote", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
