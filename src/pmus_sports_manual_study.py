"""READ-ONLY study of the owner's MANUAL sports fills.

OWNER 2026-11-18: "im getting so many manual fills right now. Why can't we
replicate this on a larger scale?" -- sports, resting and waiting, and they
make money. Before copying anything, measure what those fills actually are.

Input is the activity walk the bot's ACCOUNT_AUDIT already makes every cycle
(/v1/portfolio/activities), so this costs no extra venue call and has no
order path at all: it is a pure function over rows already in memory.

A row is attributed to the OWNER when its market is a sports market
(SPORTS_PREFIXES) that this bot has NEVER traded (not in its book). That is
conservative: a market both the bot and the owner touched is left out rather
than mis-attributed. Crypto (cpc*) and every non-sports market is excluded,
so nothing of the BTC lanes is read as manual.

Per market it reports what the venue says happened: trades, contracts,
average price, passive (resting) vs aggressive share, first/last fill time,
realized P&L from trades and from settlement (the same arithmetic as
pmus_sports_pnl.venue_realized), and whether the market has settled. Only
settled markets are labelled realized.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

SPORTS_PREFIXES = ("aec-", "asc-", "tsc-", "atc-", "astatc-")


def _d(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    try:
        return D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def _body(r: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    for k in ("trade", "positionResolution"):
        if isinstance(r.get(k), Mapping):
            return r[k]
    return None


def manual_rows(rows: Iterable[Mapping[str, Any]], ours: Iterable[str],
                prefixes=SPORTS_PREFIXES) -> List[Mapping[str, Any]]:
    """Activity rows in sports markets this bot never traded."""
    ours = set(ours)
    out = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        b = _body(r)
        if not b:
            continue
        slug = str(b.get("marketSlug") or "")
        if slug and slug.startswith(tuple(prefixes)) and slug not in ours:
            out.append(r)
    return out


def manual_orders(rows: Iterable[Mapping[str, Any]], ours: Iterable[str],
                  max_orders: int = 20) -> List[Dict[str, Any]]:
    """The venue ORDERS behind the owner's manual sports trades, newest
    first: one entry per order id, taken from OUR side's execution record
    on each trade row (the aggressor's when we were the aggressor, else the
    passive one).

    OWNER 2026-11-25: "Use these details to locate the original order
    through authenticated venue execution or transaction history." The
    account ledger separates manual results from bot results only for
    orders the owner attests BY ID, and the venue's app does not show the
    id of a filled order. This walk already holds it. The id is printed in
    full because the attestation needs it; nothing else about the account
    is. Read-only, no venue call of its own."""
    seen: Dict[str, Dict[str, Any]] = {}
    for r in manual_rows(rows, ours):
        t = r.get("trade")
        if not isinstance(t, Mapping):
            continue
        ex = t.get("aggressorExecution" if t.get("isAggressor")
                   else "passiveExecution")
        o = ex.get("order") if isinstance(ex, Mapping) else None
        if not isinstance(o, Mapping) or not o.get("id"):
            continue
        oid = str(o["id"])
        avg = o.get("avgPx")
        ent = seen.setdefault(oid, {
            "order_id": oid, "market": str(t.get("marketSlug")),
            "manual_indicator": o.get("manualOrderIndicator"),
            "intent": o.get("intent"), "action": o.get("action"),
            "side": o.get("side"), "outcome_side": o.get("outcomeSide"),
            "filled": str(o.get("cumQuantity")),
            "avg_px": (avg.get("value") if isinstance(avg, Mapping)
                       else avg),
            "created": str(o.get("createTime") or ""),
            "aggressor": bool(t.get("isAggressor")), "trades": 0,
            "last_trade": ""})
        ent["trades"] += 1
        ts = str(t.get("createTime") or "")
        if ts > ent["last_trade"]:
            ent["last_trade"] = ts
    out = sorted(seen.values(), key=lambda e: e["last_trade"], reverse=True)
    return out[:max(0, int(max_orders))]


def summarize(rows: Iterable[Mapping[str, Any]], ours: Iterable[str],
              max_markets: int = 40) -> Dict[str, Any]:
    """Per-market summary of the owner's manual sports activity."""
    per: Dict[str, Dict[str, Any]] = {}
    sample_keys = None
    for r in manual_rows(rows, ours):
        if isinstance(r.get("trade"), Mapping):
            t = r["trade"]
            if sample_keys is None:
                sample_keys = sorted(t.keys())[:40]
            slug = str(t.get("marketSlug"))
            m = per.setdefault(slug, _new())
            q = _d(t.get("qtyDecimal")) or _d(t.get("qty")) or D(0)
            px = _d(t.get("price"))
            m["trades"] += 1
            m["contracts"] += q
            if px is not None:
                m["notional"] += px * q
                m["px_min"] = px if m["px_min"] is None else min(m["px_min"],
                                                                  px)
                m["px_max"] = px if m["px_max"] is None else max(m["px_max"],
                                                                  px)
            if t.get("isAggressor") is True:
                m["aggressive"] += 1
            elif t.get("isAggressor") is False:
                m["passive"] += 1
            rp = _d(t.get("realizedPnl"))
            if rp is not None:
                m["trade_realized"] += rp
            ts = str(t.get("createTime") or "")
            if ts:
                m["first"] = ts if not m["first"] else min(m["first"], ts)
                m["last"] = ts if not m["last"] else max(m["last"], ts)
        elif isinstance(r.get("positionResolution"), Mapping):
            b = r["positionResolution"]
            slug = str(b.get("marketSlug"))
            m = per.setdefault(slug, _new())
            before = b.get("beforePosition") if isinstance(
                b.get("beforePosition"), Mapping) else {}
            after = b.get("afterPosition") if isinstance(
                b.get("afterPosition"), Mapping) else {}
            a, bf = _d(after.get("realized")), _d(before.get("realized"))
            if a is not None:
                m["settlement_realized"] += a - (bf or D(0))
            m["settled"] = True
    markets = []
    tot = _new()
    for slug, m in per.items():
        m["realized"] = m["trade_realized"] + m["settlement_realized"]
        n_side = m["passive"] + m["aggressive"]
        markets.append({
            "market": slug, "sport": slug.split("-")[1] if "-" in slug
            else "?", "trades": m["trades"], "contracts": str(m["contracts"]),
            "avg_px": str(round(m["notional"] / m["contracts"], 4))
            if m["contracts"] else None,
            "px_range": [str(m["px_min"]), str(m["px_max"])],
            "passive_share": round(m["passive"] / n_side, 3) if n_side
            else None,
            "first": m["first"], "last": m["last"],
            "settled": m["settled"], "realized_usd": str(m["realized"])})
        for k in ("trades", "passive", "aggressive"):
            tot[k] += m[k]
        tot["contracts"] += m["contracts"]
        if m["settled"]:
            tot["settled_markets"] += 1
            tot["settled_realized"] += m["realized"]
        else:
            tot["open_trade_realized"] += m["realized"]
    markets.sort(key=lambda x: x["last"] or "", reverse=True)
    n_side = tot["passive"] + tot["aggressive"]
    return {
        "markets": len(per), "trades": tot["trades"],
        "contracts": str(tot["contracts"]),
        "passive_share": round(tot["passive"] / n_side, 3) if n_side
        else None,
        "settled_markets": tot["settled_markets"],
        "settled_realized_usd": str(tot["settled_realized"]),
        "unsettled_trade_realized_usd": str(tot["open_trade_realized"]),
        "by_market": markets[:max_markets],
        "trade_fields": sample_keys,
        "label": ("READ-ONLY: the owner's manual sports fills (sports markets "
                  "this bot never traded), from the audit's existing activity "
                  "walk. Only settled_realized_usd is realized."),
    }


def _new() -> Dict[str, Any]:
    return {"trades": 0, "contracts": D(0), "notional": D(0), "px_min": None,
            "px_max": None, "passive": 0, "aggressive": 0,
            "trade_realized": D(0), "settlement_realized": D(0),
            "settled": False, "first": "", "last": "",
            "settled_markets": 0, "settled_realized": D(0),
            "open_trade_realized": D(0)}
