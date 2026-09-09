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


RESIDUAL_ECONOMICS = None
RESIDUAL_NOTE = ("UNMEASURED on this venue -- we hold no fills here. It is "
                 "not zero, and it is the term that actually sank PMUS: "
                 "-6.458%/$ came from unpaired inventory, not pair cost.")

CREDITED_INCENTIVES_USD = D("0.00")


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
        "share_denominator": ("reported as a curve over band width; the "
                              "venue's proximity rule is unknown to us"),
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": None,
    }
