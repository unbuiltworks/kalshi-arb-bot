"""Derive the three ADAPTED arms' inventories from OUR OWN evidenced maker fills.

WHAT THIS REUSES, AND WHY. The service already rests shadow maker quotes and
decides each one against public prints under the four fill criteria. This does
not re-run that: it reads the evidenced fills it produced and applies each cap
policy to them, so the arms share one evidence pipeline and cannot diverge from
it. The frozen R/S/D rows are read and never written.

STRICT ONLY. A fill enters an arm's inventory at VOLUME_AHEAD_CLEARED and
nowhere else. `evidenced_fill is True` alone is not sufficient; the row must
also carry the strict criterion. Undecided rows are UNKNOWN and are counted,
never treated as no-fill.

THE COUNTERFACTUAL LIMITATION, stated plainly. Withholding a side under a cap
removes our quote from that side; the fills that quote would have taken are
then excluded. That is sound. What it cannot capture is whether withholding one
side would have changed the OTHER side's fills -- our shadow quotes take no
liquidity and signal nothing, so the effect should be nil, but it is an
assumption and not a measurement. This is
HISTORICAL_ACTION_FILTER_COUNTERFACTUAL applied to our own quotes, one step
closer to executable than the L5 version and still not an executable replay.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import adapted_policy as AP
from src.l5_shadow import ledger as LG

STRICT = "VOLUME_AHEAD_CLEARED"
RESOLUTIONS = "adapted_resolutions.json"
SOURCES = ("maker_outcomes.jsonl", "two_sided_outcomes.jsonl",
           "inventory_outcomes.jsonl")
OUT = "adapted_windows.jsonl"


def _strict(o: dict) -> bool:
    if o.get("evidenced_fill") is not True:
        return False
    fc = o.get("fill_criteria") or {}
    if fc.get("strongest_satisfied") == STRICT:
        return True
    v = fc.get(STRICT)
    return bool(v is True or (isinstance(v, dict) and v.get("satisfied")))


def _rows(out_dir: str) -> List[dict]:
    rows: List[dict] = []
    for name in SOURCES:
        p = os.path.join(out_dir, name)
        if not os.path.exists(p):
            continue
        for line in open(p):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    rows.sort(key=lambda r: (r.get("resolved_at") or 0,
                             str(r.get("intent_id") or "")))
    return rows


def _resolutions(out_dir: str, keys: Sequence[str]) -> Dict[str, Any]:
    """Venue resolutions, cached. A window settles once and never changes.

    Uses the service's own pm_settlement, which reports `resolved` only when the
    venue says closed AND an outcome price is at or above 0.99 -- a disputed or
    unresolved market comes back unresolved, and no P&L is booked on it.
    """
    path = os.path.join(out_dir, RESOLUTIONS)
    cache: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            cache = json.load(open(path))
        except ValueError:
            cache = {}
    fresh = False
    for k in keys:
        if cache.get(k, {}).get("resolved"):
            continue                      # settled once, never re-asked
        try:
            from src.l5_shadow import venues as V
            r = V.pm_settlement(k)
        except Exception:                                     # noqa: BLE001
            r = None
        if r is not None:
            cache[k] = r
            fresh = True
    if fresh:
        try:
            with open(path, "w") as fh:
                json.dump(cache, fh, default=str)
        except OSError:
            pass
    return cache


def build(out_dir: str, *, cohort_prefix: str = "btc-updown-15m") -> List[dict]:
    """One settled row per (window, arm, venue), plus an uncapped control."""
    by_win: Dict[tuple, List[dict]] = defaultdict(list)
    for r in _rows(out_dir):
        mk = str(r.get("market_key") or "")
        if cohort_prefix and not mk.startswith(cohort_prefix):
            continue
        by_win[(mk, r.get("venue"))].append(r)

    res = _resolutions(out_dir, sorted({mk for mk, _ in by_win}))
    out: List[dict] = []
    for (mk, venue), rs in sorted(by_win.items()):
        undecided = sum(1 for r in rs if r.get("evidenced_fill") is None)
        fills = [r for r in rs if _strict(r)]
        for arm in ("control",) + AP.ARMS:
            st = AP.new_state()
            taken = 0
            for r in fills:
                side = ("Up" if str(r.get("desired_outcome")) == "Up"
                        else "Down")
                if arm != "control" and AP.withheld(st, arm=arm) == side:
                    continue          # this side is withheld: the quote is not there
                q = D(str(r.get("filled_qty") or r.get("quote_qty") or "0"))
                px = D(str(r.get("quote_price") or "0"))
                if q <= 0:
                    continue
                st = AP.on_evidenced_fill(st, side=side, qty=q, cost=q * px)
                taken += 1
            e = AP.economics(st)
            up, dn = D(e["up_qty"]), D(e["dn_qty"])
            row = {
                "window": mk, "venue": venue, "arm": arm,
                "fills_taken": taken,
                "strict_fills_available": len(fills),
                "undecided_quotes": undecided,
                "two_sided_evidenced": bool(up > 0 and dn > 0),
                "matched_qty": float(D(e["matched_qty"])),
                "residual_qty": float(D(e["residual_qty"])),
                "pair_cost": (float(D(e["pair_cost"]))
                              if e["pair_cost"] else None),
                "normalized_imbalance": (float(D(e["normalized_imbalance"]))
                                         if e["normalized_imbalance"] else None),
                "settled": False, "matched_pnl": None, "residual_pnl": None,
                "total_pnl": None, "fees": 0.0,
                "resolution": (res.get(mk) or {}).get("resolution_source"),
                "hindsight_fills": sum(1 for r in fills
                                       if r.get("hindsight") is True),
                "labels": ("strict VOLUME_AHEAD_CLEARED fills only; shadow "
                           "inventory, never venue-confirmed and never "
                           "realized profit"),
            }
            r = res.get(mk)
            if r and r.get("resolved") and (up > 0 or dn > 0):
                pos = {"venue": venue, "cohort": "BTC15m", "market_key": mk,
                       "clock": "venue", "n_fills": taken, "intent_ids": [],
                       "up_qty": up, "dn_qty": dn,
                       "up_cost": D(str(st["up_cost"])),
                       "dn_cost": D(str(st["dn_cost"])), "fees": D("0")}
                sett = LG.settle(pos, r)
                if sett.get("state") == "SETTLED":
                    row["settled"] = True
                    row["matched_pnl"] = float(sett["matched_pair_pnl"])
                    row["residual_pnl"] = float(sett["residual_pnl"])
                    row["total_pnl"] = float(sett["realized_shadow"])
                    row["fees"] = float(sett["fees_charged"])
                    row["winning_outcome"] = sett.get("winning_outcome")
            out.append(row)
    return out


def report(out_dir: str) -> Dict[str, Any]:
    rows = build(out_dir)
    p = os.path.join(out_dir, OUT)
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    from analysis.l5.adapted.prospective import report as score
    rep = score(out_dir)
    rep["collector"] = {
        "windows": len({r["window"] for r in rows}),
        "rows": len(rows), "strict_criterion": STRICT,
        "limitation": ("HISTORICAL_ACTION_FILTER_COUNTERFACTUAL over our own "
                       "shadow quotes; not an executable replay"),
    }
    return rep
