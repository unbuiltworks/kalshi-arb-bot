"""Settlement reconciliation, and the one rule about the word REALIZED.

THE LABELS, IN ORDER, AND WHAT EACH ONE IS ALLOWED TO MEAN:

    SUBMITTED   a POST crossed the boundary. Not a resting order.
    RESTING     the venue reports our order open. Not a fill.
    FILLED      the venue reports executions, counted by id. Not money.
    SETTLED     the market resolved and the venue states a payout.
    REALIZED    settlement cash is confirmed against the account.

`realized_pnl()` returns a number ONLY when a settlement record carries a
confirmed cash movement. Absent that it returns `None` with a refusal
string, and it does so for every tempting substitute by name:

  * mark-to-market on the residual                 -> NOT realized
  * a modelled pair payout                         -> NOT realized
  * the locked $0.04/contract from a cleared gate   -> NOT realized
  * an ESTIMATED maker rebate                       -> NOT realized
  * a venue `realizedPnl` field on a trade row      -> NOT realized
  * a balance change with no source identity        -> NOT realized

The last two matter most, because both LOOK authoritative. A trade row's
`realizedPnl` is the venue's own running figure and is not settlement cash;
a balance delta without a settlement identity could be anything, including
another service on the same account.

ACCOUNTING IS DECOMPOSED, NEVER NETTED INTO ONE NUMBER. CLAUDE.md requires
matched-pair economics, directional residual P&L, spread capture, maker
rebates, liquidity rewards, fees and settlement cashflows to be accounted
SEPARATELY. `reconcile()` reports each line on its own, with its own label,
and refuses to emit a single blended "profit".
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.stage6 import bounds as B
from analysis.l5.stage6 import pair_economics as PE

ZERO = D("0")
USD_Q = D("0.0001")

LIFECYCLE = ("SUBMITTED", "RESTING", "FILLED", "SETTLED", "REALIZED")

#: things that are NOT realized profit, named so a future reader cannot
#: reintroduce one by accident.
NOT_REALIZED = (
    "theoretical fills",
    "stale or public BBO opportunities",
    "modeled pair profit without fills",
    "shadow inventory",
    "mark-to-market gains on the residual",
    "estimated maker rebates or liquidity rewards",
    "unconfirmed fills",
    "a venue realizedPnl field on a trade row",
    "balance changes without sufficient source identity",
    "the locked pair gross implied by a cleared pair-cost gate",
)


def _d(v: Any) -> D:
    return D(str(v))


def credited_rebates_usd(executions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Maker rebates the venue ACTUALLY credited, and nothing else.

    On PMUS a credited maker rebate appears as a NEGATIVE
    `commissionNotionalCollected` on our own execution. A positive value is
    a charge, not a rebate, and is reported separately. An absent field is
    absent -- never read as zero credited.
    """
    credited, charged, missing = ZERO, ZERO, 0
    for e in executions or []:
        v = e.get("commissionNotionalCollected")
        if v is None:
            missing += 1
            continue
        d = _d(v)
        if d < 0:
            credited += -d
        else:
            charged += d
    return {
        "credited_usd": str(credited.quantize(USD_Q)),
        "charged_usd": str(charged.quantize(USD_Q)),
        "executions_missing_the_field": missing,
        "source": "negative commissionNotionalCollected on OUR execution",
        "absent_field_read_as_zero_credited": False,
        "label": ("CREDITED, from the venue. Separate from trading P&L and "
                  "from any ESTIMATE."),
    }


def settlement_confirmed(settlement: Optional[Dict[str, Any]]
                         ) -> Dict[str, Any]:
    """Is there a settlement record with CONFIRMED cash, and whose?"""
    if not settlement:
        return {"confirmed": False,
                "why": "NO_SETTLEMENT_RECORD"}
    missing = [k for k in ("market_slug", "resolved_outcome",
                           "cash_usd", "source_identity")
               if settlement.get(k) in (None, "")]
    if missing:
        return {"confirmed": False,
                "why": "SETTLEMENT_RECORD_INCOMPLETE:" + ",".join(missing),
                "note": ("a balance change without a source identity is not "
                         "a settlement; another service shares this "
                         "account.")}
    return {"confirmed": True,
            "market_slug": settlement["market_slug"],
            "resolved_outcome": settlement["resolved_outcome"],
            "cash_usd": str(_d(settlement["cash_usd"])),
            "source_identity": settlement["source_identity"]}


def realized_pnl(inv, settlement: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    """REALIZED net, or None with a refusal. Never an estimate."""
    conf = settlement_confirmed(settlement)
    paid = inv.first.cost_usd + (ZERO if inv.complement is None
                                 else inv.complement.cost_usd)
    if not conf["confirmed"]:
        return {
            "label": "NOT_REALIZED",
            "realized_net_usd": None,
            "refusal": conf["why"],
            "cash_paid_usd": str(paid.quantize(USD_Q)),
            "highest_label_earned": ("FILLED" if inv.first.filled_qty > 0
                                     else "SUBMITTED"),
            "not_realized_includes": list(NOT_REALIZED),
        }
    cash_in = _d(conf["cash_usd"])
    rebates = credited_rebates_usd(
        list(inv.first.executions)
        + (list(inv.complement.executions) if inv.complement else []))
    return {
        "label": "REALIZED",
        "realized_net_usd": str((cash_in - paid).quantize(USD_Q)),
        "settlement_cash_in_usd": str(cash_in.quantize(USD_Q)),
        "cash_paid_usd": str(paid.quantize(USD_Q)),
        "credited_rebates_usd_SEPARATE": rebates["credited_usd"],
        "rebates_netted_into_realized": False,
        "settlement": conf,
    }


def reconcile(inv, *, settlement: Optional[Dict[str, Any]] = None,
              fill_ts: Optional[str] = None) -> Dict[str, Any]:
    """The seven CLAUDE.md accounting lines, each on its own, plus the
    realized verdict. No blended profit figure is produced."""
    matched = inv.matched_qty()
    f_vwap = inv.first.vwap()
    c_vwap = inv.complement.vwap() if inv.complement else None
    pair = None
    if matched > 0 and f_vwap is not None and c_vwap is not None:
        pair = PE.fee_safe_pair_cost(f_vwap, c_vwap, fill_ts=fill_ts)
    all_execs = (list(inv.first.executions)
                 + (list(inv.complement.executions) if inv.complement
                    else []))
    return {
        "probe": "STAGE-6-S6P",
        "slug": inv.slug,
        "lifecycle_labels": list(LIFECYCLE),
        "1_matched_pair_economics": {
            "matched_qty": str(matched),
            "fee_safe_pair_cost_per_contract":
                (pair["fee_safe_pair_cost"] if pair else None),
            "locked_gross_per_contract":
                (pair["locked_gross_per_contract"] if pair else None),
            "clears_ceiling_on_ACTUAL_fills":
                (pair["clears_ceiling"] if pair else None),
            "ceiling_breached_by_the_fills": (
                bool(pair) and not pair["clears_ceiling"]),
            "ceiling_breach_note": (
                None if (pair is None or pair["clears_ceiling"]) else
                "ANOMALY: the realized pair cost is above the $%s ceiling "
                "even though the gate cleared it at the quoted prices. A "
                "post-only BUY cannot fill above its limit, so this means "
                "a fill price, a quantity or a fee was not what the gate "
                "assumed. Investigate before any further probe; do NOT "
                "treat the pair as locked."
                % B.PAIR_COST_CEILING),
            "label": ("LOCKED economics of a completed pair. NOT realized "
                      "until settlement cash is confirmed."),
            "na_not_zero": None if matched > 0 else
            "N/A -- no matched quantity. Reported as N/A, never as zero.",
        },
        "2_directional_residual_pnl": {
            "residual_qty": str(inv.residual_qty()),
            "residual_side": inv.residual_side(),
            "residual_cost_usd": str(inv.residual_notional_usd()),
            "mark_to_market_usd": None,
            "label": ("the residual's outcome is known only at settlement. "
                      "No mark-to-market figure is produced, and no "
                      "predictive skill is assumed."),
        },
        "3_spread_capture": {
            "value_usd": None,
            "label": ("N/A for a post-only pair measured at settlement: "
                      "spread capture is not separable from the pair cost "
                      "without a counterfactual we do not have."),
        },
        "4_maker_rebates_credited": credited_rebates_usd(all_execs),
        "4b_maker_rebate_estimate_FOR_REFERENCE_ONLY": (
            PE.maker_rebate_estimate(f_vwap, inv.first.filled_qty,
                                     fill_ts=fill_ts)
            if f_vwap is not None else None),
        "5_liquidity_rewards": {
            "value_usd": None,
            "label": ("NOT CLAIMED. No liquidity reward is assumed until it "
                      "is credited and source-accounted."),
        },
        "6_fees": {
            "charged_usd": credited_rebates_usd(all_execs)["charged_usd"],
            "basis": "venue commissionNotionalCollected, positive values",
            "schedule_fingerprint": PE.FS.FINGERPRINT,
        },
        "7_settlement_cashflow": settlement_confirmed(settlement),
        "realized": realized_pnl(inv, settlement),
        "inventory": inv.snapshot(),
        "bounds": B.summary(),
        "blended_profit_figure_emitted": False,
    }
