#!/usr/bin/env python3
"""MAKER_OPEN -> HYBRID_COMPLETE, measured offline against captured books.

THE QUESTION. Our recycling failure is entirely in the second leg: a maker
OPEN fills, the complement is desired, an enormous queue stands in front of
it, the touch moves, we requote, and the position is never closed. Requiring
BOTH legs to be passive is our constraint, not L5's. So: at every recorded
book state after a first fill, could an IMMEDIATE, EXECUTABLE complement
have locked a pair below par after real fees?

MAKER IS A MEANS, NOT THE OBJECTIVE. There is no value in protecting maker
status while a guaranteed-profitable completion expires. Model A is frozen
and is not consulted here -- this changes only how the complement is
acquired, never which side, size or price the policy wants.

WHAT "EXECUTABLE" MEANS HERE, AND IT IS NOT SYMMETRIC. The venue publishes
ONE ladder, priced in YES:

  buying YES immediately  ->  lifts the ASK side; size is the ask levels at
                              or below what we pay, and cost is the ask.
  buying NO  immediately  ->  IS a sell of YES, so it hits the BID side;
                              size is the bid levels at or above (1 - n),
                              and cost per unit is (1 - bid).

Using the ask ladder for a NO complement would be the wrong side of the book
and would invent liquidity that is not there.

THE STRUCTURAL PRIOR THIS STUDY EXISTS TO TEST. At the INSTANT of a maker
fill the immediate complement cannot be cheap, and the arithmetic says so
before any data is read:

  rested a YES bid at p, hit by an aggressor SELL  ->  the surviving best
  bid b satisfies b <= p, and buying NO costs (1 - b), so the gross pair is
  p + 1 - b >= 1.

  rested a NO bid at n (a YES ask at 1 - n), lifted by an aggressor BUY ->
  the surviving best ask a satisfies a >= 1 - n, and the gross pair is
  n + a >= 1.

Either way the gross pair is >= $1.00 at t=0, with equality only when more
size survives at our own price, and fees then push it strictly negative. A
sub-par immediate completion therefore requires the book to MOVE after our
fill -- specifically, to move in favour of the leg we already hold. Whether
that happens, how often, for how long and in what size is an empirical
question, and it is the question this module answers.

NO MIDPOINTS AND NO ASSUMED FILLS. Prices come from the recorded book, size
from the recorded ladder, fees from the venue's published schedule. An
opportunity is counted only where displayed size actually covers us.

OFFLINE ONLY. Nothing here enables taker execution and no order is sent.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Gemini's published rates. A hybrid pair pays the MAKER rate on the leg we
#: rested and the TAKER rate on the leg we lifted -- never maker on both.
MAKER_RATE = D("0.0175")
TAKER_RATE = D("0.07")

#: reporting thresholds. NOT gates, not proposed, not fitted.
THRESHOLDS = (D("1.00"), D("0.99"), D("0.98"), D("0.96"))

LABELS = ("OFFLINE STUDY. No order was sent and taker execution is not "
          "enabled anywhere. Prices are the recorded book, size the recorded "
          "ladder, fees the published schedule. Nothing here is submitted, "
          "filled, settled, realized or a cost basis.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def fee_per_unit(rate: D, price: D) -> D:
    """The venue's fee, per contract, at `price`."""
    return rate * price * (D("1") - price)


def executable_complement(book: Mapping[str, Any], depth: Any, leg: str,
                          qty: D) -> Dict[str, Any]:
    """What buying `leg` RIGHT NOW costs, and whether the size is there.

    Returns the volume-weighted price actually payable for `qty`, walking
    the correct side of the one published ladder. `executable` is False --
    with a reason -- whenever the displayed size does not cover us, so a
    missing opportunity is never silently priced at the touch.
    """
    side = str(leg).upper()
    b = (book or {}).get("yes" if side == "YES" else "no") or {}
    touch = _dec(b.get("ask"))
    if not isinstance(depth, Mapping) or not depth.get("readable"):
        return {"executable": False, "why": "ladder unreadable",
                "touch_price": (str(touch) if touch is not None else None)}

    if side == "YES":
        raw = (depth.get("asks") or {}).get("levels") or []
        priced = [(_dec(l.get("price")), _dec(l.get("qty"))) for l in raw]
        priced = [(p, q) for p, q in priced
                  if p is not None and q is not None and q > 0]
        priced.sort(key=lambda r: r[0])                 # cheapest ask first
        cost_of = lambda p: p                           # noqa: E731
    else:
        # buying NO is a SELL of YES: it consumes the BID ladder, best first
        raw = (depth.get("bids") or {}).get("levels") or []
        priced = [(_dec(l.get("price")), _dec(l.get("qty"))) for l in raw]
        priced = [(p, q) for p, q in priced
                  if p is not None and q is not None and q > 0]
        priced.sort(key=lambda r: -r[0])                # highest bid first
        cost_of = lambda p: D("1") - p                  # noqa: E731

    need, spend, got, used = qty, D("0"), D("0"), 0
    for px, size in priced:
        if need <= 0:
            break
        take = min(need, size)
        spend += take * cost_of(px)
        got += take
        need -= take
        used += 1
    if got < qty:
        return {"executable": False, "why": "displayed size does not cover us",
                "available": str(got), "wanted": str(qty),
                "touch_price": (str(touch) if touch is not None else None)}
    vwap = spend / qty
    # TWO READS, TWO CLOCKS -- AND WE DO NOT GET TO PICK THE FLATTERING ONE.
    # `depth` is fetched separately from `book` and carries its own
    # timestamp. When the ladder prices this complement BELOW the book's own
    # touch the two disagree, and taking the cheaper of two contradictory
    # reads is how a study invents liquidity. Neither read is known to be
    # the stale one, so the state is not counted at all.
    if touch is None:
        return {"executable": False, "why": "no ask on this leg in the book",
                "ladder_vwap": str(vwap)}
    if vwap < touch:
        return {"executable": False,
                "why": "book and ladder disagree: ladder is inside the touch",
                "touch_price": str(touch), "ladder_vwap": str(vwap),
                "disagreement": str(touch - vwap)}
    return {"executable": True, "qty": str(qty), "vwap": str(vwap),
            "touch_price": (str(touch) if touch is not None else None),
            "levels_consumed": used,
            "depth_at_top": str(priced[0][1]) if priced else "0"}


# ------------------------------------------------------------------ basis
def residual_basis(fills: Sequence[Mapping[str, Any]]
                   ) -> Optional[Tuple[str, D, D]]:
    """(leg, qty, cost per unit) of the UNMATCHED inventory, matched FIFO.

    The engine's average cost answers a different question: it averages the
    whole leg including the part already paired off. What a completion has
    to beat is the cost of the contracts still open, so those are paired
    oldest-against-oldest and what is left over is priced on its own.
    """
    q: Dict[str, List[List[D]]] = {"YES": [], "NO": []}
    for f in fills or []:
        leg = str(f.get("leg")).upper()
        if leg not in q:
            continue
        amt, px = _dec(f.get("qty")), _dec(f.get("price"))
        if amt is None or px is None or amt <= 0:
            continue
        other = "NO" if leg == "YES" else "YES"
        while amt > 0 and q[other]:
            take = min(amt, q[other][0][0])
            q[other][0][0] -= take
            amt -= take
            if q[other][0][0] <= 0:
                q[other].pop(0)
        if amt > 0:
            q[leg].append([amt, px])
    for leg in ("YES", "NO"):
        if q[leg]:
            tot = sum(r[0] for r in q[leg])
            spend = sum(r[0] * r[1] for r in q[leg])
            return leg, tot, spend / tot
    return None


def all_in_basis(first_cost: D, comp_vwap: D) -> Dict[str, D]:
    """The one number the decision turns on, with its parts kept separate."""
    mf = fee_per_unit(MAKER_RATE, first_cost)
    tf = fee_per_unit(TAKER_RATE, comp_vwap)
    gross = first_cost + comp_vwap
    return {"gross": gross, "maker_fee": mf, "taker_fee": tf,
            "fees": mf + tf, "all_in": gross + mf + tf}


# --------------------------------------------------------------- the hook
class Completer:
    """`ab_arms.replay`'s per-cycle hook: price the immediate complement.

    With `threshold` None this only OBSERVES -- every residual state is
    priced and recorded and the replay is bit-for-bit the maker-only arm.
    With a threshold set it also TAKES the first state whose all-in basis
    clears it, which is the MAKER_OPEN -> HYBRID_COMPLETE arm.
    """

    TAKER_SOURCE = "HYBRID_TAKER_COMPLETE_OFFLINE"

    def __init__(self, *, symbol: str = "", threshold: Any = None) -> None:
        self.symbol = symbol
        self.threshold = _dec(threshold) if threshold is not None else None
        self.rows: List[Dict[str, Any]] = []
        self.episodes: List[Dict[str, Any]] = []
        self.taker_fills: List[Dict[str, Any]] = []
        self._ep: Optional[Dict[str, Any]] = None

    # -- episode bookkeeping ------------------------------------------
    def _open(self, *, leg: str, tte: Optional[D], cost: D, qty: D) -> None:
        self._ep = {"symbol": self.symbol, "residual_leg": leg,
                    "complement_leg": "NO" if leg == "YES" else "YES",
                    "opened_tte_s": (float(tte) if tte is not None else None),
                    "first_leg_cost_per_unit": str(cost),
                    "opened_qty": str(qty), "states": 0,
                    "executable_states": 0, "priced": [],
                    "closed_by": None, "closed_tte_s": None}

    def _close(self, how: str, tte: Optional[D]) -> None:
        if self._ep is None:
            return
        ep = self._ep
        ep["closed_by"] = how
        ep["closed_tte_s"] = float(tte) if tte is not None else None
        if ep["opened_tte_s"] is not None and ep["closed_tte_s"] is not None:
            ep["duration_s"] = ep["opened_tte_s"] - ep["closed_tte_s"]
        ep.update(self._summarize(ep))
        self.episodes.append(ep)
        self._ep = None

    @staticmethod
    def _summarize(ep: Mapping[str, Any]) -> Dict[str, Any]:
        """Per-threshold reach, timing, duration and size for one episode."""
        priced = [r for r in ep["priced"] if r.get("executable")]
        out: Dict[str, Any] = {}
        for th in THRESHOLDS:
            hits = [r for r in priced if _dec(r["all_in_pair_basis"]) <= th]
            key = "at_or_below_%s" % str(th).replace(".", "_")
            if not hits:
                out[key] = {"states": 0, "reached": False}
                continue
            secs = [r["seconds_after_open"] for r in hits]
            best = min(hits, key=lambda r: _dec(r["all_in_pair_basis"]))
            out[key] = {
                "states": len(hits), "reached": True,
                "first_seconds_after_open": min(secs),
                "last_seconds_after_open": max(secs),
                "span_s": max(secs) - min(secs),
                "best_all_in_basis": best["all_in_pair_basis"],
                "best_locked_cents_per_unit": best["locked_cents_per_unit"],
                "executable_qty_at_best": best["executable_qty"],
            }
        out["min_all_in_basis"] = (
            min(str(r["all_in_pair_basis"]) for r in priced)
            if priced else None)
        return out

    # -- the hook itself ----------------------------------------------
    def __call__(self, *, cycle: Mapping[str, Any], now_ms: Any, inv: Any,
                 live: Dict[str, Any], fills: Sequence[Mapping[str, Any]]
                 ) -> None:
        tte = _dec(cycle.get("tte_s"))
        rb = residual_basis(inv.fills)
        if rb is None:
            if self._ep is not None:
                self._close("MAKER_COMPLETE_FILLED", tte)
            return
        leg, qty, cost = rb
        if self._ep is None:
            self._open(leg=leg, tte=tte, cost=cost, qty=qty)
        elif self._ep["residual_leg"] != leg:
            # the residual flipped sides: the old episode ended and a new
            # one began. Recording it as one episode would merge two
            # different positions into a single basis.
            self._close("RESIDUAL_FLIPPED", tte)
            self._open(leg=leg, tte=tte, cost=cost, qty=qty)
        ep = self._ep
        comp = ep["complement_leg"]
        ep["states"] += 1

        ex = executable_complement(cycle.get("book") or {},
                                   cycle.get("depth"), comp, qty)
        row: Dict[str, Any] = {
            "symbol": self.symbol, "tte_s": float(tte) if tte is not None
            else None,
            "seconds_after_open": ((ep["opened_tte_s"] - float(tte))
                                   if tte is not None
                                   and ep["opened_tte_s"] is not None
                                   else None),
            "residual_leg": leg, "residual_qty": str(qty),
            "complement_leg": comp,
            "first_leg_cost_per_unit": str(cost),
            "executable": bool(ex.get("executable")),
        }
        if not ex.get("executable"):
            row["why_not"] = ex.get("why")
            row["touch"] = ex.get("touch_price")
            row["ladder_vwap"] = ex.get("ladder_vwap")
            ep["priced"].append(row)
            self.rows.append(row)
            return

        ep["executable_states"] += 1
        vwap = _dec(ex["vwap"])
        parts = all_in_basis(cost, vwap)
        row.update({
            "executable_qty": ex["qty"], "complement_vwap": str(vwap),
            "touch": ex.get("touch_price"),
            "levels_consumed": ex.get("levels_consumed"),
            "gross_pair_basis": str(parts["gross"]),
            "maker_fee_per_unit": str(parts["maker_fee"]),
            "taker_fee_per_unit": str(parts["taker_fee"]),
            "all_in_pair_basis": str(parts["all_in"]),
            "locked_cents_per_unit": str((D("1") - parts["all_in"])
                                         * D("100")),
        })
        ep["priced"].append(row)
        self.rows.append(row)

        if self.threshold is None or parts["all_in"] > self.threshold:
            return
        # ---- HYBRID ARM: take it, and stop working the passive rung ----
        inv.apply_fill(comp, qty, vwap, source=self.TAKER_SOURCE)
        cancelled = live.pop(comp, None)
        self.taker_fills.append({
            **row, "arm_action": "TAKER_COMPLETE",
            "cancelled_resting_complement": cancelled is not None,
            "threshold": str(self.threshold), "labels": LABELS})
        self._close("TAKER_COMPLETE", tte)

    def finish(self) -> None:
        """Close an episode still open when the contract expired."""
        self._close("WINDOW_ENDED_STILL_RESIDUAL", None)

    # -- economics of the arm -----------------------------------------
    def economics(self) -> Dict[str, Any]:
        """Fees paid at the REAL rate per leg, never maker-on-both."""
        n = len(self.taker_fills)
        if not n:
            return {"taker_completions": 0, "pairs_locked": 0,
                    "net_locked_usd": None, "labels": LABELS}
        locked = D("0")
        qty = D("0")
        for t in self.taker_fills:
            q = _dec(t["executable_qty"]) or D("0")
            qty += q
            locked += (D("1") - (_dec(t["all_in_pair_basis"]) or D("0"))) * q
        return {"taker_completions": n, "pairs_locked": str(qty),
                "net_locked_usd_if_settled": str(locked),
                "mean_locked_cents_per_unit": str(locked / qty * D("100"))
                if qty else None,
                "labels": LABELS}
