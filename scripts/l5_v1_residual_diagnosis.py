"""L5-ADAPTED INVENTORY ENGINE V1 -- diagnosis of Variant C stranded residual.

DIAGNOSIS, NOT OPTIMISATION. No threshold is introduced, nothing is fitted, and
no parameter is searched. theta, clip, residual_cap_k and hold_s are read from
the frozen engine. The question is only: for each residual Variant C carried to
settlement, WHAT HAPPENED NEXT in the real full-depth tape, and which of four
mechanisms could actually have changed that window's outcome.

COHORT. The 49 out-of-sample windows, which are CONSUMED: they were spent as
Variant C's single held-out validation and are diagnostic data from here on.
Nothing in this module may become validation evidence for V1; V1 must be
validated on forward windows that did not exist when it was written.

THE FOUR MECHANISMS, and what each is credited with:

  A PERSISTENT MAKER   an acceptable complement already resting, priced from the
                       held side's all-in basis, left in place while the market
                       came to us. Classified per window as never reached /
                       displayed only / traded at / traded through / fillable
                       after queue, the last using the queue-aware model at
                       q10, q20 and q30 -- display alone is never a fill.
  B CONTINUOUS INVENTORY  cheaper same-side inventory appearing while we wait
                       lowers the aggregate basis and so RAISES the acceptable
                       complement price. Credited only where the cheaper
                       acquisition would itself have filled on an attributable
                       print, and only if the recomputed complement then becomes
                       fillable.
  C SPEED              an acceptable complement that really occurred but lasted
                       less than the observation cadence. Duration and the
                       displayed/traded volume inside it are measured.
  D OPEN               loss already embedded when the first pair was formed from
                       flat, where both quotes are uncapped by design and the
                       one-tick spread makes the pair cost ~0.99.

Recoverable dollars are computed against what actually happened: a stranded
residual of q at all-in basis b loses q*b when its side loses; completing it at
p instead costs q*(b + p - 1). The difference is what the mechanism could have
saved, and it is reported per window and per mechanism.
"""
from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

OOS_TAPE = "data/pmus_btc15m_tape_oos/oos_tape_ordered.jsonl"
SETTLE = "analysis/l5/r7_oos_settlement_authoritative.json"
OUT = "analysis/l5/v1_residual_diagnosis.json"
CLIPS = (10, 20, 30)


def cohort():
    by = CR.load_tape(OOS_TAPE)
    win = json.load(open(SETTLE))["winners"]
    coh = {s: win[s] for s in sorted(by)
           if CR.evaluate(s, by[s]).get("tape_test_passes")
           and s not in USABLE and s in win}
    return by, coh


# --------------------------------------------------------------- instrumented run

def trace(rows, *, winner: str, clip: D) -> Dict[str, Any]:
    """Variant C over one window, recording the state every decision saw."""
    eng = E.NativeEngine(clip=clip, prospective_cap=False,
                         complement_controller=True,
                         extend_requires_balance=True)
    venue = P.ReplayVenue(fill_model="queue_aware", respect_limit=True)
    ladder = None
    steps: List[Dict[str, Any]] = []
    opens: List[Dict[str, Any]] = []
    fills: List[Dict[str, Any]] = []

    for row in rows:
        kind = row.get("kind")
        if kind == "book":
            ladder = row
            eng.ladder = row
            continue
        if kind != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        try:
            eng.on_book(row, ladder, execute_taker=lambda it:
                        (it["qty"], it["cost"], it["fee"]))
        except E.StaleBook:
            continue
        if eng.locked_at is None:
            for side in ("up", "down"):
                st = eng.resting.get(side)
                if not st:
                    continue
                got = venue.fills_for(eng, row, side, D(str(st["px"])),
                                      D(str(st["qty"])), ladder)
                if got is None:
                    continue
                qty, fpx = got
                if not eng._cap_permits(side, qty):
                    continue
                before = min(eng.bk.q["up"], eng.bk.q["down"])
                eng.on_fill(side, qty, qty * fpx, maker=True)
                fills.append({"t": row.get("t"), "side": side,
                              "px": fpx, "qty": qty})
                if before == 0 and min(eng.bk.q["up"], eng.bk.q["down"]) > 0:
                    opens.append({"t": row.get("t"), "side": side,
                                  "px": fpx, "qty": qty,
                                  "other_basis": eng.bk.avg(
                                      "down" if side == "up" else "up")})
        r = eng.bk.residual()
        steps.append({
            "t": row.get("t"), "ladder": ladder, "row": row,
            "resid": r,
            "held": ("up" if r > 0 else "down" if r < 0 else None),
            "basis": (eng.bk.economic_basis("up") if r > 0
                      else eng.bk.economic_basis("down") if r < 0 else D(0)),
            "cap": (eng.complement_cap("down") if r > 0
                    else eng.complement_cap("up") if r < 0 else None),
        })
    res = eng.settle(winner)
    res["steps"] = steps
    res["opens"] = opens
    res["fills"] = fills
    return res


def _epoch(t) -> Optional[float]:
    return S._epoch(t)


# ---------------------------------------------------------------- mechanism A/C

def classify_complement(steps, i0: int, *, clip: D) -> Dict[str, Any]:
    """From step i0 onward, what happened to a complement resting at the cap?

    The cap is recomputed from the basis at each step, so it tightens if the
    held side grew -- it is never taken from the future.
    """
    reached = displayed = traded_at = traded_through = 0
    fill_qty = {str(c): D(0) for c in CLIPS}
    windows_of_opportunity: List[Dict[str, Any]] = []
    run: Optional[Dict[str, Any]] = None
    qstate: Dict[str, Any] = {}
    best_seen: Optional[D] = None

    for st in steps[i0:]:
        r, held, cap = st["resid"], st["held"], st["cap"]
        if r == 0 or held is None or cap is None or cap <= 0:
            if run:
                windows_of_opportunity.append(run)
                run = None
            continue
        comp = S.complement(held)
        row, lad = st["row"], st["ladder"]
        # the best own-terms price at which the market would SELL us the
        # complement right now, from the contemporaneous full-depth ladder
        lv = S.buy_levels(lad, comp) if lad else []
        best = None
        shown = D(0)
        for level in lv:
            px = S._d(level[0])
            qy = S._d(level[1])
            if px is None or qy is None or qy <= 0:
                continue
            if best is None or px < best:
                best = px
            if px <= cap:
                shown += qy
        if best is not None and (best_seen is None or best < best_seen):
            best_seen = best
        last = S._d(row.get("last"))
        own_last = S.own_price(comp, last) if last is not None else None
        vol = S._d(row.get("shares_traded_delta_lower_bound")) or D(0)

        hit = False
        if best is not None and best <= cap:
            reached += 1
            displayed += 1
            hit = True
        if own_last is not None and own_last == cap and vol > 0:
            traded_at += 1
            hit = True
        if own_last is not None and own_last < cap and vol > 0:
            traded_through += 1
            hit = True

        # would a resting clip at the cap actually have filled, after queue?
        for c in CLIPS:
            q, fpx = S.maker_fill(row, comp, D(c), queue_aware=True,
                                  fill_model="queue_aware", ladder=lad,
                                  qstate=qstate.setdefault(str(c), {}),
                                  limit_px=cap)
            if q > 0 and fpx is not None:
                fill_qty[str(c)] += q

        if hit:
            if run is None:
                run = {"t0": st["t"], "t1": st["t"], "ticks": 0,
                       "displayed": D(0), "traded": D(0), "cap": str(cap)}
            run["t1"] = st["t"]
            run["ticks"] += 1
            run["displayed"] += shown
            run["traded"] += vol
        elif run:
            windows_of_opportunity.append(run)
            run = None
    if run:
        windows_of_opportunity.append(run)

    for w in windows_of_opportunity:
        a, b = _epoch(w["t0"]), _epoch(w["t1"])
        w["duration_s"] = (None if a is None or b is None else b - a)
        w["displayed"] = str(w["displayed"])
        w["traded"] = str(w["traded"])

    if traded_through:
        verdict = "traded_through"
    elif traded_at:
        verdict = "traded_at"
    elif displayed:
        verdict = "displayed_only"
    else:
        verdict = "never_reached"
    return {"verdict": verdict, "reached_ticks": reached,
            "displayed_ticks": displayed, "traded_at_ticks": traded_at,
            "traded_through_ticks": traded_through,
            "best_own_terms_seen": (None if best_seen is None
                                    else str(best_seen)),
            "fillable_qty": {k: str(v) for k, v in fill_qty.items()},
            "opportunities": windows_of_opportunity}


# ------------------------------------------------------------------ mechanism B

def cheaper_same_side(steps, i0: int, *, clip: D) -> Dict[str, Any]:
    """Would cheaper HELD-side inventory have appeared, and would it have filled?

    A fill is credited only on an attributable print at or through the price --
    display alone is explicitly not enough. The recomputed cap is then reported
    together with whether the complement becomes fillable under it.
    """
    st0 = steps[i0]
    held, basis0 = st0["held"], st0["basis"]
    if held is None:
        return {"available": False}
    best_px: Optional[D] = None
    best_t = None
    qstate: Dict[str, Any] = {}
    filled = D(0)
    for st in steps[i0:]:
        if st["held"] != held:
            break
        row, lad = st["row"], st["ladder"]
        q, fpx = S.maker_fill(row, held, D(clip), queue_aware=True,
                              fill_model="queue_aware", ladder=lad,
                              qstate=qstate, limit_px=None)
        if q > 0 and fpx is not None and fpx < basis0:
            filled += q
            if best_px is None or fpx < best_px:
                best_px = fpx
                best_t = st["t"]
    if best_px is None:
        return {"available": False}
    q0 = abs(st0["resid"])
    new_basis = (q0 * basis0 + filled * best_px) / (q0 + filled)
    new_cap = E.THETA - new_basis
    from decimal import ROUND_FLOOR
    new_cap = (new_cap / E.PRICE_TICK).to_integral_value(
        rounding=ROUND_FLOOR) * E.PRICE_TICK
    return {"available": True, "cheapest_px": str(best_px), "at": best_t,
            "qty_fillable": str(filled), "basis_before": str(basis0),
            "basis_after": str(new_basis),
            "cap_before": str(st0["cap"]), "cap_after": str(new_cap),
            "cap_improvement": str(new_cap - D(str(st0["cap"])))}


# ------------------------------------------------------------------ mechanism D

def open_cost(res) -> Dict[str, Any]:
    """What the OPEN cost, and how much loss that embedded before any wait.

    Two cases matter and both are reported. Where a pair formed from flat, its
    cost is the uncapped two-sided quote -- the known ~0.99 issue. Where no pair
    ever formed, the first acquisition's price IS the whole story: a first leg
    taken at 0.95 leaves a complement cap of 0.01, which no market was ever
    going to fill, so that window was lost at its first fill and no amount of
    patience, extension or speed could have changed it.
    """
    fills = res.get("fills") or []
    opens = res.get("opens") or []
    out: Dict[str, Any] = {"pair_formed": bool(opens)}
    if fills:
        f = fills[0]
        out["first_acquisition"] = {
            "t": f["t"], "side": f["side"], "px": str(f["px"]),
            "qty": str(f["qty"]),
            "implied_cap_for_complement": str(
                (E.THETA - D(str(f["px"]))).quantize(D("0.01")))}
        out["first_acquisition_above_theta"] = bool(D(str(f["px"])) > E.THETA)
    if opens:
        o = opens[0]
        pair = D(str(o["px"])) + D(str(o["other_basis"]))
        out.update({"t": o["t"], "side": o["side"], "px": str(o["px"]),
                    "other_basis": str(o["other_basis"]),
                    "first_pair_cost": str(pair),
                    "above_theta": bool(pair > E.THETA),
                    "above_dollar": bool(pair > 1),
                    "embedded_loss_per_pair": str(pair - D(1))})
    return out


# ------------------------------------------------------------------ attribution

def attribute(slug, winner, rows, *, clip: D) -> Dict[str, Any]:
    res = trace(rows, winner=winner, clip=clip)
    steps = res["steps"]
    net = P.net_of(res)
    rq = D(str(res["resid_qty"]))
    out: Dict[str, Any] = {
        "window": slug[-5:], "winner": winner, "net": str(net),
        "matched_qty": str(res["matched_qty"]), "resid_qty": str(rq),
        "resid_pnl": str(res["resid_pnl"]),
        "matched_pnl": str(res["matched_pnl"]),
        "open": open_cost(res)}
    if rq == 0:
        out["stranded"] = False
        out["mechanism"] = "no_residual"
        out["recoverable"] = "0"
        return out
    out["stranded"] = True
    i0 = next((i for i, st in enumerate(steps) if st["resid"] != 0), None)
    if i0 is None:
        out["mechanism"] = "unknown"
        out["recoverable"] = "0"
        return out
    st0 = steps[i0]
    out["complement_required_at"] = st0["t"]
    out["held"] = st0["held"]
    out["basis_at_requirement"] = str(st0["basis"])
    out["cap_at_requirement"] = str(st0["cap"])
    a = classify_complement(steps, i0, clip=clip)
    b = cheaper_same_side(steps, i0, clip=clip)
    out["A_persistent_maker"] = a
    out["B_continuous_inventory"] = b

    # what the residual actually cost, and what completing it would have cost
    basis_end = (res.get("resid_basis") or st0["basis"])
    lost = D(str(res["resid_pnl"]))
    fill_at = a.get("best_own_terms_seen")
    recoverable = D(0)
    mech = "unrecoverable_price_never_returned"
    fillable = any(D(v) > 0 for v in a["fillable_qty"].values())
    if fillable:
        mech = "A_persistent_maker"
        px = D(str(min(D(str(a["best_own_terms_seen"])),
                       D(str(st0["cap"])))))
        recoverable = -lost - rq * (D(str(st0["basis"])) + px - D(1))
    elif b.get("available") and D(b["cap_after"]) > D(b["cap_before"]) \
            and a.get("best_own_terms_seen") is not None \
            and D(a["best_own_terms_seen"]) <= D(b["cap_after"]):
        mech = "B_continuous_inventory"
        px = D(a["best_own_terms_seen"])
        recoverable = -lost - rq * (D(b["basis_after"]) + px - D(1))
    elif a["opportunities"]:
        brief = [o for o in a["opportunities"]
                 if o.get("duration_s") is not None and o["duration_s"] <= 0]
        mech = "C_speed" if brief else "A_persistent_maker"
        recoverable = D(0)
    elif out["open"].get("first_acquisition_above_theta") or (
            D(str(st0["cap"])) <= D("0.05")):
        mech = "D_open_economics"
        recoverable = D(0)
    out["mechanism"] = mech
    out["loss_on_residual"] = str(lost)
    out["recoverable"] = str(recoverable if recoverable > 0 else D(0))
    out["best_complement_price_seen"] = fill_at
    return out


if __name__ == "__main__":
    clip = D(str(int(sys.argv[1]))) if len(sys.argv) > 1 else D(10)
    by, coh = cohort()
    rows_out = []
    for slug, winner in coh.items():
        rows_out.append(attribute(slug, winner, by[slug], clip=clip))
        print(".", end="", flush=True)
    print()
    doc = {"LABEL": "V1_RESIDUAL_DIAGNOSIS",
           "cohort": "oos49_CONSUMED_diagnostic_only",
           "clip": str(clip), "theta": str(E.THETA),
           "labels": ("Diagnosis of a consumed cohort. Never validation "
                      "evidence. No threshold introduced, nothing fitted."),
           "windows": rows_out}
    json.dump(doc, open(OUT, "w"), indent=1, default=str)
    print("WROTE " + OUT)
