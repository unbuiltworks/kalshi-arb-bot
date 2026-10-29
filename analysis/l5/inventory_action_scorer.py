"""Score OPEN / COMPLEMENT / REDUCE / WAIT in expected net settlement dollars.

NOT a fitted model. Every term is either an identity from the venue's tick
geometry or a named free parameter swept over its plausible range, because
the Stage 1 sample (5 fill-bearing windows) cannot identify any of them. The
point is to find which decisions are ROBUST to the unknowns -- a decision
that flips inside the plausible range is reported as undecided rather than
resolved.

CONDITIONAL, NOT INDEPENDENT, which is the whole reason this file exists.
A resting maker bid d ticks behind the touch fills only when the market comes
DOWN through it. So:

  * P(complete | first leg filled) < P(complete) unconditionally -- the fill
    is itself evidence the book is leaving.
  * q, the true win probability of the leg we are left holding, is BELOW its
    fill price F. F is the market's breakeven probability BEFORE the move
    that filled us; after it, fair value is lower.

Writing them as independent is the error that makes a losing policy look
break-even, so q and P(complete|fill) are the two swept axes.
"""

from __future__ import annotations

from decimal import Decimal as D

TICK = D("0.01")
OPEN, COMPLEMENT, REDUCE, WAIT = "OPEN", "COMPLEMENT", "REDUCE", "WAIT"


def planned_pair(depth_ticks: int, spread_ticks: int = 1) -> D:
    """What the engine's entry test prices: both legs off ONE snapshot."""
    return D(1) - D(spread_ticks) * TICK - 2 * D(depth_ticks) * TICK


def pair_at_fill(depth_ticks: int, spread_ticks: int = 1) -> D:
    """What the fill can actually deliver. An UP bid d ticks under the touch
    fills only when the bid REACHES it; the ask is then one spread above our
    own fill price, not d+spread above it."""
    return D(1) - D(spread_ticks) * TICK - D(depth_ticks) * TICK


def score_open(*, F: D, depth_ticks: int, spread_ticks: int, pair_cap: D,
               p_fill: D, p_complete_given_fill: D, q_given_fill: D,
               fee_per_contract: D = D(0),
               credited_rebate_per_contract: D = D(0)) -> dict:
    """Expected net settlement dollars per contract of a prospective OPEN.

    `q_given_fill` is the win probability of the leg CONDITIONAL on having
    been filled -- not F. Setting it to F asserts no adverse selection.
    """
    pf = pair_at_fill(depth_ticks, spread_ticks)
    # The cap decides whether a complement may even be SUBMITTED. If the
    # achievable pair is outside it, the engine refuses, so completion is
    # impossible by construction and no fill probability can rescue it.
    completable = pf <= pair_cap
    p_c = p_complete_given_fill if completable else D(0)
    # a completed pair redeems exactly $1 per pair whatever the outcome
    pair_value = (D(1) - pf) - fee_per_contract * 2
    # the leg we are left holding: wins $1 with prob q, else nothing
    residual_value = q_given_fill * D(1) - F - fee_per_contract
    ev_if_filled = p_c * pair_value + (D(1) - p_c) * residual_value
    ev = p_fill * (ev_if_filled + credited_rebate_per_contract)
    return {
        "action": OPEN,
        "planned_pair": str(planned_pair(depth_ticks, spread_ticks)),
        "pair_at_fill": str(pf),
        "pair_cap": str(pair_cap),
        "complement_submittable_at_fill": completable,
        "p_complete_effective": str(p_c),
        "pair_value_per_pair": str(pair_value),
        "residual_value_per_contract": str(residual_value),
        "ev_per_contract": ev,
        "basis": ("expected net SETTLEMENT dollars per contract; no "
                  "hypothetical maker fill is counted as certain -- p_fill "
                  "and p_complete_given_fill both multiply through"),
    }


def score_wait() -> dict:
    return {"action": WAIT, "ev_per_contract": D(0),
            "basis": "no position, no fee, no exposure: exactly zero"}


def score_reduce(*, F: D, exit_px: D, q_given_fill: D,
                 taker_fee_per_contract: D) -> dict:
    """Crossing out of a held leg. Reported for completeness; on this venue
    a taker exit realises (exit_px - F) and pays a fee, against holding
    which is worth (q*1 - F)."""
    hold = q_given_fill * D(1) - F
    ex = exit_px - F - taker_fee_per_contract
    return {"action": REDUCE, "hold_value": str(hold), "exit_value": str(ex),
            "ev_per_contract": ex,
            "improves_on_hold": ex > hold,
            "basis": "variance reduction, not edge; fee is paid for certainty"}


def sweep(*, F: D, depth_ticks: int, spread_ticks: int, pair_cap: D,
          p_fill: D = D("1.0")) -> dict:
    """Is OPEN ever better than WAIT across the plausible unknowns?"""
    rows = []
    best = None
    for pc10 in range(0, 11):                 # P(complete | fill): 0.0 .. 1.0
        for adv in range(0, 6):               # adverse haircut on q: 0 .. 25%
            p_c = D(pc10) / D(10)
            q = F * (D(1) - D(adv) * D("0.05"))
            r = score_open(F=F, depth_ticks=depth_ticks,
                           spread_ticks=spread_ticks, pair_cap=pair_cap,
                           p_fill=p_fill, p_complete_given_fill=p_c,
                           q_given_fill=q)
            rows.append((float(p_c), float(q), float(r["ev_per_contract"])))
            if best is None or r["ev_per_contract"] > best[2]:
                best = (float(p_c), float(q), float(r["ev_per_contract"]))
    positive = [r for r in rows if r[2] > 0]
    return {"cells": len(rows), "positive_cells": len(positive),
            "best_cell": {"p_complete": best[0], "q": best[1],
                          "ev_per_contract": best[2]},
            "open_ever_beats_wait": bool(positive),
            "pair_at_fill": str(pair_at_fill(depth_ticks, spread_ticks)),
            "complement_submittable": pair_at_fill(depth_ticks,
                                                   spread_ticks) <= pair_cap}
