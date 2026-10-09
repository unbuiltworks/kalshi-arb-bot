"""What the replay was missing, measured from R4/R5/R6 rather than assumed.

THE REPLAY'S ASSUMPTION, AND WHY IT IS WRONG. `pmus_l5_pair_policy_study`
decides a fill from one question: did printed volume clear the queue standing
at our price. Three live first-order effects are absent from that model, and
all three were measured on 2026-09-29:

  DECISION-TO-ARRIVAL LATENCY. Our price is chosen against one book and meets
  another. In R5 the gap was ~2 s and the market moved 2-4 ticks inside it.
  ARRIVAL-book crossing predicted all 13 R5 outcomes exactly; DECISION-book
  crossing predicted none of the four rejections.

  POST-ONLY REJECTION. A maker-only order that would cross on arrival is
  REJECTED, not repriced and not rested. The replay has no reject outcome at
  all, so it silently converts every rejection into a resting quote that may
  later fill. 5 of 28 live BTC15m orders were rejected (17.9%).

  ACCEPTANCE AND LATENCY ARE NOT INSTANT. R4's UP order rested 10.7 s before
  filling; a 2026-10-07 EXEC-1 order showed ORDER_STATE_NEW for ~15 s and then
  FILLED, with rest_time 65 s. A replay that fills at the first qualifying
  print is early.

WHAT THIS MODULE IS. A small, testable decision layer the replay can call
BEFORE it asks its queue question, plus the three live runs frozen as
regression cases. It introduces no strategy and changes no threshold.

LABELS. `post_only_verdict` is a venue-mechanics prediction validated on 13
R5 orders. It is not a fill model: an ACCEPTED order still has to clear its
queue, which remains the replay's own question, and queue position is not
observable from public data.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

TICK = D("0.01")

#: R5's measured decision-to-arrival gap, used as the default when a replay
#: has no latency of its own. Not a tuned parameter: it is an observation.
R5_MEDIAN_ARRIVAL_LAG_S = 2.0


def _d(x: Any) -> Optional[D]:
    try:
        return None if x is None else D(str(x))
    except Exception:                                          # noqa: BLE001
        return None


def own_price_to_wire(side_up: bool, own_price: Any) -> Optional[D]:
    """PMUS single-book semantics, applied once.

    An UP buy at own price X is a wire BUY at X. A DOWN buy at own price X is
    a wire SELL at 1-X carrying ORDER_INTENT_BUY_SHORT. The venue's own R4
    records confirm this representation.
    """
    p = _d(own_price)
    if p is None:
        return None
    return p if side_up else (D(1) - p)


def crosses_on_arrival(side_up: bool, own_price: Any,
                       arrival_bid: Any, arrival_ask: Any) -> Optional[bool]:
    """Would this maker order take liquidity against the ARRIVAL book?

    UP buy at X is marketable iff X >= ask. DOWN buy at own X is a wire sell
    at 1-X, marketable iff 1-X <= bid. Returns None when the arrival book is
    not two-sided, because then it is not knowable.
    """
    wire = own_price_to_wire(side_up, own_price)
    bid, ask = _d(arrival_bid), _d(arrival_ask)
    if wire is None or bid is None or ask is None:
        return None
    # A LOCKED OR CROSSED BOOK IS NOT KNOWABLE, not a rejection. ask <= bid
    # means the snapshot is degenerate (or stale mid-update), and inventing a
    # rejection from it would put fabricated rejections into the replay --
    # the opposite of the defect this module exists to fix.
    if ask <= bid:
        return None
    return bool(wire >= ask) if side_up else bool(wire <= bid)


def post_only_verdict(side_up: bool, own_price: Any, *,
                      decision_bid: Any, decision_ask: Any,
                      arrival_bid: Any, arrival_ask: Any) -> dict:
    """ACCEPT / REJECT, judged on the arrival book, with the decision book
    reported beside it so the staleness is visible rather than implied."""
    at_arrival = crosses_on_arrival(side_up, own_price, arrival_bid,
                                    arrival_ask)
    at_decision = crosses_on_arrival(side_up, own_price, decision_bid,
                                     decision_ask)
    if at_arrival is None:
        return {"verdict": "UNKNOWN", "why": "ARRIVAL_BOOK_NOT_TWO_SIDED",
                "crossed_at_arrival": None,
                "crossed_at_decision": at_decision}
    return {
        "verdict": "REJECT" if at_arrival else "ACCEPT",
        "why": ("POST_ONLY_WOULD_CROSS_AT_ARRIVAL" if at_arrival
                else "RESTS_AS_MAKER"),
        "crossed_at_arrival": at_arrival,
        "crossed_at_decision": at_decision,
        "staleness_changed_the_outcome": bool(at_arrival != at_decision),
        "BASIS": "validated on 13 R5 orders: arrival-book crossing predicted "
                 "13/13 outcomes, decision-book crossing predicted 0/4 "
                 "rejections",
    }


def residual_settlement_pnl(up_qty: Any, down_qty: Any, cost_usd: Any,
                            settlement_px_long: Any) -> dict:
    """FULL-inventory settlement, counting every losing residual.

    `settlement_px_long` is the venue's own field: 1.0000 means the long (UP)
    side paid, 0.0000 means it expired worthless. Matched pairs pay $1 per
    pair either way; the residual decides the rest.
    """
    up, dn, cost = _d(up_qty) or D(0), _d(down_qty) or D(0), _d(cost_usd) or D(0)
    px = _d(settlement_px_long)
    if px is None:
        return {"status": "UNDECIDABLE", "reason": "NO_SETTLEMENT_PX"}
    up_wins = px >= D("0.5")
    gross = (up if up_wins else dn) * D(1)
    matched = min(up, dn)
    return {
        "status": "SETTLED",
        "long_side_won": bool(up_wins),
        "matched_pairs": str(matched),
        "residual_shares_signed": str(up - dn),
        "gross_return_usd": str(gross),
        "cost_usd": str(cost),
        "trading_pnl_usd": str(gross - cost),
        "pair_cost_per_matched": (str((cost / matched).quantize(D("0.0001")))
                                  if matched > 0 else None),
        "LABEL": "settlement-based; commissions handled separately because "
                 "the venue's sign convention is a credit on these fills",
    }


# --------------------------------------------------------- regression set ---
#: The three live BTC15m runs, as frozen cases. Every number here is
#: venue-confirmed or closes to the cent against a venue-confirmed figure;
#: see PMUS_R456_RECONCILIATION.md for provenance.
LIVE_CASES: Dict[str, dict] = {
    "R4": {
        "market": "cpc-btc-updown-15m-2026-09-29-0045z",
        "orders_submitted": 2, "filled": 1, "rejected": 1, "expired": 0,
        "up_qty": "10", "down_qty": "0", "cost_usd": "2.60",
        "settlement_px_long": "0.0000",
        "fill_latency_s": 10.701, "reject_latency_s": 0.094,
        "note": "two-sided quote 234 ms apart while FLAT; DOWN rejected with "
                "no venue reason; collateral refuted at 5.8x coverage",
    },
    "R5": {
        "market": "cpc-btc-updown-15m-2026-09-29-1115z",
        "orders_submitted": 13, "filled": 8, "rejected": 4, "expired": 1,
        "up_qty": "30", "down_qty": "50", "cost_usd": "38.30",
        "settlement_px_long": "1.0000",
        "trading_pnl_usd_settlement_only": "-8.30000",
        "net_commissions_usd": "+0.24000",
        "cash_delta_usd": "-8.06000",
        "cashflow_lines": [
            ["fills_cost_usd", "-38.30000", "VENUE mandate_consumption"],
            ["settlement_proceeds_usd", "+30.00000",
             "RECON 30 UP x 1.00; settlement_px 1.0000 is VENUE"],
            ["net_commissions_usd", "+0.24000",
             "RECON residual against the VENUE cash anchor"],
            ["total_usd", "-8.06000",
             "VENUE 54.58385 -> 46.52385"],
        ],
        "note": "4 post-only rejections, all predicted by the arrival book",
    },
    "R6": {
        "market": "cpc-btc-updown-15m-2026-09-29-1815z",
        "orders_submitted": 16, "filled": 8, "rejected": 0, "expired": 0,
        "up_qty": "40", "down_qty": "40", "cost_usd": "36.50",
        "settlement_px_long": "1.0000",
        # THE TWO FIGURES ARE BOTH RIGHT AND MEASURE DIFFERENT THINGS.
        # trading_pnl_usd is settlement only: 40 x $1.00 less the $36.50 of
        # fills. cash_delta_usd is the authoritative balance change. The
        # difference is net commission CREDITS, which this venue returns as a
        # negative commissionNotionalTotalCollected on maker fills.
        "trading_pnl_usd_settlement_only": "+3.50000",
        "net_commissions_usd": "+0.05040",
        "cash_delta_usd": "+3.55040",
        "cashflow_lines": [
            ["fills_cost_usd", "-36.50000", "VENUE mandate_consumption"],
            ["settlement_proceeds_usd", "+40.00000",
             "RECON 40 UP x 1.00; settlement_px 1.0000 is VENUE"],
            ["net_commissions_usd", "+0.05040",
             "RECON residual against the VENUE cash anchor"],
            ["total_usd", "+3.55040",
             "VENUE 46.52385 -> 50.07425 displayedCash"],
        ],
        "note": "40 matched pairs at 0.9125; one order outlived the process "
                "and filled unsupervised (CSWG9ZB98YCA)",
    },
}
