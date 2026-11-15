"""Venue-confirmed economics for the sports two-sided bot.

LIVE 2026-11-15: the bot's ACCOUNT_AUDIT summed `realizedPnl` from every
activity row, but a POSITION_RESOLUTION row carries no such field -- the
venue states settlement P&L as `afterPosition.realized` (cumulative for the
position; see src/pmus_settlement_auditor.py). So every settled market read
as blank and "settled realized P&L" was unknowable. This module is pure: it
takes the bot's own activity rows and book and returns the owner's seven
numbers, each labelled with its source.

PMUS positions are NET: buying NO against a long YES closes YES, and the
venue realizes that on the TRADE row (`trade.realizedPnl`). A lone leg
realizes at resolution. Per market:

    realized = sum(trade.realizedPnl) + sum(after.realized - before.realized)

Fees are reported separately from realized (whether the venue's realizedPnl
nets commission is not assumed). Nothing here is theoretical: unconfirmed
numbers are None, never zero.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional


def _amt(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def _body(r: Mapping[str, Any]):
    for k in ("trade", "positionResolution"):
        if isinstance(r.get(k), Mapping):
            return k, r[k]
    return None, None


def venue_realized(rows: Iterable[Mapping[str, Any]],
                   ours: Iterable[str]) -> Dict[str, Any]:
    """Per-market realized P&L from the venue's own rows, our markets only."""
    ours = set(ours)
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        kind, b = _body(r)
        if not b:
            continue
        slug = str(b.get("marketSlug") or "")
        if slug not in ours:
            continue
        m = per.setdefault(slug, {"trade_realized": D(0), "trades": 0,
                                  "trades_missing_realized": 0,
                                  "settlement_realized": D(0),
                                  "settled": False})
        if kind == "trade":
            m["trades"] += 1
            v = _amt(b.get("realizedPnl"))
            if v is None:
                m["trades_missing_realized"] += 1
            else:
                m["trade_realized"] += v
        else:
            before = b.get("beforePosition") if isinstance(
                b.get("beforePosition"), Mapping) else {}
            after = b.get("afterPosition") if isinstance(
                b.get("afterPosition"), Mapping) else {}
            a = _amt(after.get("realized"))
            if a is None:
                continue
            m["settlement_realized"] += a - (_amt(before.get("realized"))
                                             or D(0))
            m["settled"] = True
    for m in per.values():
        m["realized"] = m["trade_realized"] + m["settlement_realized"]
    settled = {s: m for s, m in per.items() if m["settled"]}
    return {
        "markets": len(per),
        "settled_markets": len(settled),
        "settled_slugs": sorted(settled),
        "settled_realized_usd": sum((m["realized"] for m in settled.values()),
                                    D(0)),
        "unsettled_trade_realized_usd": sum(
            (m["realized"] for s, m in per.items() if s not in settled),
            D(0)),
        "trades_missing_realized": sum(m["trades_missing_realized"]
                                       for m in per.values()),
        "by_market": {s: str(m["realized"]) for s, m in sorted(per.items())},
        "settled_by_sport": _by_sport({s: m["realized"]
                                       for s, m in settled.items()}),
    }


def _by_sport(vals: Mapping[str, D]) -> Dict[str, str]:
    out: Dict[str, D] = {}
    for slug, v in vals.items():
        parts = slug.split("-")
        k = parts[1] if len(parts) > 2 else "?"
        out[k] = out.get(k, D(0)) + v
    return {k: str(v) for k, v in sorted(out.items())}


def book_economics(markets: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """The bot's own book: orders, fills, completed pairs and their gross
    margin, and the cost still sitting in unmatched legs."""
    orders = fills_qty = pairs = margin = unmatched_cost = D(0)
    n_orders = 0
    for mk in markets.values():
        for o in (mk.get("orders") or {}).values():
            n_orders += 1
            fills_qty += D(str(o.get("filled") or "0"))
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        cy, cn = D(mk["cost"]["YES"]), D(mk["cost"]["NO"])
        matched = min(y, n)
        if matched > 0:
            avg = cy / y + cn / n
            pairs += matched
            margin += matched * (D(1) - avg)
        if y > n and y > 0:
            unmatched_cost += cy * (y - n) / y
        elif n > y and n > 0:
            unmatched_cost += cn * (n - y) / n
    return {"orders_submitted": n_orders,
            "filled_contracts": str(fills_qty),
            "completed_pairs": str(pairs),
            "gross_pair_margin_usd": str(margin.quantize(D("0.0001"))),
            "unmatched_cost_usd": str(unmatched_cost.quantize(D("0.0001")))}


def book_settled(markets: Mapping[str, Mapping[str, Any]],
                 since: Optional[float] = None) -> Dict[str, Any]:
    """INDEPENDENT settled P&L from the bot's own venue-confirmed fills and
    the venue's PUBLIC final settlement price: YES pays px, NO pays 1-px.
    Correct with or without netting (gross fills, gross payout). Fees are
    not in the fill cost and are reported separately. `since` keeps only
    markets whose first order was placed at/after that epoch second."""
    tot = D(0)
    per: Dict[str, D] = {}
    for slug, mk in markets.items():
        px = mk.get("settle_px")
        if px is None:
            continue
        if since is not None and _first_order_t(mk) < since:
            continue
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        if y == 0 and n == 0:
            continue
        pnl = y * D(px) + n * (D(1) - D(px)) - D(mk["cost"]["YES"]) \
            - D(mk["cost"]["NO"])
        per[slug] = pnl
        tot += pnl
    worst = min(per.items(), key=lambda kv: kv[1]) if per else None
    return {"markets": len(per), "settled_usd": tot,
            "by_sport": _by_sport(per),
            "worst": {"slug": worst[0], "usd": str(worst[1])} if worst
            else None,
            "by_market": {k: str(v) for k, v in per.items()}}


def _first_order_t(mk: Mapping[str, Any]) -> float:
    ts = [float(o.get("placed_at") or 0) for o in
          (mk.get("orders") or {}).values()]
    return min(ts) if ts else 0.0


def discrepancies(venue_by_market: Mapping[str, str],
                  book_by_market: Mapping[str, str], settled_slugs,
                  tol: D = D("0.02"), limit: int = 8) -> list:
    """Markets where the venue's settled figure and the book's disagree."""
    out = []
    for s in settled_slugs:
        if s in venue_by_market and s in book_by_market:
            d = D(venue_by_market[s]) - D(book_by_market[s])
            if abs(d) > tol:
                out.append({"slug": s, "venue": venue_by_market[s],
                            "book": book_by_market[s], "diff": str(d)})
    out.sort(key=lambda r: -abs(D(r["diff"])))
    return out[:limit]
