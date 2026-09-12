"""READ-ONLY Gemini complement-price counterfactuals for the L5 transfer.

This module does not invent entry timing or direction. It first replays the
canonical primary Gemini L5 shadow arm (reprice_180s:modal, strict), then uses
only its ACTUAL complete-order decision timestamps to compare execution prices
on the identical tape:

- canonical target (legacy complement economics)
- current touch
- 1 populated level back
- 2 populated levels back
- maximum post-only price that still preserves >=1c after known maker fees

Strict trade-through remains hypothetical maker-fill evidence, not a venue
fill. At-price is reported separately as an optimistic upper bound. No order
transport or credentials exist here.
"""
from __future__ import annotations

import statistics
from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional

from src import gemini_l5_queue_shadow as q
from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb

ONE = D("1")
PRIMARY_SHELL = "reprice_180s"
PRIMARY_POLICY = "modal"


def _row_at_or_before(rows: Iterable[Mapping[str, Any]], t: float) -> Optional[Mapping[str, Any]]:
    valid = [r for r in rows or [] if float(r.get("t") or 0) <= t and r.get("depth")]
    return max(valid, key=lambda r: float(r.get("t") or 0)) if valid else None


def _pair(first_side: str, first_px: D, comp_px: D) -> tuple[D, D]:
    return ((first_px, comp_px) if first_side == "YES"
            else (comp_px, first_px))


def _pair_cost(first_side: str, first_px: D, comp_px: D) -> D:
    yes, no = _pair(first_side, first_px, comp_px)
    return yes + no + tb.maker_fee(yes) + tb.maker_fee(no)


def _edge(first_side: str, first_px: D, comp_px: D) -> D:
    yes, no = _pair(first_side, first_px, comp_px)
    return tb.pair_edge(yes, no)


def _opposite_ask_for_buy(row: Mapping[str, Any], side_yes: bool) -> Optional[D]:
    try:
        return D(str(row["ask"])) if side_yes else ONE - D(str(row["bid"]))
    except Exception:
        return None


def _max_fee_safe_post_only(row: Mapping[str, Any], side_yes: bool,
                            first_side: str, first_px: D) -> Optional[D]:
    """Highest maker BUY price inside/at the spread preserving >=1c edge."""
    arms = q.populated_price_arms(row.get("depth") or {}, side_yes, tb.TICK, max_back=0)
    if not arms:
        return None
    touch = D(str(arms[0]["price"]))
    opp_ask = _opposite_ask_for_buy(row, side_yes)
    if opp_ask is None:
        return None
    best = None
    px = touch
    while px <= tb.MAX_PRICE and px < opp_ask:
        if px >= tb.MIN_PRICE and _edge(first_side, first_px, px) >= tb.MIN_EDGE:
            best = px
        px += tb.TICK
    return best


def _candidate(row: Mapping[str, Any], side_yes: bool, name: str, px: D,
               first_side: str, first_px: D, prints: list,
               t_from: float, t_until: float) -> dict:
    fee_safe = (tb.MIN_PRICE <= px <= tb.MAX_PRICE
                and _edge(first_side, first_px, px) >= tb.MIN_EDGE)
    strict = (tb.complement_fill(prints, first_side, px, t_from, t_until, "strict")
              if fee_safe else None)
    optimistic = (tb.complement_fill(prints, first_side, px, t_from, t_until, "optimistic")
                  if fee_safe else None)
    qm = q.queue_metrics(row.get("depth") or {}, side_yes, px)
    return {
        "candidate": name,
        "price": str(px),
        "fee_safe": fee_safe,
        "pair_cost_after_known_fees": str(_pair_cost(first_side, first_px, px)),
        "edge_after_known_fees": str(_edge(first_side, first_px, px)),
        "queue": qm.as_dict(),
        "strict_fill": strict,
        "optimistic_fill": optimistic,
        "strict_completed": strict is not None,
        "optimistic_completed": optimistic is not None,
        "strict_latency_s": (round(strict["t"] - t_from, 3) if strict else None),
    }


def evaluate(symbol: str, bbo_rows: list, prints: list, *,
             model: Mapping[str, Any], art: Optional[Mapping[str, Any]]) -> dict:
    """Evaluate complement-price alternatives at canonical complete attempts."""
    shell = {**gs.SHELLS[PRIMARY_SHELL], "name": PRIMARY_SHELL}
    base = gs.replay_window(symbol, bbo_rows, prints, model=model, art=art,
                            policy=PRIMARY_POLICY, mode="strict", shell=shell)
    expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(symbol))
    if expiry is None:
        return {"symbol": symbol, "status": "BAD_TICKER"}

    first_fills = [f for f in base.get("fills") or []
                   if f.get("relation") in ("open", "extend")]
    complete_orders = [o for o in base.get("orders") or []
                       if o.get("relation") == "complete"]
    out = {
        "symbol": symbol,
        "status": "SCORED" if complete_orders else "NO_COMPLETE_ATTEMPT",
        "primary_arm": f"{PRIMARY_SHELL}:{PRIMARY_POLICY}",
        "canonical_status": base.get("status"),
        "canonical_stop_reason": base.get("stop_reason"),
        "canonical_first_fills": len(first_fills),
        "canonical_complete_orders": len(complete_orders),
        "canonical_complete_fills": len([f for f in base.get("fills") or []
                                         if f.get("relation") == "complete"]),
        "attempts": [],
        "orders_placed": 0,
        "labels": "READ-ONLY same-tape counterfactual; strict trade-through is hypothetical; P&L not realized",
    }

    for co in complete_orders:
        t0 = float(co.get("t") or 0)
        comp_side = str(co.get("side") or "")
        comp_yes = comp_side == "YES"
        first_side = "NO" if comp_yes else "YES"
        prior = [f for f in first_fills if f.get("side") == first_side
                 and float(f.get("t") or 0) <= t0]
        if not prior:
            continue
        # q=1 fills in this engine, so arithmetic mean is the surplus average.
        first_px = sum((D(str(f["px"])) for f in prior), D("0")) / D(len(prior))
        row = _row_at_or_before(bbo_rows, t0)
        if row is None:
            continue
        end = min(t0 + float(gs.SHELLS[PRIMARY_SHELL]["rest_max_s"]),
                  expiry - tb.STOP_BEFORE_EXPIRY_S)
        if end <= t0:
            continue

        named: list[tuple[str, D]] = [("canonical_target", D(str(co["px"])))]
        populated = q.populated_price_arms(row.get("depth") or {}, comp_yes,
                                           tb.TICK, max_back=2)
        for a in populated:
            named.append((str(a["arm"]), D(str(a["price"]))))
        mx = _max_fee_safe_post_only(row, comp_yes, first_side, first_px)
        if mx is not None:
            named.append(("max_fee_safe_post_only", mx))

        seen: set[tuple[str, str]] = set()
        candidates = []
        for name, px in named:
            ident = (name, str(px))
            if ident in seen:
                continue
            seen.add(ident)
            candidates.append(_candidate(row, comp_yes, name, px, first_side,
                                         first_px, prints, t0, end))

        out["attempts"].append({
            "t": t0,
            "tte_s": round(expiry - t0, 3),
            "first_side": first_side,
            "first_avg_price": str(first_px),
            "first_fill_count": len(prior),
            "complement_side": comp_side,
            "canonical_target": str(co.get("px")),
            "canonical_filled": bool(co.get("filled")),
            "spread": row.get("spread_depth"),
            "flow_60s": row.get("flow_60s"),
            "movement_60s": row.get("movement_60s"),
            "candidates": candidates,
        })
    return out


def summarize(records: Iterable[Mapping[str, Any]]) -> dict:
    records = list(records or [])
    attempts = [a for r in records for a in r.get("attempts") or []]
    names = sorted({c.get("candidate") for a in attempts for c in a.get("candidates") or []
                    if c.get("candidate")})
    by = {}
    for name in names:
        cs = [c for a in attempts for c in a.get("candidates") or []
              if c.get("candidate") == name]
        safe = [c for c in cs if c.get("fee_safe")]
        strict = [c for c in safe if c.get("strict_completed")]
        optimistic = [c for c in safe if c.get("optimistic_completed")]
        lats = [float(c["strict_latency_s"]) for c in strict
                if c.get("strict_latency_s") is not None]
        edges = [float(c["edge_after_known_fees"]) for c in strict]
        queues = [float((c.get("queue") or {}).get("queue_ahead_qty") or 0) for c in safe]
        by[name] = {
            "opportunities": len(cs),
            "fee_safe_opportunities": len(safe),
            "strict_completions": len(strict),
            "strict_completion_rate_pct": (round(100.0 * len(strict) / len(safe), 1)
                                           if safe else None),
            "optimistic_completions": len(optimistic),
            "optimistic_completion_rate_pct": (round(100.0 * len(optimistic) / len(safe), 1)
                                               if safe else None),
            "strict_latency_s_p50": (round(statistics.median(lats), 3) if lats else None),
            "strict_edge_after_fees_mean": (round(statistics.fmean(edges), 4) if edges else None),
            "queue_ahead_qty_p50": (round(statistics.median(queues), 3) if queues else None),
        }
    return {
        "windows": len(records),
        "windows_with_complete_attempt": sum(bool(r.get("attempts")) for r in records),
        "complete_attempts": len(attempts),
        "by_candidate": by,
        "semantics": q.SEMANTICS,
    }
