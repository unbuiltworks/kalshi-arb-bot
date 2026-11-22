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

CASH BASIS (owner 2026-11-22, venue cash 321.3138 -> 321.6638 on the first
pilot): the venue nets a YES+NO pair when the second leg fills and pays its
$1 at once. A completed pair is therefore REALIZED CASH at pairing; it does
not wait for a resolution row (none follows for a flat market). Reported
separately, never mixed:

  realized_net_usd        pair edge of every reconciled completed pair
                          - fees charged, + the residual leg's result once
                          its market has settled
  outstanding_*           the unpaired contracts still held and what was
                          paid for them (cash out, not yet a result)
  open_residual_worst_usd those contracts settling worthless
  unrealized_pair_edge_usd always 0 for completed pairs (they are cash);
                          kept so a reader never has to infer it
  fees / rebates          ours only; rebates never netted into P&L

`venue_net` (slug -> signed venue position) makes the netting a checked
fact rather than an assumption: an unresolved market whose venue position
is not |YES - NO| is `position_unconfirmed` and fails closed like
unreconciled fills.

Labels: realized = paired cash + settled residuals. Nothing here places,
cancels or reads the venue itself.
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


def cash_usd(balances: Any) -> Optional[D]:
    """Account cash from the venue's /v1/account/balances answer (a list of
    entries carrying buyingPower; mapping and top-level shapes accepted)."""
    entries: list = []
    raw = balances.get("balances") if isinstance(balances, Mapping) else None
    if isinstance(raw, list):
        entries.extend(raw)
    elif isinstance(raw, Mapping):
        entries.append(raw)
    if isinstance(balances, Mapping):
        entries.append(balances)
    for e in entries:
        if not isinstance(e, Mapping):
            continue
        for k in ("buyingPower", "availableBalance", "cashBalance"):
            v = e.get(k)
            if isinstance(v, Mapping):
                v = v.get("value") if v.get("value") is not None \
                    else v.get("amount")
            d = _d(v)
            if d is not None:
                return d
    return None


def cash_check(cash: Optional[D], expected: D,
               base: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Account-wide venue cash against what the campaign's reconciled trading
    explains, both measured from a stored baseline {cash, expected}.
    REPORT ONLY: the account is shared, so other strategies' trades, reward
    credits and transfers appear in `unexplained_usd`; it is evidence for a
    human, not a gate."""
    if cash is None:
        return {"ok": False, "why": "cash_unreadable"}
    if not base:
        return {"ok": True, "cash_usd": str(cash), "baseline": None}
    d_cash = cash - D(str(base["cash"]))
    d_exp = expected - D(str(base["expected"]))
    return {"ok": True, "cash_usd": str(cash),
            "baseline": {"cash": str(base["cash"]), "t": base.get("t")},
            "cash_change_usd": str(d_cash),
            "explained_by_campaign_usd": str(d_exp),
            "unexplained_usd": str(d_cash - d_exp),
            "label": "account-wide cash; shared account, report only"}


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


def market_result(mk: Mapping[str, Any], v: Optional[Mapping[str, Any]],
                  venue_net: Optional[D] = None,
                  check_position: bool = False) -> Dict[str, Any]:
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
    pair_edge = matched * (1 - cy / y - cn / n) if matched > 0 else D(0)
    # cash basis: completed pairs are paid by the venue at pairing
    paired_cash = pair_edge - v["charged"]
    out.update(matched=str(matched), pair_edge=str(pair_edge),
               realized_pair_cash=str(paired_cash))
    if not v["resolved"] and check_position and \
            abs(abs(venue_net or D(0)) - res_q) > _TOL:
        # the venue does not hold what the book says is unpaired: the
        # netting (and so the realized cash) is not proven for this market
        out.update(status="position_unconfirmed",
                   venue_net=str(venue_net or D(0)))
        return out
    if not v["resolved"] or px is None:
        # the unpaired leg is not realized yet: its WORST settled outcome
        # counts toward the campaign stop (owner 2026-11-21: never
        # underestimate losses). worst_case_net = paired cash - residual cost
        worst = min(y - cy - cn, n - cy - cn) - v["charged"]
        flat = res_q == 0 and matched > 0
        out.update(status="paired_flat" if flat and not v["resolved"]
                   else "open" if not v["resolved"]
                   else "awaiting_settle_px", worst_case_net=str(worst),
                   realized_net=str(paired_cash),
                   outstanding_qty=str(res_q),
                   outstanding_cost=str(res_cost))
        return out
    px = D(str(px))
    gross = y * px + n * (1 - px) - cy - cn
    out.update(status="settled", settle_px=str(px), gross=str(gross),
               residual=str(gross - pair_edge),
               net=str(gross - v["charged"]),
               realized_net=str(gross - v["charged"]),
               outstanding_qty="0", outstanding_cost="0")
    return out


def book_worst_case(markets: Mapping[str, Mapping[str, Any]],
                    since: Optional[float] = None) -> D:
    """The campaign's worst outcome from the BOT'S OWN BOOK, right now:
    settled markets at their result, every other market with its unpaired
    contracts worthless. No venue rows needed, so the engine can check it
    every step instead of waiting for the hourly audit. A floor for the
    loss stop, never a P&L figure."""
    tot = D(0)
    for s_ in pilot_slugs(markets, since):
        mk = markets[s_]
        y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
        c = D(str(mk["cost"]["YES"])) + D(str(mk["cost"]["NO"]))
        px = mk.get("settle_px")
        if px is not None:
            tot += y * D(str(px)) + n * (1 - D(str(px))) - c
        else:
            tot += min(y, n) - c
    return tot


UNCONFIRMED = ("fills_unconfirmed", "position_unconfirmed")


def meter(markets: Mapping[str, Mapping[str, Any]],
          rows: Iterable[Mapping[str, Any]],
          since: Optional[float] = None,
          venue_net: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """`venue_net`: {slug: signed venue position} from a COMPLETE positions
    walk, or None when no such walk is available (position check skipped,
    reported as `positions_checked: False`)."""
    slugs = pilot_slugs(markets, since)
    vs = venue_side(rows, slugs)
    chk = venue_net is not None
    per = {s: market_result(markets[s], vs.get(s),
                            _d((venue_net or {}).get(s)), chk)
           for s in slugs}
    tot = {k: D(0) for k in ("net", "gross", "pair_edge", "residual",
                             "fees_charged", "rebates_credited")}
    status: Dict[str, int] = {}
    worst = None
    open_worst = D(0)
    ex = {"fills": 0, "contracts": D(0), "spent": D(0), "pairs": D(0),
          "pair_cost_sum": D(0), "residual_qty": D(0), "residual_cost": D(0)}
    realized = D(0)
    out_qty = out_cost = fees_all = rebates_all = D(0)
    for r in per.values():
        if r["status"] in UNCONFIRMED:
            continue
        fees_all += D(r["fees_charged"])
        rebates_all += D(r["rebates_credited"])
        if "realized_net" in r:
            realized += D(r["realized_net"])
            out_qty += D(r["outstanding_qty"])
            out_cost += D(r["outstanding_cost"])
    for r in per.values():
        if r["status"] in UNCONFIRMED:
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
            # ---- cash basis: what the venue has actually paid or charged
            "realized_net_usd": str(realized),
            "outstanding_contracts": str(out_qty),
            "outstanding_cost_usd": str(out_cost),
            "open_residual_worst_usd": str(-out_cost),
            "unrealized_pair_edge_usd": "0",
            "fees_charged_usd": str(fees_all),
            "rebates_credited_usd": str(rebates_all),
            # cash this campaign's reconciled trading explains, account-wide
            # cross-check only: realized + rebates - cash tied in residuals
            "expected_cash_change_usd": str(realized + rebates_all - out_cost),
            "positions_checked": chk,
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
                "label": "venue-reconciled fills only; a completed pair's "
                         "edge is realized cash at pairing (venue nets and "
                         "pays $1); residual legs realize at settlement"},
            "worst_market": ({"slug": worst, "net": per[worst]["net"]}
                             if worst else None),
            "unconfirmed": sorted(s for s, r in per.items()
                                  if r["status"] in UNCONFIRMED),
            "by_market": per,
            "label": "realized_net_usd = completed-pair cash (venue fills "
                     "reconciled, position netted) - fees charged + settled "
                     "residual results; settled_* = markets with a venue "
                     "resolution and final price only; rebates and rewards "
                     "separate, never netted"}
