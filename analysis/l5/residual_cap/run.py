"""P0-P4: residual-cap shadow arms, each with its OWN inventory history.

    python3 analysis/l5/residual_cap/run.py

SHADOW ONLY. Live cap stays 6. UNBOUNDED is diagnostic and is not a live
candidate. Nothing here submits, prices or reprices anything.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_band_semantics as bs                 # noqa: E402
from src import l5_residual_cap_arms as rc              # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
OUT = HERE / "residual_cap_arms.json"

REALIZED = {
    "aec-setkameua-sapole-salvas-2026-09-13": D("-1.0779"),
    "aec-nfl-buf-hou-2026-09-13": D("-2.5896"),
    "aec-mlb-lad-mia-2026-09-13": D("6.0034"),
}
SETTLE_T = {                     # venue resolution times, for capital-hours
    "aec-setkameua-sapole-salvas-2026-09-13": "2026-09-13T18:49:01+00:00",
    "aec-nfl-buf-hou-2026-09-13": "2026-09-13T20:28:17+00:00",
    "aec-mlb-lad-mia-2026-09-13": "2026-09-13T20:47:07+00:00",
}


def _ep(t) -> float:
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def _d(x, dflt=D("0")) -> D:
    v = bs._d(x)
    return dflt if v is None else v


def load():
    sessions = [json.loads(l) for l in gzip.open(SESSIONS, "rt")]
    by_market, orders = defaultdict(list), defaultdict(list)
    for s in sessions:
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else None
        for d in ds:
            by_market[str(d.get("slug"))].append(d)
        for o in (s.get("orders") or []):
            orders[slug].append(o)
    for ds in by_market.values():
        ds.sort(key=lambda d: (str(d.get("t_utc") or ""),
                               int(d.get("decision_idx") or 0)))
    return dict(by_market), dict(orders)


def production_fills(ds):
    """Every fill production actually took, recovered from inventory deltas,
    with the side and the price it paid. The only fills that exist."""
    out, prev = [], None
    for d in ds:
        inv = d.get("inventory_before") or {}
        cur = (_d(inv.get("up")), _d(inv.get("dn")), _d(inv.get("spent")))
        if prev is not None:
            dup, ddn, dsp = cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2]
            if dup > 0 and ddn <= 0:
                out.append({"at": d, "side": "up", "qty": dup,
                            "px": (dsp / dup) if dup else D("0")})
            elif ddn > 0 and dup <= 0:
                out.append({"at": d, "side": "dn", "qty": ddn,
                            "px": (dsp / ddn) if ddn else D("0")})
            elif dup > 0 and ddn > 0:
                # both moved inside one gap: split proportionally at the
                # blended price, and SAY SO rather than invent an order
                tot = dup + ddn
                out.append({"at": d, "side": "up", "qty": dup,
                            "px": (dsp / tot) if tot else D("0"),
                            "blended": True})
                out.append({"at": d, "side": "dn", "qty": ddn,
                            "px": (dsp / tot) if tot else D("0"),
                            "blended": True})
        prev = cur
    return out


def seed_from(ds) -> "rc.ArmInventory":
    """Start every arm from the position production ALREADY held at the
    first retained decision.

    Railway retention starts mid-campaign for several markets, so
    reconstructing inventory purely from deltas begins at zero and
    understates the residual for the rest of the run -- which silently
    loosens every cap. mzp-ezt was admitting extends at cap 6 that
    production's own cap-6 had refused, for exactly this reason.
    """
    inv = rc.ArmInventory()
    first = (ds[0].get("inventory_before") or {}) if ds else {}
    up, dn = _d(first.get("up")), _d(first.get("dn"))
    inv.up, inv.dn = up, dn
    inv.cost_up = up * _d(first.get("avg_up"))
    inv.cost_dn = dn * _d(first.get("avg_dn"))
    for d0 in ds:
        u = bs._d(d0.get("behavioral_unit"))
        if u:
            inv.unit = u
            break
    return inv


def winner_of(slug, ds):
    if slug not in REALIZED:
        return None
    last = ds[-1].get("inventory_before") or {}
    return bs.winning_side(REALIZED[slug], _d(last.get("up")),
                           _d(last.get("dn")), _d(last.get("spent")))


def simulate(by_market, orders, *, cap: int, band_mode: str, fill_rule: str,
             extend_fill_times):
    """One arm. Its inventory is its own from the first divergence on."""
    per_market, extras = {}, []
    for slug, ds in by_market.items():
        arm = seed_from(ds)
        fills = production_fills(ds)
        fills_at = defaultdict(list)
        for f in fills:
            fills_at[id(f["at"])].append(f)
        refused_prod_extends = 0
        extra_fills, blocked_cap, blocked_band, blocked_spend = 0, 0, 0, 0
        opps = 0
        matched_at_extra = []
        for d in ds:
            # 1. production's own fills land first -- unless THIS arm would
            #    have refused them, which a bigger cap can genuinely do
            for f in fills_at.get(id(d), ()):
                is_extend = (arm.surplus_side is not None
                             and f["side"] == arm.surplus_side)
                if is_extend and not arm.cap_allows_extend(f["qty"], cap):
                    refused_prod_extends += 1
                    continue
                arm.add(f["side"], f["qty"], f["px"], label="production",
                        t_utc=str(d.get("t_utc")))
            # 2. then the gate this arm would have applied to THIS decision
            caps = [str(c) for c in (d.get("cap_binding") or [])]
            refusal = str(d.get("refusal") or "")
            # a residual_cap row records NO acted_draw -- the cap-fallback
            # branch overwrites `acted` and leaves the draw null -- but the
            # cap only ever fires on a sampled EXTEND, so the draw is known
            # from the gate that caught it. Reading acted_draw alone dropped
            # both of these rows and reported cap>=8 as having nothing extra
            # to admit under the CURRENT band.
            sampled = ("extend" if refusal.startswith("residual_cap")
                       else (d.get("acted_draw") or d.get("acted")))
            if sampled not in ("extend", "open") or not caps:
                continue
            eb = d.get("entry_band_left") or {}
            held = _d(eb.get("held_inventory"))
            if "entry_band_left" in caps:
                if held <= 0:
                    continue                      # ENTRY is untouched in every arm
                opps += 1
                if band_mode == "current":
                    blocked_band += 1
                    continue
                bid = _d(eb.get("bid"))
            elif "residual_cap" in caps:
                opps += 1
                bid = None
            else:
                continue
            side = arm.surplus_side or ("dn" if _d(
                (d.get("inventory_before") or {}).get("dn")) > 0 else "up")
            if bid is not None:
                px = bs.held_side_price(bid, side)
            else:
                px = arm.avg(side) or D("0.5")     # cap refusals record no bid
            qty = _d((d.get("planned_safe_size") or {}).get("contracts"))
            if qty <= 0:
                continue
            if not arm.cap_allows_extend(qty, cap):
                blocked_cap += 1
                continue
            if not bs.spend_cap_allows(arm.spent, qty * px):
                blocked_spend += 1
                continue
            if fill_rule == "TAPE_SUPPORTED" and not bs.tape_support(
                    _ep(d.get("t_utc")), slug, extend_fill_times):
                continue
            held_at_fill = arm.up if side == "up" else arm.dn
            arm.add(side, qty, px, label="counterfactual_extend",
                    t_utc=str(d.get("t_utc")))
            extra_fills += 1
            matched_at_extra.append((arm.matched, d, side, qty, px,
                                     held_at_fill))
        w = winner_of(slug, ds)
        pnl = rc.decompose_pnl(arm, w)
        # production's own terminal position, scored the same way
        prod = seed_from(ds)
        for f in fills:
            prod.add(f["side"], f["qty"], f["px"])
        prod_pnl = rc.decompose_pnl(prod, w)
        per_market[slug] = {
            "band_opportunities": opps,
            "extra_extend_fills": extra_fills,
            "blocked_by_band": blocked_band,
            "blocked_by_cap": blocked_cap,
            "blocked_by_spend_cap": blocked_spend,
            "production_extends_this_arm_would_refuse": refused_prod_extends,
            "arm_up": str(arm.up), "arm_dn": str(arm.dn),
            "arm_spent": str(arm.spent.quantize(D("0.0001"))),
            "peak_residual_units": str(arm.peak_residual_units.quantize(
                D("0.001"))),
            "peak_residual_contracts": str(arm.peak_residual_usd),
            "peak_unmatched_usd": str(arm.peak_unmatched_usd.quantize(
                D("0.0001"))),
            "matched_inventory": str(arm.matched),
            "production_matched": str(prod.matched),
            "production_spent": str(prod.spent.quantize(D("0.0001"))),
            "production_terminal_residual": str(abs(prod.residual)),
            "terminal_residual": str(abs(arm.residual)),
            "winner": w,
            "arm_pnl": pnl, "production_pnl": prod_pnl,
            "delta_vs_production": (
                str((D(pnl["total_pnl"]) - D(prod_pnl["total_pnl"])).quantize(
                    D("0.0001"))) if pnl and prod_pnl else "UNSETTLED"),
        }
        # P3: what happened after each extra extend -- and the test is
        # NOT "did matched inventory grow at all". matched = min(up, dn), so
        # a contract added to the side already held becomes matched ONLY if
        # the opposite side later exceeds the quantity that side held at the
        # moment of the fill. An earlier version asked whether total matched
        # grew, which was true whenever ANY later completion filled and
        # classified 27 of 28 extras as productive when none of them were.
        final_opposite = {"up": arm.dn, "dn": arm.up}
        for m_then, d, side, qty, px, held_at_fill in matched_at_extra:
            became_matched = final_opposite[side] > held_at_fill
            later_complete_orders = any(
                o.get("relation") == "complete" for o in orders.get(slug, ()))
            if w is None:
                cls = rc.CLASS_D
            elif became_matched:
                cls = rc.CLASS_A
            elif later_complete_orders:
                cls = rc.CLASS_B_THEN_C
            else:
                cls = rc.CLASS_C
            extras.append({"market": slug, "t_utc": str(d.get("t_utc")),
                           "side": side, "contracts": str(qty),
                           "price_proxy": str(px), "class": cls,
                           "held_side_qty_at_fill": str(held_at_fill),
                           "final_opposite_qty": str(final_opposite[side]),
                           "became_matched": became_matched})
    return per_market, extras


def main() -> int:
    by_market, orders = load()
    extend_fill_times = defaultdict(list)
    for slug, ds in by_market.items():
        inv = rc.ArmInventory()
        for f in production_fills(ds):
            if inv.surplus_side is not None and f["side"] == inv.surplus_side:
                extend_fill_times[slug].append(_ep(f["at"].get("t_utc")))
            inv.add(f["side"], f["qty"], f["px"])

    grid, p3 = {}, {}
    caps = list(rc.CAP_ARMS) + [rc.UNBOUNDED]
    for band_mode in rc.BAND_MODES:
        for cap in caps:
            name = f"band={band_mode}|cap={'UNBOUNDED' if cap == rc.UNBOUNDED else cap}"
            cell = {}
            for rule in rc.FILL_RULES:
                pm, extras = simulate(by_market, orders, cap=cap,
                                      band_mode=band_mode, fill_rule=rule,
                                      extend_fill_times=extend_fill_times)
                settled = [v for v in pm.values()
                           if v["delta_vs_production"] != "UNSETTLED"]
                deltas = [D(v["delta_vs_production"]) for v in settled]
                cell[rule] = {
                    "extra_extend_fills": sum(v["extra_extend_fills"]
                                              for v in pm.values()),
                    "production_extends_refused_by_this_arm": sum(
                        v["production_extends_this_arm_would_refuse"]
                        for v in pm.values()),
                    "blocked_by_band": sum(v["blocked_by_band"]
                                           for v in pm.values()),
                    "blocked_by_cap": sum(v["blocked_by_cap"]
                                          for v in pm.values()),
                    "settled_markets": len(settled),
                    "total_delta_vs_production": str(
                        sum(deltas, D("0")).quantize(D("0.0001"))),
                    "matched_pair_pnl_delta": str(sum(
                        (D(v["arm_pnl"]["matched_pair_pnl"])
                         - D(v["production_pnl"]["matched_pair_pnl"])
                         for v in settled), D("0")).quantize(D("0.0001"))),
                    "directional_residual_pnl_delta": str(sum(
                        (D(v["arm_pnl"]["directional_residual_pnl"])
                         - D(v["production_pnl"]["directional_residual_pnl"])
                         for v in settled), D("0")).quantize(D("0.0001"))),
                    "capital_deployed_delta_usd": str(sum(
                        (D(v["arm_spent"]) - D(v["production_spent"])
                         for v in pm.values()), D("0")).quantize(D("0.0001"))),
                    # THE matched-inventory number. The matched-pair P&L line
                    # below moves even at constant matched QUANTITY, because
                    # buying cheap extra inventory lowers that side's average
                    # cost and the split is taken at averages. Contracts do
                    # not have that artifact, so contracts is the answer to
                    # "did extra capacity produce matched inventory".
                    "matched_contracts_delta": str(sum(
                        (D(v["matched_inventory"])
                         - D(v["production_matched"])
                         for v in pm.values()), D("0"))),
                    "peak_residual_units": rc.quantiles(
                        [D(v["peak_residual_units"]) for v in pm.values()]),
                    "terminal_residual": rc.quantiles(
                        [D(v["terminal_residual"]) for v in pm.values()]),
                    "peak_unmatched_usd": rc.quantiles(
                        [D(v["peak_unmatched_usd"]) for v in pm.values()]),
                    "settlement_delta_quantiles": rc.quantiles(sorted(deltas)),
                    "worst_market_delta": (
                        str(min(deltas)) if deltas else None),
                    "by_market": {k: v for k, v in pm.items()
                                  if v["band_opportunities"]
                                  or v["production_extends_this_arm_would_refuse"]},
                }
                if rule == "TAPE_SUPPORTED":
                    p3[name] = dict(Counter(e["class"] for e in extras))
                    cell[rule]["extras_detail"] = extras
            grid[name] = cell

    doc = {
        "labels": "SHADOW ONLY; live residual cap stays 6; UNBOUNDED is a "
                  "diagnostic measurement of suppressed action mass and is "
                  "not a live candidate",
        "design": {
            "independent_inventory": "each arm keeps its own up/dn/cost and "
                                     "adjudicates every later gate on it",
            "arms_can_lose_fills": "an arm that spends headroom early "
                                   "REFUSES production extends it can no "
                                   "longer afford; reported per cell",
            "no_imputed_completions": "no opposite-side fill is ever invented "
                                      "after a counterfactual extend",
            "structural_note": "matched = min(up, dn); extra inventory on the "
                               "side already held cannot become matched "
                               "unless the opposite side later exceeds the "
                               "original held quantity",
        },
        "grid": grid,
        "p3_outcome_classes": p3,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    for name, cell in grid.items():
        t = cell["TAPE_SUPPORTED"]
        print(f"{name:<34} extra={t['extra_extend_fills']:<3} "
              f"refused_prod={t['production_extends_refused_by_this_arm']:<3} "
              f"blk_band={t['blocked_by_band']:<3} blk_cap={t['blocked_by_cap']:<3} "
              f"delta={t['total_delta_vs_production']:>9} "
              f"matched_delta={t['matched_pair_pnl_delta']:>8}")
    print()
    for name, c in p3.items():
        if c:
            print(name, c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
