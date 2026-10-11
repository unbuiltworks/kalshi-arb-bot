"""The full chain, in one place, with every stage labelled by what it IS.

    order -> submitted -> fill -> matched P&L -> residual P&L -> fees
          -> rebate -> reward -> settlement -> realized net

WHY A CHAIN AND NOT A TOTAL. Each stage answers a different question and the
labels are not interchangeable. An order is an intention. A submission is an
intention the venue acknowledged. A fill is inventory. Matched and residual are
economics that do not exist until the market SETTLES. A rebate is money only
once credited; a reward is money only once PAID. `realized_net` is the last
line and it is reachable only from a settled window with venue-confirmed
economics -- every earlier stage reports None for it rather than a best guess.

The one rule that shapes the whole module: NOTHING BEFORE SETTLEMENT IS
REALIZED. A filled position has a cost, not a result.

Matched/residual arithmetic is l5_shadow.ledger.settle, unchanged -- the same
splitter the shadow ledger uses, so a live row and a shadow row cannot disagree
about what a matched pair earned.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

LABEL = "LIVE_PROBE_ACCOUNTING/chain"

STAGES = ("order", "submitted", "fill", "matched_pnl", "residual_pnl",
          "fees", "rebate", "reward", "settlement", "realized_net")

# Stage reached -> is a realized line permitted to exist yet?
REALIZED_REQUIRES = "settlement"


def _d(v: Any, default: str = "0") -> D:
    try:
        return D(str(v)) if v is not None else D(default)
    except Exception:                                         # noqa: BLE001
        return D(default)


def chain(*, market_key: str, venue: str,
          orders: Sequence[Mapping[str, Any]] = (),
          submissions: Sequence[Mapping[str, Any]] = (),
          fills: Sequence[Mapping[str, Any]] = (),
          rebates: Sequence[Mapping[str, Any]] = (),
          rewards: Sequence[Mapping[str, Any]] = (),
          resolution: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """One market's complete economic record, stage by stage."""
    from src.l5_shadow import ledger as LG

    up_q = sum((_d(f.get("qty")) for f in fills
                if str(f.get("side")) == "Up"), D("0"))
    dn_q = sum((_d(f.get("qty")) for f in fills
                if str(f.get("side")) == "Down"), D("0"))
    up_c = sum((_d(f.get("cost")) for f in fills
                if str(f.get("side")) == "Up"), D("0"))
    dn_c = sum((_d(f.get("cost")) for f in fills
                if str(f.get("side")) == "Down"), D("0"))
    # fees are what the VENUE charged us on our own side, never the
    # counterparty's fee and never an estimate
    fees = sum((_d(r.get("fee_charged_usd")) for r in rebates), D("0"))
    rebate_credited = sum((_d(r.get("rebate_credited_usd"))
                           for r in rebates), D("0"))
    # PAID only. An accrual is not income.
    reward_credited = sum((_d(r.get("credited_usd")) for r in rewards
                           if r.get("credited")), D("0"))
    reward_accrued = sum((_d(r.get("amount_usd")) for r in rewards
                          if not r.get("credited")), D("0"))

    pos = {"venue": venue, "cohort": "BTC15m", "market_key": market_key,
           "clock": "venue", "n_fills": len(fills), "intent_ids": [],
           "up_qty": up_q, "dn_qty": dn_q, "up_cost": up_c, "dn_cost": dn_c,
           "fees": fees}
    s = LG.settle(pos, dict(resolution) if resolution else None)
    settled = s.get("state") == "SETTLED"

    matched = _d(s.get("matched_pair_pnl")) if settled else None
    residual = _d(s.get("residual_pnl")) if settled else None
    trading = (matched + residual) if settled else None
    # REALIZED NET exists only past settlement, and it is trading P&L net of
    # fees plus ONLY what the venue actually credited.
    realized_net = ((trading - fees + rebate_credited + reward_credited)
                    if settled else None)

    reached = ("settlement" if settled else
               "fill" if fills else
               "submitted" if submissions else
               "order" if orders else None)

    return {
        "label": LABEL, "market_key": market_key, "venue": venue,
        "stage_reached": reached,
        "order": {"n": len(orders)},
        "submitted": {"n": len(submissions),
                      "note": "acknowledged by the venue; not yet inventory"},
        "fill": {"n": len(fills), "up_qty": str(up_q), "dn_qty": str(dn_q),
                 "up_cost": str(up_c), "dn_cost": str(dn_c),
                 "note": "inventory with a COST, never a result"},
        "matched_pnl": (str(matched) if matched is not None else None),
        "residual_pnl": (str(residual) if residual is not None else None),
        "fees": str(fees),
        "rebate": {"credited_usd": str(rebate_credited),
                   "resolution": "FILL",
                   "rows": len(rebates)},
        "reward": {"credited_usd": str(reward_credited),
                   "accrued_not_credited_usd": str(reward_accrued),
                   "fill_resolution": "NOT_IDENTIFIABLE",
                   "rows": len(rewards)},
        "settlement": {"settled": settled,
                       "state": s.get("state"),
                       "winning_outcome": s.get("winning_outcome"),
                       "residual_side": s.get("residual_side")},
        "realized_net": (str(realized_net) if realized_net is not None
                         else None),
        "trading_pnl": (str(trading) if trading is not None else None),
        "labels": (
            "realized_net exists ONLY past settlement. Before it, the position "
            "has a cost and no result. Rebates are credited-only and joined to "
            "the fill that earned them; rewards are credited-only and reach "
            "market resolution, never fill -- accruals are excluded from every "
            "realized line."),
    }


def roll_up(chains: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Across markets, keeping settled and unsettled apart."""
    cs = list(chains)
    done = [c for c in cs if c.get("settlement", {}).get("settled")]
    net = sum((_d(c.get("realized_net")) for c in done), D("0"))
    trading = sum((_d(c.get("trading_pnl")) for c in done), D("0"))
    fees = sum((_d(c.get("fees")) for c in done), D("0"))
    reb = sum((_d(c.get("rebate", {}).get("credited_usd")) for c in done), D("0"))
    rew = sum((_d(c.get("reward", {}).get("credited_usd")) for c in done), D("0"))
    return {"label": LABEL, "markets": len(cs), "settled": len(done),
            "unsettled": len(cs) - len(done),
            "trading_pnl": str(trading), "fees": str(fees),
            "rebate_credited": str(reb), "reward_credited": str(rew),
            "realized_net": str(net),
            "identity_holds": net == (trading - fees + reb + rew),
            "labels": ("unsettled markets contribute NOTHING to realized_net; "
                       "they are counted, never netted.")}
