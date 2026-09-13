"""READ-ONLY continuous multi-fill inventory-path shadow for Gemini L5 transfer.

This layer tests the L5 mechanism as a moving inventory book: repeated maker
accumulation, completion, reset-to-flat, and re-entry inside one short-duration
window. A completed pair does NOT end the research replay; a fresh behavioral
cycle begins on the remaining same-window tape. Strict trade-through remains
hypothetical maker-fill evidence only. No credentials, order transport, or live
execution exist here.
"""
from __future__ import annotations

import statistics
from decimal import Decimal as D, ROUND_FLOOR
from typing import Any, Iterable, Mapping, Optional

from src import gemini_l5_complement_cf as ccf
from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb

PRIMARY_SHELL = "reprice_180s"
PRIMARY_POLICY = "modal"
MAX_ACTIONS = 64
MAX_CYCLES = 16
ONE = D("1")


def _d(v: Any, default: str = "0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def _tick_floor(px: D) -> D:
    ticks = (px / tb.TICK).to_integral_value(rounding=ROUND_FLOOR)
    return ticks * tb.TICK


def _inventory_path(fills: Iterable[Mapping[str, Any]]) -> list[dict]:
    up_q = dn_q = D("0")
    up_cost = dn_cost = D("0")
    out: list[dict] = []
    prev_sign = 0
    matched_prev = D("0")
    for i, f in enumerate(sorted(fills, key=lambda x: float(x.get("t") or 0)), 1):
        side = str(f.get("side") or "")
        qty = _d(f.get("qty"), "1")
        px = _d(f.get("px"))
        if side == "YES":
            up_q += qty; up_cost += px * qty
        elif side == "NO":
            dn_q += qty; dn_cost += px * qty
        else:
            continue
        matched = min(up_q, dn_q)
        residual = up_q - dn_q
        sign = 1 if residual > 0 else (-1 if residual < 0 else 0)
        avg_up = (up_cost / up_q) if up_q else None
        avg_dn = (dn_cost / dn_q) if dn_q else None
        pair_cost = (avg_up + avg_dn) if avg_up is not None and avg_dn is not None else None
        out.append({
            "fill_idx": i, "cycle": f.get("cycle"), "t": f.get("t"),
            "relation": f.get("relation"), "side": side, "px": str(px), "qty": str(qty),
            "up_qty": str(up_q), "dn_qty": str(dn_q), "matched": str(matched),
            "residual": str(residual),
            "avg_up": str(avg_up) if avg_up is not None else None,
            "avg_dn": str(avg_dn) if avg_dn is not None else None,
            "pair_cost_pre_fee": str(pair_cost) if pair_cost is not None else None,
            "matched_increment": str(matched - matched_prev),
            "residual_sign_flip": bool(prev_sign and sign and sign != prev_sign),
        })
        matched_prev = matched
        if sign:
            prev_sign = sign
    return out


def _ladder_prices(touch: D, max_safe: D) -> list[tuple[str, D]]:
    named: list[tuple[str, D]] = [("touch", touch)]
    if max_safe <= touch:
        if max_safe != touch:
            named.append(("max_fee_safe_post_only", max_safe))
        return named
    span = max_safe - touch
    for name, frac in (("inside_25", D("0.25")), ("inside_50", D("0.50")),
                       ("inside_75", D("0.75"))):
        px = _tick_floor(touch + span * frac)
        if px > touch and px < max_safe and all(px != p for _, p in named):
            named.append((name, px))
    if all(max_safe != p for _, p in named):
        named.append(("max_fee_safe_post_only", max_safe))
    return named


def _decision_ladder(base: Mapping[str, Any], bbo_rows: list, prints: list,
                     expiry: float, cycle: int) -> list[dict]:
    rows: list[dict] = []
    qmod = __import__("src.gemini_l5_queue_shadow", fromlist=["populated_price_arms"])
    for d in base.get("decisions") or []:
        t0 = float(d.get("t") or 0)
        inv = d.get("inventory_before") or {}
        residual = _d(inv.get("residual"))
        if residual == 0:
            continue
        acted = str(d.get("acted") or "")
        recommended = (d.get("v2_recommended") or d.get("recommended_relation")
                       or (d.get("prop") or {}).get("recommended_relation"))
        if acted not in ("extend", "complete") and recommended not in ("extend", "complete"):
            continue
        row = ccf._row_at_or_before(bbo_rows, t0)
        if row is None:
            continue
        first_side = "YES" if residual > 0 else "NO"
        comp_yes = residual < 0
        avg_key = "avg_up" if residual > 0 else "avg_dn"
        try:
            first_px = D(str(inv.get(avg_key)))
        except Exception:
            continue
        populated = qmod.populated_price_arms(row.get("depth") or {}, comp_yes, tb.TICK, max_back=0)
        if not populated:
            continue
        touch = D(str(populated[0]["price"]))
        max_safe = ccf._max_fee_safe_post_only(row, comp_yes, first_side, first_px)
        if max_safe is None:
            continue
        end = min(t0 + float(gs.SHELLS[PRIMARY_SHELL]["rest_max_s"]), expiry - tb.STOP_BEFORE_EXPIRY_S)
        if end <= t0:
            continue
        candidates = [ccf._candidate(row, comp_yes, name, px, first_side, first_px,
                                     prints, t0, end)
                      for name, px in _ladder_prices(touch, max_safe)]
        rows.append({
            "cycle": cycle, "t": t0, "tte_s": round(expiry - t0, 3),
            "state": d.get("state"), "recommended": recommended, "acted": acted,
            "refusal": d.get("refusal"), "p_complete": ccf._relation_prob(d, "complete"),
            "p_extend": ccf._relation_prob(d, "extend"), "residual": str(residual),
            "behavioral_residual": inv.get("behavioral_residual"), "first_side": first_side,
            "first_avg_price": str(first_px), "flow_60s": row.get("flow_60s"),
            "movement_60s": row.get("movement_60s"), "spread": row.get("spread_depth"),
            "candidates": candidates,
        })
    return rows


def _continuous_cycles(symbol: str, bbo_rows: list, prints: list, *,
                       model: Mapping[str, Any], art: Optional[Mapping[str, Any]]) -> list[dict]:
    """Replay successive flat-to-flat campaigns on the remaining SAME tape.

    This is intentionally a research counterfactual, not the canonical executor:
    after a matched flat cycle, L5-style inventory management can open again rather
    than treating the first pair as terminal. Residual/failed cycles remain terminal.
    """
    shell = {**gs.SHELLS[PRIMARY_SHELL], "name": PRIMARY_SHELL}
    remaining = list(bbo_rows)
    cycles: list[dict] = []
    last_cut = float("-inf")
    for cycle in range(1, MAX_CYCLES + 1):
        if not remaining:
            break
        base = gs.replay_window(symbol, remaining, prints, model=model, art=art,
                                policy=PRIMARY_POLICY, mode="strict", shell=shell,
                                max_actions=MAX_ACTIONS)
        base["cycle"] = cycle
        cycles.append(base)
        fills = base.get("fills") or []
        inv = base.get("inventory") or {}
        flat = _d(inv.get("residual")) == 0
        matched = _d(inv.get("matched")) > 0
        if not (fills and flat and matched):
            break
        cut = max(float(f.get("t") or 0) for f in fills)
        if cut <= last_cut:
            break
        last_cut = cut
        remaining = [r for r in remaining if float(r.get("t") or 0) > cut]
    return cycles


def evaluate(symbol: str, bbo_rows: list, prints: list, *,
             model: Mapping[str, Any], art: Optional[Mapping[str, Any]]) -> dict:
    expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(symbol))
    if expiry is None:
        return {"symbol": symbol, "status": "BAD_TICKER", "orders_placed": 0}
    cycles = _continuous_cycles(symbol, bbo_rows, prints, model=model, art=art)
    all_fills: list[dict] = []
    all_ladders: list[dict] = []
    total_orders = total_decisions = 0
    pair_pnl = D("0")
    for c in cycles:
        n = int(c.get("cycle") or 0)
        for f in c.get("fills") or []:
            all_fills.append({**f, "cycle": n})
        all_ladders.extend(_decision_ladder(c, bbo_rows, prints, expiry, n))
        total_orders += len(c.get("orders") or [])
        total_decisions += len(c.get("decisions") or [])
        if c.get("pair_pnl_theoretical") is not None:
            pair_pnl += _d(c.get("pair_pnl_theoretical"))
    path = _inventory_path(all_fills)
    matched_increments = sum(1 for r in path if _d(r.get("matched_increment")) > 0)
    sign_flips = sum(1 for r in path if r.get("residual_sign_flip"))
    last = cycles[-1] if cycles else {}
    completed_cycles = sum(1 for c in cycles
                           if _d((c.get("inventory") or {}).get("matched")) > 0
                           and _d((c.get("inventory") or {}).get("residual")) == 0)
    return {
        "symbol": symbol,
        "status": last.get("status"), "stop_reason": last.get("stop_reason"),
        "primary_arm": f"continuous_flat_reset:{PRIMARY_SHELL}:{PRIMARY_POLICY}:max_actions={MAX_ACTIONS}",
        "cycles": len(cycles), "completed_cycles": completed_cycles,
        "fills": len(all_fills), "orders": total_orders, "decisions": total_decisions,
        "final_inventory": (path[-1] if path else last.get("inventory")),
        "pair_pnl_theoretical": str(pair_pnl) if cycles else None,
        "matched_increments": matched_increments, "residual_sign_flips": sign_flips,
        "inventory_path": path, "decision_ladders": all_ladders,
        "cycle_results": [{"cycle": c.get("cycle"), "status": c.get("status"),
                           "stop_reason": c.get("stop_reason"),
                           "fills": len(c.get("fills") or []),
                           "inventory": c.get("inventory"),
                           "pair_cost_after_fees": c.get("pair_cost_after_fees"),
                           "pair_pnl_theoretical": c.get("pair_pnl_theoretical")}
                          for c in cycles],
        "orders_placed": 0,
        "labels": "READ-ONLY continuous multi-fill inventory shadow; flat matched cycles restart on remaining tape; strict trade-through hypothetical; all P&L theoretical",
    }


def summarize(records: Iterable[Mapping[str, Any]]) -> dict:
    records = list(records or [])
    ladders = [d for r in records for d in r.get("decision_ladders") or []]
    names = sorted({c.get("candidate") for d in ladders for c in d.get("candidates") or [] if c.get("candidate")})
    by = {}
    for name in names:
        cs = [c for d in ladders for c in d.get("candidates") or [] if c.get("candidate") == name]
        safe = [c for c in cs if c.get("fee_safe")]
        strict = [c for c in safe if c.get("strict_completed")]
        by[name] = {
            "opportunities": len(cs), "fee_safe_opportunities": len(safe),
            "strict_completions": len(strict),
            "strict_completion_rate_pct": round(100 * len(strict) / len(safe), 1) if safe else None,
            "strict_latency_s_p50": (round(statistics.median(float(c["strict_latency_s"])
                                      for c in strict if c.get("strict_latency_s") is not None), 3)
                                      if any(c.get("strict_latency_s") is not None for c in strict) else None),
        }
    with_fills = [r for r in records if int(r.get("fills") or 0) > 0]
    return {
        "windows": len(records), "windows_with_fills": len(with_fills),
        "cycles": sum(int(r.get("cycles") or 0) for r in records),
        "completed_cycles": sum(int(r.get("completed_cycles") or 0) for r in records),
        "total_fills": sum(int(r.get("fills") or 0) for r in records),
        "matched_increments": sum(int(r.get("matched_increments") or 0) for r in records),
        "residual_sign_flips": sum(int(r.get("residual_sign_flips") or 0) for r in records),
        "max_actions_per_cycle": MAX_ACTIONS, "max_cycles": MAX_CYCLES,
        "inside_spread_ladder": by, "orders_placed": 0,
        "labels": "READ-ONLY continuous inventory cycles; no venue orders; all fills hypothetical strict trade-through",
    }
