"""The maker-completion leak: measure it, then suppress it counterfactually.

WHY THIS MODULE EXISTS. The corrected 49-window out-of-sample audit found
windows that finish FULLY MATCHED, with zero residual, and still lose money:
`0600z` bought 40 pairs for -$8.20, a pair cost of ~$1.205. The cause is
structural, not a replay defect (audited: no resting order ever filled worse
than its own limit). theta=0.96 gates the TAKER `marginal_complete` walk only.
`NativeEngine.on_book` step 4 rests one clip per side at OUR touch with no
basis test and no theta test, so a maker fill on the second side can complete
a pair at ANY price the market reaches -- 0.97 and 0.99 in that window.

WHAT IS MEASURED. Every acquisition that increases matched inventory, with the
incremental effective matched-pair cost computed from the cost basis actually
available BEFORE the action:

    maker  ->  fill_px + avg(opposite side)              (no maker fee at PMUS)
    taker  ->  avg(held side) + cost/n + fee/n           (the theta-gated walk)

DIAGNOSIS, NOT A NEW THRESHOLD. The suppression counterfactual exists to
attribute dollars, not to install a gate. Nothing here is fitted: the two bars
reported are $1.00 (the arithmetic point at which a matched pair cannot profit)
and theta itself, read from the frozen engine and never written.

The suppressed replay is SEQUENTIAL. A suppressed fill does not reappear
later, no shares are assumed to fill at a better price, and the engine keeps
quoting exactly as it would have -- the only difference is that the venue does
not hand us that one economically invalid maker fill.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P

#: the arithmetic ceiling. A matched pair pays exactly $1 at settlement, so a
#: pair acquired for more than $1 cannot profit. This is not a tuned number.
DOLLAR = D("1")

#: the buckets the audit reports, as (label, lo, hi] with lo exclusive.
BUCKETS = (("<=0.96", None, D("0.96")),
           ("0.96-1.00", D("0.96"), D("1.00")),
           ("1.00-1.05", D("1.00"), D("1.05")),
           (">1.05", D("1.05"), None))


def bucket_of(cost: D) -> str:
    for label, lo, hi in BUCKETS:
        if (lo is None or cost > lo) and (hi is None or cost <= hi):
            return label
    return ">1.05"


def _matched(bk) -> D:
    return min(bk.q["up"], bk.q["down"])


def _pair_cost_maker(bk, side: str, px: D) -> Optional[D]:
    """Incremental effective pair cost of a maker fill, or None if it matches
    nothing. `delta > 0` implies the opposite side already holds inventory, so
    its average is a real basis and never the zero placeholder."""
    other = "down" if side == "up" else "up"
    if bk.q[other] <= bk.q[side]:
        return None                       # this fill extends, it does not match
    return px + bk.avg(other)


# ------------------------------------------------------------------ measure

def instrument(rows: Sequence[Mapping[str, Any]], *, winner: str,
               clip: D) -> Dict[str, Any]:
    """Replay one window, recording every matched-increasing acquisition.

    The walk mirrors `pmus_native_parity.replay_window` exactly. The taker leg
    is observed by wrapping `Book.add`, which is where the completion path
    puts its fill; the maker leg is observed at the fill site, before
    `on_fill`, because that is the only point where the pre-action basis still
    exists.
    """
    eng = E.NativeEngine(clip=D(str(clip)), prospective_cap=False)
    venue = P.ReplayVenue(fill_model="queue_aware")
    ladder: Optional[Mapping[str, Any]] = None
    acqs: List[Dict[str, Any]] = []

    def _taker(it):
        # the completion path's own numbers, captured before the engine
        # applies them so the held-side basis is the pre-action one
        held = "up" if it.get("side") == "down" else "down"
        n, cost, fee = it["qty"], it["cost"], it["fee"]
        if n and n > 0:
            acqs.append({
                "kind": "taker_ECONOMIC_COMPLETE", "side": it.get("side"),
                "t": it.get("t"), "qty": D(str(n)),
                "px": D(str(cost)) / D(str(n)),
                "basis_other": eng.bk.avg(held),
                "fee": D(str(fee or 0)),
                "pair_cost": (eng.bk.avg(held) + D(str(cost)) / D(str(n))
                              + D(str(fee or 0)) / D(str(n))),
                "matched_delta": D(str(n))})
        return n, cost, fee

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
            eng.on_book(row, ladder, execute_taker=_taker)
        except E.StaleBook:
            continue
        if eng.locked_at is not None:
            continue
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
            before = _matched(eng.bk)
            pc = _pair_cost_maker(eng.bk, side, fpx)
            eng.on_fill(side, qty, qty * fpx, maker=True)
            delta = _matched(eng.bk) - before
            if delta > 0 and pc is not None:
                acqs.append({
                    "kind": "maker_resting_fill", "side": side,
                    "t": row.get("t"), "qty": D(str(qty)), "px": fpx,
                    "basis_other": pc - fpx, "fee": D(0),
                    "pair_cost": pc, "matched_delta": delta})

    out = eng.settle(winner)
    out["acquisitions"] = acqs
    return out


# -------------------------------------------------------------- counterfactual

def replay_suppressed(rows: Sequence[Mapping[str, Any]], *, winner: str,
                      clip: D, ceiling: D) -> Dict[str, Any]:
    """`replay_window`, with matched-increasing MAKER fills above `ceiling`
    refused by the venue and nothing else changed.

    The taker path is untouched: it is already theta-gated, and changing it
    would confound the attribution. Suppression is decided from the pre-action
    basis only -- no future price is consulted.
    """
    eng = E.NativeEngine(clip=D(str(clip)), prospective_cap=False)
    venue = P.ReplayVenue(fill_model="queue_aware")
    ladder: Optional[Mapping[str, Any]] = None
    suppressed_qty, suppressed_events = D(0), 0

    def _taker(it):
        return it["qty"], it["cost"], it["fee"]

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
            eng.on_book(row, ladder, execute_taker=_taker)
        except E.StaleBook:
            continue
        if eng.locked_at is not None:
            continue
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
            pc = _pair_cost_maker(eng.bk, side, fpx)
            if pc is not None and pc > ceiling:
                # THE ONE CHANGE. The fill does not happen. It is not deferred,
                # re-priced or assumed to fill later; the walk simply continues
                # from the state in which it did not happen.
                suppressed_qty += D(str(qty))
                suppressed_events += 1
                continue
            eng.on_fill(side, qty, qty * fpx, maker=True)

    out = eng.settle(winner)
    out["suppressed_qty"] = suppressed_qty
    out["suppressed_events"] = suppressed_events
    return out


def net_of(r: Mapping[str, Any]) -> D:
    return P.net_of(r)
