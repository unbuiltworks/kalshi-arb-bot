"""ECONOMIC_MEASUREMENT_PROBE_V0 -- the smallest thing that measures anything.

WHAT IT IS NOT. It is NOT cap5 and it is not a revival of any arm of
ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0, which FAILED. No behaviour here is claimed
to be profitable and none is qualified as a strategy. The label carries that.

HOW THE FAILED RESULT SHAPED IT, concretely. The decomposition found that
residual P&L is a PURE directional bet -- sign determined entirely by whether
the leftover side won, 38 of 38 settled windows -- and that the bet is
adversely selected, winning 0.3421 overall and 0.2381 on PMUS. It also found
that residual EXTENSION is what builds that exposure, and that no observable
risk state removes most of the damage (39.9% at best, by an oracle no policy
can run). cap5's error was capping extension at five. V0 does not cap
extension; it REMOVES it: at most ONE fill per side, ever, per window. Maximum
residual is therefore one leg, and it is bounded by construction rather than by
a threshold that the evidence says does not work.

WHAT IT MEASURES, none of which needs a profitable policy to be worth knowing:
queue position and whether a resting minimum-size quote fills at all; the fee
actually charged (taker-only here, so a pure maker should be charged nothing --
that is a measurement, not an assumption); the maker rebate actually credited,
joined to the exact fill; settlement; and the realized net.

WHAT IT CANNOT MEASURE, established at zero risk rather than by trading:
liquidity rewards. BTC15m markets carry rewards.rates == null and their rewards
endpoint lists no program, against a control where rewards-enabled markets
return one entry each. No size qualifies -- not 5, not 50 -- so the 50-share
minimum is a threshold in front of an empty pool, and the >=50 experiment would
buy a guaranteed zero.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Tuple

from src.l5_risk import shell as RISK

LABEL = "ECONOMIC_MEASUREMENT_PROBE_V0"
STATUS = "NOT_A_QUALIFIED_STRATEGY/measurement_only"
LINEAGE = ("ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 = FAIL (af1d0f57, finalised "
           "84acb4d9). cap5/8/13 are not revived; extension is removed, not "
           "capped.")

# ---- venue facts, observed and not assumed (btc-updown-15m, 2026-09-16) ----
VENUE_ORDER_MIN = D("5")        # orderMinSize / minimum_order_size
VENUE_TICK = D("0.01")          # orderPriceMinTickSize
VENUE_MAX_PRICE = D("0.99")     # 1.00 - tick: the dearest tradeable price
FEE_RATE = D("0.07")            # taker-only
FEE_REBATE_RATE = D("0.2")      # maker's share of the taker's fee

# ---- behaviour ----
ORDER_QTY = VENUE_ORDER_MIN     # exactly the venue minimum, never more
MAX_FILLS_PER_SIDE = 1          # NO EXTENSION. this is the whole design.
MAX_OUTSTANDING_ORDERS = 1      # one resting order at a time
SIDES = ("Up", "Down")
RESIDUAL_POLICY = "HOLD_TO_SETTLEMENT"   # never liquidate to flatten

# ---- the envelope ----
MAX_INVENTORY_SHARES = ORDER_QTY * len(SIDES)          # 10
MAX_MARKET_SPEND = MAX_INVENTORY_SHARES * VENUE_MAX_PRICE   # 9.90
MAX_RESIDUAL_QTY = ORDER_QTY                            # 5 -- one unmatched leg
MAX_RESIDUAL_USD = MAX_RESIDUAL_QTY * VENUE_MAX_PRICE   # 4.95
MAX_CONCURRENT_MARKETS = 1
DAILY_LOSS_STOP = D("-10.00")
DEADMAN_S = 300.0


def reachable_states() -> List[Tuple[D, D, D]]:
    """Every inventory state the behaviour can actually reach, priced at the
    WORST tradeable price. Enumerated rather than reasoned about, because the
    worst case is the only number that matters here."""
    out = []
    for up_fills in range(MAX_FILLS_PER_SIDE + 1):
        for dn_fills in range(MAX_FILLS_PER_SIDE + 1):
            up = ORDER_QTY * up_fills
            dn = ORDER_QTY * dn_fills
            spent = (up + dn) * VENUE_MAX_PRICE
            out.append((up, dn, spent))
    return out


def max_loss_per_market() -> Dict[str, Any]:
    """The worst guaranteed floor over every reachable state.

    Uses l5_risk.shell.guaranteed_floor -- min(up, dn) - spent -- so this agrees
    with the number the running risk controls use, rather than being a second
    opinion about it.
    """
    rows = []
    for up, dn, spent in reachable_states():
        floor = RISK.guaranteed_floor(up, dn, spent)
        rows.append({"up": str(up), "dn": str(dn), "spent": str(spent),
                     "guaranteed_floor": str(floor)})
    worst = min(rows, key=lambda r: D(r["guaranteed_floor"]))
    return {"worst_state": worst, "max_loss_usd": str(-D(worst["guaranteed_floor"])),
            "all_states": rows}


def max_total_loss() -> Dict[str, Any]:
    """Absolute worst case for the whole probe.

    The stop is checked BEFORE acquiring and is inclusive, so the probe can sit
    just inside the stop and then take on one more market's full loss. The bound
    is therefore |stop| + one market, and nothing else can be added after it
    because the stop latches and holds inventory to settlement.
    """
    per = D(max_loss_per_market()["max_loss_usd"])
    stop = -DAILY_LOSS_STOP
    return {
        "daily_loss_stop_usd": str(DAILY_LOSS_STOP),
        "max_loss_per_market_usd": str(per),
        "max_concurrent_markets": MAX_CONCURRENT_MARKETS,
        "max_total_loss_usd": str(stop + per),
        "max_open_cash_at_risk_usd": str(MAX_MARKET_SPEND),
        "why": ("the stop is pre-trade and inclusive: the probe may sit at "
                f"just better than {DAILY_LOSS_STOP} and then acquire one more "
                f"market whose worst guaranteed floor is -{per}. After that the "
                "stop latches, no further exposure is acquired, and residual is "
                "held to settlement rather than liquidated."),
    }


def envelope_env() -> Dict[str, str]:
    """Exactly the five values an owner would set to arm this envelope.
    Setting them is the authorization step; nothing here sets them."""
    return {
        "L5PROBE_DAILY_LOSS_STOP": str(DAILY_LOSS_STOP),
        "L5PROBE_MAX_INVENTORY_SHARES": str(MAX_INVENTORY_SHARES),
        "L5PROBE_MAX_MARKET_SPEND": str(MAX_MARKET_SPEND),
        "L5PROBE_MAX_CONCURRENT_MARKETS": str(MAX_CONCURRENT_MARKETS),
        "L5PROBE_DEADMAN_S": str(DEADMAN_S),
    }


def expected_rebate_usd(price: Any, qty: Any = ORDER_QTY) -> D:
    """What a maker fill of this size would be credited IF a taker hits it.

    Not income and not a forecast: it is the venue's own formula, reported so
    the measurement has something to be compared against. The rebate depends on
    COUNTERPARTY flow we do not control, so it is never assumed to arrive.
    """
    p = D(str(price))
    taker_fee = FEE_RATE * min(p, D("1") - p) * D(str(qty))
    return (FEE_REBATE_RATE * taker_fee).quantize(D("0.0001"))


def describe() -> Dict[str, Any]:
    return {
        "label": LABEL, "status": STATUS, "lineage": LINEAGE,
        "behaviour": {
            "order_qty": str(ORDER_QTY),
            "max_fills_per_side": MAX_FILLS_PER_SIDE,
            "max_outstanding_orders": MAX_OUTSTANDING_ORDERS,
            "extension": "REMOVED (not capped)",
            "residual_policy": RESIDUAL_POLICY,
            "sides": list(SIDES),
        },
        "envelope": {
            "max_inventory_shares": str(MAX_INVENTORY_SHARES),
            "max_market_spend_usd": str(MAX_MARKET_SPEND),
            "max_residual_qty": str(MAX_RESIDUAL_QTY),
            "max_residual_usd": str(MAX_RESIDUAL_USD),
            "max_concurrent_markets": MAX_CONCURRENT_MARKETS,
            "daily_loss_stop_usd": str(DAILY_LOSS_STOP),
            "deadman_s": DEADMAN_S,
        },
        "loss": max_total_loss(),
        "rewards": {
            "measurable": False,
            "state": "NO_ACTIVE_REWARD_PROGRAM",
            "why": ("rewards.rates is null and the rewards endpoint lists no "
                    "program for BTC15m, against a discriminating control"),
            "min_size_is_binding": False,
        },
        "measures": ["queue_position", "fill_or_no_fill", "fees_charged",
                     "maker_rebate_credited_joined_to_fill", "settlement",
                     "matched_pnl", "residual_pnl", "realized_net"],
    }
