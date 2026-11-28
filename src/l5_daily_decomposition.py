"""L5's trading day, decomposed from his own activity rows. No graph reading.

The methodology is the corrected one from L5_SEPTEMBER_PNL_DECOMPOSITION.md,
and the correction it carries is the whole point: a market L5 loses
completely redeems NOTHING, so filtering on `redeem_qty > 0` deletes losers
by construction. That filter once turned a -$5,048 September into a
+$7,126 September. Here every market L5 touched in the day is carried, and a
market with no redemption row is scored as the total loss it is.

Per market per day:

    spend    sum of usdcSize over BUY rows
    proceeds sum of usdcSize over SELL rows
    redeem   sum of usdcSize over REDEEM rows
    net      redeem + proceeds - spend

Two cohorts, because they behave nothing alike:

    two-sided   L5 bought BOTH outcomes of the market -- the pair trade. Its
                economics are matched-pair economics plus whatever residual
                the imbalance leaves.
    one-sided   L5 bought one outcome only. There is no pair; the whole
                position is directional and settles to 0 or 1.

Within two-sided markets the pair is split further:

    matched     min(qty_up, qty_down) shares, which settle to exactly $1 per
                pair whatever happens. Its P&L is 1 - pair_cost per pair, and
                it is known at trade time, not at settlement.
    residual    the imbalance, |qty_up - qty_down| shares, which settles to
                0 or 1 and is pure direction.

Incentives are counted separately and never folded in. A rebate cannot make
a bad trade good; it can only be reported beside it.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Sequence

BUY, SELL = "BUY", "SELL"


def _d(v: Any) -> D:
    return D(str(v if v is not None else 0))


def by_market(trades: Iterable[Mapping[str, Any]],
              redeems: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Fold a day's rows into one record per conditionId."""
    m: Dict[str, Dict[str, Any]] = {}

    def rec(cid: str) -> Dict[str, Any]:
        r = m.get(cid)
        if r is None:
            r = {"conditionId": cid, "slug": None, "title": None,
                 "spend": D(0), "proceeds": D(0), "redeem": D(0),
                 "qty": collections.defaultdict(D),
                 "cost": collections.defaultdict(D),
                 "n_trades": 0, "n_redeems": 0}
            m[cid] = r
        return r

    for t in trades:
        cid = str(t.get("conditionId") or "")
        if not cid:
            continue
        r = rec(cid)
        r["slug"] = r["slug"] or t.get("slug")
        r["title"] = r["title"] or t.get("title")
        r["n_trades"] += 1
        usd, qty = _d(t.get("usdcSize")), _d(t.get("size"))
        outcome = str(t.get("outcome") or t.get("outcomeIndex") or "")
        if str(t.get("side") or "").upper() == SELL:
            r["proceeds"] += usd
            r["qty"][outcome] -= qty
            r["cost"][outcome] -= usd
        else:
            r["spend"] += usd
            r["qty"][outcome] += qty
            r["cost"][outcome] += usd
    for x in redeems:
        cid = str(x.get("conditionId") or "")
        if not cid:
            continue
        r = rec(cid)
        r["redeem"] += _d(x.get("usdcSize"))
        r["n_redeems"] += 1
    return m


def classify(rec: Mapping[str, Any]) -> str:
    sides = [o for o, q in rec["qty"].items() if q > 0]
    return "two_sided" if len(sides) >= 2 else "one_sided"


def pair_split(rec: Mapping[str, Any]) -> Dict[str, Any]:
    """Matched pairs and the residual, with the matched leg's locked P&L.

    A matched pair pays exactly $1 at settlement regardless of outcome, so
    its economics are fixed the moment both legs fill: $1 minus what the pair
    cost. The residual is whatever is left over on one side and is pure
    direction -- it settles to $1 or to nothing, and nothing about it is
    locked.
    """
    qty = {o: q for o, q in rec["qty"].items() if q > 0}
    cost = rec["cost"]
    if len(qty) < 2:
        o = next(iter(qty), None)
        return {"matched_pairs": D(0), "matched_cost": D(0),
                "matched_locked_pnl": D(0),
                "residual_qty": qty.get(o, D(0)) if o else D(0),
                "residual_side": o, "residual_cost": cost.get(o, D(0))}
    lo = min(qty.values())
    # per-share average cost on each side, so a partial match is priced at
    # what those shares actually cost rather than at the whole side's total
    avg = {o: (cost[o] / qty[o] if qty[o] else D(0)) for o in qty}
    matched_cost = sum((avg[o] * lo for o in qty), D(0))
    big = max(qty, key=lambda o: qty[o])
    resid = qty[big] - lo
    return {"matched_pairs": lo, "matched_cost": matched_cost,
            "matched_locked_pnl": lo * D(1) - matched_cost,
            "residual_qty": resid, "residual_side": big,
            "residual_cost": avg[big] * resid}


def decompose_day(trades: Sequence[Mapping[str, Any]],
                  redeems: Sequence[Mapping[str, Any]],
                  incentives: Mapping[str, Sequence[Mapping[str, Any]]]
                  ) -> Dict[str, Any]:
    """One day, decomposed. Every market carried, losers included."""
    markets = by_market(trades, redeems)
    tot = {"markets": len(markets), "trades": len(trades),
           "redeems": len(redeems),
           "spend": D(0), "proceeds": D(0), "redeem": D(0),
           "matched_pairs": D(0), "matched_cost": D(0),
           "matched_locked_pnl": D(0),
           "residual_qty": D(0), "residual_cost": D(0)}
    cohort = {"two_sided": {"n": 0, "spend": D(0), "net": D(0),
                            "total_loss": 0},
              "one_sided": {"n": 0, "spend": D(0), "net": D(0),
                            "total_loss": 0}}
    for r in markets.values():
        net = r["redeem"] + r["proceeds"] - r["spend"]
        k = classify(r)
        cohort[k]["n"] += 1
        cohort[k]["spend"] += r["spend"]
        cohort[k]["net"] += net
        if r["redeem"] == 0 and r["spend"] > 0:
            cohort[k]["total_loss"] += 1
        for f in ("spend", "proceeds", "redeem"):
            tot[f] += r[f]
        ps = pair_split(r)
        for f in ("matched_pairs", "matched_cost", "matched_locked_pnl",
                  "residual_qty", "residual_cost"):
            tot[f] += ps[f]
    tot["trading_net"] = tot["redeem"] + tot["proceeds"] - tot["spend"]
    tot["incentives"] = {k: sum((_d(x.get("usdcSize")) for x in v), D(0))
                         for k, v in incentives.items()}
    tot["incentives_total"] = sum(tot["incentives"].values(), D(0))
    tot["total_with_incentives"] = tot["trading_net"] + tot["incentives_total"]
    tot["cohorts"] = cohort
    # the residual's own settlement result: what the directional leg did,
    # separated from the matched leg whose result was locked at trade time
    tot["residual_settlement_pnl"] = (tot["trading_net"]
                                      - tot["matched_locked_pnl"])
    tot["labels"] = ("every market carried; a market with no redemption row "
                     "is a total loss, not a missing row. Incentives are "
                     "reported beside trading, never inside it.")
    return tot
