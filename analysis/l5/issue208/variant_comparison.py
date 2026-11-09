"""A / B / C / D on identical windows. Does any variant make money?

ISSUE #206 item 5, and its promotion rule: "require matched-pair
economics... explicitly fail promotion if edge unproven" and "If every
variant loses money, report NO PROMOTION. Do not disguise negative
economics with assumed rebates."

THE FOUR VARIANTS, all on the SAME frames of the SAME windows:

  A  SEQUENTIAL AT THE TOUCH. One leg rests at the touch, the complement
     is sought afterwards at the touch -- the shape ROUTE B's rescue path
     uses (`book_completion_depth_ticks` 0).

     NOT "current deployed", and I mislabelled it that way first. The
     deployed ENTRY is already simultaneous at depth 2
     (`dual_quote_enabled_now` true, `dual_max_placements_per_pass` 2,
     entry depth 2), i.e. variant B. A is what the engine falls back to
     when it rescues a leg at the touch, and the headline result is that
     this shape completes ZERO pairs.
  B  SIMULTANEOUS at depth 2, both legs quoted from one frame, held.
  C  SIMULTANEOUS at depth 2 plus INVENTORY-AWARE completion: when one leg
     fills alone, the complement is quoted at the depth that is actually
     admissible, and held to the completion cutoff.
  D  C plus a BOUNDED TAKER completion on the stranded leg. RESEARCH ONLY
     -- taker execution is disabled in production and this variant exists
     to price what we are giving up by refusing it.

HOW A FILL IS DECIDED. `queue_scenarios.leg_outcome` and its PROVEN /
POSSIBLE verdicts, unchanged. PROVEN is a queue-independent LOWER bound:
the trade volume at our own price exceeded the depth that was ahead of us
when we joined. No look-ahead, and a touch is never a fill.

WHAT IS COUNTED. Matched-pair economics, residual carry at the measured
-0.0236/contract, taker fees from the venue schedule, and the CONFIRMED
CREDITED maker rebate of 0.00727/fill -- which is reported SEPARATELY so
that no variant can be made to look profitable by it.

NOTHING HERE IS A FILL, A PAIR OR A PROFIT. Historical replay only.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from analysis.l5.issue208 import queue_scenarios as QS   # noqa: E402

TICK = QS.TICK
UP, DOWN = QS.UP, QS.DOWN

#: measured residual carry per unmatched contract, settled.
RESIDUAL = QS.RESIDUAL_PER_CONTRACT
#: CONFIRMED CREDITED maker rebate, $2.13 over 293 fills.
REBATE = D("0.00727")
#: the deployed fee-safe pair ceiling.
PAIR_CAP = D("0.96")
#: the deployed first-leg own-price band.
BAND_LO, BAND_HI = D("0.30"), D("0.60")


def _own(side: str, bid: D, ask: D, depth: int) -> D:
    return (bid if side == UP else (D(1) - ask)) - depth * TICK


def in_band(p: D) -> bool:
    return BAND_LO <= p <= BAND_HI


def band_refusal(up_own: D, dn_own: D, floor: Optional[D] = None
                 ) -> Optional[str]:
    """What the DEPLOYED gate does to a two-legged plan, and nothing more.

    A CORRECTION OF THIS FILE'S FIRST VERSION. B, C and D required BOTH
    legs in the band, which refused entries production would have taken.
    `l5_live_execution` calls `TS.first_leg_entry_refusal` exactly once per
    `handle`, on `nxt` -- and `nxt` is the CHEAPER leg, because `order` is
    `sorted(legs, key=price)` and nothing is claimed on a first leg. So the
    dear leg is never tested at entry, and the ceiling cannot bind a
    two-legged plan at all: the cheaper of two legs summing to <= 0.96 is
    at most 0.48.

    Only the FLOOR can refuse such a plan, and it refuses exactly when the
    book sits far enough from the middle that the cheap side is under 0.30.
    """
    cheap = min(up_own, dn_own)
    lo = BAND_LO if floor is None else floor
    if lo > 0 and cheap < lo:
        return "first_leg_below_min"
    if BAND_HI > 0 and cheap > BAND_HI:
        return "first_leg_above_max"
    return None


def _fill(side, own, frames, i, frac, arm, ttl=None):
    o = QS.leg_outcome(side, own, frames, start=i, queue_fraction=frac,
                       own_qty=D(1), ttl_s=ttl)
    return QS.filled(o, arm=arm), o


def variant_A(frames, i, bid, ask, frac, arm):
    """CURRENT: one leg at the TOUCH, complement sought after.

    Depth 0, so the pair is 1 - spread and the completion is sequential.
    The cheaper side opens, which is what the deployed path prefers.
    """
    up_own, dn_own = _own(UP, bid, ask, 0), _own(DOWN, bid, ask, 0)
    if up_own <= 0 or dn_own <= 0:
        return None
    first, f_own = ((UP, up_own) if up_own <= dn_own else (DOWN, dn_own))
    if not in_band(f_own):
        return {"state": "NO_ENTRY", "why": "first_leg_band"}
    ok, _ = _fill(first, f_own, frames, i, frac, arm)
    if ok is None:
        return None
    if not ok:
        return {"state": "NO_FILL"}
    # the complement, sought from the SAME frame onward at the touch
    other = DOWN if first == UP else UP
    o_own = dn_own if first == UP else up_own
    pair = f_own + o_own
    if pair > PAIR_CAP:
        return {"state": "RESIDUAL", "legs": 1, "pair": pair,
                "why": "pair_above_cap"}
    ok2, _ = _fill(other, o_own, frames, i, frac, arm)
    if ok2:
        return {"state": "PAIR", "legs": 2, "pair": pair}
    return {"state": "RESIDUAL", "legs": 1, "pair": pair}


def variant_B(frames, i, bid, ask, frac, arm, depth=2,
              floor_waiver=False, floor=None):
    """SIMULTANEOUS at depth 2, both legs from one frame, held."""
    up_own, dn_own = _own(UP, bid, ask, depth), _own(DOWN, bid, ask, depth)
    if up_own <= 0 or dn_own <= 0:
        return None
    pair = up_own + dn_own
    if pair > PAIR_CAP:
        return {"state": "NO_ENTRY", "why": "pair_above_cap"}
    _no = band_refusal(up_own, dn_own, floor=floor)
    # THE WAIVER UNDER TEST, and it is tested only AFTER the cap. The
    # floor's own stated premise is "a complement the 0.96 pair cap cannot
    # reach once the cheap side gets cheaper". On a two-legged plan whose
    # pair ALREADY clears the cap that premise is false by construction --
    # the dear complement is priced, admissible and submitted. Whether the
    # waiver pays is an empirical question about how often the dear leg
    # fills, which is what this variant measures.
    if _no is not None and not (floor_waiver and _no == "first_leg_below_min"):
        return {"state": "NO_ENTRY", "why": _no}
    uf, _ = _fill(UP, up_own, frames, i, frac, arm)
    df, _ = _fill(DOWN, dn_own, frames, i, frac, arm)
    if uf is None or df is None:
        return None
    if uf and df:
        return {"state": "PAIR", "legs": 2, "pair": pair}
    if uf or df:
        return {"state": "RESIDUAL", "legs": 1, "pair": pair}
    return {"state": "NO_FILL"}


def variant_C(frames, i, bid, ask, frac, arm, depth=2,
              floor_waiver=False, floor=None):
    """B plus INVENTORY-AWARE completion of a stranded leg.

    When one leg fills alone, requote the complement at the SHALLOWEST
    depth whose pair still clears the cap -- which is the decision the
    deployed controller makes one depth at a time -- and hold it.
    """
    base = variant_B(frames, i, bid, ask, frac, arm, depth=depth,
                     floor_waiver=floor_waiver, floor=floor)
    if base is None or base.get("state") != "RESIDUAL":
        return base
    up_own, dn_own = _own(UP, bid, ask, depth), _own(DOWN, bid, ask, depth)
    uf, _ = _fill(UP, up_own, frames, i, frac, arm)
    held, need = ((UP, DOWN) if uf else (DOWN, UP))
    h_own = up_own if held == UP else dn_own
    # walk OUTWARD from the touch: the shallowest admissible complement
    for d2 in range(0, 6):
        n_own = _own(need, bid, ask, d2)
        if n_own <= 0:
            continue
        if h_own + n_own > PAIR_CAP:
            continue
        ok, _ = _fill(need, n_own, frames, i, frac, arm)
        if ok:
            return {"state": "PAIR", "legs": 2, "pair": h_own + n_own,
                    "completed_at_depth": d2}
    return dict(base, why="no_admissible_complement_filled")


def variant_D(frames, i, bid, ask, frac, arm, depth=2):
    """C plus a BOUNDED TAKER completion. RESEARCH ONLY.

    A stranded leg is completed by CROSSING at the displayed touch, paying
    the taker fee, only if the resulting pair still clears the cap.
    """
    base = variant_C(frames, i, bid, ask, frac, arm, depth=depth)
    if base is None or base.get("state") != "RESIDUAL":
        return base
    up_own, dn_own = _own(UP, bid, ask, depth), _own(DOWN, bid, ask, depth)
    uf, _ = _fill(UP, up_own, frames, i, frac, arm)
    held = UP if uf else DOWN
    h_own = up_own if held == UP else dn_own
    # crossing pays the far side of the book, at depth 0 and no better
    x_own = _own(DOWN if held == UP else UP, bid, ask, 0)
    fee = QS.taker_fee_per_contract(x_own) if hasattr(
        QS, "taker_fee_per_contract") else D("0.0325")
    if x_own <= 0 or (h_own + x_own + fee) > PAIR_CAP:
        return dict(base, why="taker_pair_above_cap")
    return {"state": "PAIR", "legs": 2, "pair": h_own + x_own + fee,
            "crossed": True}


def variant_E(frames, i, bid, ask, frac, arm, depth=2):
    """C, plus the FLOOR WAIVER on a plan whose pair already clears the cap.

    This is the only variant that admits an entry the deployed gate
    refuses, and it admits exactly one class of them: a two-legged plan
    whose cheaper leg is under 0.30 while the PAIR is at or under 0.96.
    The ceiling is untouched (it cannot bind a two-legged plan anyway),
    and a strand is charged the full measured residual carry.
    """
    return variant_C(frames, i, bid, ask, frac, arm, depth=depth,
                     floor_waiver=True)


#: THE CODE'S OWN DEFAULT FLOOR. `MIN_FIRST_LEG_OWN_PRICE` is 0.20; the
#: deployed Railway value is 0.30. On L5's own 2,216-window history the
#: 0.2-0.3 bucket completes 80.7% (n=109) against 80.3% for 0.3-0.4
#: (n=142) -- and against 76.2% for 0.6-0.7, which the band PERMITS.
#: Completion only degrades below 0.20 (67.5%) and collapses below 0.10
#: (28.4%). The six 2026-10-23 windows the 0.30 value cites opened at 0.12
#: and 0.06, which are below 0.20, so they do not distinguish the two.
CODE_DEFAULT_FLOOR = D("0.20")


def variant_F(frames, i, bid, ask, frac, arm, depth=2):
    """C at the CODE's floor of 0.20 rather than the deployed 0.30.

    A narrower change than E: it still refuses the sub-0.20 legs whose
    completion rate really does fall off, and admits only the 0.2-0.3
    band that the history says completes as well as 0.3-0.4.
    """
    return variant_C(frames, i, bid, ask, frac, arm, depth=depth,
                     floor=CODE_DEFAULT_FLOOR)


VARIANTS = (("A_sequential_at_touch", variant_A),
            ("B_simultaneous_d2", variant_B),
            ("C_inventory_aware_completion", variant_C),
            ("D_bounded_taker_RESEARCH", variant_D),
            ("E_C_plus_floor_waiver", variant_E),
            ("F_C_at_code_floor_0.20", variant_F))


def score(res: Mapping[str, Any]) -> Tuple[D, D, D, int]:
    """matched, residual, rebate, fills -- kept apart, never summed here."""
    st = res.get("state")
    if st == "PAIR":
        return (D(1) - D(str(res["pair"]))), D(0), 2 * REBATE, 2
    if st == "RESIDUAL":
        return D(0), RESIDUAL, REBATE, 1
    return D(0), D(0), D(0), 0


def run(sources, *, arms=QS.SCENARIOS, root="") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    agg = collections.defaultdict(lambda: collections.Counter())
    money = collections.defaultdict(lambda: [D(0), D(0), D(0), 0])
    windows = collections.defaultdict(set)
    for path, shape in sources:
        full = os.path.join(root, path) if root else path
        if not os.path.exists(full):
            continue
        for slug, frames in sorted(QS.load(full, shape).items()):
            for i in range(len(frames) - 1):
                tt = QS.touches(frames[i])
                if tt is None:
                    continue
                bid, ask = tt
                for aname, arm, frac in arms:
                    for vname, fn in VARIANTS:
                        r = fn(frames, i, bid, ask, frac, arm)
                        if r is None:
                            continue
                        key = (vname, aname)
                        agg[key][r["state"]] += 1
                        if r.get("why"):
                            agg[key]["why:" + r["why"]] += 1
                        m, c, b, f = score(r)
                        mm = money[key]
                        mm[0] += m
                        mm[1] += c
                        mm[2] += b
                        mm[3] += f
                        windows[key].add(slug)
    for key in sorted(agg):
        vname, aname = key
        cnt = agg[key]
        n = sum(cnt[s] for s in ("PAIR", "RESIDUAL", "NO_FILL", "NO_ENTRY"))
        if not n:
            continue
        m, c, b, f = money[key]
        q = D(n)
        out.append({
            "variant": vname, "arm": aname, "entries": n,
            "windows": len(windows[key]),
            "pairs": cnt["PAIR"], "residual": cnt["RESIDUAL"],
            "no_fill": cnt["NO_FILL"], "no_entry": cnt["NO_ENTRY"],
            "p_pair": str((D(cnt["PAIR"]) / q).quantize(D("0.0001"))),
            "p_residual": str((D(cnt["RESIDUAL"]) / q).quantize(D("0.0001"))),
            "matched_per_entry": str((m / q).quantize(D("0.000001"))),
            "residual_per_entry": str((c / q).quantize(D("0.000001"))),
            "net_ex_rebate_per_entry": str(((m + c) / q)
                                           .quantize(D("0.000001"))),
            "rebate_per_entry": str((b / q).quantize(D("0.000001"))),
            "net_inc_rebate_per_entry": str(((m + c + b) / q)
                                            .quantize(D("0.000001"))),
            "fills": f,
            "why": {k[4:]: v for k, v in cnt.items() if k.startswith("why:")},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--group", choices=("oos", "in", "both"), default="oos")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    src = {"oos": QS.OUT_OF_SAMPLE, "in": QS.IN_SAMPLE,
           "both": list(QS.OUT_OF_SAMPLE) + list(QS.IN_SAMPLE)}[a.group]
    rows = run(src, root=a.root)
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    print("%-30s %-12s %6s %4s %8s %8s %12s %10s %12s" % (
        "variant", "arm", "n", "win", "P(pair)", "P(res)",
        "net_ex_reb", "rebate", "net_inc_reb"))
    for r in rows:
        print("%-30s %-12s %6d %4d %8s %8s %12s %10s %12s" % (
            r["variant"], r["arm"], r["entries"], r["windows"],
            r["p_pair"], r["p_residual"], r["net_ex_rebate_per_entry"],
            r["rebate_per_entry"], r["net_inc_rebate_per_entry"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
