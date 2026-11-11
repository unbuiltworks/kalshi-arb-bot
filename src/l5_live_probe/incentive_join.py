"""Can OUR OWN account's rebates and rewards be joined to what generated them?

THE TWO ANSWER DIFFERENTLY, AND THE DIFFERENCE IS THE POINT.

MAKER REBATES -- joinable to the exact FILL. The venue's trade row carries both
sides' executions plus `isAggressor`, which says which one is ours; our side's
`commissionNotionalCollected` is negative when the venue paid us. The row also
carries the trade `id` and `marketSlug`, so credit -> (fill, market) is an
EXACT join, not an allocation. pmus_incentive_audit already computes this
correctly; what it does NOT do is keep the rows -- it truncates `our_rows` to
20 -- so the aggregate survives and the linkage is thrown away. For a probe
that must attribute every cent, retention is the whole requirement.

LIQUIDITY REWARDS -- NOT joinable to a fill, and we do not pretend otherwise.
The earnings rows carry `programType` and `marketSlug` but no programId and no
trade id, because the venue pays for RESTING QUOTES over an epoch rather than
for any individual fill. Reward -> market is available; reward -> order and
reward -> fill are NOT_IDENTIFIABLE by construction. Splitting an epoch's
market reward across that market's fills by volume would be exactly the
proportional allocation that retrospective L5 forensics was closed for.

So the join resolution is reported per instrument, and anything that cannot
reach fill resolution says NOT_IDENTIFIABLE rather than carrying a number.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

LABEL = "INCENTIVE_JOIN/own_account"

FILL_RESOLVED = "FILL"
MARKET_RESOLVED = "MARKET"
NOT_IDENTIFIABLE = "NOT_IDENTIFIABLE"

# A reward status only counts as money when the venue says it paid.
CREDITED_REWARD_STATUSES = ("PAID", "CREDITED", "SETTLED")


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def _commission(ex: Any) -> D:
    if not isinstance(ex, Mapping):
        return D("0")
    raw = ex.get("commissionNotionalCollected")
    v = _d(raw.get("value") if isinstance(raw, Mapping) else raw)
    return v if v is not None else D("0")


def rebate_rows(trades: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One row per trade, RETAINED IN FULL -- never truncated.

    A credit whose fill we no longer have is a credit we cannot attribute, and
    an aggregate we cannot decompose is exactly what 'unattributable' means.
    """
    out: List[Dict[str, Any]] = []
    for tr in trades:
        if not isinstance(tr, Mapping):
            continue
        ours_key = ("aggressorExecution" if tr.get("isAggressor")
                    else "passiveExecution")
        ours = _commission(tr.get(ours_key))
        tid = tr.get("id")
        slug = tr.get("marketSlug")
        out.append({
            "resolution": FILL_RESOLVED if (tid and slug) else NOT_IDENTIFIABLE,
            "trade_id": tid, "market": slug, "order_id": tr.get("orderId"),
            "our_side": ours_key,
            "qty": str(_d(tr.get("qtyDecimal")) or _d(tr.get("qty")) or "0"),
            "our_commission_usd": str(ours),
            # the venue's sign convention: negative commission = paid TO us
            "rebate_credited_usd": str(-ours if ours < 0 else D("0")),
            "fee_charged_usd": str(ours if ours > 0 else D("0")),
        })
    return out


def reward_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One row per earnings entry, at the resolution the venue actually gives."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        st = str(r.get("status") or "STATUS_ABSENT").upper()
        amt = _d(r.get("reward")) or _d(r.get("amount")) or D("0")
        slug = r.get("marketSlug") or r.get("slug")
        out.append({
            # a reward names a market and an epoch, never a fill
            "resolution": MARKET_RESOLVED if slug else NOT_IDENTIFIABLE,
            "fill_resolution": NOT_IDENTIFIABLE,
            "market": slug, "program_type": r.get("programType"),
            "epoch": r.get("epoch") or r.get("periodStart") or r.get("date"),
            "status": st, "amount_usd": str(amt),
            "credited": st in CREDITED_REWARD_STATUSES,
            "credited_usd": str(amt if st in CREDITED_REWARD_STATUSES
                                else D("0")),
        })
    return out


def coverage(rebates: Iterable[Mapping[str, Any]],
             rewards: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Can every credited dollar be pointed at what earned it?"""
    rb = list(rebates)
    rw = list(rewards)
    rb_credit = sum((D(r["rebate_credited_usd"]) for r in rb), D("0"))
    rb_joined = sum((D(r["rebate_credited_usd"]) for r in rb
                     if r["resolution"] == FILL_RESOLVED), D("0"))
    rw_credit = sum((D(r["credited_usd"]) for r in rw), D("0"))
    rw_market = sum((D(r["credited_usd"]) for r in rw
                     if r["resolution"] == MARKET_RESOLVED), D("0"))
    return {
        "label": LABEL,
        "rebates": {
            "rows": len(rb), "credited_usd": str(rb_credit),
            "joined_to_a_fill_usd": str(rb_joined),
            "unattributable_usd": str(rb_credit - rb_joined),
            "fully_attributable": rb_credit == rb_joined,
            "resolution": FILL_RESOLVED,
        },
        "rewards": {
            "rows": len(rw), "credited_usd": str(rw_credit),
            "joined_to_a_market_usd": str(rw_market),
            "joined_to_a_fill_usd": None,
            "fill_resolution": NOT_IDENTIFIABLE,
            "resolution": MARKET_RESOLVED,
            "why": ("the venue pays for resting quotes over an epoch, not for "
                    "any individual fill, so reward -> fill does not exist in "
                    "the source. It is reported NOT_IDENTIFIABLE rather than "
                    "split across a market's fills by volume."),
        },
        "labels": ("credited only. An accrued or pending reward is not income "
                   "and is never added to a realized line."),
    }
