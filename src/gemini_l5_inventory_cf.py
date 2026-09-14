"""READ-ONLY continuous inventory/accounting shadow for canonical L5 on Gemini.

This is NOT a separate Gemini strategy.  It applies the shared L5 behavioral
engine to Gemini tape and reports the same inventory/action/accounting concepts
we use for PMUS: running UP/DOWN averages, matched inventory, directional
residual, sampled/recommended/acted action, gain_per_unit, effective pair cost,
execution conversion, fees, and settlement state.

The replay remains theoretical: public books/prints only, strict trade-through
is hypothetical maker-fill evidence, and settlement is a proxy.  No venue
credentials or order transport exist in this module.
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

FINDING_CLASSES = {
    "shared": "SHARED L5 BEHAVIOR",
    "execution": "VENUE-SPECIFIC EXECUTION",
    "economics": "VENUE-SPECIFIC ECONOMICS/SETTLEMENT",
}


def _d(v: Any, default: str = "0") -> D:
    try:
        return D(str(v))
    except Exception:
        return D(default)


def _tick_floor(px: D) -> D:
    ticks = (px / tb.TICK).to_integral_value(rounding=ROUND_FLOOR)
    return ticks * tb.TICK


def _horizon(symbol: str) -> str:
    s = str(symbol)
    if "BTC05M" in s or "ETH05M" in s:
        return "5m"
    if "BTC15M" in s or "ETH15M" in s:
        return "15m"
    return "other"


def _sampled_action(decision: Mapping[str, Any]) -> Optional[str]:
    return (((decision.get("shadow") or {}).get("stochastic") or {}).get("sampled"))


def _decision_gain(decision: Mapping[str, Any], row: Mapping[str, Any]) -> Optional[D]:
    """Explanatory state only, never a trading rule.

    For the currently-held residual side, mark the held inventory at the
    executable maker-side touch and compare it with its running average cost.
    A positive value means the held side appreciated relative to its running
    average, mechanically cheapening the complementary side.  This is the
    cross-venue gain_per_unit concept; it must not be interpreted as an entry
    predictor or a COMPLETE gate.
    """
    inv = decision.get("inventory_before") or {}
    residual = _d(inv.get("residual"))
    if residual == 0:
        return None
    try:
        if residual > 0:
            avg = D(str(inv.get("avg_up")))
            mark = D(str(row.get("bid")))
        else:
            avg = D(str(inv.get("avg_dn")))
            mark = ONE - D(str(row.get("ask")))
    except Exception:
        return None
    return mark - avg


def _inventory_path(fills: Iterable[Mapping[str, Any]]) -> list[dict]:
    up_q = dn_q = D("0")
    up_cost = dn_cost = D("0")
    fees = D("0")
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
        fees += tb.maker_fee(px, qty)
        matched = min(up_q, dn_q)
        residual = up_q - dn_q
        total = up_q + dn_q
        sign = 1 if residual > 0 else (-1 if residual < 0 else 0)
        avg_up = (up_cost / up_q) if up_q else None
        avg_dn = (dn_cost / dn_q) if dn_q else None
        pair_cost = (avg_up + avg_dn) if avg_up is not None and avg_dn is not None else None
        out.append({
            "fill_idx": i, "cycle": f.get("cycle"), "t": f.get("t"),
            "relation": f.get("relation"), "side": side, "px": str(px), "qty": str(qty),
            "liquidity_role": "maker", "fill_evidence": "strict_trade_through_shadow",
            "up_qty": str(up_q), "dn_qty": str(dn_q), "matched": str(matched),
            "residual": str(residual),
            "avg_up": str(avg_up) if avg_up is not None else None,
            "avg_dn": str(avg_dn) if avg_dn is not None else None,
            "pair_cost_pre_fee": str(pair_cost) if pair_cost is not None else None,
            "effective_pair_cost_pre_fee": str(pair_cost) if pair_cost is not None else None,
            "matched_fraction": (str((D("2") * matched / total)) if total else None),
            "gross_spend": str(up_cost + dn_cost),
            "maker_fees_modeled_cumulative": str(fees),
            "rebates_observed_cumulative": None,
            "matched_increment": str(matched - matched_prev),
            "residual_sign_flip": bool(prev_sign and sign and sign != prev_sign),
            "accounting_stage": "SHADOW",
            "venue_fill_confirmed": False,
            "settled": False,
            "realized": False,
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
        row = ccf._row_at_or_before(bbo_rows, t0)
        if row is None:
            continue
        sampled = _sampled_action(d)
        recommended = (d.get("v2_recommended") or d.get("recommended_relation")
                       or (d.get("prop") or {}).get("recommended_relation"))
        gain = _decision_gain(d, row)
        acted = str(d.get("acted") or "")
        base_row = {
            "cycle": cycle, "t": t0, "tte_s": round(expiry - t0, 3),
            "state": d.get("state"), "sampled_action": sampled,
            "recommended": recommended, "acted": acted,
            "shell_override": bool((sampled is not None and sampled != acted)
                                   or (recommended and recommended != acted)),
            "refusal": d.get("refusal"),
            "p_complete": ccf._relation_prob(d, "complete"),
            "p_extend": ccf._relation_prob(d, "extend"),
            "residual": str(residual),
            "behavioral_residual": inv.get("behavioral_residual"),
            "gain_per_unit": str(gain) if gain is not None else None,
            "favorable_gain_state": bool(gain is not None and gain > 0),
            "flow_60s": row.get("flow_60s"),
            "movement_60s": row.get("movement_60s"),
            "spread": row.get("spread_depth"),
            "book": d.get("book"),
            "classification": FINDING_CLASSES["shared"],
        }
        if residual == 0:
            rows.append({**base_row, "candidates": []})
            continue
        if acted not in ("extend", "complete") and recommended not in ("extend", "complete"):
            rows.append({**base_row, "candidates": []})
            continue
        first_side = "YES" if residual > 0 else "NO"
        comp_yes = residual < 0
        avg_key = "avg_up" if residual > 0 else "avg_dn"
        try:
            first_px = D(str(inv.get(avg_key)))
        except Exception:
            rows.append({**base_row, "candidates": []})
            continue
        populated = qmod.populated_price_arms(row.get("depth") or {}, comp_yes, tb.TICK, max_back=0)
        if not populated:
            rows.append({**base_row, "candidates": []})
            continue
        touch = D(str(populated[0]["price"]))
        max_safe = ccf._max_fee_safe_post_only(row, comp_yes, first_side, first_px)
        if max_safe is None:
            rows.append({**base_row, "first_side": first_side,
                         "first_avg_price": str(first_px), "candidates": []})
            continue
        end = min(t0 + float(gs.SHELLS[PRIMARY_SHELL]["rest_max_s"]), expiry - tb.STOP_BEFORE_EXPIRY_S)
        if end <= t0:
            rows.append({**base_row, "first_side": first_side,
                         "first_avg_price": str(first_px), "candidates": []})
            continue
        candidates = [ccf._candidate(row, comp_yes, name, px, first_side, first_px,
                                     prints, t0, end)
                      for name, px in _ladder_prices(touch, max_safe)]
        touch_c = next((c for c in candidates if c.get("candidate") == "touch"), None)
        rows.append({
            **base_row,
            "first_side": first_side,
            "first_avg_price": str(first_px),
            "effective_pair_cost_touch_after_known_fees": (
                touch_c.get("pair_cost_after_known_fees") if touch_c else None),
            "candidates": candidates,
            "execution_classification": FINDING_CLASSES["execution"],
        })
    return rows


def _continuous_cycles(symbol: str, bbo_rows: list, prints: list, *,
                       model: Mapping[str, Any], art: Optional[Mapping[str, Any]]) -> list[dict]:
    """Run the existing canonical replay with an expanded action budget.

    The inventory accounting below is cumulative across every fill in the
    market.  The underlying historical replay still terminates if it reaches
    a flat COMPLETE state, so a subsequent replay is used only to consume the
    remaining tape.  This is an approximation at the rare flat boundary and
    is explicitly reported; it is not treated as a separate strategy.
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


def _switch_stats(path: list[Mapping[str, Any]]) -> tuple[int, int, Optional[float]]:
    if not path:
        return 0, 0, None
    switches = 0
    runs = 1
    gaps: list[float] = []
    prev_side = path[0].get("side")
    prev_t = float(path[0].get("t") or 0)
    for row in path[1:]:
        side = row.get("side")
        t = float(row.get("t") or 0)
        if side != prev_side:
            switches += 1
            runs += 1
        if t >= prev_t:
            gaps.append(t - prev_t)
        prev_side, prev_t = side, t
    return switches, runs, (statistics.median(gaps) if gaps else None)


def evaluate(symbol: str, bbo_rows: list, prints: list, *,
             model: Mapping[str, Any], art: Optional[Mapping[str, Any]]) -> dict:
    expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(symbol))
    if expiry is None:
        return {"symbol": symbol, "status": "BAD_TICKER", "orders_placed": 0}
    cycles = _continuous_cycles(symbol, bbo_rows, prints, model=model, art=art)
    all_fills: list[dict] = []
    all_orders: list[dict] = []
    all_decisions: list[dict] = []
    all_ladders: list[dict] = []
    pair_pnl = D("0")
    residual_pnl = D("0")
    fees_modeled = D("0")
    for c in cycles:
        n = int(c.get("cycle") or 0)
        for f in c.get("fills") or []:
            all_fills.append({**f, "cycle": n})
        for o in c.get("orders") or []:
            all_orders.append({**o, "cycle": n, "liquidity_role": "maker",
                               "fill_evidence": "strict_trade_through_shadow"})
        for d in c.get("decisions") or []:
            all_decisions.append({**d, "cycle": n})
        all_ladders.extend(_decision_ladder(c, bbo_rows, prints, expiry, n))
        if c.get("pair_pnl_theoretical") is not None:
            pair_pnl += _d(c.get("pair_pnl_theoretical"))
        if c.get("hold_pnl_theoretical") is not None:
            residual_pnl += _d(c.get("hold_pnl_theoretical"))
        if c.get("fees_theoretical") is not None:
            fees_modeled += _d(c.get("fees_theoretical"))
    path = _inventory_path(all_fills)
    matched_increments = sum(1 for r in path if _d(r.get("matched_increment")) > 0)
    sign_flips = sum(1 for r in path if r.get("residual_sign_flip"))
    switches, runs, gap_p50 = _switch_stats(path)
    last = cycles[-1] if cycles else {}
    completed_cycles = sum(1 for c in cycles
                           if _d((c.get("inventory") or {}).get("matched")) > 0
                           and _d((c.get("inventory") or {}).get("residual")) == 0)
    final = path[-1] if path else last.get("inventory")
    gross_spend = _d((final or {}).get("gross_spend") or (final or {}).get("spent"))
    net_theoretical = pair_pnl + residual_pnl
    return {
        "symbol": symbol, "horizon": _horizon(symbol),
        "status": last.get("status"), "stop_reason": last.get("stop_reason"),
        "primary_arm": f"canonical_l5:{PRIMARY_SHELL}:{PRIMARY_POLICY}:max_actions={MAX_ACTIONS}",
        "cycles": len(cycles), "flat_boundary_replays": max(len(cycles) - 1, 0),
        "flat_boundary_behavioral_approximation": bool(len(cycles) > 1),
        "completed_cycles": completed_cycles,
        "fills": len(all_fills), "orders": len(all_orders), "decisions": len(all_decisions),
        "final_inventory": final,
        "pair_pnl_theoretical": str(pair_pnl) if cycles else None,
        "residual_settlement_pnl_theoretical": str(residual_pnl) if cycles else None,
        "net_pnl_theoretical": str(net_theoretical) if cycles else None,
        "maker_fees_modeled": str(fees_modeled),
        "rebates_observed": None,
        "gross_spend_theoretical": str(gross_spend),
        "net_per_gross_spend_theoretical": (str(net_theoretical / gross_spend)
                                             if gross_spend > 0 else None),
        "matched_increments": matched_increments, "residual_sign_flips": sign_flips,
        "switches": switches, "runs": runs, "inter_fill_gap_s_p50": gap_p50,
        "inventory_path": path, "decision_ladders": all_ladders,
        "canonical_decisions": all_decisions, "canonical_orders": all_orders,
        "cycle_results": [{"cycle": c.get("cycle"), "status": c.get("status"),
                           "stop_reason": c.get("stop_reason"),
                           "fills": len(c.get("fills") or []),
                           "inventory": c.get("inventory"),
                           "pair_cost_after_fees": c.get("pair_cost_after_fees"),
                           "pair_pnl_theoretical": c.get("pair_pnl_theoretical"),
                           "hold_pnl_theoretical": c.get("hold_pnl_theoretical"),
                           "fees_theoretical": c.get("fees_theoretical")}
                          for c in cycles],
        "accounting": {
            "stage": "SHADOW", "submitted": False, "venue_filled": False,
            "settled": False, "realized": False,
            "settled_realized_net_usd": None,
            "settled_realized_net_per_capital": None,
        },
        "finding_classification": {
            "inventory_action_state": FINDING_CLASSES["shared"],
            "fill_queue_spread_depth": FINDING_CLASSES["execution"],
            "fees_rebates_settlement": FINDING_CLASSES["economics"],
        },
        "orders_placed": 0,
        "labels": "READ-ONLY canonical-L5 Gemini shadow; cumulative inventory/accounting; strict trade-through hypothetical; all P&L theoretical",
    }


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _p50(values: Iterable[Any]) -> Optional[float]:
    vals = []
    for v in values:
        if v is None:
            continue
        try:
            vals.append(float(v))
        except Exception:
            pass
    return round(statistics.median(vals), 4) if vals else None


def _group_summary(records: list[Mapping[str, Any]]) -> dict:
    decisions = [d for r in records for d in r.get("canonical_decisions") or []]
    orders = [o for r in records for o in r.get("canonical_orders") or []]
    fills = [f for r in records for f in r.get("inventory_path") or []]
    ladders = [d for r in records for d in r.get("decision_ladders") or []]
    complete_orders = [o for o in orders if o.get("relation") == "complete"]
    extend_orders = [o for o in orders if o.get("relation") == "extend"]
    complete_fills = [o for o in complete_orders if o.get("filled")]
    extend_fills = [o for o in extend_orders if o.get("filled")]
    favorable = [d for d in ladders if d.get("favorable_gain_state")]
    matched_fracs = [(r.get("final_inventory") or {}).get("matched_fraction") for r in records]
    terminal_resid = [abs(float(_d((r.get("final_inventory") or {}).get("residual"))))
                      for r in records]
    pair_costs = [c.get("pair_cost_after_fees") for r in records
                  for c in r.get("cycle_results") or [] if c.get("pair_cost_after_fees")]
    pair_pnl = sum(_d(r.get("pair_pnl_theoretical")) for r in records)
    residual_pnl = sum(_d(r.get("residual_settlement_pnl_theoretical")) for r in records)
    fees = sum(_d(r.get("maker_fees_modeled")) for r in records)
    gross = sum(_d(r.get("gross_spend_theoretical")) for r in records)
    return {
        "windows": len(records),
        "decisions": len(decisions), "fills": len(fills),
        "fills_per_action": round(len(fills) / len(decisions), 4) if decisions else None,
        "complete_orders": len(complete_orders), "complete_fills": len(complete_fills),
        "complete_conversion_pct": _pct(len(complete_fills), len(complete_orders)),
        "extend_orders": len(extend_orders), "extend_fills": len(extend_fills),
        "extend_conversion_pct": _pct(len(extend_fills), len(extend_orders)),
        "switches_per_window": round(sum(int(r.get("switches") or 0) for r in records) / len(records), 4) if records else None,
        "runs_per_window": round(sum(int(r.get("runs") or 0) for r in records) / len(records), 4) if records else None,
        "inter_fill_gap_s_p50": _p50(r.get("inter_fill_gap_s_p50") for r in records),
        "favorable_gain_states_per_window": round(len(favorable) / len(records), 4) if records else None,
        "matched_fraction_p50": _p50(matched_fracs),
        "terminal_residual_abs_p50": _p50(terminal_resid),
        "pair_cost_after_known_fees_p50": _p50(pair_costs),
        "pair_pnl_theoretical_total": str(pair_pnl),
        "residual_settlement_pnl_theoretical_total": str(residual_pnl),
        "maker_fees_modeled_total": str(fees),
        "rebates_observed_total": None,
        "gross_spend_theoretical_total": str(gross),
        "net_theoretical_total": str(pair_pnl + residual_pnl),
        "net_per_gross_spend_theoretical": (str((pair_pnl + residual_pnl) / gross) if gross > 0 else None),
        "settled_realized_net_usd_per_window": None,
        "settled_realized_net_per_capital": None,
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
            "strict_completion_rate_pct": _pct(len(strict), len(safe)),
            "strict_latency_s_p50": _p50(c.get("strict_latency_s") for c in strict),
        }
    grouped = {h: [r for r in records if r.get("horizon") == h] for h in ("5m", "15m")}
    with_fills = [r for r in records if int(r.get("fills") or 0) > 0]
    return {
        "windows": len(records), "windows_with_fills": len(with_fills),
        "by_horizon": {h: _group_summary(rs) for h, rs in grouped.items()},
        "overall": _group_summary(records),
        "flat_boundary_replays": sum(int(r.get("flat_boundary_replays") or 0) for r in records),
        "flat_boundary_behavioral_approximation_note": (
            "inventory/accounting is cumulative; only the rare post-flat behavioral continuation currently re-enters through the canonical replay initializer"),
        "inside_spread_ladder": by,
        "max_actions_per_segment": MAX_ACTIONS, "max_segments": MAX_CYCLES,
        "orders_placed": 0,
        "finding_classification": FINDING_CLASSES,
        "labels": "READ-ONLY canonical L5 cross-venue metrics; no venue orders; shadow fills and P&L are theoretical",
    }
