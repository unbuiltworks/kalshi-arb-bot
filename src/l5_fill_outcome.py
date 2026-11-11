"""Per-fill outcome: the join from each actual fill to its settlement.

WHY THIS EXISTS. `fill_evidence` already records a fill's book, price, size,
depth and whether an order was resting. `L5_WINDOW_ACCOUNTING` already records
the window's settlement. Nothing joined them, so the one question the whole
measurement programme turns on -- DID THIS FILL LAND ON THE WINNING OUTCOME --
was not answerable from our own records at all. The 81% adverse-selection
figure is a REPLAY estimate; these rows are what make a live number possible.

PURE. No I/O, no venue, no submit, no refuse. It is handed the fills and the
venue's own settlement line and returns rows. A failure here must never be
able to change a settlement or stop trading, which is why the caller holds it
inside the accounting swallow.

THE TWO ADVERSE-SELECTION DENOMINATORS, and they are not interchangeable:

  `residual_side_lost`   per WINDOW, for windows ending with residual: did the
                         unmatched side lose? This is the metric the replay
                         reports as 81% and L5's early era as 57%, so it is
                         the one the live comparison must use.
  `uncomplemented_lost`  per FILL, over fills that never got a complement.
                         More observations and so a tighter interval, but NOT
                         comparable to the 81%/57% figures.

NEVER A FILL. Every row here describes a fill the venue already confirmed. A
modelled or resting order produces no row: `fills` is the venue-confirmed set
the caller passes in, and an empty one yields no rows rather than zeros.
"""
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

UP, DOWN = "UP", "DOWN"


def _d(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = D(str(v))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    return x if x.is_finite() else None


def _side(v: Any) -> Optional[str]:
    s = str(v or "").strip().upper()
    if s.startswith("U") or s in ("YES", "Y"):
        return UP
    if s.startswith("D") or s in ("NO", "N"):
        return DOWN
    return None


def fill_outcomes(*, slug: str,
                  fills: Sequence[Mapping[str, Any]],
                  settled_side: Any,
                  actual_fees_usd: Any = None,
                  resolution_source: Any = None) -> Dict[str, Any]:
    """One row per venue-confirmed fill, plus the window's two denominators.

    `settled_side` is the venue's own resolved outcome. UNKNOWN IS NOT A
    GUESS: when it cannot be read, every row is emitted with
    `on_winner: None` and the window contributes to NO denominator, because
    an unresolved window is missing data and not a 50/50.
    """
    win = _side(settled_side)
    rows: List[Dict[str, Any]] = []
    qty_by_side = {UP: D(0), DOWN: D(0)}

    norm: List[Dict[str, Any]] = []
    for f in fills or ():
        s = _side(f.get("side"))
        q = _d(f.get("qty") if f.get("qty") is not None else f.get("delta"))
        p = _d(f.get("own_px") if f.get("own_px") is not None
               else f.get("fill_own_px"))
        if s is None or q is None or q <= 0:
            rows.append({"coid": str(f.get("coid") or ""), "slug": str(slug),
                         "unreadable": True,
                         "why": "side or qty unreadable",
                         "side": None, "qty": None, "own_px": None,
                         "on_winner": None, "complemented": None})
            continue
        qty_by_side[s] += q
        norm.append({"coid": str(f.get("coid") or ""), "side": s, "qty": q,
                     "own_px": p, "t": f.get("t"),
                     "queue_ahead": f.get("queue_ahead"),
                     "level_depth": f.get("level_depth"),
                     "latency_s": _d(f.get("latency_s")),
                     "order_resting": f.get("order_resting")})

    matched = min(qty_by_side[UP], qty_by_side[DOWN])
    # complement credit is allocated in fill order, so a fill is "complemented"
    # exactly while cumulative same-side quantity stays inside the matched
    # amount. Deterministic, and it never credits more complement than exists.
    seen = {UP: D(0), DOWN: D(0)}
    for r in norm:
        s = r["side"]
        before = seen[s]
        seen[s] = before + r["qty"]
        covered = max(D(0), min(r["qty"], matched - before))
        r["complemented"] = bool(covered > 0)
        r["complement_qty"] = str(covered)
        r["on_winner"] = None if win is None else bool(s == win)
        if win is not None and r["own_px"] is not None:
            redeem = D(1) if s == win else D(0)
            r["gross_settle_usd"] = str(r["qty"] * (redeem - r["own_px"]))
        else:
            r["gross_settle_usd"] = None
        r["slug"] = str(slug)
        r["settled_side"] = win
        r["resolution_source"] = (None if resolution_source is None
                                  else str(resolution_source))
        r["qty"] = str(r["qty"])
        r["own_px"] = None if r["own_px"] is None else str(r["own_px"])
        r["latency_s"] = (None if r["latency_s"] is None
                          else str(r["latency_s"]))
        r["labels"] = ("A VENUE-CONFIRMED FILL joined to the venue's own "
                       "settlement. Never a modelled or resting order.")
        rows.append(r)

    resid = qty_by_side[UP] - qty_by_side[DOWN]
    resid_side = UP if resid > 0 else (DOWN if resid < 0 else None)
    # the window's contribution to the metric the 81%/57% figures use
    residual_side_lost: Optional[bool] = None
    if resid_side is not None and win is not None:
        residual_side_lost = bool(resid_side != win)

    uncomp = [r for r in rows if r.get("complemented") is False
              and r.get("on_winner") is not None]
    return {
        "label": "L5_FILL_OUTCOME",
        "slug": str(slug),
        "settled_side": win,
        "resolution_source": (None if resolution_source is None
                              else str(resolution_source)),
        "actual_fees_usd": (None if actual_fees_usd is None
                            else str(actual_fees_usd)),
        "fills": rows,
        "n_fills": len(norm),
        "qty_up": str(qty_by_side[UP]),
        "qty_down": str(qty_by_side[DOWN]),
        "matched_qty": str(matched),
        "residual_qty": str(abs(resid)),
        "residual_side": resid_side,
        # the clustered unit of observation: ONE per window
        "residual_side_lost": residual_side_lost,
        # the per-fill denominator, reported beside it and not mixed with it
        "uncomplemented_fills": len(uncomp),
        "uncomplemented_lost": sum(1 for r in uncomp
                                   if r["on_winner"] is False),
        "labels": ("Adverse selection has TWO denominators and they are not "
                   "interchangeable: `residual_side_lost` is per WINDOW and "
                   "is the metric the 81% replay estimate and L5's 57% use; "
                   "`uncomplemented_*` is per FILL, tighter but not "
                   "comparable to those figures. An unresolved window "
                   "contributes to neither."),
    }
