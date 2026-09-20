#!/usr/bin/env python3
"""Gemini EXECUTION adaptations A and B, as offline counterfactual arms.

MODEL A IS FROZEN AND IS NOT TOUCHED HERE. Both arms take the actions the
frozen policy asked for and change only HOW the resulting rung is placed:

  A  IMPROVE_1        one tick better than the price Model A chose, and only
                      when that stays strictly inside the ask. Never crosses.
  B  QUEUE_PRESERVE   keep a resting rung that has already earned queue
                      position rather than surrendering it, whenever the old
                      price is no more expensive than the newly desired one.

B'S RULE, PREREGISTERED, NO FITTED PARAMETER:

    KEEP a resting rung when
        ahead_left <= expected_ahead_after_replacement   (we already hold at
                                                          least as good a
                                                          place in line)
          AND |p_old - p_new| <= one tick                (the desired touch
                                                          moved by a tick, so
                                                          the resting price is
                                                          still economically
                                                          acceptable)
    and requote otherwise -- a clearly superior queue, or a touch that has
    moved further than a tick, justifies losing priority. Fill, economic
    invalidation and the end-window withdrawal are handled by the policy and
    the guard and are not overridden here.

TWO EARLIER VERSIONS WERE WRONG AND THE ERRORS ARE THE POINT.

  1. `ahead_left < ahead_at_place` -- "queue position has been earned" --
     cannot hold when ahead_at_place is 0, so it excluded precisely the
     front-of-queue rungs it existed to protect, and never fired under A.

  2. `p_old <= p_new` -- "the old quote costs no more" -- is the wrong test
     for a resting BUY that wants to be FILLED. Cheaper is better only if it
     trades; that rule kept quotes drifting arbitrarily far below the market,
     so it held priority at a price nobody was trading and cost fills. The
     tick bound is what makes "economically acceptable" mean still
     competitive rather than merely cheap.

WHAT IS MEASURED, NOT ASSUMED. Every keep/requote decision records the queue
already consumed, both prices, the queue that replacement would inherit and
what the subsequent flow did, so a KEEP that would not have filled is visible
as such.

HYPOTHETICAL throughout: no order exists, nothing is submitted, filled,
settled or realized.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import tape_replay as TR

NATIVE = "NATIVE"
A_ONLY = "A_IMPROVE_1"
B_ONLY = "B_QUEUE_PRESERVE"
AB = "A+B"
ARMS = (NATIVE, A_ONLY, B_ONLY, AB)

MAKER_RATE = D("0.0175")
TICK = D("0.01")

LABELS = ("HYPOTHETICAL. No order existed; nothing was submitted, filled, "
          "settled or realized. Symmetric queue accounting: both legs queue "
          "behind the ladder an aggressor actually consumes.")


def maker_only_improve(book: Mapping[str, Any], leg: str,
                       price: Any) -> Optional[D]:
    """One tick better, or None if that would reach the ask.

    CROSSING IS NEVER AUTHORIZED. A price at or through the ask is a taker
    order wearing a maker's label, and it would also spend the spread this
    whole exercise exists to earn.
    """
    side = "yes" if str(leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    ask, p = TR._dec(q.get("ask")), TR._dec(price)
    if p is None or ask is None:
        return None
    imp = p + TICK
    return None if imp >= ask else imp


def apply_A(actions: Sequence[Mapping[str, Any]],
            book: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Model A's own actions, one tick better where that stays maker."""
    out = []
    for a in actions or []:
        b = dict(a)
        imp = maker_only_improve(book, str(a.get("leg")), a.get("price"))
        if imp is not None:
            b["native_price"] = str(a.get("price"))
            b["price"] = str(imp)
            b["improved"] = True
        else:
            b["improved"] = False
        out.append(b)
    return out


def reconcile(live: Dict[str, TR.Quote], want: Sequence[Mapping[str, Any]], *,
              now_ms: D, depth: Any, default_qty: D,
              preserve: bool) -> Dict[str, Any]:
    """_reconcile, with B's keep/requote decision made explicit.

    With `preserve` false this is the frozen behaviour: any price change is a
    cancel and a new order at the back of its level.
    """
    by_leg = {str(a.get("leg")).upper(): a for a in want}
    ev = {"placed": 0, "requoted": 0, "kept": 0, "cancelled": 0,
          "preserved": 0, "decisions": []}
    for leg in list(live):
        if leg not in by_leg:
            del live[leg]
            ev["cancelled"] += 1
    for leg, a in by_leg.items():
        p = TR._dec(a.get("price"))
        if p is None:
            continue
        q = TR._dec(a.get("qty")) or default_qty
        cur = live.get(leg)
        if cur is not None and cur.price == p:
            cur.kind = str(a.get("action"))
            ev["kept"] += 1
            continue
        if cur is not None and preserve:
            new_ahead = TR.size_ahead(depth, leg, p)
            # a CLEARLY SUPERIOR replacement is worth the place in line
            better_queue = (new_ahead is not None
                            and new_ahead < cur.ahead_left)
            # still economically acceptable: the touch moved by at most a
            # tick. Further than that and the market has genuinely left our
            # price, so priority there is worth nothing.
            no_worse = abs(cur.price - p) <= TICK
            earned = not better_queue
            ev["decisions"].append({
                "leg": leg, "old_price": str(cur.price), "new_price": str(p),
                "ahead_at_place": (str(cur.ahead) if cur.ahead is not None
                                   else None),
                "ahead_left": str(cur.ahead_left),
                "queue_consumed": (str(cur.ahead - cur.ahead_left)
                                   if cur.ahead is not None else None),
                "consumed_fraction": (float((cur.ahead - cur.ahead_left)
                                            / cur.ahead)
                                      if cur.ahead else None),
                "ticks_moved": str(abs(cur.price - p)),
                "old_still_acceptable": bool(no_worse),
                "expected_ahead_after_replacement": (str(new_ahead)
                                                     if new_ahead is not None
                                                     else None),
                "decision": "KEEP" if (earned and no_worse) else "REQUOTE",
            })
            if earned and no_worse:
                cur.kind = str(a.get("action"))
                ev["preserved"] += 1
                ev["kept"] += 1
                continue
        ahead = TR.size_ahead(depth, leg, p)
        live[leg] = TR.Quote(leg=leg, price=p, qty=q, placed_ms=now_ms,
                             ahead=ahead, queue_known=ahead is not None,
                             kind=str(a.get("action")))
        if cur is None:
            ev["placed"] += 1
        else:
            ev["requoted"] += 1
    return ev


def _fee(qty: D, price: D) -> D:
    return MAKER_RATE * qty * price * (D("1") - price)


def replay(symbol: str, cycles: Sequence[Mapping[str, Any]],
           prints: Sequence[Mapping[str, Any]], *, arm: str) -> Dict[str, Any]:
    """One contract under one arm. Rungs are the frozen policy's own."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import queue_replay as QR

    use_a = arm in (A_ONLY, AB)
    use_b = arm in (B_ONLY, AB)
    inv = CP.Inventory()
    live: Dict[str, TR.Quote] = {}
    fills: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    tape = sorted((dict(p) for p in prints),
                  key=lambda p: QR._dec(p.get("utc_ms")) or D("0"))
    prev = None
    exposure = D("0")          # capital-at-risk integrated over cycles
    for c in cycles:
        now = QR._utc_ms(c)
        if now is None:
            continue
        sl = [p for p in tape
              if (prev is None or (QR._dec(p.get("utc_ms")) or D("0")) > prev)
              and (QR._dec(p.get("utc_ms")) or D("0")) <= now]
        # ---- walk the prints against the rungs resting from LAST cycle
        for p in TR_normalize(sl):
            for leg in list(live):
                q = live.get(leg)
                if q is None or not TR.reaches(leg, q.price, p):
                    continue
                got, _ = q.take(p["amount"])
                if got <= 0:
                    continue
                was_flat = inv.residual() == 0 and inv.matched() == 0
                inv.apply_fill(leg, got, q.price, source="AB_ARM")
                fills.append({"leg": leg, "qty": str(got), "price": str(q.price),
                              "kind": q.kind, "tte_s": c.get("tte_s"),
                              "was_first_fill": was_flat,
                              "ahead_at_place": (str(q.ahead)
                                                 if q.ahead is not None else None),
                              "print_amount": str(p["amount"]),
                              "improved": bool(getattr(q, "improved", False)),
                              "preserved": bool(getattr(q, "preserved", False))})
                if q.filled >= q.qty:
                    del live[leg]
        acts = QR.actions_of(c)
        book = c.get("book") or {}
        if use_a:
            acts = apply_A(acts, book)
        ev = reconcile(live, acts, now_ms=now, depth=c.get("depth"),
                       default_qty=D("0.01"), preserve=use_b)
        decisions.extend(ev["decisions"])
        # ATTRIBUTION, PER RUNG. A rung carries the flag of how IT came to
        # rest: improved by A when placed from an improved action, preserved
        # by B when this reconcile kept it rather than replacing it.
        kept_legs = {d["leg"] for d in ev["decisions"]
                     if d["decision"] == "KEEP"}
        for a in acts:
            leg = str(a.get("leg")).upper()
            q = live.get(leg)
            if q is None:
                continue
            if a.get("improved"):
                q.improved = True
            if leg in kept_legs:
                q.preserved = True
        exposure += abs(inv.residual())
        prev = now

    r = inv.report()
    matched = TR._dec(r.get("matched_qty")) or D("0")
    basis = TR._dec(r.get("effective_matched_pair_cost"))
    fee_pos = None
    if basis is not None and matched > 0 and D("0") < basis <= D("2"):
        half = basis / D("2")
        fee_pos = bool(basis + _fee(D("1"), half) * D("2") <= D("1"))
    return {"symbol": symbol, "arm": arm, "cycles": len(cycles),
            "prints": len(tape), "fills": len(fills),
            "first_fills": sum(1 for f in fills if f["was_first_fill"]),
            "complete_fills": sum(1 for f in fills if f["kind"] == "COMPLETE"),
            "fills_from_zero_queue": sum(1 for f in fills
                                         if f["ahead_at_place"] == "0"),
            "fills_on_improved_rung": sum(1 for f in fills if f["improved"]),
            "fills_on_preserved_rung": sum(1 for f in fills if f["preserved"]),
            "matched_qty": str(matched),
            "pair_basis": (str(basis) if basis is not None else None),
            "at_or_below_par": (bool(basis <= D("1"))
                                if basis is not None and matched > 0 else None),
            "fee_positive": fee_pos,
            "terminal_residual": str(r.get("directional_residual")),
            "capital_at_risk_cycle_units": str(exposure),
            "keep_decisions": sum(1 for d in decisions if d["decision"] == "KEEP"),
            "requote_decisions": sum(1 for d in decisions
                                     if d["decision"] == "REQUOTE"),
            "decisions": decisions, "rows": fills, "labels": LABELS}


def TR_normalize(prints):
    from src.gemini_execution import hypothetical as HY
    return HY.normalize(prints)
