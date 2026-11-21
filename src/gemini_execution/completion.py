"""GEMINI_COMPLETION_MODEL_B -- optimise inventory RECYCLING, not first fills.

The transfer problem has been redefined. Fill count is no longer the metric.
The metric is whether an initial fill becomes MATCHED INVENTORY without
unacceptable residual, and every number here is denominated in DOLLARS as
well as units, because 0.01 contracts of leakage and 0.01 contracts of
matched inventory are not the same object at different prices.

    initial fill -> residual -> Model-A desired COMPLETE -> external
    opposite-side flow -> theoretical COMPLETE fill -> matched inventory ->
    remaining residual

MODEL A IS FROZEN AND UNTOUCHED. Every action replayed comes from
`continuous_probe.plan`. An arm may change only:

  * the PRICE a COMPLETE is quoted at (never through the ask -- never a cross);
  * how long a COMPLETE quote is left alone before Model A's new price
    replaces it;
  * whether an already-resting complement from BEFORE the fill is preserved
    rather than requoted;
  * whether an EXTEND that Model A requested is acted on (the VETO -- Model A
    still requests it, and what it wanted is recorded either way);
  * whether a COMPLETE-only maker quote persists through the late phase.

None of this is wired into the live path and none of it is proposed for one.

THE TAPE'S SIDE CONVENTION, which makes all of this measurable: Gemini's
public tape is priced in YES and records the AGGRESSOR. A YES rung is reached
by an aggressor SELLING; a NO rung at p is reached by an aggressor BUYING at
>= 1-p. So EXTEND and COMPLETE are reached by OPPOSITE aggressor directions,
always -- which is why a persistent one-directional move fills EXTEND and
never COMPLETE.

EVERY FILL HERE IS SHADOW/THEORETICAL. No order existed, nothing was
submitted, filled, settled or realized, and our own size would have changed
the book this is measured against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TICK = D("0.01")

# ------------------------------------------------------- the COMPLETE matrix
NATIVE = "NATIVE"
IMPROVE_1 = "IMPROVE_1"
IMPROVE_2 = "IMPROVE_2"
BEST_MAKER = "BEST_PERMISSIBLE_MAKER"
PRICE_POLICIES = (NATIVE, IMPROVE_1, IMPROVE_2, BEST_MAKER)

#: 0 is "reprice immediately", i.e. take Model A's new price every cycle
STICKY_S = (0, 15, 30, 45, 60)

#: the residual is reported at these TTE marks
RESIDUAL_MARKS_S = (60.0, 30.0, 0.0)

#: the late COMPLETE-only phase this study SHADOWS. It is not a new bound:
#: the frozen policy is ALREADY COMPLETE_ONLY below TTE 60 and WITHDRAWN
#: below TTE 30. What is missing is observation, not permission.
LATE_PHASE_HI_S = 60.0
LATE_PHASE_LO_S = 30.0

THEORETICAL = ("SHADOW/THEORETICAL. No order existed, nothing was submitted, "
               "filled, settled or realized, and our own size would have "
               "changed the book this is measured against.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Arm:
    """One observational variant. Model A is frozen; this is all that moves."""
    price_policy: str = NATIVE
    sticky_s: int = 0
    #: preserve a complement that was ALREADY resting before the first fill
    preserve_prior_complement_s: Optional[int] = None
    #: do not act on an EXTEND the frozen policy requested (it is still logged)
    extend_veto: bool = False
    #: hold a COMPLETE-only maker quote through TTE 60 -> 30
    late_complete_phase: bool = False

    @property
    def name(self) -> str:
        n = "%s+%s" % (self.price_policy,
                       "REPRICE" if not self.sticky_s
                       else "STICKY_%d" % self.sticky_s)
        if self.preserve_prior_complement_s is not None:
            n += "+PRESERVE_%d" % self.preserve_prior_complement_s
        if self.extend_veto:
            n += "+EXTEND_VETO"
        if self.late_complete_phase:
            n += "+LATE_60_30"
        return n


def matrix_arms() -> Tuple[Arm, ...]:
    """The 4 x 5 COMPLETE execution matrix. OPEN and EXTEND stay native."""
    return tuple(Arm(price_policy=p, sticky_s=s)
                 for p in PRICE_POLICIES for s in STICKY_S)


# ------------------------------------------------------- the side convention

def complement_leg(held_leg: str) -> str:
    return "NO" if str(held_leg).upper() == "YES" else "YES"


def reaching_aggressor(leg: str) -> str:
    """Which aggressor direction on the YES-priced tape reaches this leg."""
    return "sell" if str(leg).upper() == "YES" else "buy"


def reaches(leg: str, price: Any, print_: Mapping[str, Any]) -> bool:
    p, tp = _dec(price), _dec(print_.get("price"))
    if p is None or tp is None:
        return False
    if str(print_.get("type")) != reaching_aggressor(leg):
        return False
    return (tp <= p) if str(leg).upper() == "YES" else (tp >= (D("1") - p))


# ------------------------------------------------------------ the price rule

def maker_price(book: Mapping[str, Any], leg: str, native: Any,
                policy: str) -> Dict[str, Any]:
    """The arm's COMPLETE price. NEVER through the ask -- never a cross.

    Refused rather than guessed when the ask is unreadable, because a price
    whose maker-only status cannot be PROVEN is not a maker price.
    """
    side = "yes" if str(leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    ask, p = _dec(q.get("ask")), _dec(native)
    if p is None:
        return {"price": None, "applied": False, "why": "no native price"}
    if policy == NATIVE:
        return {"price": str(p), "applied": True, "policy": NATIVE}
    if ask is None:
        return {"price": str(p), "applied": False,
                "why": ("no ask on this leg, so maker-only cannot be proven; "
                        "the native price stands")}
    want = {IMPROVE_1: p + TICK, IMPROVE_2: p + 2 * TICK,
            BEST_MAKER: ask - TICK}.get(policy)
    if want is None:
        return {"price": str(p), "applied": False,
                "why": "unknown price policy %r" % (policy,)}
    if want <= p:
        return {"price": str(p), "applied": False,
                "why": ("%s would be %s, no better than the native %s"
                        % (policy, want, p))}
    if want >= ask:
        return {"price": str(p), "applied": False,
                "why": ("%s would be %s and the ask is %s; crossing is not "
                        "authorized" % (policy, want, ask))}
    return {"price": str(want), "applied": True, "policy": policy,
            "ask": str(ask), "improved_by": str(want - p)}


def _size_ahead(depth: Any, leg: str, price: Any) -> Optional[D]:
    """What a print must clear before it reaches us, for EITHER leg.

    THE DOCSTRING AND THE CODE USED TO CONTRADICT EACH OTHER. This claimed a
    NO rung's queue "is reported as unknown rather than as zero" while
    `_fill_from` computed `reached - (ahead or D("0"))` -- reporting unknown
    and then trading it as zero. Both are corrected: the queue comes from
    `tape_replay`, the single implementation, and an unknown one no longer
    resolves to a fill.
    """
    from src.gemini_execution import tape_replay as TR
    return TR.size_ahead(depth, leg, price)


def _fill_from(hits: Sequence[Mapping[str, Any]], ahead: Optional[D],
               want: Optional[D]) -> D:
    """Theoretical fill: what is left of the reaching flow after the queue.

    AN UNKNOWN QUEUE FILLS NOTHING. `ahead or D("0")` turned every
    unreadable queue into an empty one, which on the old YES-only
    `_size_ahead` meant every NO rung filled from the front. A queue that
    cannot be read cannot be cleared, so there is no computable fill.
    """
    if not hits or ahead is None:
        return D("0")
    reached = sum((_dec(t.get("amount")) or D("0")) for t in hits)
    spare = reached - ahead
    if spare <= 0:
        return D("0")
    return max(D("0"), min(want if want is not None else D("0.01"), spare))


# -------------------------------------------------- dollar-denominated state

def dollars(inv: Any) -> Dict[str, Any]:
    """The accounting the new metric needs, in DOLLARS, nothing netted.

    GROSS is everything spent on fills. MATCHED dollars are what the matched
    units cost, at the two legs' own average prices. RESIDUAL dollars are what
    the unmatched units cost. Matched and residual are different objects and
    are never summed into one number.
    """
    m = inv.matched()
    ay, an = inv.avg_yes(), inv.avg_no()
    matched_usd = (m * (ay + an)) if (m > 0 and ay is not None
                                      and an is not None) else D("0")
    r = inv.residual()
    if r > 0 and ay is not None:
        resid_usd = r * ay
    elif r < 0 and an is not None:
        resid_usd = (-r) * an
    else:
        resid_usd = D("0")
    gross = inv.spend()
    return {
        "gross_filled_usd": str(gross),
        "matched_usd": str(matched_usd),
        "residual_usd": str(resid_usd),
        "matched_units": str(m),
        "residual_units": str(r),
        "matched_dollar_share": (str(round(matched_usd / gross, 4))
                                 if gross > 0 else None),
        "residual_over_gross": (str(round(resid_usd / gross, 4))
                                if gross > 0 else None),
        "effective_matched_pair_cost": (
            str(inv.effective_matched_pair_cost())
            if inv.effective_matched_pair_cost() is not None else None),
        "labels": ("matched-pair economics and directional-residual exposure "
                   "are separate objects and are NEVER summed. None of this "
                   "is realized: a cost basis is not a profit."),
    }


# ----------------------------------------------- what was resting BEFORE us

def prior_resting_complement(symbol: str, cycles: Sequence[Mapping[str, Any]],
                             seed_ms: Any, held_leg: str) -> Dict[str, Any]:
    """The complement quote that was ALREADY resting when the fill landed.

    THIS IS THE PRIORITY CASE. On flat inventory the frozen policy quotes BOTH
    legs, so at the instant one leg fills there is already a live quote on the
    other -- with real queue age behind it. Model A's reaction to the fill is
    to want a COMPLETE at its own price, and taking that price means cancelling
    the resting quote and going to the back of a new queue. This finds what was
    there so the two can be compared.
    """
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import large_prints as LP

    t0 = _dec(seed_ms)
    before = [c for c in cycles
              if _dec(c.get("utc_ms")) is not None
              and (t0 is None or _dec(c["utc_ms"]) <= t0)]
    if not before:
        return {"existed": False, "why": "no cycle recorded before the fill"}
    c = max(before, key=lambda x: _dec(x["utc_ms"]))
    want = complement_leg(held_leg)
    flat = CP.Inventory()
    st = LP.state_from_recorded_book(symbol, c.get("tte_s"), flat,
                                     c.get("book") or {})
    got = CP.plan(st, flat, actions_taken=0)
    for a in (got.get("actions") or []):
        if str(a.get("leg")).upper() == want:
            return {"existed": True, "leg": want, "price": a.get("price"),
                    "qty": a.get("qty"), "action": a.get("action"),
                    "utc_ms": str(_dec(c["utc_ms"])), "tte_s": c.get("tte_s"),
                    "size_ahead": (str(_size_ahead(c.get("depth"), want,
                                                   a.get("price")))
                                   if _size_ahead(c.get("depth"), want,
                                                  a.get("price")) is not None
                                   else None),
                    "why": ("the frozen policy quotes BOTH legs on flat "
                            "inventory, so this was live when the fill landed")}
    return {"existed": False, "leg": want,
            "why": ("the frozen policy had no %s quote on the cycle before "
                    "the fill" % want)}


# ------------------------------------------------------- hazard features

def _tape_slice(prints: Sequence[Mapping[str, Any]], *, lo: Any,
                hi: Any) -> List[Mapping[str, Any]]:
    a, b = _dec(lo), _dec(hi)
    out = []
    for t in prints or []:
        ms = _dec(t.get("utc_ms"))
        if ms is None:
            continue
        if a is not None and ms <= a:
            continue
        if b is not None and ms > b:
            continue
        out.append(t)
    return out


def hazard_row(*, symbol: str, now_ms: Any, tte_s: Any, inv: Any,
               desired_leg: str, desired_price: Any, book: Mapping[str, Any],
               depth: Any, prints: Sequence[Mapping[str, Any]],
               rest_s: Any) -> Dict[str, Any]:
    """One residual state, with every feature the completion hazard needs.

    DISTRIBUTIONS FIRST. No threshold is applied, no model is fitted and no
    admission decision is taken from any of this. The eventual outcome columns
    are filled in by the replay once it knows them.
    """
    now = _dec(now_ms)
    side = "yes" if str(desired_leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    yq = (book or {}).get("yes") or {}
    bid, ask = _dec(q.get("bid")), _dec(q.get("ask"))
    ybid, yask = _dec(yq.get("bid")), _dec(yq.get("ask"))
    p = _dec(desired_price)
    comp_side = reaching_aggressor(desired_leg)

    counts: Dict[str, Any] = {}
    for w in (15, 30, 60, 120):
        sl = _tape_slice(prints, lo=(now - D(str(w)) * D("1000")
                                     if now is not None else None), hi=now)
        opp = [t for t in sl if str(t.get("type")) == comp_side]
        same = [t for t in sl if str(t.get("type")) != comp_side]
        counts["opposite_prints_%ds" % w] = len(opp)
        counts["opposite_contracts_%ds" % w] = str(
            sum((_dec(t.get("amount")) or D("0")) for t in opp))
        n = len(opp) + len(same)
        counts["aggressor_imbalance_%ds" % w] = (
            str(round(D(len(opp) - len(same)) / D(n), 3)) if n else None)

    prior = [t for t in prints if _dec(t.get("utc_ms")) is not None
             and (now is None or _dec(t["utc_ms"]) <= now)]
    last_opp = None
    for t in reversed(prior):
        if str(t.get("type")) == comp_side:
            last_opp = _dec(t["utc_ms"])
            break
    vel = None
    win = _tape_slice(prints, lo=(now - D("60000") if now is not None else None),
                      hi=now)
    if len(win) >= 2:
        a, b = _dec(win[0].get("price")), _dec(win[-1].get("price"))
        ta, tb = _dec(win[0].get("utc_ms")), _dec(win[-1].get("utc_ms"))
        if None not in (a, b, ta, tb) and tb > ta:
            vel = str(round((b - a) / ((tb - ta) / D("1000")) * D("60"), 5))

    return {
        "symbol": symbol, "utc_ms": (str(now) if now is not None else None),
        "tte_s": tte_s,
        "held_leg": ("YES" if inv.residual() > 0 else
                     "NO" if inv.residual() < 0 else None),
        "held_qty": str(abs(inv.residual())),
        "held_avg_price": (str(inv.avg_yes()) if inv.residual() > 0
                           and inv.avg_yes() is not None else
                           str(inv.avg_no()) if inv.residual() < 0
                           and inv.avg_no() is not None else None),
        "desired_complete_leg": str(desired_leg).upper(),
        "desired_complete_price": (str(p) if p is not None else None),
        "complete_distance_from_touch": (str(bid - p) if bid is not None
                                         and p is not None else None),
        "at_touch": (bool(bid is not None and p is not None and p == bid)),
        "queue_ahead": (str(_size_ahead(depth, desired_leg, desired_price))
                        if _size_ahead(depth, desired_leg,
                                       desired_price) is not None else None),
        "queue_readable": _size_ahead(depth, desired_leg,
                                      desired_price) is not None,
        "spread": (str(yask - ybid) if ybid is not None and yask is not None
                   else None),
        "price_velocity_per_min": vel,
        "seconds_since_opposite_flow": (
            float((now - last_opp) / D("1000"))
            if last_opp is not None and now is not None else None),
        "rest_duration_s": rest_s,
        # filled in by the replay once the future is known
        "eventual_complement_reachable": None,
        "eventual_complement_fill": None,
        "seconds_until_reach": None,
        "seconds_until_fill": None,
        **counts,
        "labels": ("OBSERVATIONAL. No threshold is applied and no decision is "
                   "taken from any of this."),
    }


@dataclass
class _Quote:
    """A live rung, with the queue it joined and how long it has been there."""
    leg: str
    price: D
    placed_ms: D
    ahead_at_place: Optional[D]
    preserved: bool = False
    consumed: D = field(default_factory=lambda: D("0"))

    def ahead_now(self) -> Optional[D]:
        """Queue ahead, aged.

        A MODEL, and labelled as one: a quote that has rested since before the
        flow arrived has had that flow eating the queue in front of it, while a
        requote joins the back of its level. Neither is observable from a
        snapshot ladder, so this is stated as a model rather than a
        measurement.
        """
        if self.ahead_at_place is None:
            return None
        return max(D("0"), self.ahead_at_place - self.consumed)


def trace(*, symbol: str, arm: Arm, seed_leg: str, seed_qty: Any,
          seed_price: Any, seed_ms: Any,
          cycles: Sequence[Mapping[str, Any]],
          prints: Sequence[Mapping[str, Any]],
          resolution: Mapping[str, Any],
          expiry_ms: Any) -> Dict[str, Any]:
    """One initial fill, recycled forward to settlement under one arm."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import large_prints as LP

    inv = CP.Inventory()
    q0, p0 = _dec(seed_qty), _dec(seed_price)
    out: Dict[str, Any] = {
        "symbol": symbol, "arm": arm.name,
        "arm_spec": {"price_policy": arm.price_policy,
                     "sticky_s": arm.sticky_s,
                     "preserve_prior_complement_s":
                         arm.preserve_prior_complement_s,
                     "extend_veto": arm.extend_veto,
                     "late_complete_phase": arm.late_complete_phase},
        "seed": {"leg": str(seed_leg).upper(),
                 "qty": str(q0) if q0 is not None else None,
                 "price": str(p0) if p0 is not None else None,
                 "utc_ms": seed_ms},
        "seed_tte_s": (float((_dec(expiry_ms) - _dec(seed_ms)) / D("1000"))
                       if _dec(expiry_ms) is not None
                       and _dec(seed_ms) is not None else None),
        "labels": THEORETICAL,
    }
    if q0 is None or p0 is None or q0 <= 0:
        out.update({"computable": False, "why": "seed fill unreadable"})
        return out
    inv.apply_fill(str(seed_leg).upper(), q0, p0,
                   source="SHADOW_COMPLETION_SEED_THEORETICAL")
    gross_seed = q0 * p0

    t0 = _dec(seed_ms)
    later = sorted([c for c in cycles if _dec(c.get("utc_ms")) is not None
                    and _dec(c["utc_ms"]) > (t0 or D("0"))],
                   key=lambda c: _dec(c["utc_ms"]))

    prior = prior_resting_complement(symbol, cycles, seed_ms, seed_leg)

    live: Dict[str, _Quote] = {}
    if (arm.preserve_prior_complement_s is not None and prior.get("existed")
            and _dec(prior.get("price")) is not None):
        live[prior["leg"]] = _Quote(
            leg=prior["leg"], price=_dec(prior["price"]),
            placed_ms=_dec(prior["utc_ms"]) or (t0 or D("0")),
            ahead_at_place=_dec(prior.get("size_ahead")), preserved=True)

    steps: List[Dict[str, Any]] = []
    complements: List[Dict[str, Any]] = []
    extends: List[Dict[str, Any]] = []
    vetoed: List[Dict[str, Any]] = []
    hazards: List[Dict[str, Any]] = []
    residual_at: Dict[str, Any] = {}
    first_reach_ms = first_fill_ms = None
    extend_usd_before_match = D("0")
    matched_yet = False

    for i, c in enumerate(later):
        book = c.get("book") or {}
        tte = c.get("tte_s")
        now = _dec(c["utc_ms"])
        nxt = (_dec(later[i + 1]["utc_ms"]) if i + 1 < len(later)
               else now + D("8000"))
        st = LP.state_from_recorded_book(symbol, tte, inv, book)
        got = CP.plan(st, inv, actions_taken=len(steps))

        acted: List[Dict[str, Any]] = []
        for a in (got.get("actions") or []):
            leg = str(a.get("leg")).upper()
            kind = a.get("action")
            want_qty = _dec(a.get("qty"))
            native = a.get("price")

            # --------------------------------- the arm picks a COMPLETE price
            chosen, priced, reason = native, None, None
            if kind == CP.COMPLETE:
                priced = maker_price(book, leg, native, arm.price_policy)
                chosen = priced.get("price") or native
                reason = priced.get("why")

            # --------------------------------- persistence: reuse or requote
            held = live.get(leg)
            reused = False
            if held is not None and kind == CP.COMPLETE:
                age_ms = now - held.placed_ms
                keep_ms = D(str(arm.preserve_prior_complement_s
                                if held.preserved
                                and arm.preserve_prior_complement_s is not None
                                else arm.sticky_s)) * D("1000")
                if age_ms < keep_ms:
                    chosen, reused = str(held.price), True
            if kind == CP.COMPLETE and not reused:
                live[leg] = _Quote(leg=leg, price=_dec(chosen) or D("0"),
                                   placed_ms=now,
                                   ahead_at_place=_size_ahead(c.get("depth"),
                                                              leg, chosen))
            quote = live.get(leg) if kind == CP.COMPLETE else None
            ahead = (quote.ahead_now() if quote is not None
                     else _size_ahead(c.get("depth"), leg, chosen))

            hits = [t for t in prints
                    if _dec(t.get("utc_ms")) is not None
                    and now < _dec(t["utc_ms"]) <= nxt
                    and reaches(leg, chosen, t)]
            reached = sum((_dec(t.get("amount")) or D("0")) for t in hits)
            filled = _fill_from(hits, ahead, want_qty)
            if quote is not None:
                quote.consumed += reached

            veto = bool(arm.extend_veto and kind == CP.EXTEND)
            row = {
                "kind": kind, "leg": leg, "native_price": native,
                "price": chosen, "price_policy_applied": (
                    bool(priced.get("applied")) if priced else None),
                "price_policy_refused": reason,
                "reused_resting_quote": reused,
                "quote_preserved_from_before_the_fill": bool(
                    quote is not None and quote.preserved and reused),
                "quote_rest_s": (float((now - quote.placed_ms) / D("1000"))
                                 if quote is not None else None),
                "size_ahead": (str(ahead) if ahead is not None else None),
                "queue_readable": ahead is not None,
                "reaching_prints": len(hits),
                "reaching_contracts": str(reached),
                "theoretical_fill": str(filled if not veto else D("0")),
                "model_a_wanted": (str(filled) if veto else None),
                "vetoed": veto,
                "utc_ms": str(now), "tte_s": tte,
            }
            if kind == CP.COMPLETE:
                opp = (book.get("no" if leg == "NO" else "yes") or {})
                row.update({"opposite_touch_bid": opp.get("bid"),
                            "opposite_touch_ask": opp.get("ask")})
                if hits and first_reach_ms is None:
                    first_reach_ms = now
                complements.append(row)
                if inv.residual() != 0:
                    hazards.append(hazard_row(
                        symbol=symbol, now_ms=now, tte_s=tte, inv=inv,
                        desired_leg=leg, desired_price=chosen, book=book,
                        depth=c.get("depth"), prints=prints,
                        rest_s=row["quote_rest_s"]))
            elif kind == CP.EXTEND:
                (vetoed if veto else extends).append(row)

            if filled > 0 and not veto:
                inv.apply_fill(leg, filled, chosen,
                               source="SHADOW_COMPLETION_REPLAY_THEORETICAL")
                if kind == CP.EXTEND and not matched_yet:
                    extend_usd_before_match += filled * (_dec(chosen) or D("0"))
                if kind == CP.COMPLETE:
                    if first_fill_ms is None:
                        first_fill_ms = now
                    if inv.matched() > 0:
                        matched_yet = True
                live.pop(leg, None)
            acted.append(row)

        for mark in RESIDUAL_MARKS_S:
            k = "tte_%d" % int(mark)
            if k not in residual_at and tte is not None and tte <= mark:
                residual_at[k] = {"residual_units": str(inv.residual()),
                                  **dollars(inv), "observed": True}

        steps.append({"utc": c.get("utc"), "tte_s": tte,
                      "window_phase": got.get("window_phase"),
                      "actions": acted})

    late = _late_phase(symbol, arm, inv, later, prints, expiry_ms)
    if late.get("ran"):
        for f in late.get("fills") or []:
            inv.apply_fill(f["leg"], _dec(f["qty"]), f["price"],
                           source="SHADOW_COMPLETION_LATE_PHASE_THEORETICAL")
            if first_fill_ms is None:
                first_fill_ms = _dec(f["utc_ms"])

    for mark in RESIDUAL_MARKS_S:
        residual_at.setdefault("tte_%d" % int(mark), {
            "residual_units": str(inv.residual()), **dollars(inv),
            "observed": False,
            "note": "no cycle reached this mark; the final state is reported"})

    reach_s = (float((first_reach_ms - t0) / D("1000"))
               if first_reach_ms is not None and t0 is not None else None)
    fill_s = (float((first_fill_ms - t0) / D("1000"))
              if first_fill_ms is not None and t0 is not None else None)
    for h in hazards:
        h["eventual_complement_reachable"] = first_reach_ms is not None
        h["eventual_complement_fill"] = first_fill_ms is not None
        h["seconds_until_reach"] = reach_s
        h["seconds_until_fill"] = fill_s

    out.update({
        "computable": True,
        "cycles_replayed": len(steps),
        "prior_resting_complement": prior,
        "complement_quotes": len(complements),
        "complement_quotes_with_a_reaching_print": sum(
            1 for r in complements if r["reaching_prints"]),
        "complement_theoretical_fills": sum(
            1 for r in complements if _dec(r["theoretical_fill"]) > 0),
        "extend_theoretical_fills": sum(
            1 for r in extends if _dec(r["theoretical_fill"]) > 0),
        "extend_fills_vetoed": sum(
            1 for r in vetoed if _dec(r["model_a_wanted"] or "0") > 0),
        "extend_usd_before_matching": str(extend_usd_before_match),
        "seconds_to_first_reachable_complement": reach_s,
        "seconds_to_complement_fill": fill_s,
        "seed_gross_usd": str(gross_seed),
        "final": {**dollars(inv), "n_fills": len(inv.fills)},
        "residual_at": residual_at,
        "late_phase": late,
        "complements": complements, "extends": extends, "vetoed": vetoed,
        "hazards": hazards,
        "settlement": CP.settlement_report(inv, dict(resolution or {})),
        "settlement_caveat": ("which way the contract resolved is a coin flip "
                              "and is NOT used to justify any behaviour here"),
        "steps": steps,
    })
    return out


def _late_phase(symbol: str, arm: Arm, inv: Any,
                cycles: Sequence[Mapping[str, Any]],
                prints: Sequence[Mapping[str, Any]],
                expiry_ms: Any) -> Dict[str, Any]:
    """SHADOW a COMPLETE-only persistent maker quote across TTE 60 -> 30.

    NOT A NEW BOUND. The frozen policy is ALREADY COMPLETE_ONLY below TTE 60
    and WITHDRAWN below TTE 30; what this phase lacks is OBSERVATION, because
    our recorded cycles usually stop before TTE 60. So the quote is priced
    from the LAST OBSERVED BOOK and the staleness is reported, rather than
    pretending we saw a book we did not.

    No OPEN. No EXTEND. No crossing. Residual only.
    """
    exp = _dec(expiry_ms)
    if not arm.late_complete_phase:
        return {"ran": False, "why": "arm does not shadow the late phase"}
    if exp is None:
        return {"ran": False, "why": "no expiry; the TTE band cannot be placed"}
    if inv.residual() == 0:
        return {"ran": False, "why": "no residual to recycle"}
    obs = [c for c in cycles if (c.get("book") or {}).get("readable")]
    if not obs:
        return {"ran": False, "why": "no observed book to price from"}
    # PRICE FROM A BOOK AT OR BEFORE THE BAND OPENS where one exists. Some
    # windows recorded cycles INSIDE the band; using one of those would price
    # the quote on information a quote placed at TTE 60 could not have had,
    # and would then report a NEGATIVE staleness, which is not a thing.
    hi = _dec(expiry_ms) - D("60000")
    before = [c for c in obs if _dec(c.get("utc_ms")) is not None
              and _dec(c["utc_ms"]) <= hi]
    inside_band = not before
    last = (before[-1] if before else obs[-1])
    leg = complement_leg("YES" if inv.residual() > 0 else "NO")
    side = "yes" if leg == "YES" else "no"
    bid = _dec(((last.get("book") or {}).get(side) or {}).get("bid"))
    if bid is None:
        return {"ran": False, "why": "the last observed book has no %s bid" % leg}
    px = maker_price(last.get("book") or {}, leg, bid, arm.price_policy)
    price = px.get("price") or str(bid)

    hi_ms, lo_ms = exp - D("60000"), exp - D("30000")
    band = [t for t in prints if _dec(t.get("utc_ms")) is not None
            and hi_ms < _dec(t["utc_ms"]) <= lo_ms]
    hits = [t for t in band if reaches(leg, price, t)]
    reached = sum((_dec(t.get("amount")) or D("0")) for t in hits)
    band_notional = sum((_dec(t.get("amount")) or D("0"))
                        * (_dec(t.get("price")) or D("0")) for t in band)
    want = abs(inv.residual())
    filled = _fill_from(hits, None, want)     # queue UNKNOWN this late
    stale_s = (max(0.0, float((hi_ms - _dec(last["utc_ms"])) / D("1000")))
               if _dec(last.get("utc_ms")) is not None else None)
    return {
        "ran": True, "leg": leg, "price": price,
        "priced_from_tte_s": last.get("tte_s"),
        "price_staleness_s_at_tte_60": stale_s,
        "priced_from_inside_the_band": inside_band,
        "pricing_caveat": (
            "no book was recorded at or before TTE 60, so this is priced from "
            "a cycle INSIDE the band -- information a quote placed at TTE 60 "
            "could not have had, and an optimistic assumption"
            if inside_band else
            "priced from the last book observed at or before TTE 60"),
        "residual_to_recycle": str(want),
        "prints_in_band": len(band),
        "band_notional_usd": str(band_notional),
        "prints_reaching_us": len(hits),
        "contracts_reaching_us": str(reached),
        "theoretical_fill": str(filled),
        "recycled_share_of_residual": (str(round(filled / want, 4))
                                       if want > 0 else None),
        "fills": ([{"leg": leg, "qty": str(filled), "price": price,
                    "utc_ms": str(_dec(hits[0]["utc_ms"]))}]
                  if filled > 0 and hits else []),
        "queue_caveat": ("no ladder is recorded this late, so the queue ahead "
                         "is UNKNOWN and is not assumed to be zero -- this "
                         "fill is an UPPER BOUND, not an estimate"),
        "labels": THEORETICAL,
    }


# ------------------------------------------------------------ the ranking

def arm_summary(traces: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """THE DECISION CRITERION: matched inventory / gross filled inventory.

    Fill count is not success and is not ranked on. Residual risk and pair
    economics are reported BESIDE the ratio, never folded into it, because a
    variant that raises fills while raising leakage is a failure.
    """
    ok = [t for t in traces if t.get("computable")]
    if not ok:
        return {"initial_fills": 0, "labels": THEORETICAL}
    gross = sum((_dec(t["final"]["gross_filled_usd"]) or D("0")) for t in ok)
    matched = sum((_dec(t["final"]["matched_usd"]) or D("0")) for t in ok)
    resid = sum((_dec(t["final"]["residual_usd"]) or D("0")) for t in ok)
    ext = sum((_dec(t.get("extend_usd_before_matching")) or D("0"))
              for t in ok)
    with_pair = [t for t in ok if (_dec(t["final"]["matched_units"]) or D("0")) > 0]
    # MATCHED/GROSS IS GAMEABLE. An arm can raise it by PAYING MORE for the
    # same pair: a basis of 1.25 moves more dollars into "matched" than a
    # basis of 0.84 does, and the pair is worse. So the same ratio is also
    # computed counting ONLY pairs formed at or below par, and the two are
    # reported together. Neither is allowed to stand alone.
    at_par = [t for t in with_pair
              if (_dec(t["final"]["effective_matched_pair_cost"]) or D("9"))
              <= D("1")]
    matched_at_par = sum((_dec(t["final"]["matched_usd"]) or D("0"))
                         for t in at_par)
    bases = sorted(b for b in
                   [_dec(t["final"]["effective_matched_pair_cost"])
                    for t in with_pair] if b is not None)
    t2r = sorted(t["seconds_to_first_reachable_complement"] for t in ok
                 if t.get("seconds_to_first_reachable_complement") is not None)
    t2f = sorted(t["seconds_to_complement_fill"] for t in ok
                 if t.get("seconds_to_complement_fill") is not None)
    # TTE at the first fill and at the complement, so the cross-venue
    # contrast can be set against L5's own numbers without translation
    seed_tte = sorted(x for x in (t.get("seed_tte_s") for t in ok)
                      if x is not None)
    comp_tte = sorted(
        x for x in (r.get("tte_s") for t in ok
                    for r in (t.get("complements") or [])
                    if (_dec(r.get("theoretical_fill")) or D("0")) > 0)
        if x is not None)
    cq = sum(int(t.get("complement_quotes") or 0) for t in ok)
    cr = sum(int(t.get("complement_quotes_with_a_reaching_print") or 0)
             for t in ok)
    return {
        "arm": ok[0].get("arm"),
        "initial_fills": len(ok),
        # --- THE RANKING METRIC
        "matched_over_gross_usd": (str(round(matched / gross, 4))
                                   if gross > 0 else None),
        # --- reported beside it, never folded in
        "matched_over_gross_usd_at_or_below_par": (
            str(round(matched_at_par / gross, 4)) if gross > 0 else None),
        "matched_pairs_over_initial_fills": round(len(with_pair) / len(ok), 4),
        "matched_pairs_at_or_below_par": len(at_par),
        "gross_filled_usd": str(gross),
        "matched_usd": str(matched),
        "residual_usd": str(resid),
        "residual_over_gross_usd": (str(round(resid / gross, 4))
                                    if gross > 0 else None),
        "extend_usd_before_matching": str(ext),
        "median_effective_pair_cost": (str(bases[len(bases) // 2])
                                       if bases else None),
        "pairs_above_par": sum(1 for b in bases if b > D("1")),
        "median_seconds_to_first_complement": (t2r[len(t2r) // 2]
                                               if t2r else None),
        "median_seconds_to_complement_fill": (t2f[len(t2f) // 2]
                                              if t2f else None),
        "median_first_fill_tte_s": (seed_tte[len(seed_tte) // 2]
                                    if seed_tte else None),
        "median_complement_tte_s": (comp_tte[len(comp_tte) // 2]
                                    if comp_tte else None),
        "complement_quotes": cq,
        "complement_reach_rate": (round(cr / cq, 4) if cq else None),
        "complement_fills": sum(int(t.get("complement_theoretical_fills") or 0)
                                for t in ok),
        "extend_fills": sum(int(t.get("extend_theoretical_fills") or 0)
                            for t in ok),
        "extend_fills_vetoed": sum(int(t.get("extend_fills_vetoed") or 0)
                                   for t in ok),
        "late_phase_fills": sum(
            1 for t in ok if (t.get("late_phase") or {}).get("fills")),
        "labels": THEORETICAL,
    }


def rank(summaries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Ordered by matched/gross AT OR BELOW PAR. A tie goes to lower residual.

    The raw matched/gross is reported beside it but is NOT ranked on, because
    an arm raises it simply by overpaying for the same pair -- on this data
    every increase in the raw ratio came from a worse basis, not from more
    pairs. Matched inventory bought above $1.00 is a matched loss and ranking
    it first would reward exactly the behaviour this study exists to catch.
    """
    def key(s):
        m = _dec(s.get("matched_over_gross_usd_at_or_below_par"))
        r = _dec(s.get("residual_over_gross_usd"))
        return (-(m if m is not None else D("-1")),
                r if r is not None else D("999"))
    return sorted([dict(s) for s in summaries], key=key)


def pareto(veto_summaries: Mapping[str, Mapping[str, Any]],
           all_summaries: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """MATCHED INVENTORY RETAINED vs RESIDUAL EXPOSURE AVOIDED.

    EACH VETO ARM IS COMPARED WITH ITS OWN NON-VETO TWIN -- the same price
    policy and the same persistence -- so the only difference on the two axes
    is the veto. Comparing every veto arm against one shared baseline would
    credit the veto with whatever the price policy did, which is not what is
    being measured.

    The veto's only defensible claim is that it removes leakage WITHOUT
    removing matched inventory. Both halves are required and neither is
    reported alone.
    """
    pts = []
    for name, s in veto_summaries.items():
        twin_name = name.replace("+EXTEND_VETO", "")
        twin = all_summaries.get(twin_name)
        if twin is None:
            pts.append({"arm": name, "twin": twin_name,
                        "comparable": False,
                        "why": "no non-veto twin to compare against"})
            continue
        m, r = (_dec(s.get("matched_usd")) or D("0"),
                _dec(s.get("residual_usd")) or D("0"))
        bm, br = (_dec(twin.get("matched_usd")) or D("0"),
                  _dec(twin.get("residual_usd")) or D("0"))
        pts.append({
            "arm": name, "twin": twin_name, "comparable": True,
            "matched_usd": str(m), "twin_matched_usd": str(bm),
            "residual_usd": str(r), "twin_residual_usd": str(br),
            "matched_retained_share": (str(round(m / bm, 4))
                                       if bm > 0 else None),
            "matched_usd_given_up": str(bm - m),
            "residual_avoided_usd": str(br - r),
            "residual_avoided_share": (str(round((br - r) / br, 4))
                                       if br > 0 else None),
            "extend_fills_suppressed": s.get("extend_fills_vetoed"),
            "dominates_twin": bool(m >= bm and r <= br),
        })
    return {
        "points": sorted(pts, key=lambda p: _dec(p.get("residual_avoided_usd"))
                         or D("0"), reverse=True),
        "dominating_arms": [p["arm"] for p in pts if p.get("dominates_twin")],
        "why": ("a veto that avoids residual by also destroying matched "
                "inventory has not improved anything. Both axes are required, "
                "each against the arm's OWN non-veto twin, and neither is "
                "reported alone."),
        "labels": THEORETICAL,
    }
