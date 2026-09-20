"""TERMINAL SHADOW ECONOMICS: what an unresolved first leg was actually worth.

WHY THIS IS THE ONLY TERM THAT MATTERS. The candidate loop's expectancy is

    E_cycle = P(complete) x completed_pair_net
            + P(residual) x mean_residual_terminal_net

and the two terms are three orders of magnitude apart in scale. A completed
pair nets a fraction of a cent; an unresolved leg carries $0.20-$0.80 of
directional exposure to a binary outcome. Nothing else in the calculation can
set the sign, so this module computes the second term and the break-even the
first one implies, and reports UNCERTAINTY rather than a point estimate.

STRICTLY SHADOW. Every input is a HYPOTHETICAL fill from `HypotheticalBook`
walked against the public tape. The only authoritative fact here is the
venue's own published `resolutionSide`, which says what the CONTRACT did, not
what our account received -- because our account held nothing. A terminal
shadow payoff is NEVER realized, never settled, and never a cost basis, and
the labels on every row say so.

FEES ARE THE PUBLISHED SCHEDULE, not an estimate: a maker fill pays
`0.0175 x C x P x (1 - P)`. REBATES ARE EXCLUDED ENTIRELY. They are additive
economics that become cash only above a daily rounding floor, and the question
here is whether the trade stands up without them.
"""
from __future__ import annotations

import math
import random
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Gemini's published maker fee rate. The fee is quadratic in price.
MAKER_RATE = D("0.0175")

SCHEMA = "gemini_econ_terminal/v1"

LABELS = (
    "SHADOW TERMINAL PAYOFF. The fill is hypothetical and the position never "
    "existed; only the contract's resolutionSide is authoritative. This is "
    "NEVER realized, settled, submitted or a cost basis.",
    "Rebates and rewards are EXCLUDED. The question is whether the loop "
    "stands up on trading economics alone.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def maker_fee(qty: Any, price: Any) -> Optional[D]:
    """The venue's published maker fee for one fill."""
    q, p = _dec(qty), _dec(price)
    if q is None or p is None:
        return None
    return MAKER_RATE * q * p * (D("1") - p)


def residual_terminal(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """One unresolved first leg, carried to the contract's published outcome.

    Returns None for anything that is not a settled residual: a matched pair
    has no directional leg left, a sequence that never filled has no position,
    and an unlabelled one has no outcome. None of those are given a zero.
    """
    if not row.get("first_fill") or row.get("matched"):
        return None
    leg = str(row.get("first_fill_leg") or "").upper()
    won_leg = str(row.get("settlement_leg") or "").upper()
    if leg not in ("YES", "NO") or won_leg not in ("YES", "NO"):
        return None
    px = _dec(row.get("first_fill_price"))
    qty = _dec(row.get("residual_qty"))
    if qty is None or qty <= 0:
        # the sequence recorded no residual quantity; fall back to the fill's
        # own size rather than inventing one, and say which was used
        qty = _dec(row.get("first_fill_qty")) or D("1")
        qty_source = "first_fill_qty"
    else:
        qty_source = "residual_qty"
    if px is None:
        return None

    won = leg == won_leg
    entry_cost = qty * px
    payout = qty * (D("1") if won else D("0"))
    fee = maker_fee(qty, px) or D("0")
    net = payout - entry_cost - fee
    bt0 = row.get("book_t0") or {}
    return {
        "schema": SCHEMA,
        "symbol": row.get("symbol"), "cohort": row.get("cohort"),
        "seq_id": row.get("seq_id"), "arm": row.get("arm"),
        # ---- the position
        "side": leg,
        "entry_price": str(px),
        "qty": str(qty), "qty_source": qty_source,
        "entry_cost_usd": str(entry_cost),
        # ---- the outcome
        "resolution_side": won_leg,
        "won": won,
        "terminal_payout_usd": str(payout),
        "fees_usd": str(fee),
        "residual_net_usd": str(net),
        "net_per_contract_usd": str(net / qty) if qty else None,
        # ---- the state it was entered from
        "tte_s": row.get("tte_s"),
        "t_candidate": row.get("t_candidate"),
        "queue_known": row.get("first_fill_queue_known"),
        "spread_at_candidate": bt0.get("spread"),
        "bid_sum_at_candidate": bt0.get("bid_sum"),
        # ---- and what the complement did
        "complement_price_appeared": bool(row.get("complement_price_appeared")),
        "time_to_complement_price_s": row.get("time_to_complement_price_s"),
        "best_complement_price": row.get("best_complement_price"),
        "complement_would_have_filled": bool(row.get("complement_filled")),
        "markout_60s_ticks": (row.get("markout_ticks_by_horizon_s")
                              or {}).get("60"),
        "labels": list(LABELS),
    }


# --------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.96) -> Optional[Dict[str, float]]:
    """Wilson score interval. Correct at the small n we actually have, where
    the normal approximation is not -- and it never produces a bound outside
    [0, 1], which a completion rate cannot be."""
    if n <= 0:
        return None
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"p": p, "lo": max(0.0, centre - half),
            "hi": min(1.0, centre + half), "n": n, "k": k, "z": z}


def breakeven_residual(p_complete: float, pair_net: float
                       ) -> Optional[Dict[str, Any]]:
    """The mean residual net per contract at which E_cycle is exactly zero.

        E = p * pair_net + (1 - p) * r = 0   =>   r* = -p * pair_net / (1 - p)

    A residual MORE favourable than r* makes the loop positive. Because
    pair_net is small and positive, r* is a small NEGATIVE number: the
    residual is allowed to lose, but only a little.
    """
    if p_complete is None or pair_net is None:
        return None
    if p_complete >= 1.0:
        return {"computable": False,
                "why": "completion is 1.0; there is no residual term to "
                       "break even against"}
    r = -(p_complete * pair_net) / (1.0 - p_complete)
    return {"computable": True,
            "breakeven_residual_net_per_contract_usd": r,
            "p_complete": p_complete, "pair_net_usd": pair_net,
            "reading": ("the residual may lose up to %.8f per contract on "
                        "average; any worse and the loop is negative" % (-r,)),
            "formula": "r* = -p * pair_net / (1 - p)"}


def _bootstrap(units: Sequence[float], *, iters: int = 4000,
               seed: int = 20260920) -> Optional[Dict[str, float]]:
    """Percentile CI on a mean by resampling whole first-fill SEQUENCES.

    Resampling the sequence rather than the two terms separately keeps the
    completion outcome and the residual payoff attached to each other, which
    is how they actually arrive.
    """
    n = len(units)
    if n < 2:
        return None
    rnd = random.Random(seed)
    means: List[float] = []
    for _ in range(iters):
        means.append(sum(units[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return {"lo": means[int(0.025 * iters)], "hi": means[int(0.975 * iters)],
            "iters": iters, "seed": seed}


def expectancy(rows: Sequence[Mapping[str, Any]], *,
               leg: Optional[str] = None) -> Dict[str, Any]:
    """E_cycle over the first fills that have a KNOWN outcome, with its CI.

    THE DENOMINATOR IS DELIBERATELY NARROW. A first fill whose contract has
    not been labelled yet has no outcome, so it is not in the mean and it is
    not a zero either -- it is counted separately as `unresolved`. Reporting
    a cohort with the unresolved rows silently dropped from the numerator and
    kept in the denominator is how a loop talks itself into looking positive.
    """
    first = [r for r in rows if r.get("first_fill")]
    if leg:
        first = [r for r in first
                 if str(r.get("first_fill_leg") or "").upper() == leg.upper()]

    matched = [r for r in first if r.get("matched")]
    resid = [r for r in first if not r.get("matched")]
    settled = [t for t in (residual_terminal(r) for r in resid) if t]
    unresolved = len(resid) - len(settled)

    # PER CONTRACT, ALWAYS. `net_pair_economics` is an absolute dollar figure
    # at whatever quantity the pair was struck in -- 0.01 in the shadow -- and
    # the residual term below is per contract. Averaging the two without
    # normalising would understate the pair term by a factor of a hundred and
    # make the break-even look four times harder than it is. The unit of this
    # whole module is ONE CONTRACT.
    pair_nets = []
    for r in matched:
        pe = r.get("pair_economics") or {}
        v = _dec(pe.get("net_pair_economics"))
        q = _dec(pe.get("matched_qty"))
        if v is None:
            continue
        if q is None or q <= 0:
            # no quantity published with the economics: the figure cannot be
            # put on a per-contract footing and is skipped rather than
            # silently treated as though it were already per contract
            continue
        pair_nets.append(float(v / q))
    resid_nets = [float(t["net_per_contract_usd"]) for t in settled
                  if t.get("net_per_contract_usd") is not None]

    known = len(matched) + len(settled)
    out: Dict[str, Any] = {
        "schema": SCHEMA, "leg": (leg or "ALL"),
        "unit": "USD per ONE contract, on the pair term and the residual "
                "term alike",
        "first_fills": len(first),
        "matched": len(matched), "residual": len(resid),
        "residual_settled": len(settled), "residual_unresolved": unresolved,
        "outcome_known": known,
        "completion_rate": wilson(len(matched), known) if known else None,
        "completed_pair_net_per_contract_mean_usd": (
            sum(pair_nets) / len(pair_nets) if pair_nets else None),
        "pair_rows_without_a_quantity": (len(matched) - len(pair_nets)),
        "residual_terminal_net_per_contract_mean_usd": (
            sum(resid_nets) / len(resid_nets) if resid_nets else None),
        "residual_win_rate": wilson(sum(1 for t in settled if t["won"]),
                                    len(settled)) if settled else None,
        "e_cycle_usd": None, "e_cycle_ci": None,
        "breakeven": None,
        "labels": list(LABELS),
        "why_incomplete": None,
    }
    if not known:
        out["why_incomplete"] = ("no first fill on this leg has a known "
                                 "outcome yet; E_cycle is UNCOMPUTABLE, not "
                                 "zero")
        return out

    # per-sequence value: a matched pair contributes its pair net, a settled
    # residual its terminal net per contract. Unresolved rows contribute
    # NOTHING and are not in n.
    units = ([float(x) for x in pair_nets]
             + [float(x) for x in resid_nets])
    if units:
        out["e_cycle_usd"] = sum(units) / len(units)
        out["e_cycle_ci"] = _bootstrap(units)
    p = out["completion_rate"]["p"] if out["completion_rate"] else None
    pn = out["completed_pair_net_per_contract_mean_usd"]
    if p is not None and pn is not None:
        out["breakeven"] = breakeven_residual(p, pn)
    if unresolved:
        out["why_incomplete"] = (
            "%d residual first fill(s) have no confirmed settlement yet and "
            "are EXCLUDED from every mean above" % unresolved)
    return out


def checkpoint(rows: Sequence[Mapping[str, Any]], *, target: int,
               leg: Optional[str] = None) -> Dict[str, Any]:
    """At `target` confirmed residual settlements: sign, uncertainty, and what
    the rest of the sample would have to do to flip it."""
    e = expectancy(rows, leg=leg)
    n = e["residual_settled"]
    out: Dict[str, Any] = {"schema": SCHEMA, "target": target, "leg": e["leg"],
                           "settled_so_far": n, "reached": n >= target,
                           "expectancy": e}
    ci = e.get("e_cycle_ci")
    ev = e.get("e_cycle_usd")
    out["sign"] = (None if ev is None else
                   ("POSITIVE" if ev > 0 else
                    "NEGATIVE" if ev < 0 else "ZERO"))
    out["sign_is_certain_at_95"] = (
        None if not ci else (ci["lo"] > 0 or ci["hi"] < 0))
    be = e.get("breakeven") or {}
    r_star = be.get("breakeven_residual_net_per_contract_usd")
    mean_r = e.get("residual_terminal_net_per_contract_mean_usd")
    if r_star is not None and mean_r is not None and n < target:
        # what mean the REMAINING (target - n) residuals must carry for the
        # whole target-sized sample to sit exactly at break-even
        need = (r_star * target - mean_r * n) / (target - n)
        out["flip_requires"] = {
            "remaining": target - n,
            "mean_residual_net_per_contract_needed_usd": need,
            "currently": mean_r, "breakeven": r_star,
            "reading": ("the remaining %d settlements would have to average "
                        "%.8f per contract to reach break-even at n=%d"
                        % (target - n, need, target))}
    elif r_star is not None and mean_r is not None:
        out["flip_requires"] = {
            "remaining": 0, "currently": mean_r, "breakeven": r_star,
            "reading": ("the target is reached; the cohort is %s break-even"
                        % ("above" if mean_r > r_star else "below"))}
    return out
