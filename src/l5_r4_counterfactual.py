"""What a DOLLAR unmatched-exposure cap would have done to R3L.

OWNER DECISION #167 5566810119 #2: "Counterfactual R3L wisc-nd using the
now-plumbed game-position data. Test candidate rules based on
settlement/game proximity and dollar-at-risk, not hindsight outcome ...
Show which rules would have bounded the $6.29 residual and what they would
have done to the two successful R3L markets. Do not select a rule because it
avoids this specific loser."

The rule under test is deliberately outcome-blind: it looks only at how many
UNMATCHED dollars a market is carrying, which is knowable before every order
and is exactly the quantity that is lost if that side expires worthless.

  unmatched_usd = |long_qty - short_qty| x (average cost of the longer side)

An order that REDUCES unmatched exposure (a complement) is always allowed --
the cap must never trap the engine in a position it is trying to close. An
order that INCREASES it is refused when it would take the market above the
cap. Nothing else in the shell changes: maker-only, fee-safe 0.98, one
outstanding order, hold to settlement.

R3L's three markets are replayed from the venue's own fill rows. Prices are
in YES terms, so a NO fill at YES-price p costs (1 - p) -- the convention the
settlement breakdowns use and that the balance reconciliation confirmed to
the cent.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Dict, List, Optional, Sequence

# Venue fill rows for the R3L campaign, in time order, as reported by the
# settlement audit (t, side, yes_price, qty). side is the OUTCOME bought.
R3L_FILLS: Dict[str, List[tuple]] = {
    "asc-cfb-lou-miss-2026-09-06-pos-6pt5": [
        ("01:22:38", "YES", "0.45", "5.55"),
        ("01:44:13", "NO",  "0.47", "5.55"),
        ("01:44:37", "YES", "0.47", "5.31"),
        ("01:45:09", "YES", "0.44", "5.68"),
        ("01:45:15", "NO",  "0.48", "5.68"),
        ("01:49:57", "NO",  "0.65", "3.90"),
        ("01:55:23", "NO",  "0.50", "1.41"),
    ],
    "tsc-cfb-lou-miss-2026-09-06-total-41pt5": [
        ("01:42:46", "YES", "0.46", "5.43"),
        ("01:43:01", "YES", "0.46", "5.43"),
        ("01:46:40", "YES", "0.46", "5.43"),
        ("01:47:15", "NO",  "0.48", "5.68"),
        ("02:12:28", "NO",  "0.79", "3.20"),
        ("02:12:58", "NO",  "0.79", "1.00"),
        ("02:13:11", "NO",  "0.77", "3.28"),
        ("02:14:33", "NO",  "0.74", "3.13"),
    ],
    "asc-cfb-wisc-nd-2026-09-06-pos-10pt5": [
        ("01:56:37", "YES", "0.37", "5.15"),
        ("01:56:45", "YES", "0.37", "5.15"),
        ("01:57:51", "YES", "0.25", "4.33"),
        ("01:58:30", "YES", "0.30", "4.64"),
    ],
}

# which outcome the venue resolved to, from the settlement audit
R3L_RESOLUTION = {
    "asc-cfb-lou-miss-2026-09-06-pos-6pt5": None,       # fully paired, no side
    "tsc-cfb-lou-miss-2026-09-06-total-41pt5": None,    # fully paired, no side
    "asc-cfb-wisc-nd-2026-09-06-pos-10pt5": "NO",       # our YES expired worthless
}


def cost(side: str, yes_price: D) -> D:
    """What one share of `side` costs, from a YES-terms price."""
    return yes_price if side == "YES" else (D(1) - yes_price)


def replay(fills: Sequence[tuple], max_unmatched_usd: Optional[D] = None,
           resolution: Optional[str] = None) -> dict:
    """Replay one market under a dollar unmatched cap (None = no cap)."""
    qty = {"YES": D(0), "NO": D(0)}
    spend = {"YES": D(0), "NO": D(0)}
    taken, refused = [], []
    peak = D(0)

    def unmatched() -> tuple:
        long_side = "YES" if qty["YES"] >= qty["NO"] else "NO"
        n = abs(qty["YES"] - qty["NO"])
        avg = (spend[long_side] / qty[long_side]) if qty[long_side] else D(0)
        return long_side, n, (n * avg).quantize(D("0.0001"))

    for t, side, px, q in fills:
        px, q = D(px), D(q)
        _, _, before = unmatched()
        qty[side] += q
        spend[side] += q * cost(side, px)
        long_side, n, after = unmatched()
        if (max_unmatched_usd is not None and after > before
                and after > max_unmatched_usd):
            qty[side] -= q                       # refuse: it would add exposure
            spend[side] -= q * cost(side, px)
            refused.append({"t": t, "side": side, "yes_price": str(px),
                            "qty": str(q), "would_reach_usd": str(after)})
            continue
        taken.append({"t": t, "side": side, "qty": str(q),
                      "unmatched_after_usd": str(after)})
        peak = max(peak, after)

    long_side, resid_qty, resid_usd = unmatched()
    matched = min(qty["YES"], qty["NO"])
    total = spend["YES"] + spend["NO"]
    pair_cost = ((total - resid_usd) / matched) if matched else None
    pair_pnl = (matched * (D(1) - pair_cost)) if matched else D(0)
    if resid_qty == 0 or resolution is None:
        resid_pnl = D(0)
    elif long_side == resolution:
        resid_pnl = resid_qty - resid_usd            # paid off at $1/share
    else:
        resid_pnl = -resid_usd
    return {
        "fills_taken": len(taken), "fills_refused": len(refused),
        "refused": refused, "path": taken,
        "spent_usd": str(total.quantize(D("0.0001"))),
        "matched_qty": str(matched),
        "matched_pair_cost": (str(pair_cost.quantize(D("0.000001")))
                              if pair_cost is not None else None),
        "matched_pnl_usd": str(pair_pnl.quantize(D("0.0001"))),
        "peak_unmatched_usd": str(peak),
        "final_unmatched_usd": str(resid_usd),
        "final_unmatched_side": long_side if resid_qty else None,
        "residual_pnl_usd": str(resid_pnl.quantize(D("0.0001"))),
        "market_pnl_usd": str((pair_pnl + resid_pnl).quantize(D("0.0001"))),
    }


def campaign(max_unmatched_usd: Optional[D] = None) -> dict:
    per = {slug: replay(fills, max_unmatched_usd, R3L_RESOLUTION[slug])
           for slug, fills in R3L_FILLS.items()}
    total = sum(D(m["market_pnl_usd"]) for m in per.values())
    spent = sum(D(m["spent_usd"]) for m in per.values())
    peak = max(D(m["peak_unmatched_usd"]) for m in per.values())
    return {
        "max_unmatched_usd": (str(max_unmatched_usd)
                              if max_unmatched_usd is not None else None),
        "per_market": per,
        "campaign_trading_pnl_usd": str(total.quantize(D("0.0001"))),
        "turnover_usd": str(spent.quantize(D("0.0001"))),
        "worst_market_peak_unmatched_usd": str(peak),
        "orders_refused": sum(m["fills_refused"] for m in per.values()),
        "labels": "counterfactual on the venue's own fill rows; rebates are "
                  "NOT modelled here (they scale with filled notional and are "
                  "reported separately)",
    }


def sweep(caps: Sequence[Optional[str]]) -> dict:
    return {("uncapped" if c is None else c):
            campaign(None if c is None else D(c)) for c in caps}
