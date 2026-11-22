"""Settled net P&L meter for the RN1 live pilot (pure, read-only).

Owner 2026-11-21: measure actual net results from venue fills and
settlements, residual losses and fees included, on the pilot's markets only.

Why not the venue's `trade.realizedPnl`: it is blank on 1120 of 1126 of our
trade rows (2026-11-21 reconciliation), so it cannot be summed. Why not the
book alone: on aec-utr-gaskan-hanbra the book missed a fill that raced a
pending cancel. So each market is counted only when three venue sources
agree:

  fills       the book's per-order fills (each read from the venue's own
              order status), RECONCILED against the venue's trade rows: the
              sum of our trade `qtyDecimal` in the market must equal the
              book's filled contracts. A mismatch -> `fills_unconfirmed`,
              excluded from the total and reported.
  settlement  a venue POSITION_RESOLUTION row for the market AND the
              venue's final public settlement price (`settle_px`).
  fees        the venue's commission on OUR execution of each trade
              (`isAggressor` picks our side); charged fees are subtracted,
              credited rebates are reported separately and never netted in.

Per settled market (YES pays px, NO pays 1 - px):

  gross     = YES qty * px + NO qty * (1 - px) - YES cost - NO cost
  pair_edge = matched * (1 - avg YES px - avg NO px)
  residual  = gross - pair_edge          (the unpaired leg's outcome)
  net       = gross - fees charged

Unsettled pilot markets with reconciled fills contribute their WORST
settled outcome (`open_worst_case_usd`), so the engine's campaign stop can
count losses before they are realized. Unreconciled fills are listed in
`unconfirmed`; the engine fails closed on them.

Labels: only `settled` markets are realized. Nothing here places, cancels
or reads the venue itself.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

PILOT_STAGES = ("rn1_open", "rn1_extend")
_TOL = D("0.000001")


def _d(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def pilot_slugs(markets: Mapping[str, Mapping[str, Any]],
                since: Optional[float] = None,
                stages: Sequence[str] = PILOT_STAGES) -> list:
    """Markets in which the pilot opened: an order with a pilot stage,
    placed at/after `since` (epoch seconds) when given."""
    out = []
    for slug, mk in markets.items():
        for o in (mk.get("orders") or {}).values():
            if str(o.get("stage") or "") in stages and (
                    since is None or float(o.get("placed_at") or 0) >= since):
                out.append(slug)
                break
    return sorted(out)


def _commission(ex: Any) -> D:
    if not isinstance(ex, Mapping):
        return D(0)
    return _d(ex.get("commissionNotionalCollected")) or D(0)


def venue_side(rows: Iterable[Mapping[str, Any]], slugs) -> Dict[str, Any]:
    """Per market from the venue's rows: our traded contracts (qtyDecimal
    only), our fees charged / rebates credited, and whether it resolved."""
    slugs = set(slugs)
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        tr, pr = r.get("trade"), r.get("positionResolution")
        body = tr if isinstance(tr, Mapping) else \
            pr if isinstance(pr, Mapping) else None
        if body is None:
            continue
        slug = str(body.get("marketSlug") or "")
        if slug not in slugs:
            continue
        m = per.setdefault(slug, {"qty": D(0), "trades": 0,
                                  "qty_unreadable": 0, "charged": D(0),
                                  "credited": D(0), "resolved": False})
        if body is pr:
            m["resolved"] = True
            continue
        if "BUSTED" in str(tr.get("state") or ""):
            continue
        m["trades"] += 1
        q = _d(tr.get("qtyDecimal"))
        if q is None:
            m["qty_unreadable"] += 1
        else:
            m["qty"] += abs(q)
        ours = _commission(tr.get("aggressorExecution" if tr.get("isAggressor")
                                  else "passiveExecution"))
        if ours < 0:
            m["credited"] += -ours
        else:
            m["charged"] += ours
    return per


def market_result(mk: Mapping[str, Any], v: Optional[Mapping[str, Any]]
                  ) -> Dict[str, Any]:
    y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
    cy, cn = D(str(mk["cost"]["YES"])), D(str(mk["cost"]["NO"]))
    book_qty = sum((D(str(o.get("filled") or "0"))
                    for o in (mk.get("orders") or {}).values()), D(0))
    v = v or {"qty": D(0), "trades": 0, "qty_unreadable": 0,
              "charged": D(0), "credited": D(0), "resolved": False}
    matched = min(y, n)
    heavy = "YES" if y > n else "NO" if n > y else None
    res_q = abs(y - n)
    res_cost = (cy * res_q / y if heavy == "YES" else
                cn * res_q / n if heavy == "NO" else D(0))
    out: Dict[str, Any] = {
        "yes": str(y), "no": str(n), "cost": str(cy + cn),
        # execution economics (owner 2026-11-22): acquisition cost, pairs,
        # residual -- reported whatever the settlement status
        "avg_yes": str(cy / y) if y else None,
        "avg_no": str(cn / n) if n else None,
        "completed_pairs": str(matched),
        "pair_cost": str(cy / y + cn / n) if matched > 0 else None,
        "residual_side": heavy, "residual_qty": str(res_q),
        "residual_cost": str(res_cost),
        "fills": sum(1 for o in (mk.get("orders") or {}).values()
                     if D(str(o.get("filled") or "0")) > 0),
        "book_filled": str(book_qty), "venue_traded": str(v["qty"]),
        "fees_charged": str(v["charged"]),
        "rebates_credited": str(v["credited"])}
    if v["qty_unreadable"] or abs(book_qty - v["qty"]) > _TOL:
        out["status"] = "fills_unconfirmed"
        return out
    if book_qty == 0:
        out["status"] = "no_fills"
        return out
    px = mk.get("settle_px")
    if not v["resolved"] or px is None:
        # not realized yet: its WORST settled outcome counts toward the
        # campaign stop (owner 2026-11-21: never underestimate losses)
        worst = min(y - cy - cn, n - cy - cn) - v["charged"]
        out.update(status="open" if not v["resolved"]
                   else "awaiting_settle_px", worst_case_net=str(worst))
        return out
    px = D(str(px))
    gross = y * px + n * (1 - px) - cy - cn
    pair_edge = matched * (1 - cy / y - cn / n) if matched > 0 else D(0)
    out.update(status="settled", settle_px=str(px),
               matched=str(matched), gross=str(gross),
               pair_edge=str(pair_edge), residual=str(gross - pair_edge),
               net=str(gross - v["charged"]))
    return out


def meter(markets: Mapping[str, Mapping[str, Any]],
          rows: Iterable[Mapping[str, Any]],
          since: Optional[float] = None) -> Dict[str, Any]:
    slugs = pilot_slugs(markets, since)
    vs = venue_side(rows, slugs)
    per = {s: market_result(markets[s], vs.get(s)) for s in slugs}
    tot = {k: D(0) for k in ("net", "gross", "pair_edge", "residual",
                             "fees_charged", "rebates_credited")}
    status: Dict[str, int] = {}
    worst = None
    open_worst = D(0)
    ex = {"fills": 0, "contracts": D(0), "spent": D(0), "pairs": D(0),
          "pair_cost_sum": D(0), "residual_qty": D(0), "residual_cost": D(0)}
    for r in per.values():
        if r["status"] == "fills_unconfirmed":
            continue
        ex["fills"] += int(r["fills"])
        ex["contracts"] += D(r["yes"]) + D(r["no"])
        ex["spent"] += D(r["cost"])
        ex["pairs"] += D(r["completed_pairs"])
        if r["pair_cost"] is not None:
            ex["pair_cost_sum"] += D(r["pair_cost"]) * D(r["completed_pairs"])
        ex["residual_qty"] += D(r["residual_qty"])
        ex["residual_cost"] += D(r["residual_cost"])
    for s, r in per.items():
        status[r["status"]] = status.get(r["status"], 0) + 1
        if "worst_case_net" in r:
            open_worst += D(r["worst_case_net"])
        if r["status"] != "settled":
            continue
        for k in tot:
            tot[k] += D(r[k])
        if worst is None or D(r["net"]) < D(per[worst]["net"]):
            worst = s
    return {"pilot_markets": len(slugs), "status": status,
            "settled_markets": status.get("settled", 0),
            **{f"settled_{k}_usd": str(v) for k, v in tot.items()},
            "open_worst_case_usd": str(open_worst),
            "execution": {
                "fills": ex["fills"], "contracts": str(ex["contracts"]),
                "spent_usd": str(ex["spent"]),
                "completed_pairs": str(ex["pairs"]),
                "avg_pair_cost": (str(ex["pair_cost_sum"] / ex["pairs"])
                                  if ex["pairs"] else None),
                "locked_pair_edge_usd": str(ex["pairs"] - ex["pair_cost_sum"]),
                "residual_contracts": str(ex["residual_qty"]),
                "residual_cost_usd": str(ex["residual_cost"]),
                "label": "venue-reconciled fills only; locked edge is NOT "
                         "realized until settlement"},
            "worst_market": ({"slug": worst, "net": per[worst]["net"]}
                             if worst else None),
            "unconfirmed": sorted(s for s, r in per.items()
                                  if r["status"] == "fills_unconfirmed"),
            "by_market": per,
            "label": "REALIZED only for status=settled (venue fills "
                     "reconciled + venue resolution + final price); net = "
                     "gross - fees charged; rebates separate, never netted"}
