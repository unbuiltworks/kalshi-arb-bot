"""Shadow adapter: run the inventory evaluator on the EXISTING continuous
cycle's own state, and emit the decision ledger row.

This is deliberately an adapter and not a service. The continuous runner keeps
its one controller, its one execution path and its one evidence file; this
module adds a parallel, read-only OPINION to each cycle row -- what the
inventory engine would have done with the state the live controller actually
had -- so the two can be compared later on identical inputs.

NOTHING HERE CAN TRADE. It takes the cycle's already-read state as plain
mappings, returns a dict, and has no venue handle, no clock and no order path.
If the state it is handed is incomplete it records a refusal with the reason;
it never substitutes a default so a decision can be produced anyway.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Mapping, Optional

from src.gemini_execution.inventory_engine import (
    Book, Config, Inventory, Limits, Prob, RestingOrder, evaluate,
)
from src.gemini_execution import inventory_prob as IP

LABEL = "L5_INVENTORY_SHADOW_V1"

#: The calibration the shadow runs with. resid_sd 0.5 is "no skill", which the
#: engine's PROB_UNCERTAIN gate turns into WAIT. It stays at no-skill until a
#: probability estimator passes chronological out-of-sample validation -- see
#: L5_INVENTORY_ENGINE_VALIDATION.md. Shipping it as no-skill is the point: the
#: shadow records the decision chain without asserting an edge it has not shown.
UNCALIBRATED = IP.Calibration(
    resid_sd=0.5, n_train=0,
    note="NO CALIBRATION ADOPTED. OOS Brier skill of the fitted mid+tilt model "
         "was NEGATIVE (-13.7%); the engine therefore WAITs by gate, which is "
         "the intended behaviour when no probability edge is established.")


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def book_from_presented(presented: Any) -> Optional[Book]:
    """Build the engine's Book from the cycle's own presented book.

    Our own-cost convention needs a YES bid and a NO bid, which is exactly what
    `presented_book` carries. A book the state declared unreadable stays
    unreadable: this returns None rather than a book with holes in it.
    """
    p = presented if isinstance(presented, Mapping) else {}
    if not p.get("present") or not p.get("readable"):
        return None
    yes = p.get("yes") if isinstance(p.get("yes"), Mapping) else {}
    no = p.get("no") if isinstance(p.get("no"), Mapping) else {}
    yb, nb = _d(yes.get("bid")), _d(no.get("bid"))
    if yb is None or nb is None or yb <= 0 or nb <= 0:
        return None
    ybq = _d(yes.get("bid_qty")) or _d(yes.get("bid_size")) or D(0)
    nbq = _d(no.get("bid_qty")) or _d(no.get("bid_size")) or D(0)
    return Book(yes_bid=yb, yes_bid_qty=ybq, no_bid=nb, no_bid_qty=nbq)


def inventory_from_cycle(inv: Any) -> Inventory:
    """UP == YES, DOWN == NO. Cost basis is used when the cycle carries it and
    is otherwise left at zero, which makes the worst case conservative (a zero
    basis understates what we paid, so it never flatters the position)."""
    m = inv if isinstance(inv, Mapping) else {}
    up = _d(m.get("yes_qty")) or D(0)
    dn = _d(m.get("no_qty")) or D(0)
    return Inventory(up_qty=up, dn_qty=dn,
                     up_cost=_d(m.get("yes_cost")) or D(0),
                     dn_cost=_d(m.get("no_cost")) or D(0),
                     fees_paid=_d(m.get("fees_paid")) or D(0))


def resting_from_working(working: Any, book: Book) -> tuple:
    out = []
    for w in (working or []):
        if not isinstance(w, Mapping):
            continue
        px = _d(w.get("price"))
        qty = _d(w.get("qty")) or _d(w.get("count")) or D(0)
        leg = str(w.get("leg") or w.get("side") or "").upper()
        if px is None or qty <= 0 or leg not in ("YES", "NO", "UP", "DOWN"):
            continue
        side = "UP" if leg in ("YES", "UP") else "DOWN"
        out.append(RestingOrder(side=side, kind="MAKER", own=px, qty=qty,
                                filled=_d(w.get("filled")) or D(0),
                                t_live=0.0, queue=book.maker_queue(side)))
    return tuple(out)


def shadow_decision(*, symbol: str, tte_s: Any, presented: Any, depth: Any,
                    flow: Any, inventory: Any, working: Any,
                    cal: IP.Calibration = UNCALIBRATED,
                    cfg: Optional[Config] = None,
                    limits: Optional[Limits] = None) -> dict:
    """The inventory engine's opinion on one existing cycle, as a ledger row."""
    book = book_from_presented(presented)
    tte = _d(tte_s)
    if book is None:
        return {"label": LABEL, "symbol": symbol, "action": "WAIT",
                "reason": "BOOK_UNREADABLE", "SHADOW_NOT_SENT": True,
                "note": "no decision produced; the cycle's own book was not "
                        "usable and no substitute was invented"}
    if tte is None:
        return {"label": LABEL, "symbol": symbol, "action": "WAIT",
                "reason": "TTE_UNKNOWN", "SHADOW_NOT_SENT": True}

    inv = inventory_from_cycle(inventory)
    f = flow if isinstance(flow, Mapping) else {}
    n_prints = f.get("n_prints") or 0
    buys = _d(f.get("buy_prints")) or D(0)
    sells = _d(f.get("sell_prints")) or D(0)
    tot = buys + sells
    feats = IP.Features(
        imb=float(book.imbalance),
        flow=(float((buys - sells) / tot) if tot > 0 else 0.0),
        mid=float(book.mid), tte=float(tte),
        book_age_s=0.0, n_prints=int(n_prints))

    prob = IP.estimate(feats, cal)
    if cfg is None:
        # PMUS fees and PMUS-MEASURED fills by default. The shadow was shown to
        # be wrong about fills, so it no longer models them: P(fill) comes from
        # 965 of our own submitted orders and refuses past the 32.2 s horizon
        # those orders reached.
        from src.gemini_execution.pmus_execution_model import pmus_config
        cfg = pmus_config()
    d = evaluate(market=symbol, t=0.0, tte=float(tte), inv=inv, book=book,
                 prob=prob, resting=resting_from_working(working, book),
                 limits=limits or Limits(), cfg=cfg)
    row = d.as_row()
    row["label"] = LABEL
    row["symbol"] = symbol
    row["SHADOW_NOT_SENT"] = True
    row["calibration_note"] = cal.note
    return row
