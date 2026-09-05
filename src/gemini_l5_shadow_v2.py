"""Gemini L5 SHADOW/replay under the SAME v2 policy the PMUS probe runs.

OWNER 5554263833 items 3-4. Two entry points, both zero-order:

* `replay(...)` -- sequential replay over the retained Gemini BTC-5m
  tape (`data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz` quotes +
  `gemini_prints_btc05m.jsonl.gz` prints). Every decision goes through
  `pmus_l5_persistence_v2.proposal` on the normalized state from
  `l5_venue_adapter` (price band | TTE | behavioral residual, last_rel,
  last_size) under the PMUS safety shell: q=1 behavioral unit, maker
  join at the touch, post-only (never at/through the opposite ask),
  rest bounded to 30s and cancelled when the touch moves, residual cap
  3 units, off-distribution -> HOLD, complement <=0.98 pair and >=1c
  after the venue's KNOWN fees, HOLD to settlement. Two policy tracks
  (modal = what the live PMUS engine executes; stochastic = the seeded
  draw the live engine only logs) x two fill tiers (strict = a print
  THROUGH our price; optimistic = a print AT our price, an upper bound).
* `live_shadow_step(...)` -- one realtime decision from a public
  Gemini book (`/v1/book/{symbol}`): what L5 would do now, side, price,
  L5 target size/aggressiveness, and executability (priceable side,
  band, fee-safe complement, spread). Nothing is sent to the venue.

Every P&L number here is THEORETICAL (fills assumed from prints, queue
position unobservable, settlement by the mid proxy). Nothing is
realized; nothing is armed.

    python3 -m src.gemini_l5_shadow_v2            # replay -> analysis/l5/gemini_l5_shadow_v2.json
    python3 -m src.gemini_l5_shadow_v2 --live 30  # 30 min public-book shadow
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.l5_venue_adapter import (GeminiAdapter, L5Inventory, NormalizedBook,
                                  MAX_RESIDUAL_UNITS, decision_record,
                                  l5_state, open_side_yes)
from src.polymarket_l5 import transfer_backtest as tb

ROOT = Path(__file__).resolve().parents[1]
BBO_PATH = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
PRINTS_PATH = ROOT / "data/transfer_backtest/gemini_prints_btc05m.jsonl.gz"
OUT_PATH = ROOT / "analysis/l5/gemini_l5_shadow_v2.json"
SPORTS_REF = ROOT / "analysis/l5/transfer_backtest_sports.json"

ONE = D("1")
UNIT = D("1")                      # q=1 behavioral unit (one contract)
MAX_ACTIONS = 6                    # PMUS_SPRING_MAX_ACTIONS default
REST_MAX_S = 30.0                  # SPORTS_MAKER_REST_MAX_S
MIN_REEVAL_S = 2.0
ADVERSE_HORIZON_S = 30.0
ADVERSE_HORIZONS_S = (5.0, 15.0, 30.0, 60.0)   # OWNER 5554553793 B item 4
UNIT_CAPITAL_USD = D("0.80")                 # max cost of one q=1 unit
POLICIES = ("modal", "stochastic")
MODES = ("strict", "optimistic")
# OWNER 5554553793 B: execution shells shadowed in parallel. The first is
# the live PMUS shell; the others vary ONLY the rest/cancel discipline
# (the identified transfer blocker). Fills stay strict; zero orders.
SHELLS = {
    "touch_cancel_30s": {"rest_max_s": 30.0, "cancel_on_touch_move": True,
                         "reprice": False, "label": "live PMUS shell"},
    "rest_60s": {"rest_max_s": 60.0, "cancel_on_touch_move": False,
                 "reprice": False, "label": "bounded rest, no cancel"},
    "rest_120s": {"rest_max_s": 120.0, "cancel_on_touch_move": False,
                  "reprice": False, "label": "bounded rest, no cancel"},
    "rest_180s": {"rest_max_s": 180.0, "cancel_on_touch_move": False,
                  "reprice": False, "label": "vendored FIRST_WAIT rest"},
    "reprice_180s": {"rest_max_s": 180.0, "cancel_on_touch_move": True,
                     "reprice": True,
                     "label": "cancel on touch move, re-join the new touch, "
                              "180s total"},
}
LABELS = ("THEORETICAL Gemini shadow/replay; zero orders; fills assumed "
          "from public prints (queue position unobservable); settlement "
          "by mid proxy; nothing realized; nothing armed")


def _book_at(rows: list, t: float) -> Optional[dict]:
    last = None
    for r in rows:
        if r["t"] <= t:
            last = r
        else:
            break
    return last


def _mid_at(rows: list, t: float) -> Optional[D]:
    r = _book_at(rows, t)
    if r is None:
        return None
    return (D(r["bid"]) + D(r["ask"])) / 2


def _touch_moved_at(rows: list, adapter: GeminiAdapter, side_yes: bool,
                    px: D, t_from: float, t_until: float,
                    expiry: float) -> Optional[float]:
    """First quote after t_from where our touch join is no longer the
    touch (the live engine cancels the resting order then)."""
    for r in rows:
        if r["t"] <= t_from:
            continue
        if r["t"] > t_until:
            break
        b = adapter.normalize(r, expiry=expiry)
        if b is None or b.join_price(side_yes) != px:
            return r["t"]
    return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _quartiles(xs: list) -> Optional[list]:
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return None
    q = statistics.quantiles(xs, n=4) if len(xs) >= 2 else [xs[0]] * 3
    return [round(v, 4) for v in q]


def _median(xs: list) -> Optional[float]:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def replay_window(symbol: str, bbo_rows: list, prints: list, *,
                  model: Mapping[str, Any], art: Optional[Mapping[str, Any]],
                  policy: str, mode: str,
                  adapter: Optional[GeminiAdapter] = None,
                  max_actions: int = MAX_ACTIONS,
                  shell: Optional[Mapping[str, Any]] = None) -> dict:
    adapter = adapter or GeminiAdapter()
    shell = dict(shell or SHELLS["touch_cancel_30s"])
    rest_max = float(shell.get("rest_max_s", REST_MAX_S))
    event = tb.event_of_symbol(symbol)
    expiry = tb.ticker_expiry_epoch(event)
    if expiry is None:
        return {"symbol": symbol, "status": "BAD_TICKER"}
    stop_at = expiry - tb.STOP_BEFORE_EXPIRY_S
    entry = None
    for r in bbo_rows:
        if tb.MIN_TTE_S <= expiry - r["t"] <= tb.ENTRY_MAX_TTE_S:
            entry = r
            break
    if entry is None:
        return {"symbol": symbol, "status": "NO_QUOTE_COVERAGE"}
    t = entry["t"]
    inv = L5Inventory()
    decisions: list[dict] = []
    orders: list[dict] = []
    fills: list[dict] = []
    model_sha = str(model.get("_sha256_16") or "")
    entry_book = adapter.normalize(entry, expiry=expiry)
    out: dict = {"symbol": symbol, "event": event, "policy": policy,
                 "fill_mode": mode, "entry_t": t,
                 "shell": shell.get("name") or "touch_cancel_30s",
                 "entry_tte_s": round(expiry - t, 1),
                 "entry_book": entry_book.as_dict() if entry_book else None}
    actions = 0
    stop_reason = None
    while t < stop_at and actions < max_actions:
        raw = _book_at(bbo_rows, t)
        book = adapter.normalize(raw, expiry=expiry) if raw else None
        if book is None:
            stop_reason = "no_book"
            break
        state = l5_state(book, inv)
        prop = pv2.proposal(model, state, inv.last_rel or "open", "q<5",
                            residual=inv.behavioral_residual())
        shadow = fid.shadow_fields(
            art, slug=symbol, decision_idx=len(decisions), model_sha=model_sha,
            base_state=state, relation=prop["recommended_relation"],
            relation_ranked=prop.get("relation_ranked"), tte_s=book.tte_s)
        relation = "open" if inv.total == 0 else prop["recommended_relation"]
        if policy == "stochastic" and inv.total > 0:
            relation = (shadow.get("stochastic") or {}).get("sampled") or relation
        off = bool(prop.get("off_distribution"))
        refusal = None
        if off and inv.total > 0:
            relation, refusal = "hold", "off_distribution_hold"
        elif relation == "extend" and abs(
                inv.behavioral_residual()) >= MAX_RESIDUAL_UNITS:
            relation, refusal = "hold", (
                f"residual_cap:{abs(inv.behavioral_residual())}"
                f">={MAX_RESIDUAL_UNITS}")
        elif relation == "sell":
            relation, refusal = "hold", "sell_is_noop"
        planned = {"contracts": str(UNIT),
                   "usd": str((book.yes_bid or D("0")) * UNIT)}
        rec = decision_record(
            venue=adapter.venue, market_id=symbol, t=t, state=state, inv=inv,
            prop=prop, acted=relation, refusal=refusal, planned_size=planned,
            shadow=shadow, book=book, policy=policy)
        decisions.append(rec)
        if relation == "hold":
            t += max(float(prop["next_reevaluation_s"]), MIN_REEVAL_S)
            actions += 1
            continue
        if relation in ("open", "extend"):
            side_yes = (open_side_yes(book) if inv.residual == 0
                        else inv.residual > 0)
            px = book.join_price(side_yes) if side_yes is not None else None
            if px is None or not adapter.entry_ok(px) or \
                    book.opposite_ask(side_yes) is None or \
                    px >= book.opposite_ask(side_yes):
                rec["refusal"] = (f"no_priceable_side:{px}" if px is None
                                  else f"entry_band_left:{px}")
                rec["acted"] = "hold"
                if inv.total == 0:
                    stop_reason = rec["refusal"]
                    break
                t += MIN_REEVAL_S
                actions += 1
                continue
            t_until = min(t + rest_max, stop_at)
            moved = None
            requotes = 0
            t_cur, px_cur = t, px
            f = None
            while True:
                if shell.get("cancel_on_touch_move"):
                    moved = _touch_moved_at(bbo_rows, adapter, side_yes,
                                            px_cur, t_cur, t_until, expiry)
                seg_until = min(t_until, moved) if moved is not None else t_until
                f = tb.first_fill(prints, "YES" if side_yes else "NO", px_cur,
                                  ONE - px_cur, t_cur, seg_until, mode)
                if f is not None or moved is None or not shell.get("reprice"):
                    break
                # re-join the NEW touch (same side, same relation) within
                # the same bounded rest; post-only still binds
                nb = adapter.normalize(_book_at(bbo_rows, moved), expiry=expiry)
                npx = nb.join_price(side_yes) if nb else None
                if (npx is None or not adapter.entry_ok(npx)
                        or nb.opposite_ask(side_yes) is None
                        or npx >= nb.opposite_ask(side_yes)):
                    break
                requotes += 1
                t_cur, px_cur, moved = moved, npx, None
            if f is not None:
                px = px_cur
            order = {"t": t, "relation": relation,
                     "side": "YES" if side_yes else "NO", "px": str(px_cur),
                     "qty": str(UNIT), "rest_until": (min(t_until, moved)
                                                      if moved is not None
                                                      else t_until),
                     "requotes": requotes,
                     "cancel_reason": ("touch_moved" if moved is not None
                                       and f is None else None),
                     "filled": f is not None,
                     "fill_t": f["t"] if f else None}
            if f is None and moved is not None:
                t_until = min(t_until, moved)
            orders.append(order)
            rec["orders_placed"] = 1
            if f:
                inv.record(side_yes, px, UNIT, relation, f["t"])
                moves: dict = {}
                for h in ADVERSE_HORIZONS_S:
                    later = _mid_at(bbo_rows, f["t"] + h)
                    if later is not None:
                        own_mid = later if side_yes else ONE - later
                        moves[str(int(h))] = float(own_mid - px)
                move = moves.get("30")
                fills.append({"t": f["t"], "relation": relation,
                              "side": order["side"], "px": str(px),
                              "tte_s": round(expiry - f["t"], 1),
                              "rest_s": round(f["t"] - t, 1),
                              "mid_move_30s": move, "mid_moves": moves,
                              "adverse": (move < 0) if move is not None else None})
                t = f["t"]
            else:
                t = t_until
        else:                                   # complete: deficit side only
            if inv.residual == 0:
                stop_reason = "flat_complete"
                break
            deficit_up = inv.residual < 0
            surplus_avg = inv.avg_dn() if deficit_up else inv.avg_up()
            opp_ask = book.opposite_ask(deficit_up)
            px, econ = adapter.complement_price(surplus_avg, opp_ask, book.tick)
            rec["pair_economics"] = econ
            if px is None:
                t += MIN_REEVAL_S
                actions += 1
                continue
            qty = min(inv.residual.copy_abs(), UNIT)
            t_until = min(t + rest_max, stop_at)
            f = tb.complement_fill(prints, "NO" if deficit_up else "YES",
                                   px, t, t_until, mode)
            orders.append({"t": t, "relation": "complete",
                           "side": "YES" if deficit_up else "NO",
                           "px": str(px), "qty": str(qty),
                           "rest_until": t_until, "filled": f is not None,
                           "fill_t": f["t"] if f else None})
            rec["orders_placed"] = 1
            if f:
                inv.record(deficit_up, px, qty, "complete", f["t"])
                fills.append({"t": f["t"], "relation": "complete",
                              "side": "YES" if deficit_up else "NO",
                              "px": str(px), "tte_s": round(expiry - f["t"], 1),
                              "rest_s": round(f["t"] - t, 1),
                              "mid_move_30s": None, "adverse": None})
                t = f["t"]
            else:
                t = t_until
        actions += 1
    # ---- theoretical settlement (mid proxy; never realized) ----
    proxy = tb.outcome_proxy(bbo_rows, expiry)
    pair_pnl = hold_pnl = None
    fees = D("0")
    if inv.matched > 0:
        y, n = inv.avg_up(), inv.avg_dn()
        fees = (adapter.maker_fee(y, inv.matched) + adapter.maker_fee(n, inv.matched))
        pair_pnl = (ONE - y - n) * inv.matched - fees
    if inv.residual != 0:
        side_up = inv.residual > 0
        cost = inv.avg_up() if side_up else inv.avg_dn()
        q = inv.residual.copy_abs()
        fee = adapter.maker_fee(cost, q)
        if proxy != "AMBIGUOUS":
            won = side_up == (proxy == "UP")
            hold_pnl = ((ONE - cost) * q - fee) if won else (-cost * q - fee)
    status = ("MATCHED" if inv.matched > 0 and inv.residual == 0 else
              "RESIDUAL_HOLD" if inv.total > 0 else "NO_FILL")
    return {**out, "status": status, "stop_reason": stop_reason,
            "decisions": decisions, "orders": orders, "fills": fills,
            "inventory": inv.snapshot(), "pair_unit_cost": str(inv.pair_cost()),
            "pair_cost_after_fees": (str(inv.pair_cost() + fees / inv.matched)
                                     if inv.matched else None),
            "fees_theoretical": str(fees),
            "outcome_proxy": proxy,
            "pair_pnl_theoretical": str(pair_pnl) if pair_pnl is not None else None,
            "hold_pnl_theoretical": str(hold_pnl) if hold_pnl is not None else None,
            "flow": tb.window_flow(prints, expiry)}


def summarize(rows: list[dict]) -> dict:
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD",
                                                   "NO_FILL")]
    dec = [d for r in ent for d in r["decisions"]]
    acted: dict[str, int] = {}
    refusals: dict[str, int] = {}
    disagree = 0
    for d in dec:
        acted[d["acted"]] = acted.get(d["acted"], 0) + 1
        if d.get("refusal"):
            key = str(d["refusal"]).split(":")[0]
            refusals[key] = refusals.get(key, 0) + 1
        disagree += int(bool(d.get("shadow_differs_from_executed")))
    orders = [o for r in ent for o in r["orders"]]
    first_orders = [o for o in orders if o["relation"] in ("open", "extend")]
    comp_orders = [o for o in orders if o["relation"] == "complete"]
    fills = [f for r in ent for f in r["fills"]]
    first_fills = [f for f in fills if f["relation"] in ("open", "extend")]
    with_first = [r for r in ent if any(f["relation"] != "complete"
                                        for f in r["fills"])]
    matched = [r for r in ent if r["status"] == "MATCHED"]
    any_matched = [r for r in ent if D(r["inventory"]["matched"]) > 0]
    holds = [r for r in ent if r["status"] == "RESIDUAL_HOLD"]
    adverse = [f for f in first_fills if f.get("adverse") is not None]
    days = sorted({datetime.fromtimestamp(r["entry_t"], timezone.utc)
                   .strftime("%y%m%d") for r in ent})
    pair_pnl = sum(D(r["pair_pnl_theoretical"]) for r in ent
                   if r.get("pair_pnl_theoretical"))
    hold_pnl = sum(D(r["hold_pnl_theoretical"]) for r in ent
                   if r.get("hold_pnl_theoretical"))
    amb = sum(1 for r in holds if r["outcome_proxy"] == "AMBIGUOUS")
    hold_won = sum(1 for r in holds if r.get("hold_pnl_theoretical")
                   and D(r["hold_pnl_theoretical"]) > 0)
    hold_lost = sum(1 for r in holds if r.get("hold_pnl_theoretical")
                    and D(r["hold_pnl_theoretical"]) < 0)
    entry_books = [r["entry_book"] for r in ent if r.get("entry_book")]
    n_days = max(len(days), 1)
    return {
        "windows_replayed": len(rows), "campaigns": len(ent),
        "distinct_days": days, "campaigns_per_day": round(len(ent) / n_days, 1),
        "decisions": len(dec),
        "decisions_per_campaign_p50": _median(
            [len(r["decisions"]) for r in ent]),
        "acted_by_relation": acted, "refusals_by_reason": refusals,
        "shadow_vs_executed_disagreements": disagree,
        "orders_submitted": len(orders),
        "first_leg_orders": len(first_orders),
        "first_leg_fills": len(first_fills),
        "first_leg_fill_rate_per_order": _pct(len(first_fills), len(first_orders)),
        "campaign_first_fill_rate": _pct(len(with_first), len(ent)),
        "complement_orders": len(comp_orders),
        "complement_fills": len(fills) - len(first_fills),
        "p_complement_given_first_fill": _pct(len(any_matched), len(with_first)),
        "two_sided_fill_campaigns": len(any_matched),
        "fully_matched_campaigns": len(matched),
        "matched_per_day": round(len(any_matched) / n_days, 2),
        "one_sided_holds": len(holds),
        "one_sided_hold_rate": _pct(len(holds), len(ent)),
        "touch_moved_cancels": sum(1 for o in first_orders
                                   if o.get("cancel_reason") == "touch_moved"),
        "median_pair_cost_after_fees": _median(
            [r["pair_cost_after_fees"] for r in any_matched]),
        "fees_theoretical_total": str(sum(D(r["fees_theoretical"]) for r in ent)),
        "pair_pnl_theoretical_total": str(pair_pnl),
        "hold_pnl_theoretical_total": str(hold_pnl),
        "holds_residual_won": hold_won, "holds_residual_lost": hold_lost,
        "holds_ambiguous_excluded": amb,
        "net_theoretical_total": str(pair_pnl + hold_pnl),
        "adverse_selection_30s": {
            "fills_measured": len(adverse),
            "pct_adverse": _pct(sum(1 for f in adverse if f["adverse"]),
                                len(adverse)),
            "mean_mid_move": (round(statistics.fmean(
                f["mid_move_30s"] for f in adverse), 4) if adverse else None)},
        "adverse_selection_by_horizon": {
            h: {"n": len([f for f in first_fills
                          if (f.get("mid_moves") or {}).get(h) is not None]),
                "pct_adverse": _pct(
                    sum(1 for f in first_fills
                        if (f.get("mid_moves") or {}).get(h) is not None
                        and f["mid_moves"][h] < 0),
                    len([f for f in first_fills
                         if (f.get("mid_moves") or {}).get(h) is not None])),
                "mean_mid_move": (round(statistics.fmean(
                    f["mid_moves"][h] for f in first_fills
                    if (f.get("mid_moves") or {}).get(h) is not None), 4)
                    if any((f.get("mid_moves") or {}).get(h) is not None
                           for f in first_fills) else None)}
            for h in ("5", "15", "30", "60")},
        "pair_cost_after_fees_p25_p50_p75": _quartiles(
            [r["pair_cost_after_fees"] for r in any_matched]),
        "matched_pairs_le_099": sum(
            1 for r in any_matched
            if r.get("pair_cost_after_fees") is not None
            and D(str(r["pair_cost_after_fees"])) <= D("0.99")),
        "residual_duration_s_p50": _median(
            [r["fills"][0]["tte_s"] for r in holds if r.get("fills")]),
        "residual_outcomes": {"won": hold_won, "lost": hold_lost,
                              "ambiguous": amb},
        "capital_turn_per_day_estimate": round(float(sum(
            D(r["pair_unit_cost"]) * D(r["inventory"]["matched"])
            for r in any_matched)) / n_days / float(UNIT_CAPITAL_USD), 3),
        "first_fill_tte_s_p50": _median([f["tte_s"] for f in first_fills]),
        "first_fill_rest_s_p50": _median([f["rest_s"] for f in first_fills]),
        "entry_spread_p50": _median([b["spread"] for b in entry_books]),
        "entry_depth_bid_p50": _median([b["yes_bid_qty"] for b in entry_books
                                        if b["yes_bid_qty"] != "None"]),
        "entry_depth_ask_p50": _median([b["yes_ask_qty"] for b in entry_books
                                        if b["yes_ask_qty"] != "None"]),
        "window_flow_two_sided_pct": _pct(
            sum(1 for r in ent if r["flow"]["two_sided"]), len(ent)),
        "capacity_q1_usd_per_day_matched": round(float(sum(
            D(r["pair_unit_cost"]) * D(r["inventory"]["matched"])
            for r in any_matched)) / n_days, 2),
    }


def replay(bbo_path: Path = BBO_PATH, prints_path: Path = PRINTS_PATH,
           limit: Optional[int] = None,
           sensitivity_actions: int = 30) -> dict:
    model = pv2.load_v2()
    art = fid.load()
    bbo = tb.load_bbo(bbo_path)
    prints = tb.load_prints(prints_path)
    symbols = sorted(s for s in bbo if s in prints)
    if limit:
        symbols = symbols[:limit]
    results: dict[str, Any] = {}
    for policy in POLICIES:
        for mode in MODES:
            rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                                  policy=policy, mode=mode) for s in symbols]
            results[f"{policy}:{mode}"] = summarize(rows)
            results[f"{policy}:{mode}"]["sample_windows"] = [
                {k: r.get(k) for k in ("symbol", "status", "stop_reason",
                                       "inventory", "pair_cost_after_fees",
                                       "outcome_proxy", "pair_pnl_theoretical",
                                       "hold_pnl_theoretical")}
                for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD")][:12]
    # OWNER 5554553793 B: the rest/cancel discipline shadowed across shells
    # (strict fills only, both policy tracks)
    shells: dict[str, Any] = {}
    for name, sh in SHELLS.items():
        for policy in POLICIES:
            rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                                  policy=policy, mode="strict",
                                  shell={**sh, "name": name}) for s in symbols]
            shells[f"{name}:{policy}"] = {**summarize(rows),
                                          "requotes": sum(
                                              o.get("requotes", 0) for r in rows
                                              for o in r.get("orders") or [])}
            shells[f"{name}:{policy}"].pop("sample_windows", None)
    # shell sensitivity: the PMUS session bound (6 decisions) covers only
    # the first minute of a 5-minute window; show the ceiling when the
    # same shell may keep deciding for the whole window
    sens = {}
    for policy in POLICIES:
        rows = [replay_window(s, bbo[s], prints[s], model=model, art=art,
                              policy=policy, mode="strict",
                              max_actions=sensitivity_actions) for s in symbols]
        sens[f"{policy}:strict:max_actions={sensitivity_actions}"] = summarize(rows)
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "labels": LABELS,
            "provenance": {"bbo": str(bbo_path.name), "prints": str(prints_path.name),
                           "symbols_with_quotes_and_prints": len(symbols),
                           "model": model.get("_sha256_16"),
                           "fidelity_artifact": (art or {}).get("_sha256_16"),
                           "mechanics_vendored_from": "feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86"},
            "shell": {"unit": str(UNIT), "max_actions": MAX_ACTIONS,
                      "rest_max_s": REST_MAX_S, "residual_cap_units": MAX_RESIDUAL_UNITS,
                      "pair_target": "0.98", "min_edge_after_known_fees": "0.01",
                      "maker_only": True, "taker": "disabled"},
            "results": results,
            "shells_strict": shells,
            "sensitivity": sens,
            "pmus_reference": pmus_reference()}


def pmus_reference() -> dict:
    """Same-engine-class PMUS/Polymarket sports figures from the retained
    strict/optimistic replay (print-only tier), for the side-by-side."""
    try:
        s = json.loads(SPORTS_REF.read_text())
    except OSError:
        return {"available": False}
    fam = (s.get("families") or {}).get("mlb:moneyline") or {}
    keep = ("slots", "first_leg_fill_rate", "p_complement_given_first_fill",
            "one_sided_hold_rate", "matched", "median_pair_cost",
            "mean_time_to_complement_s", "hold_pnl_theoretical_total")
    return {"available": True, "source": SPORTS_REF.name,
            "family": "mlb:moneyline", "flow": fam.get("flow"),
            "strict": {k: (fam.get("strict") or {}).get(k) for k in keep},
            "optimistic": {k: (fam.get("optimistic") or {}).get(k) for k in keep},
            "labels": s.get("labeling")}


# ------------------------- pre-registered transfer gate -------------------------

GATE_PATH = ROOT / "analysis/l5/gemini_transfer_gate.json"


def load_gate(path: Path = GATE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _day(r: Mapping[str, Any]) -> str:
    return datetime.fromtimestamp(float(r["entry_t"]), timezone.utc).strftime("%Y-%m-%d")


def transfer_gate_verdict(rows: list, gate: Optional[Mapping[str, Any]] = None) -> dict:
    """Evaluate ONE arm's strict rows against the frozen gate. Returns
    PASS / FAIL / INSUFFICIENT_SAMPLE / REJECT with every criterion
    named; thresholds come only from the frozen JSON."""
    gate = gate or load_gate()
    req = gate["requirements"]
    rej = gate["reject"]
    ent = [r for r in rows if r.get("status") in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")]
    s = summarize(ent) if ent else None
    days = sorted({_day(r) for r in ent})
    matched_rows = [r for r in ent if D(r["inventory"]["matched"]) > 0]
    matched_days = sorted({_day(r) for r in matched_rows})
    net_by_day = {}
    for r in ent:
        d = _day(r)
        v = D(r.get("pair_pnl_theoretical") or 0) + D(r.get("hold_pnl_theoretical") or 0)
        net_by_day[d] = net_by_day.get(d, D("0")) + v
    net = sum(net_by_day.values(), D("0"))
    checks = {}
    if s is None:
        return {"verdict": "INSUFFICIENT_SAMPLE", "windows": 0, "days": 0,
                "gate_sha": gate.get("_sha256_16"), "checks": {}}
    checks["windows"] = (len(ent), req["min_windows"], len(ent) >= req["min_windows"])
    checks["days"] = (len(days), req["min_days"], len(days) >= req["min_days"])
    sample_ok = checks["windows"][2] and checks["days"][2]
    checks["two_sided_completions"] = (len(matched_rows), req["min_two_sided_completions"],
                                       len(matched_rows) >= req["min_two_sided_completions"])
    checks["completion_days"] = (len(matched_days), req["min_completion_days"],
                                 len(matched_days) >= req["min_completion_days"])
    p = s.get("p_complement_given_first_fill")
    checks["p_complement_given_first_fill_pct"] = (
        p, req["min_p_complement_given_first_fill_pct"],
        p is not None and p >= req["min_p_complement_given_first_fill_pct"])
    le = s.get("matched_pairs_le_099") or 0
    share = round(100.0 * le / len(matched_rows), 1) if matched_rows else None
    checks["matched_le_099_share_pct"] = (share, req["min_matched_le_099_share_pct"],
                                          share is not None and share >= req["min_matched_le_099_share_pct"])
    checks["net_after_fees_usd"] = (str(net), req["min_net_after_known_fees_usd"],
                                    net >= D(str(req["min_net_after_known_fees_usd"])))
    pos_days = sum(1 for v in net_by_day.values() if v >= 0)
    checks["non_negative_days"] = (pos_days, req["min_non_negative_days"],
                                   pos_days >= req["min_non_negative_days"])
    adv = (s.get("adverse_selection_by_horizon") or {}).get("30", {}).get("pct_adverse")
    checks["adverse_30s_pct"] = (adv, req["max_adverse_selection_30s_pct"],
                                 adv is not None and adv <= req["max_adverse_selection_30s_pct"])
    hr = s.get("one_sided_hold_rate")
    checks["one_sided_hold_rate_pct"] = (hr, req["max_one_sided_hold_rate_pct"],
                                         hr is not None and hr <= req["max_one_sided_hold_rate_pct"])
    reject = (len(ent) >= rej["after_windows"] and
              (net <= D(str(rej["net_after_fees_usd_at_or_below"]))
               or len(matched_rows) < rej["two_sided_completions_below"]))
    if reject:
        verdict = "REJECT"
    elif not sample_ok:
        verdict = "INSUFFICIENT_SAMPLE"
    elif all(v[2] for v in checks.values()):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {"verdict": verdict, "windows": len(ent), "days": len(days),
            "gate_sha": gate.get("_sha256_16"), "gate_frozen_utc": gate.get("frozen_utc"),
            "checks": {k: {"value": v[0], "threshold": v[1], "ok": bool(v[2])}
                       for k, v in checks.items()},
            "net_by_day": {k: str(v) for k, v in sorted(net_by_day.items())},
            "labels": "THEORETICAL strict-fill evidence against the frozen gate; "
                      "thresholds are read from the JSON and never tuned here"}


# ------------------------- realtime shadow -------------------------

def book_from_public(sym: str, raw: Mapping[str, Any], now: float) -> dict:
    """Reduce a /v1/book payload to the tape row shape."""
    bids, asks = raw.get("bids") or [], raw.get("asks") or []
    b, a = (bids[0] if bids else {}), (asks[0] if asks else {})
    return {"symbol": sym, "t": now, "bid": b.get("price"),
            "bid_sz": b.get("amount"), "ask": a.get("price"),
            "ask_sz": a.get("amount")}


def live_shadow_step(sym: str, raw_book: Mapping[str, Any], now: float, *,
                     model: Mapping[str, Any], art: Optional[Mapping[str, Any]],
                     inv: Optional[L5Inventory] = None,
                     adapter: Optional[GeminiAdapter] = None) -> dict:
    """One realtime decision (zero orders): what L5 would do now and
    whether it is executable under the shell."""
    adapter = adapter or GeminiAdapter()
    inv = inv or L5Inventory()
    book = adapter.normalize(book_from_public(sym, raw_book, now))
    if book is None:
        return {"venue": adapter.venue, "market_id": sym, "t": now,
                "status": "NO_BOOK", "orders_placed": 0}
    state = l5_state(book, inv)
    prop = pv2.proposal(model, state, inv.last_rel or "open", "q<5",
                        residual=inv.behavioral_residual())
    shadow = fid.shadow_fields(
        art, slug=sym, decision_idx=len(inv.actions),
        model_sha=str(model.get("_sha256_16") or ""), base_state=state,
        relation=prop["recommended_relation"],
        relation_ranked=prop.get("relation_ranked"), tte_s=book.tte_s)
    relation = "open" if inv.total == 0 else prop["recommended_relation"]
    side_yes = open_side_yes(book) if inv.residual == 0 else inv.residual > 0
    px = book.join_price(side_yes) if side_yes is not None else None
    opp = book.opposite_ask(side_yes) if side_yes is not None else None
    comp, econ = (adapter.complement_price(px, ONE - px if side_yes is None
                                           else (book.no_ask if side_yes
                                                 else book.yes_ask), book.tick)
                  if px is not None else (None, {"regime": "no_price"}))
    executable = {
        "priceable_side": px is not None,
        "in_band": bool(px is not None and adapter.entry_ok(px)),
        "post_only_ok": bool(px is not None and opp is not None and px < opp),
        "tte_ok": tb.MIN_TTE_S <= book.tte_s <= tb.ENTRY_MAX_TTE_S,
        "fee_safe_complement_exists": comp is not None,
        "off_distribution": bool(prop.get("off_distribution")),
    }
    executable["all"] = all(v for k, v in executable.items()
                            if k != "off_distribution") and \
        not executable["off_distribution"]
    rec = decision_record(
        venue=adapter.venue, market_id=sym, t=now, state=state, inv=inv,
        prop=prop, acted=relation if executable["all"] else "hold",
        refusal=None if executable["all"] else "not_executable",
        planned_size={"contracts": str(UNIT), "usd": str(px) if px else None},
        shadow=shadow, book=book, policy="modal")
    rec.update({"would_side": ("YES" if side_yes else "NO") if side_yes is not None else None,
                "would_price": str(px) if px is not None else None,
                "complement_bound": str(comp) if comp is not None else None,
                "pair_economics": econ, "executability": executable,
                "status": "SHADOW_ONLY"})
    return rec


def nearest_windows(now: datetime, count: int = 2) -> list[str]:
    base = now.replace(second=0, microsecond=0)
    base += timedelta(minutes=(5 - base.minute % 5) % 5 or 5)
    return [f"GEMI-BTC05M{base + timedelta(minutes=5 * i):%y%m%d%H%M}-UP"
            for i in range(count)]


def run_live(minutes: float, fetch: Callable[[str], Any],
             out: Optional[Path] = None, poll_s: float = 5.0,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.time) -> dict:
    model, art = pv2.load_v2(), fid.load()
    end = clock() + minutes * 60
    recs: list[dict] = []
    errors = 0
    while clock() < end:
        now = clock()
        for sym in nearest_windows(datetime.fromtimestamp(now, timezone.utc)):
            try:
                raw = fetch(f"https://api.gemini.com/v1/book/{sym}"
                            "?limit_bids=1&limit_asks=1")
                recs.append(live_shadow_step(sym, raw, now, model=model, art=art))
            except Exception as exc:                      # venue/network
                errors += 1
                recs.append({"market_id": sym, "t": now, "status": "FETCH_ERROR",
                             "err": str(exc)[:100], "orders_placed": 0})
        if out:
            out.write_text("\n".join(json.dumps(r, default=str) for r in recs))
        sleep(poll_s)
    ok = [r for r in recs if r.get("status") == "SHADOW_ONLY"]
    return {"labels": LABELS, "steps": len(recs), "fetch_errors": errors,
            "executable_now": sum(1 for r in ok if r["executability"]["all"]),
            "orders_placed": 0, "records": recs[-20:]}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=float, default=0.0, help="minutes")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    a = ap.parse_args(argv)
    if a.live:
        import ssl
        import urllib.request

        def fetch(url: str):
            ctx = ssl.create_default_context(cafile=tb.CA_BUNDLE)
            req = urllib.request.Request(url, headers={"User-Agent": "l5-shadow"})
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                return json.loads(r.read())
        rep = run_live(a.live, fetch, out=a.out.with_suffix(".live.jsonl"))
        print(json.dumps({k: v for k, v in rep.items() if k != "records"}, indent=1))
        return 0
    rep = replay(limit=a.limit)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1, default=str, sort_keys=True))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "sample_windows"}
                      for k, v in rep["results"].items()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
