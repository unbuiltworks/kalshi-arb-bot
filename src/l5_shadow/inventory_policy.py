"""A continuous desired-inventory quoting process. BTC15m. MODEL_CANDIDATE.

WHAT CHANGES HERE. Every previous arm waited for L5 to fill before it had an
opinion, so the complement could only ever be quoted after his second fill was
published -- and 54.3% of his complements are already done inside our 15.16s
observation lag. This does not wait. It maps

    market state -> desired UP inventory
                 -> desired DOWN inventory
                 -> acceptable resting price levels on each side

so both sides may have eligible resting orders BEFORE either fills. That is the
only shape in which the measured mechanism can be reproduced at all: BTC15m L5
is 95.5% maker over 121 fills a window, and a maker is hit, not filled on
demand.

IT IS A CANDIDATE, NOT L5. Nothing here is claimed to be his policy. It is the
falsifiable form of the continuous-inventory hypothesis, and the three arms
exist so that it can lose.

    R  REACTIVE        quote only the side L5 just filled, after he filled it
    S  SIMULTANEOUS    both sides, now, at the current maker level
    D  DEEP            both sides, now, on a predetermined deeper grid

D IS WHY THE PAIR COST MIGHT WORK. L5 achieves 0.9624 while the live touch sum
is 0.9930 -- he earns the spread instead of paying it. Joining the touch cannot
produce that. Resting deeper can, at the cost of a lower fill probability, and
the grid is fixed in advance (bid, -1c, -2c, -3c, -4c) so no level is ever
chosen after seeing what filled.

WHAT IT MAY NOT DO, and these are checked rather than promised: it never
crosses the spread, never reprices a resting quote, never widens past the
executable ceiling to manufacture a pair, and never places a level chosen with
hindsight.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.l5_shadow import maker as MK
from src.l5_shadow import two_sided as TS

LABEL = "MODEL_CANDIDATE/continuous_desired_inventory"
ARMS = ("R", "S", "D")
# FROZEN before any scoring. Ticks below the touch that arm D will rest at.
DEEP_GRID_TICKS = (0, 1, 2, 3, 4)
# L5 BTC15m is in within ~13s of the window opening (TTE median 887 of 900) and
# two-sided within 26s. Quoting only near expiry would be a different policy.
OPEN_FROM_TTE_S = 900.0
# Gemini withdraws in the final minute on both cohorts, measured earlier, and a
# quote resting into that is a quote we know will not be hit on fair terms.
CANCEL_UNDER_TTE_S = 60.0
# how much inventory the candidate wants on EACH side of a window
DESIRED_PER_SIDE = D("10")


def desired_inventory(state: Dict[str, Any]) -> Dict[str, Any]:
    """Market state -> what we want to hold on each side, and where to rest.

    Deliberately simple and stated in full, because a candidate nobody can
    restate is a candidate nobody can refute. The target is symmetric: a
    market maker wants both sides, and any asymmetry here would be a
    directional view we have no evidence for.
    """
    tte = float(state.get("tte_s") or 0)
    held_up = D(str(state.get("inv_up") or "0"))
    held_dn = D(str(state.get("inv_dn") or "0"))
    live = OPEN_FROM_TTE_S >= tte > CANCEL_UNDER_TTE_S
    return {
        "label": LABEL,
        "quoting": live,
        "why_not": (None if live else
                    ("before the window opens" if tte > OPEN_FROM_TTE_S else
                     f"inside the final {CANCEL_UNDER_TTE_S:.0f}s, where the "
                     f"venue withdraws and a resting quote is not hit on fair "
                     f"terms")),
        "desired_up": str(DESIRED_PER_SIDE),
        "desired_dn": str(DESIRED_PER_SIDE),
        "want_up": str(max(D("0"), DESIRED_PER_SIDE - held_up)),
        "want_dn": str(max(D("0"), DESIRED_PER_SIDE - held_dn)),
        "tte_s": tte,
    }


def levels(rest: Optional[Sequence], *, tick: D, arm: str,
           grid: Sequence[int] = DEEP_GRID_TICKS) -> List[Dict[str, Any]]:
    """Acceptable resting prices on one side, for one arm.

    `rest` is the BID ladder. None means the book was unreadable -- UNKNOWN,
    never an empty side, and no level is invented for it.
    """
    if rest is None or not rest:
        return []
    best = D(str(rest[0][0]))
    ticks = (0,) if arm in ("R", "S") else tuple(grid)
    out = []
    for t in ticks:
        px = best - (D(t) * D(str(tick)))
        if px <= 0:
            continue
        out.append({"ticks_below_touch": t, "price": str(px),
                    "queue_ahead": str(MK.queue_ahead(rest, px))})
    return out


def plan(state: Dict[str, Any], *, arm: str, terms,
         up_rest: Optional[Sequence], dn_rest: Optional[Sequence],
         now: float,
         ceiling: D = TS.ADAPTED_EXECUTABLE_PAIR_CEILING) -> Dict[str, Any]:
    """One arm's full quote set for one window at one instant.

    The whole frozen ladder rests. Every (up level, dn level) combination is
    PRICED and recorded, but none is selected: which depths actually fill is
    the measurement, and the executable ceiling is applied to the pair that
    really forms rather than to a hypothetical chosen in advance.
    """
    want = desired_inventory(state)
    base = {"arm": arm, "label": LABEL, "planned_at": now,
            "market_key": state.get("market_key"),
            "cohort": state.get("cohort"), "desired": want,
            "adapted_executable_pair_ceiling": str(ceiling)}
    if not want["quoting"]:
        return {**base, "quotes": [], "pairs": [],
                "reason": f"not quoting: {want['why_not']}"}
    if terms.tick is None or terms.maker_fee is None:
        return {**base, "quotes": [], "pairs": [],
                "reason": "venue did not declare tick or maker fee"}
    ups = levels(up_rest, tick=terms.tick, arm=arm)
    dns = levels(dn_rest, tick=terms.tick, arm=arm)
    if not ups or not dns:
        missing = [s for s, v in (("Up", ups), ("Down", dns)) if not v]
        return {**base, "quotes": [], "pairs": [],
                "reason": (f"no bid to rest behind on the "
                           f"{'/'.join(missing)} side: the venue cannot host a "
                           f"two-sided position here")}
    qty = D(want["want_up"])

    # EVERY FROZEN LEVEL RESTS. The grid is TESTED, not searched.
    #
    # Choosing the cheapest admissible pair was an optimisation toward never
    # filling, and a live book showed it immediately: Gemini quoted a 1-cent
    # bid on the Down side of a market worth ~0.38, and "cheapest admissible"
    # duly picked a 0.636 pair whose Down leg could never be hit. A policy that
    # maximises pair quality minimises fill probability by construction.
    #
    # So each level is its own hypothetical order, the whole predetermined
    # ladder rests at once, and WHICH DEPTHS ACTUALLY FILL is the measurement.
    # The executable ceiling then applies to the pair that REALLY forms, at
    # scoring time, which is the only place it means anything.
    quotes = [{"outcome": side, "price": lv["price"], "qty": str(qty),
               "queue_ahead": lv["queue_ahead"],
               "ticks_below_touch": lv["ticks_below_touch"]}
              for side, lvs in (("Up", ups), ("Down", dns))
              for lv in lvs]
    pairs = []
    for u in ups:
        for d in dns:
            cost = (_eff(u, terms, qty) + _eff(d, terms, qty))
            pairs.append({
                "up_price": u["price"], "dn_price": d["price"],
                "up_ticks_below": u["ticks_below_touch"],
                "dn_ticks_below": d["ticks_below_touch"],
                "pair_cost": str(cost.quantize(D("0.000001"))),
                "within_ceiling_if_it_forms": cost <= ceiling,
                **({} if cost <= ceiling else {
                    "research_label": "RESEARCH_INELIGIBLE_FOR_LIVE",
                    "why_recorded": (
                        "priced above the live ceiling and RECORDED ANYWAY. "
                        "Whether it would have filled, and what it would have "
                        "settled at, are the observations that make the "
                        "ceiling judgeable; deleting them would make the "
                        "constraint unfalsifiable")}),
                **TS.pair_economics(cost)})
    inside = [p for p in pairs if p["within_ceiling_if_it_forms"]]
    return {**base, "quotes": quotes, "pairs": pairs,
            "combinations_within_ceiling": len(inside),
            "cheapest_pair_if_all_filled": (
                min((p["pair_cost"] for p in pairs), key=D) if pairs else None),
            "reason": (f"resting the full frozen ladder: {len(ups)} Up level(s) "
                       f"and {len(dns)} Down. {len(inside)} of {len(pairs)} "
                       f"combinations would sit inside the ceiling IF both "
                       f"legs filled -- which is what we are measuring, not "
                       f"assuming")}


def _eff(level: Dict[str, Any], terms, qty: D) -> D:
    px = D(level["price"])
    fee = D(str(terms.maker_fee(px, qty)))
    return px + (fee / qty if qty else D("0"))


def should_cancel(quote: Dict[str, Any], state: Dict[str, Any]) -> Optional[str]:
    """PREDETERMINED cancel rules, not an arbitrary post-L5-fill horizon.

    A quote lives until one of these fires. Nothing cancels because the book
    moved: repricing on movement is how a maker turns into a taker in slow
    motion, and it would also make the deep grid meaningless.
    """
    tte = float(state.get("tte_s") or 0)
    if tte <= CANCEL_UNDER_TTE_S:
        return f"tte fell inside the final {CANCEL_UNDER_TTE_S:.0f}s"
    side = "inv_up" if quote.get("outcome") == "Up" else "inv_dn"
    if D(str(state.get(side) or "0")) >= DESIRED_PER_SIDE:
        return "desired inventory reached on this side"
    if state.get("window_closed"):
        return "window closed"
    return None
