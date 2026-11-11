"""OWNER #167 comment 5574408755 item 2 -- completion ceiling from L5's
OWN behaviour: when L5 chose COMPLETE, what effective pair cost did he
accept, including above 1.00?

Scope discipline (owner): this is *what L5 did*. It is NOT *what PMUS
economics can support* -- that is a separate study; the section
``economic_overlay_attachment_points`` only names the inputs that study
would attach here. Nothing below redefines L5 as a <= 1.00 arbitrage: the
distributions are reported with their > 1.00 mass intact because "if he
says COMPLETE at 1.02, our replication target is 1.02".

Relation labels are the live model's (``polymarket_l5.action_model
._relation``: flat below one share; open = from flat, extend = surplus
side, complete = deficit side). State buckets are the frozen live state
(``state_key`` = band of the LAST OBSERVED fill price | seconds-to-
settlement at the action | |residual| shares), so every table here is
keyed like a live decision.

Three pair-cost bases for every COMPLETE fill (all pre-rebate, pre-fee):
  engine_avg       complete price + AVERAGE cost of the surplus side at that
                   moment (cum spend / cum qty from the canonical row before
                   the fill) -- this is the basis the live engine prices
                   through (``Inventory.avg`` -> ``complement_target``).
  marginal_lifo    complete price + price of the most recent surplus lot(s)
                   the fill consumes, LIFO, qty-weighted over the matched
                   part; the overshoot (qty beyond the surplus) is not a
                   completion and opens a lot on the other side.
  window_realized  the window's matched-pair unit cost at settlement
                   (wavg_up + wavg_down of the final canonical row), the same
                   number the ledger books as locked; assigned to every
                   complete of that window.

Read-only research on analysis/l5/l5_canonical_sequence.csv.gz.

    python3 -m src.l5_completion_ceiling_study
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.l5_concurrency_study import CANONICAL_PATH, load_rows, provenance
from src.polymarket_l5.action_model import (IMBALANCE_BUCKETS, PRICE_BANDS,
                                            SYSTEMATIC_REGIME_EPOCH,
                                            TTE_BUCKETS, _bucket, _relation,
                                            state_key)
from src.polymarket_l5.inventory import running_states

OUT_JSON = os.path.join("analysis", "l5", "l5_completion_ceiling.json")
OUT_MD = os.path.join("analysis", "l5", "L5_COMPLETION_CEILING.md")

EPS = 1e-9
PAIR_DP = 6          # pair costs rounded here BEFORE threshold tests
THRESHOLDS = (1.00, 1.01, 1.02, 1.03, 1.05)
BASES = ("engine_avg", "marginal_lifo", "window_realized")
INV_USD_BUCKETS = ((5.0, "inv<$5"), (25.0, "inv$5-25"), (100.0, "inv$25-100"),
                   (500.0, "inv$100-500"), (1e18, "inv>$500"))
STREAK_BUCKETS = ((2, "after_extend_x1"), (4, "after_extend_x2-3"),
                  (1e9, "after_extend_x4+"))
MIN_GROUP_N = 20     # smaller state cells are pooled into "other(<20)"
RESIDUAL_HELD_SHARES = 1.0   # live "flat" threshold (action_model._relation)


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _month(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m")


def _lane(r: dict) -> str:
    return "%s-%dm" % (r.get("asset", ""), int(_f(r.get("duration_minutes"))))


def _lifo_take(lots: List[list], qty: float) -> Tuple[float, float]:
    """Consume up to ``qty`` from the most recent open lots ([px, open_qty]).
    Returns (matched_qty, matched_cost)."""
    matched = cost = 0.0
    while qty > EPS and lots:
        lot = lots[-1]
        m = min(qty, lot[1])
        matched += m
        cost += m * lot[0]
        lot[1] -= m
        qty -= m
        if lot[1] <= EPS:
            lots.pop()
    return matched, cost


# ------------------------------ per window ------------------------------

def window_walk(rows: Sequence[dict]) -> Tuple[List[dict], dict]:
    """One pass over a window's canonical rows: relation-label every fill
    with the live rule, keep a LIFO lot ledger, and emit one record per
    COMPLETE plus one window record."""
    rs = sorted(rows, key=lambda r: (int(_f(r["timestamp"])), int(_f(r.get("trade_seq"), 0))))
    lots: Dict[str, List[list]] = {"Up": [], "Down": []}
    completes: List[dict] = []
    prev: Optional[dict] = None
    prev_rel: Optional[str] = None
    streak = 0
    n_buys = bought_usd = lot_matched_qty = lot_pair_x_qty = 0.0
    n_pre_regime = 0
    for r in rs:
        outcome, side = r["outcome"], str(r.get("side") or "").upper()
        price, qty = _f(r["price"]), _f(r["quantity"])
        usd = _f(r.get("dollars")) or price * qty
        ts = int(_f(r["timestamp"]))
        prev_res = _f(prev["residual_qty_signed"]) if prev else 0.0
        rel = _relation(prev_res, outcome, side)
        if side != "BUY":                    # L5 lifetime: two SELL rows
            _lifo_take(lots[outcome], qty)
            prev, prev_rel = r, rel
            continue
        n_buys += 1
        bought_usd += usd
        other = "Down" if outcome == "Up" else "Up"
        matched, cost = _lifo_take(lots[other], qty)
        if qty - matched > EPS:
            lots[outcome].append([price, qty - matched])
        lot_matched_qty += matched
        lot_pair_x_qty += matched * price + cost
        if rel == "complete":
            surplus = "Up" if prev_res > 0 else "Down"
            sq = _f(prev["cum_%s_qty" % surplus.lower()])
            ss = _f(prev["cum_%s_spend" % surplus.lower()])
            avg_surplus = ss / sq if sq > EPS else price
            tte = _f(r.get("seconds_to_settlement"))
            if prev_rel == "extend":
                preceding = _bucket(streak, STREAK_BUCKETS)
            else:
                preceding = "after_%s" % (prev_rel or "open")
            inv_usd = abs(prev_res) * avg_surplus
            if ts < SYSTEMATIC_REGIME_EPOCH:
                n_pre_regime += 1
            completes.append({
                "window_id": r["window_id"], "lane": _lane(r), "ts": ts,
                "month": _month(ts), "pre_regime": ts < SYSTEMATIC_REGIME_EPOCH,
                "outcome": outcome, "price": price, "qty": qty, "usd": usd,
                "completed_qty": matched, "completed_usd": price * matched,
                "overshoot_qty": max(0.0, qty - matched),
                "engine_avg": round(price + avg_surplus, PAIR_DP),
                "marginal_lifo": (round(price + cost / matched, PAIR_DP)
                                  if matched > EPS else None),
                "window_realized": None,          # filled after the walk
                "state": state_key(_f(prev["price"]), tte, prev_res),
                "own_band": _bucket(price, PRICE_BANDS),
                "tte_bucket": _bucket(tte, TTE_BUCKETS),
                "imb_bucket": _bucket(abs(prev_res), IMBALANCE_BUCKETS),
                "imb_shares_before": abs(prev_res),
                "inv_usd_before": inv_usd,
                "inv_bucket": _bucket(inv_usd, INV_USD_BUCKETS),
                "preceding": preceding,
                "maker_taker": r.get("maker_taker", ""),
            })
        streak = streak + 1 if rel == "extend" else 0
        prev, prev_rel = r, rel

    last = rs[-1]
    up_q, dn_q = _f(last["cum_up_qty"]), _f(last["cum_down_qty"])
    up_s, dn_s = _f(last["cum_up_spend"]), _f(last["cum_down_spend"])
    matched_q = min(up_q, dn_q)
    pair = ((up_s / up_q) + (dn_s / dn_q)) if matched_q > EPS else None
    res = _f(last["residual_qty_signed"])
    res_avg = (up_s / up_q if res > 0 and up_q > EPS else
               dn_s / dn_q if res < 0 and dn_q > EPS else 0.0)
    for c in completes:
        c["window_realized"] = round(pair, PAIR_DP) if pair is not None else None
    first_ts = int(_f(rs[0]["timestamp"]))
    window = {
        "window_id": last["window_id"], "lane": _lane(last),
        "month": _month(first_ts), "pre_regime": first_ts < SYSTEMATIC_REGIME_EPOCH,
        "n_buys": int(n_buys), "n_completes": len(completes),
        "any_complete": bool(completes),
        "both_sides": up_q > EPS and dn_q > EPS,
        "bought_usd": bought_usd,
        "final_matched_qty": matched_q,
        "final_pair_cost": round(pair, PAIR_DP) if pair is not None else None,
        "matched_usd": matched_q * pair if pair is not None else 0.0,
        "final_residual_qty": res,
        "residual_usd_at_settlement": abs(res) * res_avg,
        "held_residual": abs(res) >= RESIDUAL_HELD_SHARES,
        "lot_matched_qty": lot_matched_qty,
        "lot_pair_x_qty": lot_pair_x_qty,
        "settlement": last.get("settlement_outcome", ""),
    }
    return completes, window


def walk_all(rows: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    completes: List[dict] = []
    windows: List[dict] = []
    for rs in by_win.values():
        c, w = window_walk(rs)
        completes.extend(c)
        windows.append(w)
    return completes, windows


# ------------------------------ statistics ------------------------------

def _q(xs: List[float], q: float) -> float:
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))]


def _wq(pairs: List[Tuple[float, float]], q: float) -> float:
    """Weighted quantile over sorted (value, weight)."""
    tot = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * tot - EPS:
            return v
    return pairs[-1][0]


def _above(v: float, thr: float) -> bool:
    return v > thr + EPS


def dist(recs: Sequence[dict], basis: str, weight: str = "completed_usd",
         qty: str = "completed_qty") -> dict:
    """Count-, dollar- and contract-weighted distribution of one basis."""
    vals = [(r[basis], r[weight], r[qty]) for r in recs if r.get(basis) is not None]
    if not vals:
        return {"n": 0}
    vals.sort()
    xs = [v for v, _, _ in vals]
    usd = sum(w for _, w, _ in vals)
    pairs = sum(q for _, _, q in vals)
    wv = [(v, w) for v, w, _ in vals]
    out = {
        "n": len(xs), "usd": round(usd, 2), "pairs": round(pairs, 1),
        "mean": round(sum(xs) / len(xs), 4),
        "usd_wmean": round(sum(v * w for v, w, _ in vals) / usd, 4) if usd else None,
        "pair_wmean": round(sum(v * q for v, _, q in vals) / pairs, 4) if pairs else None,
        "p10": round(_q(xs, 0.10), 4), "p25": round(_q(xs, 0.25), 4),
        "p50": round(_q(xs, 0.50), 4), "p75": round(_q(xs, 0.75), 4),
        "p90": round(_q(xs, 0.90), 4), "p99": round(_q(xs, 0.99), 4),
        "max": round(xs[-1], 4),
        "usd_p50": round(_wq(wv, 0.50), 4), "usd_p90": round(_wq(wv, 0.90), 4),
        "above": {},
    }
    for thr in THRESHOLDS:
        n_ab = sum(1 for v in xs if _above(v, thr))
        u_ab = sum(w for v, w, _ in vals if _above(v, thr))
        q_ab = sum(q for v, _, q in vals if _above(v, thr))
        out["above"]["%.2f" % thr] = {
            "fills_pct": round(100 * n_ab / len(xs), 2),
            "usd_pct": round(100 * u_ab / usd, 2) if usd else None,
            "pairs_pct": round(100 * q_ab / pairs, 2) if pairs else None}
    return out


def group_summary(recs: Sequence[dict]) -> dict:
    out: Dict[str, Any] = {"n": len(recs),
                           "usd": round(sum(r["completed_usd"] for r in recs), 2)}
    for basis in BASES:
        d = dist(recs, basis)
        if d["n"] == 0:
            out[basis] = {"n": 0}
            continue
        out[basis] = {"mean": d["mean"], "p50": d["p50"], "p90": d["p90"],
                      "gt100_fills_pct": d["above"]["1.00"]["fills_pct"],
                      "gt100_pairs_pct": d["above"]["1.00"]["pairs_pct"],
                      "gt100_usd_pct": d["above"]["1.00"]["usd_pct"],
                      "gt102_usd_pct": d["above"]["1.02"]["usd_pct"]}
    return out


def by(recs: Sequence[dict], key: Callable[[dict], str],
       min_n: int = 1, order: Optional[Sequence[str]] = None) -> Dict[str, dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for r in recs:
        groups[key(r)].append(r)
    pooled: List[dict] = []
    out: Dict[str, dict] = {}
    for k in (order or sorted(groups)):
        g = groups.get(k)
        if not g:
            continue
        if len(g) >= min_n:
            out[k] = group_summary(g)
        else:
            pooled.extend(g)
    if pooled:
        out["other(<%d)" % min_n] = group_summary(pooled)
    return out


def completion_vs_hold(windows: Sequence[dict]) -> dict:
    n = len(windows)
    if not n:
        return {"windows": 0}
    held = [w for w in windows if w["held_residual"]]
    bought = sum(w["bought_usd"] for w in windows)
    matched = sum(w["matched_usd"] for w in windows)
    res_usd = sorted(w["residual_usd_at_settlement"] for w in held)
    ncomp = sorted(w["n_completes"] for w in windows)
    return {
        "windows": n,
        "any_complete_pct": round(100 * sum(w["any_complete"] for w in windows) / n, 2),
        "both_sides_pct": round(100 * sum(w["both_sides"] for w in windows) / n, 2),
        "one_sided_pct": round(100 * sum(not w["both_sides"] for w in windows) / n, 2),
        "held_residual_ge_1share_pct": round(100 * len(held) / n, 2),
        "flat_at_settlement_pct": round(100 * (n - len(held)) / n, 2),
        "median_completes_per_window": _q(ncomp, 0.5),
        "bought_usd": round(bought, 2),
        "matched_pair_usd_pct_of_bought": round(100 * matched / bought, 2) if bought else None,
        "residual_usd_at_settlement_p50": round(_q(res_usd, 0.5), 2) if res_usd else None,
        "residual_usd_at_settlement_p90": round(_q(res_usd, 0.9), 2) if res_usd else None,
        "residual_usd_at_settlement_total": round(sum(res_usd), 2),
    }


def matched_pairs_by_month(windows: Sequence[dict], completes: Sequence[dict]) -> Dict[str, dict]:
    months: Dict[str, List[dict]] = defaultdict(list)
    for w in windows:
        months[w["month"]].append(w)
    cmonths: Dict[str, List[dict]] = defaultdict(list)
    for c in completes:
        cmonths[c["month"]].append(c)
    out: Dict[str, dict] = {}
    for m in sorted(set(months) | set(cmonths)):
        ws = months.get(m, [])
        mq = sum(w["final_matched_qty"] for w in ws)
        musd = sum(w["matched_usd"] for w in ws)
        lq = sum(w["lot_matched_qty"] for w in ws)
        lusd = sum(w["lot_pair_x_qty"] for w in ws)
        cs = cmonths.get(m, [])
        cusd = sum(c["completed_usd"] for c in cs)
        out[m] = {
            "windows": len(ws), "matched_pairs": round(mq, 1),
            "usd_wavg_pair_cost_window_basis": round(musd / mq, 4) if mq > EPS else None,
            "usd_wavg_pair_cost_lot_lifo_basis": round(lusd / lq, 4) if lq > EPS else None,
            "completes": len(cs),
            "usd_wavg_engine_pair_at_complete": (
                round(sum(c["engine_avg"] * c["completed_usd"] for c in cs) / cusd, 4)
                if cusd > EPS else None),
        }
    mq = sum(w["final_matched_qty"] for w in windows)
    lq = sum(w["lot_matched_qty"] for w in windows)
    out["ALL"] = {
        "windows": len(windows), "matched_pairs": round(mq, 1),
        "usd_wavg_pair_cost_window_basis": round(
            sum(w["matched_usd"] for w in windows) / mq, 4) if mq > EPS else None,
        "usd_wavg_pair_cost_lot_lifo_basis": round(
            sum(w["lot_pair_x_qty"] for w in windows) / lq, 4) if lq > EPS else None,
        "completes": len(completes),
    }
    return out


# ------------------------------ the study ------------------------------

def study(rows: Optional[Sequence[dict]] = None, path: str = CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    completes, windows = walk_all(rows)
    n_fills = len(rows)
    relations = defaultdict(int)
    for wid_rows in _group(rows).values():
        prev = 0.0
        for r in sorted(wid_rows, key=lambda r: (int(_f(r["timestamp"])), int(_f(r.get("trade_seq"), 0)))):
            relations[_relation(prev, r["outcome"], str(r.get("side") or "").upper())] += 1
            prev = _f(r["residual_qty_signed"])
    overshoot = [c for c in completes if c["overshoot_qty"] > 1e-6]
    imb_order = [l for _, l in IMBALANCE_BUCKETS]
    tte_order = [l for _, l in TTE_BUCKETS]
    band_order = [l for _, l in PRICE_BANDS]
    inv_order = [l for _, l in INV_USD_BUCKETS]
    pre_order = ["after_open", "after_extend_x1", "after_extend_x2-3",
                 "after_extend_x4+", "after_complete", "after_sell"]
    lanes = sorted({w["lane"] for w in windows})
    out: Dict[str, Any] = {
        "owner_direction": "#167 comment 5574408755 item 2 (completion ceiling from actual L5 behaviour)",
        "scope": ("WHAT L5 DID only. Pair costs are pre-rebate, pre-fee acquisition costs of a "
                  "$1.00-paying pair, > 1.00 mass intact. What PMUS economics can support is a "
                  "separate study; see economic_overlay_attachment_points."),
        "labels": {
            "relation": ("polymarket_l5.action_model._relation on the canonical residual BEFORE the "
                         "fill: flat < 1 share -> open; surplus side -> extend; deficit side -> complete"),
            "state": ("polymarket_l5.action_model.state_key: band(LAST OBSERVED fill price) | "
                      "tte bucket at the complete | |residual shares| bucket -- identical to live keys"),
            "own_band": "band of the COMPLETE fill's own price (not part of the live key)",
            "engine_avg": "complete price + average cost (cum spend/cum qty) of the surplus side before the fill",
            "marginal_lifo": "complete price + qty-weighted price of the most recent surplus lot(s) consumed, LIFO, matched part only",
            "window_realized": "window's matched-pair unit cost at settlement (wavg_up + wavg_down, final canonical row), per complete",
            "completed_usd": "complete price x matched qty (the part of the fill that formed pairs); dollar shares use this",
            "pairs_pct": "share of completed CONTRACTS (pairs) above the threshold; dollars scale with price, contracts do not",
            "usd": "full fill dollars, including any overshoot beyond the surplus",
            "inv_usd_before": "|residual shares| x average cost of the surplus side = unmatched dollars before the complete",
            "preceding": "relation of the previous BUY in the window; extend streak length counted in fills",
            "pct_above": "share strictly above the threshold after rounding the pair cost to 6 dp (0.41+0.59 is 1.00, not above)",
            "percentiles": "p-values are fill-count percentiles; usd_p50/usd_p90 are completed-dollar-weighted",
        },
        "provenance": provenance(rows),
        "counts": {
            "fills": n_fills, "windows": len(windows),
            "relations_live_rule": dict(relations),
            "completes": len(completes),
            "completes_pre_regime": sum(c["pre_regime"] for c in completes),
            "completes_with_overshoot": len(overshoot),
            "overshoot_usd": round(sum(c["price"] * c["overshoot_qty"] for c in overshoot), 2),
            "complete_fill_usd": round(sum(c["usd"] for c in completes), 2),
            "complete_completed_usd": round(sum(c["completed_usd"] for c in completes), 2),
            "span_utc": [rows[0].get("datetime_utc"), rows[-1].get("datetime_utc")] if rows else [],
        },
        "overall": {b: dist(completes, b) for b in BASES},
        "overall_window_realized_per_window": dist(
            [{"v": w["final_pair_cost"], "w": w["matched_usd"], "q": w["final_matched_qty"]}
             for w in windows if w["final_pair_cost"] is not None], "v", "w", "q"),
        "by_imbalance_bucket": by(completes, lambda c: c["imb_bucket"], order=imb_order),
        "by_tte_bucket": by(completes, lambda c: c["tte_bucket"], order=tte_order),
        "by_last_price_band_state": by(completes, lambda c: c["state"].split("|")[0], order=band_order),
        "by_own_price_band": by(completes, lambda c: c["own_band"], order=band_order),
        "by_state": by(completes, lambda c: c["state"], min_n=MIN_GROUP_N),
        "by_lane": by(completes, lambda c: c["lane"]),
        "by_month": by(completes, lambda c: c["month"]),
        "by_inventory_usd_before": by(completes, lambda c: c["inv_bucket"], order=inv_order),
        "by_preceding_relation": by(completes, lambda c: c["preceding"], order=pre_order),
        "completion_vs_hold": {"ALL": completion_vs_hold(windows),
                               **{ln: completion_vs_hold([w for w in windows if w["lane"] == ln])
                                  for ln in lanes}},
        "matched_pairs_by_month": matched_pairs_by_month(windows, completes),
        "economic_overlay_attachment_points": {
            "note": "NAMED ONLY, not computed here. The overlay converts an observed pair cost into supported/unsupported at PMUS.",
            "inputs": [
                "maker rebate per filled dollar on PMUS, per leg (complete leg and the surplus leg it matches), from the venue schedule in force at the fill time",
                "taker fee / maker fee per filled dollar (if any) per leg",
                "tick rounding of the complement price (floor to tick, as complement_target does) and the resulting fixed-point pair cost",
                "settlement payout per pair (1.00) and any settlement/redemption cost",
                "minimum-edge parameter currently in hard_pair_bound (1.00 - MIN_EDGE) and PAIR_OBSERVATION_CAP",
                "capital lock time (complete timestamp to settlement) if a carry cost is to be charged",
            ],
            "attach_to": ("each COMPLETE record's engine_avg (the basis the engine prices through); the "
                          "comparison is supported_ceiling = 1.00 + rebates - fees - min_edge (per pair "
                          "dollar) against this study's observed distribution by state"),
            "code_point": "src/pmus_sports_l5_decision.py: hard_pair_bound / complement_target",
        },
        "limitations": [
            "fills only: a COMPLETE here is a filled deficit-side buy, not the quote L5 posted; the ceiling is the distribution of filled completes, resting completes that never filled are invisible",
            "state price band is the previous fill's price (the live model's market-price proxy), not the book at the fill; it can be the opposite side's price",
            "engine_avg uses the canonical average-cost basis (cum spend / cum qty), which includes every earlier lot on the surplus side, matched or not; marginal_lifo is the alternative reading",
            "window_realized is an average-cost allocation at settlement, not a per-fill fact; it is the number the ledger locks",
            "maker/taker is known only inside takerOnly coverage (Aug 28-31); pair costs are not conditioned on it",
            "%d completes exceed the surplus (overshoot); pair costs there cover the matched part and dollar shares use completed_usd" % len(overshoot),
            "two lifetime SELL rows are applied to the lot ledger LIFO and otherwise ignored",
            "pair costs exclude rebates and fees by construction (owner: separate the two questions)",
            "the canonical file has no fills between 2026-04 and 2026-08 (Mar %d / Apr %d / Aug %d rows); 'by month' is three eras and the August cohort dominates every pooled number" % tuple(
                sum(1 for r in rows if str(r.get("datetime_utc", ""))[:7] == m) for m in ("2026-03", "2026-04", "2026-08")),
        ],
    }
    return out


def _group(rows: Sequence[dict]) -> Dict[str, List[dict]]:
    g: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        g[r["window_id"]].append(r)
    return g


# ------------------------------ synthetic rows (tests) ------------------------------

def synthetic_rows(fills: Sequence[tuple], window_id: str = "W", asset: str = "BTC",
                   duration_minutes: int = 15, settlement: str = "Up") -> List[dict]:
    """Canonical-shaped rows from (ts, outcome, side, price, qty, tte) tuples,
    with the cumulative columns computed by polymarket_l5.inventory."""
    trades = [{"timestamp": ts, "outcome": o, "side": s, "price": p, "shares": q,
               "dollars": round(p * q, 6)} for ts, o, s, p, q, _ in fills]
    states = running_states(trades)
    rows = []
    for (ts, o, s, p, q, tte), st in zip(fills, states):
        rows.append({
            "window_id": window_id, "market_slug": "%s-updown-%dm-%d" % (asset.lower(), duration_minutes, ts),
            "asset": asset, "duration_minutes": str(duration_minutes),
            "trade_seq": str(st["trade_seq"]), "timestamp": str(ts),
            "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seconds_to_settlement": str(tte), "outcome": o, "side": s,
            "price": str(p), "quantity": str(q), "dollars": str(round(p * q, 6)),
            "maker_taker": "unknown",
            "cum_up_qty": str(st["cum_up_qty"]), "cum_up_spend": str(st["cum_up_spend"]),
            "cum_down_qty": str(st["cum_down_qty"]), "cum_down_spend": str(st["cum_down_spend"]),
            "matched_qty": str(st["matched_qty"]),
            "matched_pair_unit_cost": "" if st["matched_pair_unit_cost"] is None else str(st["matched_pair_unit_cost"]),
            "residual_qty_signed": str(st["excess_qty"]),
            "residual_side": "Up" if st["excess_qty"] > 0 else "Down" if st["excess_qty"] < 0 else "",
            "settlement_outcome": settlement,
        })
    return rows


# ------------------------------ report ------------------------------

def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return "%.1f" % v if (pct or abs(v) >= 10) else "%.4f" % v
    return str(v).replace("|", "\\|")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Markdown table; a header containing '%' formats its column as a percent."""
    pct = [("%" in h) for h in headers]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(_fmt(v, p) for v, p in zip(r, pct)) + " |")
    return "\n".join(lines)


def _group_table(groups: Dict[str, dict]) -> str:
    rows = []
    for k, g in groups.items():
        e, m, w = g["engine_avg"], g["marginal_lifo"], g["window_realized"]
        rows.append([k, g["n"], g["usd"],
                     e.get("p50"), e.get("p90"), e.get("gt100_pairs_pct"), e.get("gt100_usd_pct"), e.get("gt102_usd_pct"),
                     m.get("p50"), m.get("p90"), m.get("gt100_pairs_pct"), m.get("gt100_usd_pct"),
                     w.get("p50"), w.get("gt100_usd_pct")])
    return _table(["group", "n", "completed $", "eng p50", "eng p90", "eng >1.00 pairs%", "eng >1.00 $%", "eng >1.02 $%",
                   "marg p50", "marg p90", "marg >1.00 pairs%", "marg >1.00 $%", "realized p50", "realized >1.00 $%"], rows)


def render_md(out: dict) -> str:
    c = out["counts"]
    parts = [
        "# L5 completion ceiling -- what L5 did when he chose COMPLETE",
        "",
        "Owner direction: %s. %s" % (out["owner_direction"], out["scope"]),
        "",
        "Source: `%s` (%s rows, %d windows, %s -> %s, sha16 %s)." % (
            out["provenance"]["source_file"], c["fills"], c["windows"],
            c["span_utc"][0], c["span_utc"][1], out["provenance"]["content_sha256_16"]),
        "",
        "Relations by the live rule (`action_model._relation`): %s. COMPLETE fills: %d "
        "(%d pre-regime), fill $%s, of which $%s completed pairs; %d completes overshoot the "
        "surplus ($%s of overshoot)." % (
            json.dumps(c["relations_live_rule"]), c["completes"], c["completes_pre_regime"],
            c["complete_fill_usd"], c["complete_completed_usd"], c["completes_with_overshoot"],
            c["overshoot_usd"]),
        "",
        "## Bases",
        "",
        "* **engine_avg** -- %s" % out["labels"]["engine_avg"],
        "* **marginal_lifo** -- %s" % out["labels"]["marginal_lifo"],
        "* **window_realized** -- %s" % out["labels"]["window_realized"],
        "",
        "Dollar shares weight by completed dollars (%s). Percentiles are fill-count; usd_p50/p90 are dollar-weighted. "
        "All pair costs are pre-rebate, pre-fee." % out["labels"]["completed_usd"],
        "",
        "## Overall distribution of pair cost at COMPLETE",
        "",
    ]
    rows = []
    for b in BASES:
        d = out["overall"][b]
        rows.append([b, d["n"], d["usd"], d["mean"], d["usd_wmean"], d["pair_wmean"], d["p10"], d["p25"],
                     d["p50"], d["p75"], d["p90"], d["p99"], d["max"], d["usd_p50"], d["usd_p90"]])
    parts.append(_table(["basis", "n", "completed $", "mean", "$-wmean", "pair-wmean", "p10", "p25", "p50",
                         "p75", "p90", "p99", "max", "$-p50", "$-p90"], rows))
    parts += ["", "### Share above thresholds (fills / completed pairs / completed dollars)", ""]
    rows = []
    for b in BASES:
        ab = out["overall"][b]["above"]
        rows.append([b] + [x for t in ab.values() for x in (t["fills_pct"], t["pairs_pct"], t["usd_pct"])])
    hdr = ["basis"] + [x for t in THRESHOLDS for x in
                       (">%.2f fills%%" % t, ">%.2f pairs%%" % t, ">%.2f $%%" % t)]
    parts.append(_table(hdr, rows))
    pw = out["overall_window_realized_per_window"]
    parts += ["", "Per-window realized pair cost (windows with any matched pair, weighted by matched-pair dollars): "
              "n=%d p50=%s p90=%s max=%s; >1.00 by windows = %s%%, by pairs = %s%%, by $ = %s%%." % (
                  pw["n"], pw["p50"], pw["p90"], pw["max"], pw["above"]["1.00"]["fills_pct"],
                  pw["above"]["1.00"]["pairs_pct"], pw["above"]["1.00"]["usd_pct"]), ""]
    for title, key in (("By imbalance bucket (live key, |residual shares| before the complete)", "by_imbalance_bucket"),
                       ("By time-to-settlement bucket (live key)", "by_tte_bucket"),
                       ("By last-observed-price band (live key)", "by_last_price_band_state"),
                       ("By the complete's own price band", "by_own_price_band"),
                       ("By lane (asset-duration)", "by_lane"),
                       ("By month of the complete", "by_month"),
                       ("By unmatched dollars before the complete", "by_inventory_usd_before"),
                       ("By what preceded the complete (open / extend streak / complete)", "by_preceding_relation")):
        parts += ["## %s" % title, "", _group_table(out[key]), ""]
    st = sorted(out["by_state"].items(), key=lambda kv: -kv[1]["n"])
    parts += ["## By full live state (price band | tte | imbalance), top 25 by count", "",
              _group_table(dict(st[:25])), "",
              "(%d states with n >= %d in the JSON; smaller cells pooled.)" % (
                  sum(1 for k, _ in st if not k.startswith("other")), MIN_GROUP_N), ""]
    parts += ["## Completed at all vs held residual to settlement (per window)", ""]
    rows = []
    for k, v in out["completion_vs_hold"].items():
        rows.append([k, v["windows"], v["any_complete_pct"], v["both_sides_pct"], v["held_residual_ge_1share_pct"],
                     v["flat_at_settlement_pct"], v["median_completes_per_window"], v["bought_usd"],
                     v["matched_pair_usd_pct_of_bought"], v["residual_usd_at_settlement_p50"],
                     v["residual_usd_at_settlement_p90"], v["residual_usd_at_settlement_total"]])
    parts.append(_table(["lane", "windows", "any complete %", "both sides %", "held >=1sh %", "flat %",
                         "med completes/win", "bought $", "matched-pair $ % of bought", "resid $ p50",
                         "resid $ p90", "resid $ total"], rows))
    parts += ["", "## Dollar-weighted average pair cost of ALL matched pairs, per month", ""]
    rows = []
    for m, v in out["matched_pairs_by_month"].items():
        rows.append([m, v["windows"], v["matched_pairs"], v["usd_wavg_pair_cost_window_basis"],
                     v["usd_wavg_pair_cost_lot_lifo_basis"], v["completes"],
                     v.get("usd_wavg_engine_pair_at_complete")])
    parts.append(_table(["month", "windows", "matched pairs", "$-wavg pair (window basis)",
                         "$-wavg pair (lot LIFO basis)", "completes", "$-wavg engine pair at complete"], rows))
    eo = out["economic_overlay_attachment_points"]
    parts += ["", "## Where the PMUS economic overlay attaches (named only, not computed)", "",
              eo["note"], ""] + ["* %s" % i for i in eo["inputs"]] + [
        "", "Attach to: %s. Code point: `%s`." % (eo["attach_to"], eo["code_point"]),
        "", "## Limitations", ""] + ["* %s" % l for l in out["limitations"]]
    parts += ["", "Generated by `python3 -m src.l5_completion_ceiling_study`; JSON: `%s`." % OUT_JSON, ""]
    return "\n".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=False, default=str)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_md(out))
    print(json.dumps(out["counts"], indent=1))
    for b in BASES:
        d = out["overall"][b]
        print("%-16s n=%d p50=%s p90=%s max=%s  >1.00 fills=%s%% $=%s%%  >1.02 $=%s%%" % (
            b, d["n"], d["p50"], d["p90"], d["max"], d["above"]["1.00"]["fills_pct"],
            d["above"]["1.00"]["usd_pct"], d["above"]["1.02"]["usd_pct"]))
    print(json.dumps(out["by_imbalance_bucket"], indent=1))
    print("wrote %s and %s" % (OUT_JSON, OUT_MD))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
