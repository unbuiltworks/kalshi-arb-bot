"""THE FOUR POST-FILL PATHS, AND WHY "COMPLETE THE PAIR" IS NOT AUTOMATIC.

Once a maker fill lands we hold a directional leg, and the frozen policy has
exactly one answer for that state: acquire the complement. This module asks the
question the policy does not -- of the four things the venue actually permits,
which one maximises safe executable expectancy right now?

    A  COMPLETE_MAKER      rest a BUY on the complement outcome
    B  REDUCE_MAKER        rest a SELL of the held outcome
    C  COMPLETE_AGGRESSIVE cross to the complement's ask
    D  EXIT_AGGRESSIVE     hit the held outcome's bid

HOW THE VENUE QUOTES THESE, READ RATHER THAN DERIVED. `metadata.outcome_book`
publishes `prices.buy.{yes,no}` and `prices.sell.{yes,no}` directly, and states
that "a resting BUY on an outcome joins that outcome's SELL side". So:

    passive acquisition of X   costs    sell[X]   ( = X's bid )
    aggressive acquisition of X costs   buy[X]    ( = X's ask )
    passive disposal of X      receives buy[X]    ( = X's ask )
    aggressive disposal of X   receives sell[X]   ( = X's bid )

A passive sale receives the ask and an aggressive sale receives the bid, which
is the spread pointing the way it should.

A AND B ARE THE SAME TRADE WHILE THE COMPLEMENT IDENTITY HOLDS, AND THAT IS A
RESULT, NOT A REASON TO DROP ONE. Under `no_bid == 1 - yes_ask`:

    pair via A   = 1 - c_held - complement_bid = held_ask - c_held
    round trip B = held_ask - c_held

identical to the cent. `outcome_book` CHECKS that identity on every read
instead of assuming it, and reports a violation rather than smoothing it -- so
the two paths diverge in price exactly when the book stops being one we
understand. They ALSO differ in three ways that survive the identity:

  * CAPITAL-MINUTES. A completed pair ties up both legs until settlement; a
    round trip frees the capital now. At equal net cents the round trip wins
    on cents per capital-dollar-minute, which is the primary scoreboard metric.
  * SETTLEMENT EXPOSURE. A pair carries settlement-reference risk to expiry; a
    round trip carries none.
  * REBATE TREATMENT. Both maker paths accrue; the aggressive paths do not, and
    accrual is reported apart from every admission test regardless.

So the choice between A and B is a capital and risk question, not a price
question, and this module returns both rather than pre-collapsing them.

DISPOSITIONS ARE OURS; THE L5 LABELS ARE NOT TOUCHED. `continuous_probe`
defines the canonical vocabulary OPEN / EXTEND / COMPLETE / HOLD and that
artifact is left alone. A Gemini disposition is carried in its OWN field beside
whatever L5 asked for, so a record always answers both "what did L5 want" and
"what did the venue economics allow" without either overwriting the other.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_post_fill_paths/v1"

#: OUR vocabulary. OPEN/EXTEND/COMPLETE are spelled the same as L5's because
#: they mean the same thing; REDUCE and EXIT have no L5 counterpart and are
#: never written into a field that L5 fidelity is scored from.
OPEN, EXTEND, COMPLETE, REDUCE, EXIT = (
    "OPEN", "EXTEND", "COMPLETE", "REDUCE", "EXIT")
DISPOSITIONS = (OPEN, EXTEND, COMPLETE, REDUCE, EXIT)

#: the canonical L5 labels, restated here only so a test can assert we never
#: emit a disposition into an L5 field that is not one of them.
L5_LABELS = ("OPEN", "EXTEND", "COMPLETE", "HOLD")

PATH_A, PATH_B, PATH_C, PATH_D = (
    "A_COMPLETE_MAKER", "B_REDUCE_MAKER",
    "C_COMPLETE_AGGRESSIVE", "D_EXIT_AGGRESSIVE")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def held_and_complement(residual: D) -> Dict[str, Optional[str]]:
    """Which leg we are long and which one would pair it.

    `residual` is signed exactly as `continuous_probe.Inventory.residual`
    defines it: positive is long YES.
    """
    if residual > 0:
        return {"held": "YES", "complement": "NO"}
    if residual < 0:
        return {"held": "NO", "complement": "YES"}
    return {"held": None, "complement": None}


def _side(book: Mapping[str, Any], outcome: str, which: str) -> Optional[D]:
    leg = (book or {}).get(outcome.lower()) or {}
    return _dec(leg.get(which))


def identity_holds(book: Mapping[str, Any]) -> Dict[str, Any]:
    """Is `no_bid == 1 - yes_ask` and `no_ask == 1 - yes_bid` on this read?

    `metadata.outcome_book` already computes these deltas; recomputing them
    here keeps this module usable against a synthetic book in a test without
    reaching for the network module.
    """
    yb, ya = _side(book, "yes", "bid"), _side(book, "yes", "ask")
    nb, na = _side(book, "no", "bid"), _side(book, "no", "ask")
    if None in (yb, ya, nb, na):
        return {"checked": False, "holds": None,
                "why": "book does not publish both outcomes' bid and ask"}
    d1 = nb - (D("1") - ya)
    d2 = na - (D("1") - yb)
    return {"checked": True, "holds": bool(d1 == 0 and d2 == 0),
            "no_bid_vs_1_minus_yes_ask": str(d1),
            "no_ask_vs_1_minus_yes_bid": str(d2)}


def evaluate_paths(*, residual: Any, held_avg_cost: Any, book: Mapping[str, Any],
                   qty: Any, min_edge: Any, rescue_edge: Any = None,
                   regime: VT.FeeRegime = VT.VERIFIED_REGIME,
                   slippage: Any = "0", tick: Any = "0.01",
                   tte_s: Any = None) -> Dict[str, Any]:
    """Every path the venue permits from this state, each priced on its own side.

    A path that the book cannot price is reported UNAVAILABLE with the reason.
    It is never silently dropped, because "the venue did not quote it" and "we
    judged it uneconomic" are different facts and the scoreboard counts them
    apart.

    `rescue_edge` may be negative and applies ONLY to the aggressive disposal
    path D -- a decision to take a bounded known loss rather than carry an
    unbounded unknown one. It is never applied to A, B or C, so a rescue
    tolerance can never quietly finance a bad completion.
    """
    res = _dec(residual)
    q = _dec(qty)
    cost = _dec(held_avg_cost)
    out: Dict[str, Any] = {"schema": SCHEMA, "regime": regime.report(),
                           "identity": identity_holds(book),
                           "tte_s": None if tte_s is None else float(tte_s)}
    if res is None or res == 0:
        out.update(state="FLAT", paths={},
                   why="no residual leg; there is nothing to complete or exit")
        return out
    if cost is None or q is None or q <= 0:
        out.update(state="UNREADABLE", paths={},
                   why="held_avg_cost or qty unreadable; no path is priced")
        return out

    legs = held_and_complement(res)
    held, comp = legs["held"], legs["complement"]
    out.update(state="INVENTORY", held_leg=held, complement_leg=comp,
               held_avg_cost=str(cost), qty=str(q))

    paths: Dict[str, Any] = {}

    def unavailable(name, disposition, why):
        paths[name] = {"available": False, "disposition": disposition,
                       "admitted": False, "why": why}

    # ---- A: rest a BUY on the complement. Passive acquisition pays the
    # complement's BID, per outcome_book's own statement of the mechanic.
    a_px = _side(book, comp, "bid")
    if a_px is None:
        unavailable(PATH_A, COMPLETE, "complement bid not quoted")
    else:
        adm = VT.admits(held_avg_cost=cost, proposed_price=a_px,
                        min_edge=min_edge, regime=regime,
                        complement_liquidity="MAKER", slippage=slippage,
                        tick=tick)
        paths[PATH_A] = {
            "available": True, "disposition": COMPLETE, "liquidity": "MAKER",
            "price": str(a_px), "admitted": adm["admitted"],
            "why": adm["reason"], "ceiling": adm["ceiling"].get("ceiling"),
            "economics": VT.pair_economics(
                held_qty=q, held_avg_cost=cost, complement_qty=q,
                complement_price=a_px, regime=regime,
                complement_liquidity="MAKER"),
        }

    # ---- B: rest a SELL of the held leg. Passive disposal receives the held
    # outcome's ASK.
    b_px = _side(book, held, "ask")
    if b_px is None:
        unavailable(PATH_B, REDUCE, "held-leg ask not quoted")
    else:
        fl = VT.min_exit_price(held_avg_cost=cost, min_edge=min_edge,
                               regime=regime, exit_liquidity="MAKER",
                               slippage=slippage, tick=tick)
        ok = bool(fl.get("ok") and b_px >= D(fl["floor"]))
        paths[PATH_B] = {
            "available": True, "disposition": REDUCE, "liquidity": "MAKER",
            "price": str(b_px), "admitted": ok, "floor": fl.get("floor"),
            "why": (None if ok else
                    ("NO_ECONOMIC_FLOOR: %s" % fl.get("why")) if not fl.get("ok")
                    else "PRICE_BELOW_FLOOR: %s < %s" % (b_px, fl["floor"])),
            "economics": VT.roundtrip_economics(
                qty=q, held_avg_cost=cost, exit_price=b_px, regime=regime,
                exit_liquidity="MAKER"),
        }

    # ---- C: cross to the complement's ASK. Taker fee, and the ceiling is
    # computed at the taker rate rather than the maker rate.
    c_px = _side(book, comp, "ask")
    if c_px is None:
        unavailable(PATH_C, COMPLETE, "complement ask not quoted")
    else:
        adm = VT.admits(held_avg_cost=cost, proposed_price=c_px,
                        min_edge=min_edge, regime=regime,
                        complement_liquidity="TAKER", slippage=slippage,
                        tick=tick)
        paths[PATH_C] = {
            "available": True, "disposition": COMPLETE, "liquidity": "TAKER",
            "price": str(c_px), "admitted": adm["admitted"],
            "why": adm["reason"], "ceiling": adm["ceiling"].get("ceiling"),
            "economics": VT.pair_economics(
                held_qty=q, held_avg_cost=cost, complement_qty=q,
                complement_price=c_px, regime=regime,
                complement_liquidity="TAKER"),
        }

    # ---- D: hit the held leg's BID. The ONLY path a negative `rescue_edge`
    # reaches.
    d_px = _side(book, held, "bid")
    d_edge = min_edge if rescue_edge is None else rescue_edge
    if d_px is None:
        unavailable(PATH_D, EXIT, "held-leg bid not quoted")
    else:
        fl = VT.min_exit_price(held_avg_cost=cost, min_edge=d_edge,
                               regime=regime, exit_liquidity="TAKER",
                               slippage=slippage, tick=tick)
        ok = bool(fl.get("ok") and d_px >= D(fl["floor"]))
        paths[PATH_D] = {
            "available": True, "disposition": EXIT, "liquidity": "TAKER",
            "price": str(d_px), "admitted": ok, "floor": fl.get("floor"),
            "rescue_edge_applied": (None if rescue_edge is None
                                    else str(_dec(rescue_edge))),
            "why": (None if ok else
                    ("NO_ECONOMIC_FLOOR: %s" % fl.get("why")) if not fl.get("ok")
                    else "PRICE_BELOW_FLOOR: %s < %s" % (d_px, fl["floor"])),
            "economics": VT.roundtrip_economics(
                qty=q, held_avg_cost=cost, exit_price=d_px, regime=regime,
                exit_liquidity="TAKER"),
        }

    out["paths"] = paths
    out["admitted_paths"] = sorted(k for k, v in paths.items()
                                   if v.get("admitted"))
    out["labels"] = [
        "A and B are the same economics while the complement identity holds; "
        "they differ in capital-minutes, settlement exposure and rebate "
        "eligibility, and both are returned rather than pre-collapsed.",
        "rescue_edge reaches path D only. It can never finance a completion.",
    ]
    return out


def net_of(path: Mapping[str, Any]) -> Optional[D]:
    """The one comparable number across a pair path and a round-trip path."""
    econ = path.get("economics") or {}
    v = econ.get("net_pair_economics", econ.get("net_roundtrip"))
    return _dec(v)


def rank(evaluated: Mapping[str, Any], *, capital_weighted: bool = False,
         expected_hold_minutes: Any = None) -> List[Dict[str, Any]]:
    """Admitted paths, best first.

    Default ranking is on NET CENTS, which is the honest comparison when the
    holding period is unknown. `capital_weighted` switches to net cents per
    capital-dollar-minute -- the primary scoreboard metric -- and requires an
    explicit `expected_hold_minutes` rather than inventing one, because a
    capital-weighted ranking with a guessed denominator would silently prefer
    whichever path we guessed a shorter hold for.
    """
    rows: List[Dict[str, Any]] = []
    for name, p in (evaluated.get("paths") or {}).items():
        if not p.get("admitted"):
            continue
        net = net_of(p)
        if net is None:
            continue
        row = {"path": name, "disposition": p.get("disposition"),
               "price": p.get("price"), "net_cents": str(net)}
        if capital_weighted:
            mins = _dec(expected_hold_minutes)
            if mins is None or mins <= 0:
                row["score"] = None
                row["score_note"] = ("capital weighting requested without "
                                     "expected_hold_minutes; NOT guessed")
            else:
                # a round trip frees capital at the exit; a pair carries both
                # legs to settlement
                cap = _dec(p.get("price")) or D("0")
                if p.get("disposition") == COMPLETE:
                    cap = cap + (_dec(evaluated.get("held_avg_cost")) or D("0"))
                row["capital_dollars"] = str(cap)
                row["score"] = (str(net / (cap * mins))
                                if cap > 0 else None)
        rows.append(row)
    key = (lambda r: D(r["score"])) if capital_weighted else (
        lambda r: D(r["net_cents"]))
    return sorted([r for r in rows if not capital_weighted or r.get("score")],
                  key=key, reverse=True)
