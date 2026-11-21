"""GEMINI_REPLAY_ENGINE_V1 -- sequential counterfactual replay of a window.

WHY A REPLAY AND NOT A TRUNCATION. P1 measured CAP-K by walking the observed
fills and dropping the ones that breached a cap. That is not what a cap does.
A refused EXTEND changes inventory, inventory changes the next decision, and
the next decision changes which orders rest and therefore which fills happen
at all. Anything that edits an outcome without re-deriving the decisions that
followed it is a bookkeeping exercise wearing a simulation's name.

SO THIS DRIVES THE PRODUCTION POLICY ITSELF. `CP.plan` is the same function
`WindowGuard.step` calls in the live driver -- not a reimplementation of it.
`reconcile` below is a replay-layer model of `ws_continuous.reconcile_surface`
and is deliberately separate, because the whole point of P2-A is to vary the
reconciliation while leaving the POLICY frozen.

THE FIDELITY GATE, and nothing here may be believed without it. Fed each
recorded cycle's recorded inventory and recorded book, the policy must emit
the recorded actions -- same action, leg, price and qty. On COHORT-2 it does,
720 of 720 readable cycles, 0 mismatches, and a test asserts it. A replay that
cannot reproduce the run it replays may not be used to price an alternative
to it.

------------------------------------------------------------------------
THE EXECUTION DEFECT THIS EXISTS TO MEASURE, stated precisely.

`ws_continuous.WorkingOrders.reconcile` rebuilds the working set from the
venue's open-order list on every cycle:

    "resting_since": float(o.get("resting_since") or now),

`o` is a VENUE row. `resting_since` is our own bookkeeping key and no venue
row carries it, so the fallback is taken EVERY time and every resting order
is re-stamped as brand new. The observed consequence, from the artifacts:

    order l5cp-owner-2026-no-53fb2948 truly rested 473s (createdAt 12:50:09.881
    -> updatedAt 12:58:02.681). At every one of the ~60 cycles in between, the
    controller computed its age as 0.15-0.56s.

`reconcile_surface` retains any valid order younger than `STALE_ORDER_S`
(30.0) and marks its leg slot occupied, so the moved desired price is HELD.
With the age permanently near zero, the 30s rest floor became an INFINITE
rest floor: no resting order was ever cancelled for price movement, in any
window. That is why 68 of 750 cycles planned a mutation and 58 of 132 action
slots were forfeited while a complement sat 30 ticks stale.

The venue publishes the correct value: `createdAt` on each open order. This
module models the corrected behaviour. IT DOES NOT FIX PRODUCTION --
`ws_continuous.py` is an authorized bundle member and editing it moves
`bundle_sha`, which is an authorization-affecting change and not P2's to make.

------------------------------------------------------------------------
CONFIRMED VS COUNTERFACTUAL FILLS ARE NEVER MIXED. Every fill this module
produces is labelled `counterfactual`, priced by the tape model that
`residual_timeline.calibrate` proved against 56 confirmed fills. The confirmed
record lives in `fill_reconciliation` and nothing here overwrites it.

OFFLINE AND READ-ONLY. It opens no socket. `state()` builds the policy's input
from the recorded book rather than calling `CP.state_from_venue`, which would
fetch `terms_for` per cycle -- a network read, and the reason a first attempt
at this took minutes instead of seconds.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal as D
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import residual_timeline as RT

LABEL = "GEMINI_REPLAY_ENGINE_V1"

TICK = D("0.01")
YES, NO = "YES", "NO"

#: the frozen lane's own floor, read from the policy module rather than
#: restated here, so a change there cannot silently diverge from the replay.
MIN_REST_S = float(CP.STALE_ORDER_S)

KEEP, RETAIN, CANCEL, PLACE, HOLD = "KEEP", "RETAIN", "CANCEL", "PLACE", "HOLD"

UNREALIZED = ("COUNTERFACTUAL throughout. No order in this module was ever "
              "submitted; no fill here is confirmed, settled or REALIZED.")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _f(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, F):
        v = D(v.numerator) / D(v.denominator)
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _ms(v: Any) -> Optional[int]:
    if not v:
        return None
    try:
        return int(_dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# the policy's input, rebuilt offline
# --------------------------------------------------------------------------
def state(symbol: str, tte_s: float, book: Mapping[str, Any],
          t: float) -> Dict[str, Any]:
    """`CP.state_from_venue` without the network read it does for the tick.

    The recorded book already carries `tick`, so the only thing the live
    function adds is an HTTP call per cycle. Same shape, same fields, same
    unreadable branch -- asserted against the real one in the tests.
    """
    if (not book.get("readable") or not book.get("identity_consistent")
            or CP.crossed(book)):
        return {"market_key": symbol, "state_id": "unreadable", "t_utc": t,
                "tte_s": tte_s, "cohort": CP.cohort_label(symbol),
                "inventory_known": False, "outcomes": {},
                "unreadable_why": (
                    "book not readable" if not book.get("readable")
                    else ("complement identity violated"
                          if not book.get("identity_consistent")
                          else "CROSSED book: a bid at or above its own ask")),
                "book": book}
    tick = str(book.get("tick") or "0.01")
    return {
        "market_key": symbol, "state_id": "%s@%d" % (symbol, int(t)),
        "t_utc": t, "state_age_s": 0.0, "tte_s": tte_s,
        "cohort": CP.cohort_label(symbol), "inventory_known": True,
        "outcomes": {
            "Up": {"bid": book["yes"]["bid"], "ask": book["yes"]["ask"],
                   "tick": tick},
            "Down": {"bid": book["no"]["bid"], "ask": book["no"]["ask"],
                     "tick": tick}},
        "book": book,
    }


def inventory_at(row: Mapping[str, Any]) -> CP.Inventory:
    """Rebuild the controller's Inventory from a recorded snapshot."""
    inv = CP.Inventory()
    y, n = _d(row.get("yes_qty")) or D(0), _d(row.get("no_qty")) or D(0)
    if y:
        inv.apply_fill(YES, y, _d(row.get("avg_yes_cost")), source="replay")
    if n:
        inv.apply_fill(NO, n, _d(row.get("avg_no_cost")), source="replay")
    ssr = row.get("same_side_run") or {}
    inv.run_leg = ssr.get("leg")
    inv.run_len = int(ssr.get("len") or 0)
    return inv


# --------------------------------------------------------------------------
# the reconciliation under test
# --------------------------------------------------------------------------
def reconcile(desired: Sequence[Mapping[str, Any]],
              working: Mapping[str, Mapping[str, Any]], *,
              now: float,
              min_rest_s: float = MIN_REST_S,
              reprice_ticks: Optional[int] = None,
              reprice_only_reducing: bool = True,
              slots_per_leg: int = 2,
              safety: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """desired surface + what is resting -> KEEP / RETAIN / CANCEL / PLACE.

    `reprice_ticks` is the ONLY behavioural knob, and it is an EXECUTION
    parameter, not a strategy one:

        None  never replace a resting order for price movement. This is
              production today, and it is what the re-stamped `resting_since`
              enforces regardless of what the floor says.
        k     replace once the order has rested at least `min_rest_s` AND the
              desired price on that leg differs by at least k ticks.

    The rest floor still binds first: an order younger than `min_rest_s` is
    RETAINED whatever k says. Queue position is not surrendered to a one-tick
    twitch, and `safety` (terminal withdrawal) still overrides everything.
    """
    want: Dict[Any, Dict[str, Any]] = {}
    per_leg: Dict[str, int] = {}
    best_price: Dict[str, D] = {}
    best_action: Dict[str, str] = {}
    for a in desired:
        leg = str(a["leg"])
        want[(leg, str(a["price"]), str(a["qty"]))] = dict(a)
        per_leg[leg] = per_leg.get(leg, 0) + 1
        best_price[leg] = _d(a["price"])
        best_action[leg] = str(a.get("action") or "")

    keep: List[Dict[str, Any]] = []
    retain: List[Dict[str, Any]] = []
    cancel: List[Dict[str, Any]] = []
    matched: set = set()
    occupied: Dict[str, int] = {}

    for coid, o in list(working.items()):
        key = (o["leg"], str(o["price"]), str(o["qty"]))
        if key in want and key not in matched:
            matched.add(key)
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            keep.append({"action": KEEP, "coid": coid, "order": o})
            continue
        if safety:
            cancel.append({"action": CANCEL, "coid": coid, "order": o,
                           "why": "terminal withdrawal"})
            continue
        age = float(now) - float(o["resting_since"])
        if age < float(min_rest_s):
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            retain.append({"action": RETAIN, "coid": coid, "order": o,
                           "age_s": age, "why": "below the %.0fs rest floor"
                                                % float(min_rest_s)})
            continue
        if reprice_ticks is None:
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            retain.append({"action": RETAIN, "coid": coid, "order": o,
                           "age_s": age,
                           "why": "production: a moved price never replaces a "
                                  "resting order"})
            continue
        # REDUCING ONLY, and this is the whole scope of P2-A. The defect
        # being priced is a stale COMPLEMENT: a COMPLETE whose price moved
        # while an older complement order sat resting. Repricing the
        # ACCUMULATING leg as well would change EXTEND behaviour, which this
        # arm is explicitly not allowed to do -- and, measured, it spends the
        # ledger on re-quotes that then cannot be spent on inventory.
        if (reprice_only_reducing
                and best_action.get(o["leg"]) != "COMPLETE"):
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            retain.append({"action": RETAIN, "coid": coid, "order": o,
                           "age_s": age,
                           "why": "this leg's desired action is %s, not "
                                  "COMPLETE; only the reducing leg reprices"
                                  % (best_action.get(o["leg"]) or "none")})
            continue
        tgt = best_price.get(o["leg"])
        if tgt is None:
            cancel.append({"action": CANCEL, "coid": coid, "order": o,
                           "age_s": age,
                           "why": "the policy no longer wants this leg"})
            continue
        moved = abs(tgt - _d(o["price"])) / TICK
        if moved < reprice_ticks:
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            retain.append({"action": RETAIN, "coid": coid, "order": o,
                           "age_s": age, "moved_ticks": int(moved),
                           "why": "moved %d ticks, under the %d-tick "
                                  "threshold" % (int(moved), reprice_ticks)})
            continue
        cancel.append({"action": CANCEL, "coid": coid, "order": o,
                       "age_s": age, "moved_ticks": int(moved),
                       "why": "rested %.0fs and the desired price moved %d "
                              "ticks" % (age, int(moved))})

    place: List[Dict[str, Any]] = []
    hold: List[Dict[str, Any]] = []
    caps = {leg: min(slots_per_leg, n) for leg, n in per_leg.items()}
    for k, v in want.items():
        if k in matched:
            continue
        leg = str(v["leg"])
        if occupied.get(leg, 0) >= caps.get(leg, 0):
            hold.append({"action": HOLD, "rung": v,
                         "why": "leg slot held by a retained order"})
            continue
        occupied[leg] = occupied.get(leg, 0) + 1
        place.append({"action": PLACE, "rung": v})
    return {"keep": keep, "retain": retain, "cancel": cancel,
            "place": place, "hold": hold,
            "mutations": len(cancel) + len(place)}


# --------------------------------------------------------------------------
# the arms
# --------------------------------------------------------------------------
def improve(desired: Sequence[Mapping[str, Any]], book: Mapping[str, Any], *,
            ticks: int = 0, only_action: Optional[str] = None
            ) -> List[Dict[str, Any]]:
    """Quote `ticks` closer to the touch, still strictly MAKER.

    P2-E.5 / P3. The frozen surface rests behind the touch; this moves a rung
    toward it without ever crossing -- the price stays strictly below that
    leg's own ask, so the order rests. A rung that cannot be improved without
    crossing is left exactly where the policy put it, never dropped.
    """
    if not ticks:
        return [dict(a) for a in desired]
    out: List[Dict[str, Any]] = []
    for a in desired:
        b = dict(a)
        leg = str(a["leg"])
        side = (book.get("yes") if leg == YES else book.get("no")) or {}
        ask = _d(side.get("ask"))
        px = _d(a.get("price"))
        if (px is not None and (only_action is None
                                or str(a.get("action")) == only_action)):
            want = px + TICK * ticks
            if ask is None or want < ask:
                b["price"] = str(want)
        out.append(b)
    return out


def ladder(desired: Sequence[Mapping[str, Any]], book: Mapping[str, Any], *,
           rungs: int = 1, spacing: int = 1) -> List[Dict[str, Any]]:
    """Widen the desired surface to `rungs` levels per leg, deeper only.

    P3-C. An EXECUTION-SURFACE experiment, not a new directional policy: the
    extra rungs carry the policy's own leg and action and sit BEHIND its
    price, never in front of it, so nothing here quotes more aggressively
    than the frozen surface already does. The caps on committed dollars and
    residual are enforced by the envelope and by `gate_desired`, unchanged --
    concurrency cannot become exposure by the back door.
    """
    if rungs <= 1:
        return [dict(a) for a in desired]
    out: List[Dict[str, Any]] = []
    for a in desired:
        px = _d(a.get("price"))
        out.append(dict(a))
        if px is None:
            continue
        for k in range(1, rungs):
            deeper = px - TICK * spacing * k
            if deeper <= 0:
                break
            b = dict(a)
            b["price"] = str(deeper)
            b["rung_index"] = k
            out.append(b)
    return out


def gate_desired(desired: Sequence[Mapping[str, Any]], inv: CP.Inventory, *,
                 extend_floor: Optional[D] = None,
                 cap: Optional[int] = None) -> List[Dict[str, Any]]:
    """Behavioural overlays. COMPLETE and OPEN are NEVER suppressed.

    `extend_floor` refuses an EXTEND priced below the floor -- the L5-derived
    residual-unit-cost rule, expressed at the only moment it is actionable.
    `cap` refuses an EXTEND that would push |yes-no| past k.

    A reducing action is never gated: refusing to complete is the behaviour
    P1 found already costs money.
    """
    out: List[Dict[str, Any]] = []
    y, n = D(inv.yes_qty), D(inv.no_qty)
    for a in desired:
        act = str(a.get("action") or "")
        if act != "EXTEND":
            out.append(dict(a))
            continue
        px = _d(a.get("price"))
        if extend_floor is not None and px is not None and px < extend_floor:
            continue
        if cap is not None:
            q = _d(a.get("qty")) or D(1)
            ny, nn = ((y + q, n) if str(a["leg"]) == YES else (y, n + q))
            if abs(ny - nn) > cap:
                continue
        out.append(dict(a))
    return out


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def replay(rep: Mapping[str, Any], *,
           reprice_ticks: Optional[int] = None,
           reprice_only_reducing: bool = True,
           extend_floor: Optional[D] = None,
           cap: Optional[int] = None,
           improve_ticks: int = 0,
           improve_only_action: Optional[str] = None,
           rungs_per_side: int = 1,
           rung_spacing: int = 1,
           max_committed_usd: Optional[D] = D("4.00"),
           tier: str = RT.THROUGH,
           action_budget: int = 12,
           min_rest_s: float = MIN_REST_S,
           quote_band: Any = (60.0, 880.0),
           action_size: Any = D("1"),
           max_residual: Optional[Any] = None) -> Dict[str, Any]:
    """Replay one window sequentially under one arm.

    Defaults are PRODUCTION: never reprice, no gate, no cap. That arm exists
    to be compared against the confirmed record, not to be believed on its own.
    """
    symbol = str(rep["window"]["symbol"])
    sp = CP.probe_params(action_size=_d(action_size),
                         quote_band=(None if quote_band is None
                                     else (float(quote_band[0]),
                                           float(quote_band[1]))))
    if max_residual is not None:
        # A RISK PARAMETER, varied only to ANSWER a question. The oscillator
        # suppresses the widening side by comparing inventory against
        # max_residual, so it is the cap that actually stops participation.
        # Raising it here measures what L5-like participation would COST in
        # exposure; it is not a proposal to raise it.
        import dataclasses
        from src.l5_surface import params as _PR
        pr = dict(sp.params)
        old = pr["max_residual"]
        pr["max_residual"] = _PR.Param(
            "max_residual", _PR.OURS_ADAPTED, value=str(max_residual),
            prior_evidence="P3 participation experiment; NOT an authorization")
        sp = dataclasses.replace(sp, params=pr)
    decisions = rep.get("decisions") or []
    tp = RT.tape(rep)
    prints = list(tp["prints"])
    # each print can absorb only its own size; one taker order cannot fill an
    # unlimited number of our rungs
    left: Dict[int, D] = {p["tid"]: (p["qty"] or D(0)) for p in prints}

    inv = CP.Inventory()
    working: Dict[str, Dict[str, Any]] = {}
    used = 0
    seq = 0
    fills: List[Dict[str, Any]] = []
    mutations: List[Dict[str, Any]] = []
    unreadable = 0
    budget_blocked = 0
    committed_blocked = 0
    withdrawn = False

    for i, dec in enumerate(decisions):
        t_ms = _ms(dec.get("utc"))
        if t_ms is None:
            continue
        t = t_ms / 1000.0
        book = ((dec.get("book_state") or {}).get("presented")) or {}
        tte = float(dec.get("tte_s") or 0.0)
        st = state(symbol, tte, book, t)
        if st.get("inventory_known") is False:
            unreadable += 1
        else:
            turn = CP.plan(st, inv, sp=sp, actions_taken=used,
                           action_size=_d(action_size))
            safety = (turn.get("window_phase") == CP.WINDOW_WITHDRAWN
                      or bool(turn.get("stop")))
            if safety:
                withdrawn = True
            desired = gate_desired(turn.get("actions") or [], inv,
                                   extend_floor=extend_floor, cap=cap)
            desired = improve(desired, book, ticks=improve_ticks,
                              only_action=improve_only_action)
            desired = ladder(desired, book, rungs=rungs_per_side,
                             spacing=rung_spacing)
            plan = reconcile(desired, working, now=t, min_rest_s=min_rest_s,
                             reprice_ticks=reprice_ticks,
                             reprice_only_reducing=reprice_only_reducing,
                             slots_per_leg=max(2, rungs_per_side),
                             safety=safety)
            for c in plan["cancel"]:
                if used >= action_budget:
                    budget_blocked += 1
                    continue
                used += 1
                mutations.append({"kind": CANCEL, "cycle": dec.get("cycle"),
                                  "tte_s": tte, "leg": c["order"]["leg"],
                                  "price": str(c["order"]["price"]),
                                  "why": c.get("why")})
                working.pop(c["coid"], None)
            for p in plan["place"]:
                if used >= action_budget:
                    budget_blocked += 1
                    continue
                # CONCURRENCY IS NOT PERMISSION TO COMMIT MORE. The extra
                # rungs of a wider surface are refused once the working set's
                # notional would breach the envelope's committed cap, so a
                # participation experiment cannot silently become a size one.
                if max_committed_usd is not None:
                    live = sum((_d(o["price"]) or D(0)) * (_d(o["qty"]) or D(0))
                               for o in working.values())
                    want = ((_d(p["rung"]["price"]) or D(0))
                            * (_d(p["rung"]["qty"]) or D(0)))
                    if live + want > max_committed_usd:
                        committed_blocked += 1
                        continue
                used += 1
                seq += 1
                coid = "replay-%s-%d" % (str(p["rung"]["leg"]).lower(), seq)
                working[coid] = {"leg": str(p["rung"]["leg"]),
                                 "price": str(p["rung"]["price"]),
                                 "qty": str(p["rung"]["qty"]),
                                 "resting_since": t,
                                 "action": p["rung"].get("action")}
                mutations.append({"kind": PLACE, "cycle": dec.get("cycle"),
                                  "tte_s": tte, "leg": working[coid]["leg"],
                                  "price": working[coid]["price"],
                                  "action": working[coid]["action"]})
            if withdrawn:
                break

        # ---- fills over (t_i, t_{i+1}] ----------------------------------
        hi = None
        if i + 1 < len(decisions):
            hi = _ms(decisions[i + 1].get("utc"))
        for pr in prints:
            if pr["utc_ms"] <= t_ms or (hi is not None and pr["utc_ms"] > hi):
                continue
            for coid, o in list(working.items()):
                if left.get(pr["tid"], D(0)) <= 0:
                    break
                got = RT.qualifies(o["leg"], _d(o["price"]), pr)
                if got is None:
                    continue
                if got == RT.AT_LEVEL and tier != RT.AT_LEVEL:
                    continue
                q = min(_d(o["qty"]), left[pr["tid"]])
                if q <= 0:
                    continue
                left[pr["tid"]] -= q
                inv.apply_fill(o["leg"], q, _d(o["price"]), source="replay")
                fills.append({"counterfactual": True, "leg": o["leg"],
                              "price": str(o["price"]), "qty": str(q),
                              "utc_ms": pr["utc_ms"], "tte_s": tte,
                              "action": o.get("action"),
                              "evidence_tier": got, "print_tid": pr["tid"]})
                working.pop(coid, None)

    y, n = D(inv.yes_qty), D(inv.no_qty)
    return {
        "label": LABEL, "symbol": symbol,
        "arm": {"reprice_ticks": reprice_ticks,
                "reprice_only_reducing": reprice_only_reducing,
                "extend_floor": _f(extend_floor), "cap": cap,
                "improve_ticks": improve_ticks,
                "rungs_per_side": rungs_per_side,
                "max_committed_usd": _f(max_committed_usd),
                "max_residual": (None if max_residual is None
                                 else str(max_residual)),
                "improve_only_action": improve_only_action,
                "tier": tier, "action_budget": action_budget,
                "min_rest_s": min_rest_s},
        "yes_qty": str(y), "no_qty": str(n),
        "yes_spend": _f(D(inv.yes_cost)), "no_spend": _f(D(inv.no_cost)),
        "spend_usd": _f(D(inv.yes_cost) + D(inv.no_cost)),
        "matched_qty": str(min(y, n)), "terminal_residual": str(abs(y - n)),
        "dominant": None if y == n else (YES if y > n else NO),
        "n_fills": len(fills), "fills": fills,
        "mutations_used": used, "mutations": mutations,
        "budget_blocked": budget_blocked,
        "committed_blocked": committed_blocked,
        "unreadable_cycles": unreadable,
        "orders_left_resting": len(working),
        "withdrawn": withdrawn,
        "labels": UNREALIZED,
    }


def settle(run: Mapping[str, Any], resolution_side: Any) -> Dict[str, Any]:
    """Settlement-implied economics for a replayed window.

    Same decomposition as `residual_analysis`, on exact rationals: a matched
    pair pays $1 whatever happens, a residual unit pays $1 only if its leg won.
    """
    side = str(resolution_side or "").strip().lower()
    won = YES if side in ("yes", "up", "true") else (
        NO if side in ("no", "down", "false") else None)
    if won is None:
        return {"verdict": "UNRESOLVED"}
    y, n = F(run["yes_qty"]), F(run["no_qty"])
    sy, sn = F(run["yes_spend"] or 0), F(run["no_spend"] or 0)
    m, r = min(y, n), abs(y - n)
    dom = None if y == n else (YES if y > n else NO)
    avg_y = (sy / y) if y else F(0)
    avg_n = (sn / n) if n else F(0)
    avg_d = avg_y if dom == YES else (avg_n if dom == NO else F(0))
    dom_won = F(1) if (dom is not None and dom == won) else F(0)
    matched_pnl = m * (F(1) - avg_y - avg_n)
    residual_pnl = r * (dom_won - avg_d)
    payout = m + (r if dom_won else F(0))
    net = payout - (sy + sn)
    return {
        "verdict": "COMPUTED", "winning_leg": won,
        "matched_qty": _f(m), "terminal_residual": _f(r), "dominant": dom,
        "matched_pnl_usd": _f(matched_pnl),
        "residual_pnl_usd": _f(residual_pnl),
        "payout_usd": _f(payout), "spend_usd": _f(sy + sn),
        "net_usd": _f(net),
        "pair_cost": _f(avg_y + avg_n) if m else None,
        "residual_unit_cost": _f(avg_d) if r else None,
        "identity_holds": bool(matched_pnl + residual_pnl == net),
        "labels": UNREALIZED,
    }
