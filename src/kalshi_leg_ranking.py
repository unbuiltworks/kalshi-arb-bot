"""Rank Kalshi legs INDIVIDUALLY on the full L5 equation. Never average a
good leg with a terrible one.

    net / $ turnover = pair economics + residual economics - fees
                       + confirmed incentives

The unit of decision is a LEG, not a series. KXCRYPTOLEAD15M is one series
carrying five assets whose books differ by an order of magnitude in cost --
reporting its median hides that BTC and HYPE are not the same business. So
every number below is keyed by leg, and the series roll-up is deliberately
absent.

LEG IDENTITY. For KXCRYPTOLEAD15M the trailing ticker segment is the asset
(BTC/ETH/SOL/XRP/HYPE) and is the leg. For every other series here the
trailing segment is the window's strike or minute -- it changes every 15
minutes and is NOT an identity -- so the series itself is the leg.

--- WHAT THIS MODULE CAN NOW CERTIFY, AND WHAT IT STILL CANNOT ---

FEES: UPGRADED, from venue metadata rather than assumption. GET
/series/{ticker} declares fee_type and fee_multiplier, and ALL EIGHT series
here return fee_type="quadratic", fee_multiplier=1 -- identical to
KXBTC15M/KXETH15M, for which ceil(0.0175*C*P*(1-P)) maker is already
certified. Same declared family, same declared multiplier, therefore the
same schedule. That is a venue-declared derivation, NOT a paid invoice, and
it is labelled FEE_STATUS accordingly.

This matters per-leg because the schedule is CONVEX in price and peaks at
P=0.50. A leg resting at 0.13/0.87 pays materially less fee per contract
than one at 0.50/0.50, so fees are a genuine differentiator between legs
rather than a constant to be subtracted at the end.

SHARE OF SCORE: STILL OPEN, and one attractive route to it is now closed.
`paid_out` on a program row looked like a credited-reward observation. It is
not: it is a SETTLEMENT LAG FLAG. Measured against age since the program
ended -- 0.0% paid under 12h (480 rows), 39.6% at 12-24h, 100.0% at 24-48h
(751 rows) -- and the per-leg "paid rate" comes out at an identical n=147 /
paid=94 for all ten legs, which is the giveaway that it is an artifact of
the age mix and not a property of any leg. Every program eventually pays.
`paid_out` says nothing about whether liquidity qualified or who earned it,
and a tally that stopped at "55% never paid" would have been badly wrong.

So the denominator is reported as a CURVE, not a point: see
band_share_curve(). We do not know Kalshi's proximity rule, so instead of
inventing one band we report what our capital's share would be across every
plausible band, and let the width carry the uncertainty honestly.

RESIDUAL ECONOMICS: UNMEASURED here, and it is the term that sank PMUS
(-6.458%/$ was not a pair-cost problem, it was unpaired inventory). We hold
no fills on Kalshi, so there is no honest residual number for any leg. It is
None, not zero.
"""

import json
import math
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ------------------------------------------------------------ leg identity
# Imported from the COLLECTOR rather than redefined here. The dependency
# points analysis -> deployed and never the reverse: the shadow container's
# Dockerfile copies named files, not all of src/, so a collector that
# imported this module would break on deploy.
from src.kalshi_l5_collector import ASSET_LEG_SERIES, leg_key  # noqa: F401


# ------------------------------------------------------------------- fees
FEE_STATUS = ("DERIVED FROM VENUE METADATA, not from a paid invoice. GET "
              "/series/{ticker} declares fee_type=quadratic and "
              "fee_multiplier=1 for all eight series, identical to the "
              "certified KXBTC15M/KXETH15M pair.")

VENUE_FEE_METADATA = {s: {"fee_type": "quadratic", "fee_multiplier": 1}
                      for s in ("KXBTC15M", "KXETH15M", "KXCRYPTOLEAD15M",
                                "KXGOLD15M", "KXWTI15M", "KXSILVER15M",
                                "KXNATGAS15M", "KXCOPPER15M")}

MAKER_RATE = D("0.0175")


def maker_fee_dollars(contracts: D, price: D) -> D:
    """ceil-to-the-cent maker fee. Convex, peaking at P=0.50, which is why
    it separates legs instead of cancelling out of the comparison."""
    raw = MAKER_RATE * D(contracts) * D(price) * (D(1) - D(price))
    return D(math.ceil(raw * 100)) / D(100)


FEE_IS_ON_FILL_REWARD_IS_ON_REST = (
    "A STRUCTURAL ASYMMETRY THAT THE EQUATION HIDES. The maker fee is "
    "charged per contract EXECUTED. The liquidity reward is scored on "
    "orders RESTING, with no fill required. So the fee term and the "
    "incentive term are not incurred by the same event: an order that "
    "rests and is never hit earns reward and pays nothing. The fee figure "
    "below therefore belongs to the PAIR-MANUFACTURING business (both legs "
    "fill), not to the pure liquidity-provision business, and a leg can "
    "rank differently under the two. We cannot yet price the second "
    "because share of score is unknown.")


def pair_fee_per_dollar(pair_cost: D, yes_bid: D) -> D:
    """Maker fee to MANUFACTURE one pair -- that is, assuming BOTH resting
    legs fill -- per dollar of capital committed. Priced at the two resting
    bids, which is where a maker actually sits. See
    FEE_IS_ON_FILL_REWARD_IS_ON_REST: resting alone costs nothing."""
    n = D(1000)                      # scale up so the ceil is not the whole story
    no_bid = D(2) - D(pair_cost) - D(yes_bid)
    fee = maker_fee_dollars(n, D(yes_bid)) + maker_fee_dollars(n, no_bid)
    capital = n * (D(yes_bid) + no_bid)
    return (fee / capital) if capital > 0 else D(0)


# ------------------------------------------------- the equation, per leg
def drag_per_dollar(pair_cost: D) -> D:
    """Pair economics. Loss per dollar turned over to manufacture a pair
    that pays $1.00. Fee-independent and incentive-independent."""
    pc = D(pair_cost)
    return -(pc - D(1)) / pc


def reward_density(reward_usd: D, target_size: D, pair_cost: D) -> D:
    """HEADLINE pool per dollar of capital needed to meet Target Size on
    both sides, per reward period. Capacity-normalised headline -- it ranks
    legs correctly and is still NOT revenue, because share is unknown."""
    capital = D(target_size) * D(pair_cost)
    return (D(reward_usd) / capital) if capital > 0 else D(0)


def band_share_curve(our_capital: D, pair_cost: D,
                     band_depths: Mapping[str, D]) -> Dict[str, str]:
    """Our share of qualifying liquidity as a FUNCTION of the assumed
    proximity band, since the venue's real band is unknown to us. Reported
    as a curve so the uncertainty is visible instead of buried in one
    invented number."""
    our_contracts = D(our_capital) / D(pair_cost) if pair_cost else D(0)
    out = {}
    for band, competing in band_depths.items():
        denom = D(competing) + our_contracts
        out[band] = str((our_contracts / denom).quantize(D("0.0001"))
                        if denom > 0 else D(0))
    return out


def share_dominance(legs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """A share result that does NOT require knowing the venue's proximity
    rule.

    Kalshi normalises our score against competing qualifying liquidity and
    weights by proximity to the Reference Price. We do not know the
    weighting function. But we do not need it to compare two legs: if leg A
    carries LESS competing depth than leg B at EVERY band width we measure,
    then under ANY weighting that is non-increasing in distance, our fixed
    capital buys a larger share of A than of B. The ordering survives the
    unknown; only the magnitude does not.

    So this returns the legs that are dominated at every band (safe to rank)
    separately from the pairs whose ordering FLIPS between bands, where the
    unknown rule genuinely decides and we must say so."""
    bands = ("1", "2", "5", "10")
    depth = {L["leg"]: {b: D(L["band_min_side_depth_median"][b]) for b in bands}
             for L in legs if L.get("band_min_side_depth_median")}
    names = sorted(depth)
    dominates: Dict[str, List[str]] = {n: [] for n in names}
    ambiguous = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            lt = [depth[a][k] < depth[b][k] for k in bands]
            gt = [depth[a][k] > depth[b][k] for k in bands]
            if all(lt):
                dominates[a].append(b)      # a has less competition everywhere
            elif all(gt):
                dominates[b].append(a)
            elif any(lt) and any(gt):
                ambiguous.append([a, b])
    return {
        "basis": ("less competing near-mid depth at EVERY measured band => "
                  "larger share under ANY non-increasing proximity weighting"),
        "dominates": {k: v for k, v in dominates.items() if v},
        "ordering_depends_on_the_unknown_rule": ambiguous,
        "what_this_does_not_give": ("a share MAGNITUDE. Only the ordering is "
                                    "rule-independent; how much of the pool "
                                    "that ordering is worth is not."),
    }


MIN_PAYOUT_USD = D("1.00")
MIN_PAYOUT_SOURCE = ("Kalshi documentation: sub-$1 individual-program "
                     "payouts are not paid. Relayed by the owner "
                     "2026-09-08 with the reward-unit resolution.")


def payout_floor_test(reward_usd: D, our_capital: D, pair_cost: D,
                      competing_depth: Mapping[str, D]) -> Dict[str, Any]:
    """THE $1 FLOOR IS WHAT MAKES THE UNKNOWN DENOMINATOR BINARY.

    Kalshi does not pay an individual program payout below $1. Our pool is
    $20 a period, so we need at least a 5% share or we are paid NOTHING --
    not a small amount, nothing. That converts the denominator from an
    accuracy question into an on/off one, and the two plausible readings of
    the qualifying rule land on opposite sides of it:

      NARROW (our order competes only with near-mid liquidity) -- several
      legs clear 5% comfortably.
      FULL BOOK (our order competes with all resting size) -- our $111
      clears 5% on nothing, and every leg pays zero.

    So the question is not 'how much would we earn'. It is 'would we be
    paid at all', and no amount of public book data answers it. One
    credited payout on a funded account does."""
    our_contracts = D(our_capital) / D(pair_cost) if pair_cost else D(0)
    need = MIN_PAYOUT_USD / D(reward_usd) if reward_usd else None
    out = {}
    for band, competing in competing_depth.items():
        denom = D(competing) + our_contracts
        share = (our_contracts / denom) if denom > 0 else D(0)
        earned = D(reward_usd) * share
        out[band] = {
            "share": str(share.quantize(D("0.0001"))),
            "gross_usd": str(earned.quantize(D("0.0001"))),
            "paid": bool(earned >= MIN_PAYOUT_USD),
            "forfeited_usd": (str(earned.quantize(D("0.0001")))
                              if earned < MIN_PAYOUT_USD else "0"),
        }
    return {"share_needed_for_any_payout": (str(need.quantize(D("0.0001")))
                                            if need else None),
            "by_denominator": out,
            "why_this_is_the_crux": ("the readings disagree about whether we "
                                     "are paid at all, not about how much")}


RESIDUAL_ECONOMICS = None
RESIDUAL_NOTE = ("UNMEASURED on this venue -- we hold no fills here. It is "
                 "not zero, and it is the term that actually sank PMUS: "
                 "-6.458%/$ came from unpaired inventory, not pair cost.")

CREDITED_INCENTIVES_USD = D("0.00")

# --------------------------------------------------- the measured verdict
# 1,160 book snapshots, 12 legs, ~97 observations each, read-only public
# GETs 2026-09-09T00:23-01:05Z, 30s cadence. Artifact:
# analysis/kalshi/leg_ranking.json
#
# RE-RUN AT DOUBLE THE SAMPLE (575 -> 1,160 rows) and the verdict held:
# copper and natgas keep the frontier position, and the full-book
# denominator still pays ZERO on every leg. Individual figures moved --
# copper's cost worsened from -2.51% to -2.79%/$ and natgas improved to
# -2.51%, swapping their order -- so the numbers below are the LARGER
# sample. The conclusion did not depend on the swap.
LEG_VERDICT = {
    "best_drag_with_meaningful_reward_density": ("KXCOPPER15M", "KXNATGAS15M"),
    "why": ("they sit in the MIDDLE of the cost table (natgas -2.51%/$, "
            "copper -2.79%/$) but at the TOP of the reward-density table "
            "(0.0654 per capital dollar, 3.4x the KXCRYPTOLEAD15M legs, "
            "because the same $20 pool sits against target size 300 rather "
            "than 1000) and near the BOTTOM of the competition table (58.6 "
            "and 78.5 contracts within 2c of mid, against 1,976 on WTI and "
            "4,424 on gold). And per the complete venue census they are "
            "ranks 1-5 of 300 active series on reward density, 444x the "
            "venue median"),
    "cheapest_execution_but_crowded": ("KXWTI15M", "KXGOLD15M", "KXSILVER15M"),
    "emptiest_books_but_expensive_and_third_the_density": (
        "KXCRYPTOLEAD15M:BTC", "KXCRYPTOLEAD15M:HYPE",
        "KXCRYPTOLEAD15M:ETH", "KXCRYPTOLEAD15M:SOL",
        "KXCRYPTOLEAD15M:XRP"),
    "the_irony": ("the two cheapest legs of all, KXBTC15M and KXETH15M at "
                  "-1.75%/$ and -1.68%/$, pay NOTHING. Their programs were "
                  "withdrawn on 2026-05-12. The best execution on the venue "
                  "is the part of it that stopped paying."),
}

STABILITY = {
    "finding": ("THE COMMODITY LEGS HOLD THEIR PRICE; THE CRYPTO LEGS DO "
                "NOT. Splitting a 2,234-snapshot / 81-minute window in half "
                "and comparing median pair cost early vs late: gold, WTI, "
                "copper, KXBTC15M and KXETH15M all moved 0.000, silver "
                "-0.001, natgas +0.010 -- while every KXCRYPTOLEAD15M leg "
                "moved 0.010 to 0.020, with SOL +0.020 and HYPE -0.020."),
    "why_it_matters": ("a 15-minute market must be re-quoted about a "
                       "hundred times a day. A leg whose pair cost wanders "
                       "two cents between quotes prices differently every "
                       "time you touch it, and the drag figure that ranked "
                       "it is then a average over a moving target rather "
                       "than a property you can plan against."),
    "effect_on_the_verdict": ("it STRENGTHENS copper and natgas rather than "
                              "changing the ordering. They were chosen on "
                              "cost and competition; stability is a third "
                              "axis they also win, and the legs they beat "
                              "are the unstable ones."),
    "caveat": ("81 minutes, all inside 00:16-01:37Z. This is NOT the "
               "off-hours test: it does not cross the CME maintenance "
               "break or reach Asian hours, and commodity 15m books could "
               "behave differently there. Do not read it as a 24-hour "
               "claim."),
}

PAYOUT_FLOOR_VERDICT = {
    "finding": ("UNDER THE FULL-BOOK DENOMINATOR, EVERY LEG PAYS ZERO. Our "
                "$111 earns a gross of $0.0094 to $0.6407 per period across "
                "all ten incentivised legs -- every one of them under the "
                "$1.00 minimum, so every one is forfeited in full, not "
                "paid small."),
    "under_narrow_bands": ("at a 2c band eight of ten legs clear the floor, "
                           "and at 5c six do"),
    "consequence": ("the unknown denominator is not an accuracy problem, it "
                    "is a BINARY one. The two readings of the qualifying "
                    "rule disagree about whether we are paid AT ALL, and "
                    "the gap between them is the whole result."),
    "what_settles_it": ("one credited payout on a funded account. No volume "
                        "of public book data can substitute, because the "
                        "quantity we are missing -- our own score against "
                        "everyone else's -- is never published."),
    "note_on_capital": ("this is a property of OUR bankroll, not of the "
                        "venue. The floor is $1 against a $20 pool, so it "
                        "binds until our share reaches 5%."),
}

P0_LEG_ANSWER = (
    "KXCOPPER15M and KXNATGAS15M are the legs that minimise trading drag "
    "while retaining meaningful reward density. But the ranking does not "
    "settle the venue: under the conservative denominator our capital is "
    "below the payout floor on every leg, and no public data closes that.")


# ------------------------------------------------- the paid_out dead end
PAID_OUT_IS_A_LAG_FLAG = {
    "claim_tested": "paid_out on a program row is a credited-reward observation",
    "verdict": "FALSE - it is a settlement lag flag",
    "by_age_since_end": {"0-1h": "0.0%", "1-3h": "0.0%", "3-6h": "0.0%",
                         "6-12h": "0.0%", "12-24h": "39.6%",
                         "24-48h": "100.0%"},
    "per_leg_tell": ("all ten legs return an identical n=147 / paid=94, "
                     "which is the age mix and not a leg property"),
    "consequence": ("a tally that stopped at the headline 'about half of "
                    "past programs never paid' would have been badly wrong"),
}


# --------------------------------------------------------------- the tape
THE_TENSION = (
    "DRAG AND SHARE PULL IN OPPOSITE DIRECTIONS ACROSS LEGS, and this is "
    "the reason a series average is worse than useless here. The legs with "
    "the CHEAPEST books carry the THICKEST competition near the mid, "
    "because cheap books are cheap precisely because many makers are "
    "already standing there. The legs with almost no near-mid competition "
    "are wide, and you pay that width on every pair you manufacture.\n\n"
    "So there is no leg that is simply 'best'. There is a frontier, and "
    "where you want to sit on it depends on which business you are in: "
    "pair manufacturing is punished by width, pure liquidity provision is "
    "punished by crowding, and the fee falls only on the first of those. "
    "Any single ranking that hides this frontier is selling one number "
    "where the decision needs two.")


def frontier(legs: Sequence[Mapping[str, Any]], band: str = "2"
             ) -> List[Dict[str, Any]]:
    """The two axes side by side, never collapsed into a score: measured
    cost to manufacture a pair, and competing near-mid depth (less is a
    larger share of the pool). A leg is on the FRONTIER when no other leg
    beats it on both."""
    pts = []
    for L in legs:
        d = L.get("band_min_side_depth_median")
        if not d:
            continue
        cost = -(D(L["drag_per_dollar"])
                 - D(L["maker_fee_per_dollar_if_both_legs_fill"]))
        pts.append({"leg": L["leg"], "cost_per_dollar": str(cost),
                    "competing_depth": d[band],
                    "headline_reward_usd": L.get("headline_reward_usd")})
    for a in pts:
        a["on_frontier"] = not any(
            D(b["cost_per_dollar"]) < D(a["cost_per_dollar"])
            and D(b["competing_depth"]) < D(a["competing_depth"])
            for b in pts if b is not a)
    pts.sort(key=lambda x: D(x["cost_per_dollar"]))
    return pts


def load_tape(path: str) -> List[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def rank_legs(tape: Sequence[Mapping[str, Any]],
              our_capital: D = D("111.00")) -> Dict[str, Any]:
    """One row per leg, ranked on the equation. No cross-leg averaging."""
    by: Dict[str, List[Mapping[str, Any]]] = {}
    for r in tape:
        by.setdefault(leg_key(r["ticker"]), []).append(r)

    legs = []
    for k, rows in by.items():
        pcs = [float(r["pair_cost"]) for r in rows]
        med = D(str(_pct(pcs, 0.5)))
        ybs = [float(r["yes_bid"]) for r in rows]
        y_med = D(str(_pct(ybs, 0.5)))
        prog = [r for r in rows if r.get("reward_raw")]
        reward = (D(prog[0]["reward_raw"]) / D(10_000)) if prog else None
        target = D(str(prog[0]["target"])) if prog and prog[0].get("target") else None
        bands = {b: D(str(_pct([min(float(r[f"band{b}_yes"]),
                                    float(r[f"band{b}_no"])) for r in rows],
                               0.5) or 0))
                 for b in ("1", "2", "5", "10")}
        drag = drag_per_dollar(med)
        fee = pair_fee_per_dollar(med, y_med)
        dens = (reward_density(reward, target, med)
                if reward is not None and target else None)
        legs.append({
            "leg": k,
            "observations": len(rows),
            "pair_cost_median": str(med),
            "pair_cost_p10": str(D(str(_pct(pcs, 0.10)))),
            "pair_cost_p90": str(D(str(_pct(pcs, 0.90)))),
            "yes_bid_median": str(y_med),
            "drag_per_dollar": str(drag.quantize(D("0.000001"))),
            "maker_fee_per_dollar_if_both_legs_fill":
                str(fee.quantize(D("0.000001"))),
            "headline_reward_usd": (str(reward) if reward is not None else None),
            "target_size": (str(target) if target is not None else None),
            "reward_density_per_capital_dollar": (
                str(dens.quantize(D("0.000001"))) if dens is not None else None),
            "band_min_side_depth_median": {b: str(v) for b, v in bands.items()},
            "share_curve": band_share_curve(our_capital, med, bands),
            "residual_economics": RESIDUAL_ECONOMICS,
            "credited_incentives_usd": str(CREDITED_INCENTIVES_USD),
        })

    # rank on what IS measured: drag net of fees. Reward density is reported
    # beside it but not folded in, because share is unknown and folding an
    # unknown-share headline into a rank would launder it into revenue.
    legs.sort(key=lambda L: -(D(L["drag_per_dollar"])
                              - D(L["maker_fee_per_dollar_if_both_legs_fill"])))
    for i, L in enumerate(legs, 1):
        L["rank_by_measured_cost"] = i
    return {
        "kind": "KALSHI_LEG_RANKING",
        "unit_of_decision": "leg",
        "legs": legs,
        "ranked_on": ("drag_per_dollar - maker fee (both legs fill), the two terms "
                      "that are measured. Reward density is shown beside the "
                      "rank, never inside it."),
        "fee_status": FEE_STATUS,
        "fee_vs_reward_asymmetry": FEE_IS_ON_FILL_REWARD_IS_ON_REST,
        "residual_note": RESIDUAL_NOTE,
        "share_dominance": share_dominance(legs),
        "frontier": frontier(legs),
        "the_tension": THE_TENSION,
        "share_denominator": ("reported as a curve over band width; the "
                              "venue's proximity rule is unknown to us"),
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": None,
    }
