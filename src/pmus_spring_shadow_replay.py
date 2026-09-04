"""Strict read-only SPRING-L5 slot replay over authenticated WS state.

This module is deliberately independent of every executor.  It answers one
narrow question for observation only:

    Did a 300s slot START in the spring 35-65c observation band, get a
    maker-first fill at the prevailing YES bid, and later show public trade
    evidence consistent with a maker complement that would complete the pair
    at <= 0.98 (observation cap, OWNER DECISION 5533476555)?

All outputs are THEORETICAL.  No orders, credentials, REST mutations, arming
keys, or execution admission live here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN
from typing import Any, Mapping, Optional

SLOT_S = 300.0
WINDOW_S = 3600.0
SPRING_LO = D("0.35")    # OWNER DECISION 5533476555: observation 35-65
SPRING_HI = D("0.65")
# OBSERVATION completion cap (5533476555). The live >=1c-after-known-fees
# execution bound lives in q1.complement_bound and is separate/unchanged.
SPRING_PAIR_TARGET = D("0.98")
TICK = D("0.01")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v)) if v is not None else None
    except Exception:
        return None


def _floor_tick(v: D) -> D:
    return (v / TICK).to_integral_value(rounding=ROUND_DOWN) * TICK


def replay_slot(snapshot: Mapping[str, Any], trades: list[Mapping[str, Any]],
                slot_start: float) -> dict:
    """Replay one slot using only the prevailing snapshot and later trades."""
    slot_end = slot_start + SLOT_S
    join = _d(snapshot.get("bid"))
    if join is None:
        return {"status": "SPRING_NO_QUOTE"}
    if not (SPRING_LO <= join <= SPRING_HI):
        return {"status": "SPRING_OUTSIDE_BAND", "join": str(join)}
    if snapshot.get("ask_book_missing", True):
        return {"status": "SPRING_ONE_SIDED_BOOK", "join": str(join)}

    inslot = [t for t in trades
              if t.get("t") is not None
              and slot_start < float(t["t"]) <= slot_end]
    first_fills = []
    for t in inslot:
        px = _d(t.get("px"))
        if (px is not None and t.get("dir_inferred") == "sell"
                and px <= join):
            first_fills.append((t, px))
    if not first_fills:
        return {"status": "SPRING_NO_FILL", "join": str(join),
                "inslot_trades": len(inslot)}

    f1, _ = first_fills[0]
    comp = _floor_tick(SPRING_PAIR_TARGET - join)
    if comp <= 0:
        return {"status": "SPRING_NO_LEGAL_COMPLEMENT", "join": str(join)}
    yes_threshold = D("1") - comp

    hits = []
    for t in inslot:
        px = _d(t.get("px"))
        if (float(t.get("t", 0)) > float(f1["t"])
                and px is not None
                and t.get("dir_inferred") == "buy"
                and px >= yes_threshold):
            hits.append((t, px))
    if hits:
        pair_cost = join + comp
        return {
            "status": "SPRING_MATCHED",
            "join": str(join),
            "comp": str(comp),
            "pair_cost": str(pair_cost),
            "locked_edge_theoretical": str(D("1") - pair_cost),
            "t_first": float(f1["t"]),
            "t_comp": float(hits[0][0]["t"]),
            "yes_threshold_for_comp": str(yes_threshold),
        }
    # the strict <=0.96 complement never printed; did the ordinary
    # fee-safe (<=0.99, >=1c edge) complement print? Advisory context for
    # the pair-cost distribution — never a spring completion.
    fee_safe_pair = None
    comp99 = _floor_tick(D("0.99") - join)
    if comp99 > 0:
        thresh99 = D("1") - comp99
        for t in inslot:
            px = _d(t.get("px"))
            if (float(t.get("t", 0)) > float(f1["t"]) and px is not None
                    and t.get("dir_inferred") == "buy" and px >= thresh99):
                fee_safe_pair = str(join + comp99)
                break
    return {
        "status": "SPRING_ONE_SIDED_HOLD",
        "join": str(join),
        "target_comp": str(comp),
        "yes_threshold_for_comp": str(yes_threshold),
        "residual_since": float(f1["t"]),
        "fee_safe_pair_cost": fee_safe_pair,
    }


def summarize_state(s: Mapping[str, Any], now: float,
                    window_s: float = WINDOW_S) -> dict:
    """Strict time-aligned spring replay across the authenticated state."""
    trades = []
    for t in s.get("trades", []) or []:
        try:
            tt = float(t.get("t", 0))
        except (TypeError, ValueError):
            continue
        if not t.get("px") or now - tt > window_s:
            continue
        px = _d(t.get("px"))
        if px is None:
            continue
        trades.append({"t": tt, "px": px,
                       "dir_inferred": t.get("dir", "unknown")})

    history = []
    for h in s.get("bbo_history", []) or []:
        try:
            ht = float(h.get("t", 0))
        except (TypeError, ValueError):
            continue
        if now - ht <= window_s + SLOT_S:
            history.append(h)
    history.sort(key=lambda h: float(h.get("t", 0)))

    def prevailing(start: float, end: float) -> Optional[Mapping[str, Any]]:
        before = [h for h in history if float(h.get("t", 0)) <= start]
        if before:
            return before[-1]
        inside = [h for h in history if start < float(h.get("t", 0)) <= end]
        return inside[0] if inside else None

    counts: dict[str, int] = {}
    matches: list[dict] = []
    fee_safe_only = 0
    latest: Optional[dict] = None
    if history and trades:
        # Match the canonical WS gate's grid anchoring exactly so strict and
        # ordinary evidence are comparable without using current-BBO lookahead.
        t = min(float(x["t"]) for x in trades) - 0.001
        t_end = max(float(x["t"]) for x in trades)
        while t <= t_end:
            snap = prevailing(t, t + SLOT_S)
            if snap is None:
                row = {"status": "SPRING_NO_QUOTE"}
            else:
                row = replay_slot(snap, trades, t)
            st = row["status"]
            counts[st] = counts.get(st, 0) + 1
            if st == "SPRING_MATCHED":
                matches.append(row)
            elif row.get("fee_safe_pair_cost"):
                fee_safe_only += 1
            latest = {"status": st, "slot_start": t}
            t += SLOT_S

    matched = counts.get("SPRING_MATCHED", 0)
    # evaluable = slots where a spring join actually existed (in band,
    # two-sided book), so a completion COULD have been evidenced
    evaluable = sum(counts.get(k, 0) for k in
                    ("SPRING_MATCHED", "SPRING_ONE_SIDED_HOLD",
                     "SPRING_NO_FILL"))
    lat = sorted(float(m["t_comp"]) - float(m["t_first"]) for m in matches)
    missing = []
    if not history:
        missing.append("no_bbo_history")
    if not trades:
        missing.append("no_trades_in_window")
    if history and trades and evaluable == 0:
        missing.append("no_evaluable_slots")
    return {
        "spring_pair_target": str(SPRING_PAIR_TARGET),
        "spring_slot_counts_theoretical": counts,
        "spring_evaluable_slots": evaluable,
        "spring_matched_fraction": (round(matched / evaluable, 4)
                                    if evaluable else None),
        "spring_matched_slots": matched,
        "spring_repeated_completion": matched >= 2,
        "spring_latest_slot": ({"status": latest["status"],
                                "age_s": round(now - latest["slot_start"],
                                               1)}
                               if latest else None),
        "spring_completion_latency_s": (
            {"p50": round(lat[len(lat) // 2], 1),
             "p95": round(lat[int(0.95 * (len(lat) - 1))], 1),
             "n": len(lat)} if lat else None),
        "spring_pair_costs": [m["pair_cost"] for m in matches][-12:],
        # key name kept for consumer compatibility; completions are
        # <= SPRING_PAIR_TARGET by construction (0.98 per 5533476555)
        "spring_le_096_completions": matched,
        "spring_fee_safe_completions": matched + fee_safe_only,
        "spring_evidence_missing": missing,
        "spring_matches_theoretical": matches[-3:],
        "spring_labels": ("SHADOW/THEORETICAL; strict "
                          f"<={SPRING_PAIR_TARGET} slot replay"),
    }
