"""What the VENUE requires, read from the venue -- not what we would like.

This chooses nothing. It reports the constraints the smallest technically valid
BTC15m measurement must satisfy, so the exposure envelope can be selected as a
decision rather than inherited from a default.

THE CONSTRAINT THAT DOMINATES THE CHOICE: on Polymarket's BTC updown markets
`rewards_min_size` is 50 while `order_min_size` is 5. The smallest legal order
is therefore TEN TIMES too small to earn a liquidity reward. A 5-contract probe
can measure fills, fees and maker rebates; it cannot measure rewards at all.
Measuring rewards is a separate, 10x-larger decision and this module refuses to
blur them.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "VENUE_ENVELOPE/observed"

FIELDS = ("order_min_size", "tick", "fee_rate", "fee_rebate_rate",
          "fee_taker_only", "rewards_min_size", "rewards_max_spread",
          "accepting_orders")


def observe(market_key: str) -> Dict[str, Any]:
    """Live, read-only. Absent fields stay None -- never a stand-in value."""
    from src.l5_shadow import venues as V
    w = V.pm_window(market_key)
    if not w:
        return {"label": LABEL, "market_key": market_key,
                "observed": False,
                "why": "the venue returned no market object"}
    return {"label": LABEL, "market_key": market_key, "observed": True,
            "venue": w.get("venue"),
            **{k: w.get(k) for k in FIELDS},
            "terms_source": w.get("terms_source")}


def minimums(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The dict the preflight's ORDER_SIZE_CONSTRAINTS check consumes."""
    if not obs.get("observed"):
        return None
    if obs.get("order_min_size") is None:
        return None
    return {"qty": obs["order_min_size"]}


def constraints(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Every operational consequence, stated so the envelope can be chosen."""
    if not obs.get("observed"):
        return {"label": LABEL, "determinable": False,
                "why": obs.get("why", "market not observed")}
    omin = D(str(obs["order_min_size"]))
    rmin = (D(str(obs["rewards_min_size"]))
            if obs.get("rewards_min_size") is not None else None)
    tick = D(str(obs["tick"])) if obs.get("tick") is not None else None
    notes: List[str] = []
    if rmin is not None and rmin > omin:
        notes.append(
            f"REWARDS ARE OUT OF REACH AT THE MINIMUM: rewards_min_size "
            f"{rmin} is {rmin / omin:.0f}x order_min_size {omin}. A "
            f"{omin}-contract probe measures fills, fees and maker rebates and "
            f"earns NO liquidity reward. Reward measurement is a separate "
            f"decision at >= {rmin} contracts.")
    if obs.get("fee_taker_only"):
        notes.append(
            "FEES ARE TAKER-ONLY: a pure maker pays no fee, so a maker-only "
            "probe measures the rebate side without a fee offset. A taker "
            "leg would be charged and must be accounted separately.")
    if obs.get("fee_rebate_rate") is not None:
        notes.append(
            f"the maker rebate is {obs['fee_rebate_rate']} of the taker's fee "
            f"(fee_rate {obs.get('fee_rate')}), so rebate income is bounded by "
            f"COUNTERPARTY flow we do not control and cannot be assumed.")
    if obs.get("rewards_max_spread") is not None:
        notes.append(
            f"a reward-eligible quote must rest within {obs['rewards_max_spread']}c "
            f"of the midpoint; a quote outside that band earns nothing however "
            f"long it rests.")
    # worst-case cash at the venue minimum: one contract pays at most $1
    worst = omin * D("1")
    return {
        "label": LABEL, "determinable": True,
        "market_key": obs["market_key"], "venue": obs.get("venue"),
        "order_min_size": str(omin),
        "tick": (str(tick) if tick is not None else None),
        "rewards_min_size": (str(rmin) if rmin is not None else None),
        "rewards_max_spread": obs.get("rewards_max_spread"),
        "fee_rate": obs.get("fee_rate"),
        "fee_rebate_rate": obs.get("fee_rebate_rate"),
        "fee_taker_only": obs.get("fee_taker_only"),
        "smallest_valid_order_qty": str(omin),
        "max_cash_at_risk_one_side_usd": str(worst),
        "reward_eligible_at_minimum": (rmin is not None and omin >= rmin),
        "notes": notes,
        "labels": ("observed from the venue; NOTHING here selects an exposure. "
                   "The envelope remains an owner decision."),
    }
