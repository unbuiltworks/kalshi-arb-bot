"""THE GEMINI SLATE: what else is executable, and does any of it recycle?

We have been optimising BTC5m. The venue lists 1,001 events across Sports,
Crypto, Weather, Commodities and Economics. This enumerates what is actually
open and runs the SAME two-sided recycling diagnostics on each, so the choice
of habitat rests on measured flow rather than on which contract we happened to
start with.

NO HEADLINE VOLUME RANKING. Volume is what made BTC5m look attractive; what
matters is whether external flow arrives on BOTH sides, because that is what
the inventory oscillator needs to complete. A contract with ten times the
notional and one-directional flow is worse than a quiet one that trades both
ways.

PUBLIC READS ONLY. Nothing here places, sizes or authorises anything.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

EVENTS_PATH = "/v1/prediction-markets/events"
PAGE = 50

#: a contract needs at least this many prints before a two-sidedness figure
#: is stated at all; below it the balance is an artefact of one or two trades
MIN_PRINTS_FOR_BALANCE = 6


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def fetch_page(offset: int = 0, limit: int = PAGE,
               *, timeout: float = 25.0) -> Dict[str, Any]:
    from src.l5_shadow import venues as V
    r = V.get("%s%s?limit=%d&offset=%d" % (V.GEMINI, EVENTS_PATH, limit,
                                           offset), timeout=timeout)
    if not r.get("ok"):
        return {"readable": False, "offset": offset, "err": r.get("err")}
    try:
        d = json.loads(r["body"])
    except ValueError:
        return {"readable": False, "offset": offset, "err": "unparseable"}
    return {"readable": True, "offset": offset,
            "events": d.get("data") or [],
            "total": ((d.get("pagination") or {}).get("total"))}


def enumerate_slate(*, max_events: int = 1200,
                    pause_s: float = 0.15) -> Dict[str, Any]:
    """Every listed event, paged. Unreadable pages are reported, not skipped."""
    out: List[Dict[str, Any]] = []
    bad: List[Dict[str, Any]] = []
    total = None
    off = 0
    while off < max_events:
        p = fetch_page(off)
        if not p.get("readable"):
            bad.append(p)
            break
        total = p.get("total") if total is None else total
        evs = p.get("events") or []
        if not evs:
            break
        out.extend(evs)
        off += len(evs)
        if total is not None and off >= total:
            break
        time.sleep(pause_s)
    return {"events": out, "n": len(out), "total_reported": total,
            "unreadable_pages": bad}


def live_contracts(events: Sequence[Mapping[str, Any]],
                   *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Contracts that are open RIGHT NOW, with what we need to diagnose them.

    A contract is only a candidate if it is still accepting interest; a
    resolved one cannot be quoted into, whatever its history says.
    """
    t = time.time() if now is None else now
    rows = []
    for e in events:
        for c in (e.get("contracts") or []):
            sym = c.get("instrumentSymbol")
            if not sym:
                continue
            state = str(c.get("marketState") or c.get("status") or "")
            resolved = c.get("resolutionSide") not in (None, "")
            exp = c.get("expiryDate") or e.get("expiryDate")
            rows.append({
                "symbol": sym,
                "event_ticker": e.get("ticker") or e.get("slug"),
                "category": e.get("category"),
                "series": ((e.get("series") or {}).get("title")
                           if isinstance(e.get("series"), Mapping)
                           else e.get("series")),
                "title": e.get("title"),
                "is_live": bool(e.get("isLive")),
                "market_state": state,
                "resolved": resolved,
                "expiry": exp,
                "tick": c.get("priceIncrement"),
                "min_qty": c.get("quantityMinimum"),
                "strike": ((c.get("strike") or {}).get("value")
                           if isinstance(c.get("strike"), Mapping) else None),
                "candidate": bool(not resolved),
            })
    return rows


def tape_diagnostics(prints: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The two-sided diagnostics, from a contract's own public tape.

    ALTERNATION is the one that matters most and is the one a volume ranking
    cannot see: how often the aggressor direction CHANGES. A tape that runs
    buy-buy-buy-buy cannot complete an inventory cycle however large it is.
    """
    rows = []
    for t in prints or []:
        ms, px, amt = (_dec(t.get("utc_ms")), _dec(t.get("price")),
                       _dec(t.get("amount")))
        if ms is None or px is None or amt is None:
            continue
        rows.append((ms, px, amt, str(t.get("type"))))
    if not rows:
        return {"prints": 0, "readable": True, "two_sided": None,
                "why": "no prints on this tape"}
    rows.sort(key=lambda r: r[0])
    buys = [r for r in rows if r[3] == "buy"]
    sells = [r for r in rows if r[3] == "sell"]
    span_s = float((rows[-1][0] - rows[0][0]) / D("1000"))
    notional = sum(a * p for _, p, a, _ in rows)
    flips = sum(1 for i in range(1, len(rows)) if rows[i][3] != rows[i - 1][3])
    enough = len(rows) >= MIN_PRINTS_FOR_BALANCE
    return {
        "readable": True,
        "prints": len(rows),
        "contracts": str(sum(a for _, _, a, _ in rows)),
        "notional_usd": str(notional),
        "span_s": round(span_s, 1),
        "prints_per_min": (str(round(D(len(rows)) / D(str(span_s)) * D("60"),
                                     3)) if span_s > 0 else None),
        "notional_per_min": (str(round(notional / D(str(span_s)) * D("60"), 3))
                             if span_s > 0 else None),
        "buy_prints": len(buys), "sell_prints": len(sells),
        "two_sided": (bool(buys and sells) if enough else None),
        "side_balance": (round(min(len(buys), len(sells))
                               / max(len(buys), len(sells)), 3)
                         if enough and max(len(buys), len(sells)) else None),
        "alternations": flips,
        "alternation_rate": (round(flips / (len(rows) - 1), 3)
                             if len(rows) > 1 else None),
        "median_print": str(sorted(a for _, _, a, _ in rows)[len(rows) // 2]),
        "max_print": str(max(a for _, _, a, _ in rows)),
        "balance_stated": enough,
        "why_no_balance": (None if enough else
                           "%d prints is below the %d needed before a "
                           "two-sidedness figure means anything"
                           % (len(rows), MIN_PRINTS_FOR_BALANCE)),
    }


def rank_rows(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Ordered by ALTERNATION then two-sidedness then activity. NOT by volume.

    Deliberately not a score: it is a lexicographic sort so a reader can see
    exactly what is being preferred and in what order, with no weights to
    argue about and no cutoff anywhere.
    """
    def key(r):
        d = r.get("tape") or {}
        alt = _dec(d.get("alternation_rate"))
        bal = _dec(d.get("side_balance"))
        ppm = _dec(d.get("prints_per_min"))
        return (-(alt if alt is not None else D("-1")),
                -(bal if bal is not None else D("-1")),
                -(ppm if ppm is not None else D("-1")))
    return sorted([dict(r) for r in rows], key=key)


def by_series(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Pooled per series, because one contract's tape is a small sample."""
    groups: Dict[str, List[Mapping[str, Any]]] = {}
    for r in rows:
        k = str(r.get("series") or r.get("category") or "UNKNOWN")
        groups.setdefault(k, []).append(r)
    out = {}
    for k, g in groups.items():
        ps = sum(int((r.get("tape") or {}).get("prints") or 0) for r in g)
        notion = sum(_dec((r.get("tape") or {}).get("notional_usd"))
                     or D("0") for r in g)
        alts = [_dec((r.get("tape") or {}).get("alternation_rate"))
                for r in g]
        alts = [a for a in alts if a is not None]
        bals = [_dec((r.get("tape") or {}).get("side_balance")) for r in g]
        bals = [b for b in bals if b is not None]
        two = [r for r in g
               if (r.get("tape") or {}).get("two_sided") is True]
        out[k] = {
            "contracts": len(g), "prints": ps,
            "notional_usd": str(notion),
            "contracts_with_two_sided_flow": len(two),
            "two_sided_share": (round(len(two) / len(g), 3) if g else None),
            "median_alternation_rate": (str(sorted(alts)[len(alts) // 2])
                                        if alts else None),
            "median_side_balance": (str(sorted(bals)[len(bals) // 2])
                                    if bals else None),
            "contracts_with_a_stated_balance": len(bals),
        }
    return out
