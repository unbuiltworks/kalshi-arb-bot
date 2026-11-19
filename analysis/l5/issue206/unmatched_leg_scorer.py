"""Every disposition of an unmatched leg, priced, on real ladders.

ISSUE #206 PRIORITY 3: "For every unmatched leg, compare: maintain
existing maker quote / improve maker price by one tick / cancel and
replace / submit or adjust complementary maker quote / reduce exposure /
hold until settlement. Score each choice using executable prices,
realistic fill probabilities, fees, adverse selection, time remaining and
worst-case residual loss."

WHAT THIS IS. An AUDIT of the controller's choices, not a second
controller. `l5_book_completion.decide` already returns COMPLETE / REPRICE
/ HOLD and already holds the economics; a competing scorer wired into the
money path would be a second author of the same decision. So this scores
the options OFFLINE on recorded books and reports whether the action the
controller takes is the one the economics favour.

THE SIX OPTIONS, AND WHERE EACH ONE LIVES

  1 maintain the existing maker quote      -> HOLD, complement at depth 2
  2 improve the maker price by one tick    -> REPRICE to depth 1
  3 cancel and replace                     -> the MECHANISM of 2 and 4;
                                              it has no separate economics,
                                              so it is priced as the
                                              destination price it buys
  4 submit/adjust the complementary quote   -> COMPLETE, at the depth the
                                              cap admits (0..5)
  5 reduce exposure                        -> NOT in this module, and not
                                              in production: reducing by
                                              crossing is
                                              `l5_defensive_completion`,
                                              taker, and
                                              `taker_crossing_reachable`
                                              is false on the deployed
                                              service. Priced here as
                                              RESEARCH only.
  6 hold until settlement                  -> no complement; the leg is
                                              carried at the measured
                                              residual.

HOW A FILL IS DECIDED. `queue_scenarios.leg_outcome`, unchanged, and its
PROVEN verdict -- the queue-independent LOWER bound: the opposite touch
reached our price. No look-ahead, and a touch is never a fill. The TTL is
the deployed 120 s, so an option that needs longer than the order lives
does not get credit for it.

TIME REMAINING IS A HARD GATE, NOT A WEIGHT. The deployed completion
deadline is T+600 and entry shuts at T+480. An option whose fill lands
after the deadline is scored as a HOLD, because that is what the engine
would really have.

NOTHING HERE IS A FILL, A PAIR OR A PROFIT.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from analysis.l5.issue208 import queue_scenarios as QS   # noqa: E402

TICK = QS.TICK
UP, DOWN = QS.UP, QS.DOWN

#: measured carry of ONE unmatched contract to settlement.
RESIDUAL = QS.RESIDUAL_PER_CONTRACT
#: the deployed fee-safe pair ceiling for a maker completion.
PAIR_CAP = D("0.96")
#: the deployed order TTL and completion deadline.
TTL_S = 120.0
COMPLETE_CUTOFF_S = 600.0
#: taker fee charged on a crossing completion. RESEARCH ONLY.
TAKER_FEE = D("0.0325")
#: CONFIRMED CREDITED maker rebate per fill -- and at the deployed clip of
#: ONE contract it is UNEARNABLE. `L5_EFFECTIVE_RUNTIME_CONFIG` says so in
#: its own words: "rebate floor at p=0.50 is 1.6 contracts, confirmed
#: floor 2.52 (earns $0.00 below it)". So it is reported and NEVER added.
REBATE_UNEARNABLE_BELOW_QTY = D("2.52")


def own(side: str, bid: D, ask: D, depth: int) -> D:
    return (bid if side == UP else (D(1) - ask)) - depth * TICK


def _fill(side, own_px, frames, i, arm, frac) -> Tuple[Optional[bool],
                                                       Optional[float]]:
    o = QS.leg_outcome(side, own_px, frames, start=i, queue_fraction=frac,
                       own_qty=D(1), ttl_s=TTL_S)
    return QS.filled(o, arm=arm), o.get("seconds_held")


def options(side_needed: str, held_own: D, frames, i, bid, ask, arm, frac,
            t_in_window: float) -> List[Dict[str, Any]]:
    """Every disposition, each with its own executable price and verdict."""
    out: List[Dict[str, Any]] = []

    def add(name, depth, *, taker=False):
        if taker:
            px = own(side_needed, bid, ask, 0)
            fee = TAKER_FEE
            # a cross fills immediately or not at all; it cannot be late
            ok, held_s = (True, 0.0) if px > 0 else (None, None)
        else:
            px = own(side_needed, bid, ask, depth)
            fee = D(0)
            ok, held_s = _fill(side_needed, px, frames, i, arm, frac)
        if px <= 0 or ok is None:
            return
        pair = held_own + px + fee
        admissible = pair <= PAIR_CAP
        # TIME IS A GATE. A fill that lands past the completion deadline is
        # not a completion the engine could have had.
        late = (held_s is not None
                and (t_in_window + float(held_s)) > COMPLETE_CUTOFF_S)
        completes = bool(ok) and admissible and not late
        ev = ((D(1) - pair) if completes else RESIDUAL)
        out.append({
            "option": name, "depth": depth, "complement_own": str(px),
            "pair": str(pair), "admissible": admissible,
            "would_fill": bool(ok), "late": late, "completes": completes,
            "taker": taker, "fee": str(fee),
            "seconds_to_fill": held_s,
            "ev_per_contract": str(ev.quantize(D("0.000001")))})

    add("4_complement_at_touch", 0)
    add("2_improve_one_tick", 1)
    add("1_maintain_at_depth_2", 2)
    for d in (3, 4, 5):
        add("4_complement_deeper_d%d" % d, d)
    add("5_reduce_by_crossing_RESEARCH", 0, taker=True)
    # 6: no complement at all. Always available, never refused.
    out.append({
        "option": "6_hold_to_settlement", "depth": None,
        "complement_own": None, "pair": None, "admissible": True,
        "would_fill": False, "late": False, "completes": False,
        "taker": False, "fee": "0", "seconds_to_fill": None,
        "ev_per_contract": str(RESIDUAL.quantize(D("0.000001")))})
    return out


def controller_choice(opts: List[Mapping[str, Any]]) -> str:
    """What `l5_book_completion.decide` does, in this module's vocabulary.

    NOT a reimplementation of its arithmetic -- it is the SHAPE of its
    rule, which is the only part an audit needs: it quotes a complement at
    `book_completion_depth_ticks` (the deployed value is 0, the touch), it
    REPRICES when that price is inadmissible but a nearby one is not, and
    it HOLDS otherwise. The candidate's adaptive retry is what lets the
    REPRICE walk outward; without it the depth is fixed.
    """
    touch = next((o for o in opts
                  if o["option"] == "4_complement_at_touch"), None)
    if touch is not None and touch["admissible"]:
        return "COMPLETE"
    deeper = [o for o in opts
              if o["depth"] not in (None, 0) and not o["taker"]
              and o["admissible"]]
    return "REPRICE" if deeper else "HOLD"


def best(opts: List[Mapping[str, Any]], *, allow_taker: bool) -> Mapping:
    live = [o for o in opts if o["admissible"]
            and (allow_taker or not o["taker"])]
    if not live:
        return next(o for o in opts if o["option"] == "6_hold_to_settlement")
    return max(live, key=lambda o: D(o["ev_per_contract"]))


def run(sources, *, root="", arms=QS.SCENARIOS) -> Dict[str, Any]:
    tally = collections.defaultdict(lambda: collections.Counter())
    ev = collections.defaultdict(lambda: collections.defaultdict(lambda: D(0)))
    agree = collections.defaultdict(lambda: collections.Counter())
    legs = collections.Counter()
    for path, shape in sources:
        full = os.path.join(root, path) if root else path
        if not os.path.exists(full):
            continue
        for slug, frames in sorted(QS.load(full, shape).items()):
            t0 = float(frames[0]["t"])
            for i in range(len(frames) - 1):
                tt = QS.touches(frames[i])
                if tt is None:
                    continue
                bid, ask = tt
                t_in = float(frames[i]["t"]) - t0
                for aname, arm, frac in arms:
                    # WHICH LEG IS UNMATCHED. The entry is the deployed
                    # simultaneous depth-2 pair; a leg is unmatched when it
                    # filled and its sibling did not.
                    u_own, d_own = own(UP, bid, ask, 2), own(DOWN, bid, ask, 2)
                    if u_own <= 0 or d_own <= 0:
                        continue
                    uf, _ = _fill(UP, u_own, frames, i, arm, frac)
                    df, _ = _fill(DOWN, d_own, frames, i, arm, frac)
                    if uf is None or df is None:
                        continue
                    if uf == df:
                        continue          # matched, or nothing filled
                    held_side = UP if uf else DOWN
                    held_own = u_own if uf else d_own
                    need = DOWN if uf else UP
                    legs[aname] += 1
                    opts = options(need, held_own, frames, i, bid, ask,
                                   arm, frac, t_in)
                    if not opts:
                        continue
                    ctrl = controller_choice(opts)
                    b = best(opts, allow_taker=False)
                    bt = best(opts, allow_taker=True)
                    tally[aname][b["option"]] += 1
                    agree[aname]["controller:" + ctrl] += 1
                    # does the controller's SHAPE reach the best option?
                    reach = {"COMPLETE": ("4_complement_at_touch",),
                             "REPRICE": tuple(
                                 o["option"] for o in opts
                                 if o["depth"] not in (None, 0)
                                 and not o["taker"]),
                             "HOLD": ("6_hold_to_settlement",)}[ctrl]
                    agree[aname]["agrees" if b["option"] in reach
                                 else "diverges"] += 1
                    for o in opts:
                        ev[aname][o["option"]] += D(o["ev_per_contract"])
                    ev[aname]["BEST_maker_only"] += D(b["ev_per_contract"])
                    ev[aname]["BEST_with_taker"] += D(bt["ev_per_contract"])
    return {"unmatched_legs": dict(legs),
            "argmax_counts": {k: dict(v) for k, v in tally.items()},
            "controller_agreement": {k: dict(v) for k, v in agree.items()},
            "ev_totals": {k: {kk: str(vv) for kk, vv in v.items()}
                          for k, v in ev.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--group", choices=("oos", "in", "both"), default="oos")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    src = {"oos": QS.OUT_OF_SAMPLE, "in": QS.IN_SAMPLE,
           "both": list(QS.OUT_OF_SAMPLE) + list(QS.IN_SAMPLE)}[a.group]
    rep = run(src, root=a.root)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    for arm in sorted(rep["unmatched_legs"]):
        n = rep["unmatched_legs"][arm]
        print("\n=== arm %s -- %d unmatched legs ===" % (arm, n))
        if not n:
            continue
        print("  mean EV per contract, by disposition:")
        tot = rep["ev_totals"][arm]
        for k in sorted(tot, key=lambda k: -float(tot[k])):
            print("    %-34s %+10.6f" % (k, float(tot[k]) / n))
        print("  argmax (maker only):")
        for k, v in sorted(rep["argmax_counts"][arm].items(),
                           key=lambda kv: -kv[1]):
            print("    %-34s %5d  (%.1f%%)" % (k, v, 100.0 * v / n))
        ag = rep["controller_agreement"][arm]
        print("  controller: %s" % {k: v for k, v in sorted(ag.items())})
        if ag.get("agrees") is not None:
            print("    reaches the best maker option in %.1f%% of legs"
                  % (100.0 * ag.get("agrees", 0) / n))
    print("\nREBATES ARE NOT ADDED ANYWHERE ABOVE. At the deployed clip of "
          "1 contract the confirmed floor is %s contracts and the credited "
          "rebate is $0.00." % REBATE_UNEARNABLE_BELOW_QTY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
