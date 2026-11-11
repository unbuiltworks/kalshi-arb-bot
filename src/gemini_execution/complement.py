"""COMPLEMENT REACHABILITY: can Gemini support the SECOND half of the cycle?

The prospective replay reproduced BROAD_LEAKAGE on this venue: six
hypothetical first fills, COMPLETE requested on every path that had cycles
left, ZERO complement fills, and the only follow-on fills were EXTEND. Before
anything is done about first-fill capture, the question is whether the second
half of the L5 inventory cycle is reachable here at all.

THE MECHANISM THAT MAKES IT MEASURABLE. The public tape is priced in YES and
records the AGGRESSOR, so which side of the flow can reach which of our rungs
is not a matter of opinion:

    holding YES   EXTEND (buy more YES)  <- reached by an aggressor SELLING YES
                  COMPLETE (buy NO)      <- reached by an aggressor BUYING  YES
    holding NO    EXTEND (buy more NO)   <- reached by an aggressor BUYING  YES
                  COMPLETE (buy YES)     <- reached by an aggressor SELLING YES

So EXTEND and COMPLETE are reached by OPPOSITE aggressor directions, always.
Our first fill happens BECAUSE an aggressor traded in some direction. If flow
is persistent in that direction, the same side that filled us keeps filling
our EXTEND and never touches our COMPLETE -- which is exactly the observed
pattern, and this module measures whether that is what is happening.

MODEL A IS UNTOUCHED. Every action replayed comes from the frozen
`continuous_probe.plan`. The Model B arms change ONLY the price a COMPLETE is
quoted at, or how long a quote is left alone; neither is wired into the
policy and neither is proposed for the live path.

SETTLEMENT IS REPORTED AND NEVER USED AS JUSTIFICATION. Which way a contract
resolved is a coin flip; it cannot make a behaviour good or bad. It is
recorded because the residual's terminal value is part of the accounting, and
for no other reason.

EVERY FILL HERE IS SHADOW/THEORETICAL.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from math import comb
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the arms compared. The last one is Model B and touches COMPLETE only.
NATIVE = "NATIVE"
IMPROVE_1 = "IMPROVE_1"
REST_ARMS = ("IMPROVE_1_REST_15", "IMPROVE_1_REST_30",
             "IMPROVE_1_REST_45", "IMPROVE_1_REST_60")
COMPETITIVE_COMPLETE = "NATIVE_OPEN_EXTEND_COMPETITIVE_COMPLETE"
ARMS = (NATIVE, IMPROVE_1) + REST_ARMS + (COMPETITIVE_COMPLETE,)

REST_SECONDS = {"IMPROVE_1_REST_15": 15, "IMPROVE_1_REST_30": 30,
                "IMPROVE_1_REST_45": 45, "IMPROVE_1_REST_60": 60}

TICK = D("0.01")

#: the residual is reported at these TTE marks
RESIDUAL_MARKS_S = (60.0, 30.0, 0.0)

#: an arm has to beat NATIVE by more than one seed's worth of luck before
#: it is allowed to claim it changed the failure mode. 1-of-18 against
#: 0-of-18 is p=0.50 on the exact test -- a coin flip, not a finding.
MATERIAL_ALPHA = 0.05

#: matched inventory bought ABOVE par is a matched LOSS. An arm that only
#: forms pairs by overpaying has not fixed leakage, it has paid for it.
PAR = D("1")

THEORETICAL = ("SHADOW/THEORETICAL. No order existed, nothing was submitted, "
               "filled, settled or realized, and our own size would have "
               "changed the book this is measured against.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def complement_leg(held_leg: str) -> str:
    """The leg that reduces residual, given what we hold."""
    return "NO" if str(held_leg).upper() == "YES" else "YES"


def reaching_aggressor(leg: str) -> str:
    """Which aggressor direction on the YES-priced tape reaches this leg.

    A rung on YES is reached by an aggressor SELLING YES. A rung on NO is
    reached by an aggressor BUYING YES, because selling NO to us IS buying
    YES. Verified against CONTINUOUS-5, whose passive NO buy at 0.46 appears
    on the tape as an aggressor YES buy at 0.54.
    """
    return "sell" if str(leg).upper() == "YES" else "buy"


def reaches(leg: str, price: Any, print_: Mapping[str, Any]) -> bool:
    p, tp = _dec(price), _dec(print_.get("price"))
    if p is None or tp is None:
        return False
    if str(print_.get("type")) != reaching_aggressor(leg):
        return False
    return (tp <= p) if str(leg).upper() == "YES" else (tp >= (D("1") - p))


def flow_direction_after(prints: Sequence[Mapping[str, Any]], *,
                         from_ms: Any, to_ms: Any,
                         held_leg: str) -> Dict[str, Any]:
    """Was the flow after our fill one-sided, and on which side?

    THE QUESTION THAT WOULD EXPLAIN BROAD_LEAKAGE. `extend_side` is the
    aggressor direction that reaches our EXTEND; `complement_side` is the one
    that reaches our COMPLETE. They are always opposite.
    """
    lo, hi = _dec(from_ms), _dec(to_ms)
    ext_side = reaching_aggressor(held_leg)
    comp_side = reaching_aggressor(complement_leg(held_leg))
    ext_n = comp_n = 0
    ext_v = comp_v = D("0")
    for t in prints or []:
        ms = _dec(t.get("utc_ms"))
        px, amt = _dec(t.get("price")), _dec(t.get("amount"))
        if ms is None or px is None or amt is None:
            continue
        if lo is not None and ms <= lo:
            continue
        if hi is not None and ms > hi:
            continue
        if str(t.get("type")) == ext_side:
            ext_n += 1
            ext_v += amt * px
        elif str(t.get("type")) == comp_side:
            comp_n += 1
            comp_v += amt * px
    total_n, total_v = ext_n + comp_n, ext_v + comp_v
    return {
        "held_leg": str(held_leg).upper(),
        "extend_is_reached_by": ext_side,
        "complement_is_reached_by": comp_side,
        "prints_toward_extend": ext_n, "prints_toward_complement": comp_n,
        "notional_toward_extend": str(ext_v),
        "notional_toward_complement": str(comp_v),
        "extend_share_of_prints": (round(ext_n / total_n, 3)
                                   if total_n else None),
        "extend_share_of_notional": (str(round(ext_v / total_v, 4))
                                     if total_v else None),
        "one_sided_toward_extend": (bool(comp_n == 0 and ext_n > 0)
                                    if total_n else None),
        "no_flow_at_all": total_n == 0,
        "why": ("EXTEND and COMPLETE are reached by OPPOSITE aggressor "
                "directions, always. If the flow after our fill keeps coming "
                "from the side that filled us, EXTEND fills and COMPLETE "
                "cannot."),
    }


def complement_flow_coverage(prints: Sequence[Mapping[str, Any]], *,
                             from_ms: Any, last_cycle_ms: Any, to_ms: Any,
                             held_leg: str) -> Dict[str, Any]:
    """WHERE the complement-direction flow arrives relative to our coverage.

    A complement quote can only be reached by a print that lands while we are
    still quoting. Flow that arrives after our last recorded cycle is not
    absent flow and it is not one-sided flow -- it is flow we were not there
    for. This separates the two, because they have opposite remedies: absent
    complement flow says the venue cannot support the second leg at all;
    uncovered complement flow says our window stops before the counterparties
    arrive.
    """
    lo, last, hi = _dec(from_ms), _dec(last_cycle_ms), _dec(to_ms)
    comp_side = reaching_aggressor(complement_leg(held_leg))
    inside_n = after_n = 0
    inside_v = after_v = D("0")
    for t in prints or []:
        ms = _dec(t.get("utc_ms"))
        px, amt = _dec(t.get("price")), _dec(t.get("amount"))
        if ms is None or px is None or amt is None:
            continue
        if str(t.get("type")) != comp_side:
            continue
        if lo is not None and ms <= lo:
            continue
        if hi is not None and ms > hi:
            continue
        if last is not None and ms > last:
            after_n += 1
            after_v += amt * px
        else:
            inside_n += 1
            inside_v += amt * px
    tot_v = inside_v + after_v
    return {
        "complement_is_reached_by": comp_side,
        "last_cycle_utc_ms": (str(last) if last is not None else None),
        "prints_inside_our_cycles": inside_n,
        "prints_after_our_last_cycle": after_n,
        "notional_inside_our_cycles": str(inside_v),
        "notional_after_our_last_cycle": str(after_v),
        "share_arriving_after_we_stopped": (
            str(round(after_v / tot_v, 4)) if tot_v else None),
        "why": ("flow we were not there for is not absent flow and not "
                "one-sided flow. The remedies are opposite."),
    }


def maker_only_improvement(book: Mapping[str, Any], leg: str,
                           price: Any) -> Dict[str, Any]:
    """One tick better on this leg, refused unless it stays maker-only."""
    side = "yes" if str(leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    ask, p = _dec(q.get("ask")), _dec(price)
    if p is None:
        return {"exists": False, "why": "no price to improve"}
    if ask is None:
        return {"exists": False,
                "why": "no ask on this leg, so maker-only cannot be proven"}
    imp = p + TICK
    if imp >= ask:
        return {"exists": False, "maker_only": False,
                "why": ("%s + 1 tick (%s) would reach the ask (%s); crossing "
                        "is not authorized" % (p, imp, ask))}
    return {"exists": True, "maker_only": True, "price": str(imp)}


def _size_ahead(depth: Any, leg: str, price: Any) -> Optional[D]:
    """What a print must clear before it reaches us, for EITHER leg.

    ONE IMPLEMENTATION, DELIBERATELY. This used to be its own copy that
    returned None for every NO leg, and the caller then filled anyway with
    `ahead or 0` -- so a NO rung was reported as having an unknown queue and
    simultaneously traded as if it had none. Delegating to `tape_replay` means
    there is exactly one place where queue position can be wrong.
    """
    from src.gemini_execution import tape_replay as TR
    return TR.size_ahead(depth, leg, price)


def trace(*, symbol: str, arm: str, seed_leg: str, seed_qty: Any,
          seed_price: Any, seed_ms: Any,
          cycles: Sequence[Mapping[str, Any]],
          prints: Sequence[Mapping[str, Any]],
          resolution: Mapping[str, Any],
          expiry_ms: Any) -> Dict[str, Any]:
    """One first fill, reconstructed forward to settlement under one arm.

    The frozen policy decides every action. The arm only changes the PRICE a
    COMPLETE is quoted at, and how long a quote is held before being
    requoted. OPEN and EXTEND are native in every arm.
    """
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import large_prints as LP

    inv = CP.Inventory()
    q, p0 = _dec(seed_qty), _dec(seed_price)
    out: Dict[str, Any] = {
        "symbol": symbol, "arm": arm,
        "seed": {"leg": str(seed_leg).upper(),
                 "qty": str(q) if q is not None else None,
                 "price": str(p0) if p0 is not None else None,
                 "utc_ms": seed_ms},
        "labels": THEORETICAL,
    }
    if q is None or p0 is None or q <= 0:
        out.update({"computable": False, "why": "seed fill unreadable"})
        return out
    inv.apply_fill(str(seed_leg).upper(), q, p0,
                   source="SHADOW_COMPLEMENT_SEED_THEORETICAL")

    t0 = _dec(seed_ms)
    later = sorted(
        [c for c in cycles if _dec(c.get("utc_ms")) is not None
         and _dec(c["utc_ms"]) > (t0 or D("0"))],
        key=lambda c: _dec(c["utc_ms"]))

    rest_s = REST_SECONDS.get(arm)
    steps: List[Dict[str, Any]] = []
    complements: List[Dict[str, Any]] = []
    extends: List[Dict[str, Any]] = []
    residual_at: Dict[str, Any] = {}
    # a held quote under a REST arm: leg -> (price, placed_ms)
    held: Dict[str, Any] = {}
    first_reachable_ms = None
    first_fill_ms = None

    for i, c in enumerate(later):
        book = c.get("book") or {}
        tte = c.get("tte_s")
        st = LP.state_from_recorded_book(symbol, tte, inv, book)
        got = CP.plan(st, inv, actions_taken=len(steps))
        now_ms = _dec(c["utc_ms"])
        nxt = (_dec(later[i + 1]["utc_ms"]) if i + 1 < len(later)
               else now_ms + D("8000"))

        acted: List[Dict[str, Any]] = []
        for a in (got.get("actions") or []):
            leg = str(a.get("leg")).upper()
            kind = a.get("action")
            price = a.get("price")

            # ---- the arm chooses the COMPLETE price only
            improved = None
            if kind == CP.COMPLETE and arm in (
                    IMPROVE_1, COMPETITIVE_COMPLETE) + REST_ARMS:
                improved = maker_only_improvement(book, leg, price)
                if improved.get("exists"):
                    price = improved["price"]
            # ---- a REST arm keeps the previous price rather than requoting
            reused = False
            if rest_s is not None and leg in held:
                hp, hms = held[leg]
                if now_ms - hms < D(str(rest_s)) * D("1000"):
                    price, reused = hp, True
            if rest_s is not None and not reused:
                held[leg] = (price, now_ms)

            ahead = _size_ahead(c.get("depth"), leg, price)
            hits = [t for t in prints
                    if _dec(t.get("utc_ms")) is not None
                    and now_ms < _dec(t["utc_ms"]) <= nxt
                    and reaches(leg, price, t)]
            reached = sum((_dec(t["amount"]) or D("0")) for t in hits)
            # AN UNREADABLE QUEUE IS NOT AN EMPTY ONE. When the ladder
            # cannot be read the fill is UNCOMPUTABLE and is reported as
            # such; it is never resolved to a fill, which is what
            # `ahead or 0` silently did on every NO rung.
            filled = D("0")
            computable = ahead is not None
            if computable and hits and reached > ahead:
                spare = reached - ahead
                filled = max(D("0"), min(_dec(a.get("qty")) or D("0.01"),
                                         spare))
            row = {
                "kind": kind, "leg": leg, "price": price,
                "native_price": a.get("price"),
                "improved": (improved or {}).get("price"),
                "improvement_refused": ((improved or {}).get("why")
                                        if improved and not improved.get(
                                            "exists") else None),
                "reused_held_quote": reused,
                "size_ahead": (str(ahead) if ahead is not None else None),
                "queue_readable": ahead is not None,
                "reaching_prints": len(hits),
                "reaching_contracts": str(reached),
                # A NON-COMPUTABLE FILL IS RECORDED AS ZERO AND FLAGGED, not
                # as None: every consumer already reads this as a number, and
                # a nullable field would only move the `or 0` problem into
                # them. The flag carries the distinction and the summary
                # counts it, so an unreadable queue stays visible.
                "fill_computable": computable,
                "theoretical_fill": str(filled),
                "utc_ms": str(now_ms), "tte_s": tte,
            }
            if kind == CP.COMPLETE:
                opp = (book.get("no" if leg == "NO" else "yes") or {})
                row.update({"opposite_touch_bid": opp.get("bid"),
                            "opposite_touch_ask": opp.get("ask")})
                if hits and first_reachable_ms is None:
                    first_reachable_ms = now_ms
                complements.append(row)
            elif kind == CP.EXTEND:
                extends.append(row)
            if filled > 0:
                inv.apply_fill(leg, filled, price,
                               source="SHADOW_COMPLEMENT_REPLAY_THEORETICAL")
                if kind == CP.COMPLETE and first_fill_ms is None:
                    first_fill_ms = now_ms
            acted.append(row)

        for mark in RESIDUAL_MARKS_S:
            key = "tte_%d" % int(mark)
            if key not in residual_at and tte is not None and tte <= mark:
                residual_at[key] = {
                    "residual": str(inv.residual()),
                    "matched": str(inv.matched()),
                    "spend_usd": str(inv.spend())}

        steps.append({
            "utc": c.get("utc"), "tte_s": tte,
            "window_phase": got.get("window_phase"),
            "why_no_surface": got.get("why_no_surface"),
            "actions": acted,
            "holds": [{"leg": h.get("leg"), "held_by": h.get("held_by")}
                      for h in (got.get("holds") or [])],
        })

    for mark in RESIDUAL_MARKS_S:
        residual_at.setdefault("tte_%d" % int(mark), {
            "residual": str(inv.residual()), "matched": str(inv.matched()),
            "spend_usd": str(inv.spend()),
            "note": "no cycle reached this mark; the final state is reported"})

    settle = CP.settlement_report(inv, dict(resolution or {}))
    comp_fills = [r for r in complements
                  if r.get("fill_computable", True)
                  and (_dec(r["theoretical_fill"]) or D("0")) > 0]
    ext_fills = [r for r in extends
                 if r.get("fill_computable", True)
                 and (_dec(r["theoretical_fill"]) or D("0")) > 0]
    not_computable = sum(1 for r in list(complements) + list(extends)
                         if not r.get("fill_computable", True))

    out.update({
        "computable": True,
        "cycles_replayed": len(steps),
        "flow_after_seed": flow_direction_after(
            prints, from_ms=t0, to_ms=expiry_ms, held_leg=seed_leg),
        "complement_flow_coverage": complement_flow_coverage(
            prints, from_ms=t0,
            last_cycle_ms=(_dec(later[-1]["utc_ms"]) if later else t0),
            to_ms=expiry_ms, held_leg=seed_leg),
        "complement_quotes": len(complements),
        "complement_quotes_with_a_reaching_print": sum(
            1 for r in complements if r["reaching_prints"]),
        "quotes_with_an_unreadable_queue": not_computable,
        "complement_theoretical_fills": len(comp_fills),
        "extend_quotes": len(extends),
        "extend_theoretical_fills": len(ext_fills),
        "seconds_to_first_reachable_complement": (
            float((first_reachable_ms - t0) / D("1000"))
            if first_reachable_ms is not None and t0 is not None else None),
        "seconds_to_complement_fill": (
            float((first_fill_ms - t0) / D("1000"))
            if first_fill_ms is not None and t0 is not None else None),
        "complement_prices": [r["price"] for r in complements],
        "complements": complements,
        "extends": extends,
        "residual_at": residual_at,
        "final": {
            "matched": str(inv.matched()),
            "residual": str(inv.residual()),
            "effective_pair_cost": (
                str(inv.effective_matched_pair_cost())
                if inv.effective_matched_pair_cost() is not None else None),
            "capital_at_risk_usd": str(inv.spend()),
            "n_fills": len(inv.fills),
        },
        "settlement": settle,
        "settlement_caveat": ("which way the contract resolved is a coin "
                              "flip and is NOT used to justify any "
                              "behaviour here"),
        "steps": steps,
    })
    return out


def arm_summary(traces: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """THE PRIMARY METRIC, per arm.

    Not fills. Initial fills that BECOME MATCHED INVENTORY, and the residual
    and capital that came with them.
    """
    ok = [t for t in traces if t.get("computable")]
    matched = [t for t in ok if _dec(t["final"]["matched"]) > 0]
    bases = [_dec(t["final"]["effective_pair_cost"]) for t in matched]
    bases = [b for b in bases if b is not None]
    resid = [abs(_dec(t["final"]["residual"]) or D("0")) for t in ok]
    cap = [_dec(t["final"]["capital_at_risk_usd"]) or D("0") for t in ok]
    comp_q = sum(int(t.get("complement_quotes") or 0) for t in ok)
    comp_r = sum(int(t.get("complement_quotes_with_a_reaching_print") or 0)
                 for t in ok)
    comp_f = sum(int(t.get("complement_theoretical_fills") or 0) for t in ok)
    ext_f = sum(int(t.get("extend_theoretical_fills") or 0) for t in ok)
    t2r = [t["seconds_to_first_reachable_complement"] for t in ok
           if t.get("seconds_to_first_reachable_complement") is not None]
    t2f = [t["seconds_to_complement_fill"] for t in ok
           if t.get("seconds_to_complement_fill") is not None]
    return {
        "initial_fills": len(ok),
        "became_matched_inventory": len(matched),
        "matched_share": (round(len(matched) / len(ok), 3) if ok else None),
        "median_pair_basis": (str(sorted(bases)[len(bases) // 2])
                              if bases else None),
        "total_residual_units": str(sum(resid, D("0"))),
        "median_residual_units": (str(sorted(resid)[len(resid) // 2])
                                  if resid else None),
        "total_capital_at_risk_usd": str(sum(cap, D("0"))),
        "complement_quotes": comp_q,
        "complement_quotes_with_a_reaching_print": comp_r,
        "complement_reach_rate": (round(comp_r / comp_q, 4) if comp_q else None),
        "complement_theoretical_fills": comp_f,
        "extend_theoretical_fills": ext_f,
        "extend_to_complement_fill_ratio": (
            str(round(D(ext_f) / D(comp_f), 2)) if comp_f else
            ("INFINITE (%d extends, 0 complements)" % ext_f if ext_f
             else None)),
        "median_seconds_to_first_reachable_complement": (
            sorted(t2r)[len(t2r) // 2] if t2r else None),
        "median_seconds_to_complement_fill": (
            sorted(t2f)[len(t2f) // 2] if t2f else None),
        "labels": THEORETICAL,
    }


def pooled_flow_coverage(traces: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The coverage split pooled over every seed. Arm-independent.

    Which cycles we recorded does not depend on the arm -- only the quote
    price does -- so this is computed once and answers the question the
    one-sidedness hypothesis does not: of the complement-direction flow that
    existed after our fill, how much of it arrived while we were still there?
    """
    ok = [t for t in traces if t.get("computable")]
    in_n = af_n = 0
    in_v = af_v = D("0")
    per_seed = []
    for t in ok:
        c = t.get("complement_flow_coverage") or {}
        i_v = _dec(c.get("notional_inside_our_cycles")) or D("0")
        a_v = _dec(c.get("notional_after_our_last_cycle")) or D("0")
        in_n += int(c.get("prints_inside_our_cycles") or 0)
        af_n += int(c.get("prints_after_our_last_cycle") or 0)
        in_v += i_v
        af_v += a_v
        per_seed.append({
            "symbol": t.get("symbol"),
            "seed_kind": t.get("seed_kind"),
            "held_leg": (t.get("seed") or {}).get("leg"),
            "notional_inside_our_cycles": str(i_v),
            "notional_after_our_last_cycle": str(a_v),
            "share_arriving_after_we_stopped": c.get(
                "share_arriving_after_we_stopped"),
        })
    tot = in_v + af_v
    return {
        "seeds": len(ok),
        "complement_prints_inside_our_cycles": in_n,
        "complement_prints_after_our_last_cycle": af_n,
        "complement_notional_inside_our_cycles": str(in_v),
        "complement_notional_after_our_last_cycle": str(af_v),
        "share_of_complement_notional_arriving_after_we_stopped": (
            str(round(af_v / tot, 4)) if tot else None),
        "per_seed": per_seed,
        "why": ("if most of the complement-direction notional lands after our "
                "last cycle, the complement failure is a COVERAGE failure. "
                "That is a different fault from absent or one-sided flow and "
                "it has a different remedy."),
        "labels": THEORETICAL,
    }


def _fisher_one_sided_greater(a: int, b: int, c: int, d: int) -> float:
    """P(arm matches >= a | the arm and NATIVE convert at the same rate).

    The exact hypergeometric tail on the 2x2 [[a, b], [c, d]] -- arm matched /
    unmatched over NATIVE matched / unmatched. No approximation, no library,
    and it does not care that the counts are tiny, which is the whole point:
    with 18 seeds a single matched pair is not separable from zero.
    """
    n1, m, n = a + b, a + c, a + b + c + d
    if n1 <= 0 or n <= 0 or m <= 0:
        return 1.0
    denom = comb(n, n1)
    if denom == 0:
        return 1.0
    hi = min(m, n1)
    return float(sum(comb(m, k) * comb(n - m, n1 - k)
                     for k in range(a, hi + 1)) / denom)


def leakage_verdict(by_arm: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Does any arm MATERIALLY change BROAD_LEAKAGE?

    Three gates, all of which must hold, because the owner's metric is
    "initial fills that subsequently become matched inventory WITHOUT
    unacceptable residual accumulation" -- not matched inventory at any price:

      1. SEPARABLE. The arm's matched count has to beat NATIVE by more than
         one seed's worth of luck (exact test, alpha = 0.05). An arm that
         converts one extra seed out of eighteen has found one seed.
      2. AT OR BELOW PAR. A matched pair whose basis is above $1.00 is a
         matched loss; forming those faster makes the leak worse, not better.
      3. NO WORSE RESIDUAL. Buying matched inventory by accumulating more
         unmatched exposure is the failure mode, not a fix for it.

    An arm that raises the complement REACH rate without clearing all three
    has measured the failure mode more precisely. It has not changed it.
    """
    native = by_arm.get(NATIVE) or {}
    nat_m = int(native.get("became_matched_inventory") or 0)
    nat_n = int(native.get("initial_fills") or 0)
    nat_resid = _dec(native.get("total_residual_units"))

    rows = []
    for arm, s in by_arm.items():
        m = int(s.get("became_matched_inventory") or 0)
        n = int(s.get("initial_fills") or 0)
        basis = _dec(s.get("median_pair_basis"))
        resid = _dec(s.get("total_residual_units"))
        p = _fisher_one_sided_greater(m, n - m, nat_m, nat_n - nat_m)
        separable = bool(arm != NATIVE and p <= MATERIAL_ALPHA)
        at_par = bool(basis is not None and basis <= PAR)
        resid_ok = bool(resid is not None and nat_resid is not None
                        and resid <= nat_resid)
        rows.append({
            "arm": arm,
            "initial_fills": n,
            "became_matched": m,
            "matched_share": s.get("matched_share"),
            "median_pair_basis": s.get("median_pair_basis"),
            "complement_reach_rate": s.get("complement_reach_rate"),
            "complement_fills": s.get("complement_theoretical_fills"),
            "extend_fills": s.get("extend_theoretical_fills"),
            "residual_units": s.get("total_residual_units"),
            "capital_usd": s.get("total_capital_at_risk_usd"),
            "matched_gain_over_native": m - nat_m,
            "p_vs_native_exact": round(p, 4),
            "gate_separable_from_native": separable,
            "gate_pair_basis_at_or_below_par": at_par,
            "gate_residual_not_worse_than_native": resid_ok,
            "materially_changes_leakage": bool(
                separable and at_par and resid_ok),
        })

    material = [r["arm"] for r in rows if r["materially_changes_leakage"]]
    any_matched = any((r["became_matched"] or 0) > 0 for r in rows)
    return {
        "arms": rows,
        "arms_that_materially_change_leakage": material,
        "any_arm_forms_matched_inventory": any_matched,
        "any_arm_materially_changes_leakage": bool(material),
        "verdict": ("SOME_ARM_MATERIALLY_CHANGES_LEAKAGE" if material
                    else "BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM"),
        "matched_inventory_formed_but_not_materially": bool(
            any_matched and not material),
        "alpha": MATERIAL_ALPHA,
        "why": ("materially changing BROAD_LEAKAGE means matched inventory "
                "forms often enough to be separable from NATIVE, at or below "
                "par, and without a worse residual. An arm that raises the "
                "complement REACH rate, or converts one extra seed out of "
                "eighteen, has measured the failure mode more precisely -- "
                "not changed it."),
        "labels": THEORETICAL,
    }
