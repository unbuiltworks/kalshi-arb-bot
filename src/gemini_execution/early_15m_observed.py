#!/usr/bin/env python3
"""EARLY_15M, priced from what actually traded -- both legs, never one.

WHY THIS REPLACES THE EARLIER REPLAY. `early_15m.replay_early` priced the
complement at `1 - held - tick`, so every pair basis it produced was 0.99 by
construction and the "51.8% par-constrained" it reported was a restatement of
its own arithmetic. Nothing about a pair's cost can be learned that way.

WHAT IS OBSERVABLE HERE, AND IT IS BOTH LEGS. Gemini's public trade tape is
priced in YES, carries the AGGRESSOR's direction, and serves expired
contracts. So:

  a resting YES rung at h is reached by an aggressor SELL at <= h;
  a resting NO rung at n is reached by an aggressor BUY at >= 1 - n.

Both legs are therefore priced from prices that ACTUALLY TRADED. If we hold
YES bought at h, and a later BUY prints at YES price b, then buying NO at that
moment costs 1 - b, and the pair basis is h + (1 - b) -- two real executions,
not one real and one assumed. The pair is at or below par exactly when
b >= h, which is a condition on two observed trades.

WHAT IS NOT OBSERVABLE, AND IS NOT INVENTED. Gemini serves NO book history.
Whether OUR order would have been at the front of the queue when that print
arrived cannot be recovered at any price, so `maker_reachable` is
UNOBSERVABLE on every row and is never silently promoted to True. Every
figure below is therefore an UPPER BOUND conditional on having been filled,
and says so.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Gemini's published maker rate; one leg's fee is rate * qty * p * (1 - p)
MAKER_RATE = D("0.0175")

UNOBSERVABLE = "UNOBSERVABLE"

#: the early interval this study is about
EARLY_LO_S = 300.0
EARLY_HI_S = 900.0

#: the frozen Model A band, so the control is measured by THIS instrument
#: rather than by a second one whose differences would confound the answer.
LATE_LO_S = 0.0
LATE_HI_S = 300.0

LABELS = (
    "Both legs priced from prices that ACTUALLY TRADED on the public tape. "
    "Queue position is UNOBSERVABLE -- Gemini serves no book history -- so "
    "every pair here is conditional on having been filled and is an UPPER "
    "BOUND. Nothing is submitted, filled, settled or realized.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def maker_fee(qty: D, price: D) -> D:
    return MAKER_RATE * qty * price * (D("1") - price)


def _norm(prints: Sequence[Mapping[str, Any]], *, expiry_ms: Any
          ) -> List[Dict[str, Any]]:
    exp = _dec(expiry_ms)
    out: List[Dict[str, Any]] = []
    for p in prints or []:
        ms = _dec(p.get("utc_ms"))
        px = _dec(p.get("price"))
        amt = _dec(p.get("amount"))
        if amt is None:
            amt = _dec(p.get("qty"))
        side = str(p.get("type") or p.get("side") or "").lower()
        if None in (ms, px, amt) or exp is None or side not in ("buy", "sell"):
            continue
        out.append({"utc_ms": ms, "price": px, "amount": amt, "side": side,
                    "tte_s": float((exp - ms) / D("1000"))})
    out.sort(key=lambda r: r["utc_ms"])
    return out


def replay_observed(symbol: str, prints: Sequence[Mapping[str, Any]], *,
                    expiry_ms: Any, qty: Any = "0.01",
                    lo: float = EARLY_LO_S, hi: float = EARLY_HI_S
                    ) -> Dict[str, Any]:
    """One contract's early interval, both legs priced from real trades.

    `lo`/`hi` bound the TTE interval. They default to the early window, and
    passing LATE_LO_S/LATE_HI_S measures the frozen [0, 300] band with this
    same code -- the control and the treatment must differ only in timing.

    The first print inside the window sets the held leg: a SELL means a
    resting YES bid was reached at that price, a BUY means a resting NO bid
    was. The complement is the first subsequent print of the OPPOSITE
    aggressor direction, and its price is what buying the other side cost at
    that instant.
    """
    q = _dec(qty) or D("0.01")
    tape = [r for r in _norm(prints, expiry_ms=expiry_ms)
            if lo <= r["tte_s"] <= hi]
    base = {"symbol": symbol, "prints_in_early_window": len(tape),
            "band_tte_s": [lo, hi],
            "maker_reachable": UNOBSERVABLE,
            "why_unreachable_unknown": ("Gemini serves no book history, so "
                                        "queue position at the moment of the "
                                        "print cannot be recovered."),
            "labels": LABELS}
    if not tape:
        return dict(base, computable=False,
                    why="no prints in %g-%gs" % (lo, hi))

    first = tape[0]
    # a SELL reaches a resting YES bid; a BUY reaches a resting NO bid
    held_leg = "YES" if first["side"] == "sell" else "NO"
    held_price = first["price"] if held_leg == "YES" else (D("1")
                                                           - first["price"])
    want = "buy" if held_leg == "YES" else "sell"

    comp = None
    for r in tape[1:]:
        if r["side"] == want:
            comp = r
            break
    if comp is None:
        return dict(base, computable=True, first_fill=True,
                    held_leg=held_leg, held_price=str(held_price),
                    first_fill_tte_s=round(first["tte_s"], 1),
                    opposite_flow=False,
                    opposite_price=UNOBSERVABLE,
                    pair_basis=UNOBSERVABLE,
                    terminal_residual=str(q),
                    terminal_residual_leg=held_leg,
                    why_no_pair="no opposite-direction print followed")

    # what the other side actually cost at that instant
    comp_price = (D("1") - comp["price"]) if held_leg == "YES" else comp["price"]
    basis = held_price + comp_price
    fees = maker_fee(q, held_price) + maker_fee(q, comp_price)
    per_pair_fee = fees / q if q else D("0")
    return dict(
        base, computable=True, first_fill=True,
        held_leg=held_leg, held_price=str(held_price),
        first_fill_tte_s=round(first["tte_s"], 1),
        opposite_flow=True,
        opposite_leg=("NO" if held_leg == "YES" else "YES"),
        opposite_price=str(comp_price),
        opposite_tte_s=round(comp["tte_s"], 1),
        seconds_to_complement=round(
            float((comp["utc_ms"] - first["utc_ms"]) / D("1000")), 1),
        pair_basis=str(basis),
        at_or_below_par=bool(basis <= D("1")),
        fees_per_pair=str(per_pair_fee),
        fee_positive=bool(basis + per_pair_fee <= D("1")),
        terminal_residual="0",
    )


def census(rows: Sequence[Mapping[str, Any]], *,
           label: str = "EARLY_15M_OBSERVED") -> Dict[str, Any]:
    """The independent counts, with UNOBSERVABLE kept separate from zero."""
    comp = [r for r in rows if r.get("computable")]
    firsts = [r for r in comp if r.get("first_fill")]
    flow = [r for r in firsts if r.get("opposite_flow")]
    priced = [r for r in flow if r.get("pair_basis") not in (None,
                                                             UNOBSERVABLE)]
    bases = sorted(float(r["pair_basis"]) for r in priced)
    t2c = sorted(float(r["seconds_to_complement"]) for r in priced)
    par = [r for r in priced if r.get("at_or_below_par")]
    feepos = [r for r in priced if r.get("fee_positive")]

    def med(xs):
        if not xs:
            return None
        n = len(xs)
        return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1]
                                               + xs[n // 2]) / 2, 4)
    bands = sorted({tuple(r["band_tte_s"]) for r in rows
                    if r.get("band_tte_s")})
    return {
        "label": label,
        "band_tte_s": (list(bands[0]) if len(bands) == 1 else
                       [list(b) for b in bands]),
        "contracts": len(rows),
        "computable": len(comp),
        "first_fill_states": len(firsts),
        "opposite_flow": len(flow),
        "price_observable_complements": len(priced),
        "maker_reachable_complements": UNOBSERVABLE,
        "pairs_at_or_below_1": len(par),
        "fee_positive_pairs": len(feepos),
        "par_rate": (round(len(par) / len(priced), 4) if priced else None),
        "fee_positive_rate": (round(len(feepos) / len(priced), 4)
                              if priced else None),
        "median_actual_pair_basis": med(bases),
        "median_seconds_to_complement": med(t2c),
        "basis_min": (round(bases[0], 4) if bases else None),
        "basis_max": (round(bases[-1], 4) if bases else None),
        "distinct_bases": len({round(b, 4) for b in bases}),
        "terminal_residual_states": sum(
            1 for r in firsts if not r.get("opposite_flow")),
        "labels": LABELS,
    }
