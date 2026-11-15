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
