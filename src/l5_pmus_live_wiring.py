"""Join the AUTHENTICATED PMUS feed to the FROZEN L5 engine.

    L5 action
      -> contemporaneous AUTHENTICATED book   (this module)
      -> L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY   (frozen 030d8bf)
      -> ORDER via the real PmusTransport, DISARMED         (l5_live_execution)

This is the production path, not a study of it. The same call transmits once
the owner arms the transport: DISARMED, `submit` raises NotArmed and the
executor records the intent as a would-submit order; armed, the identical
line sends it. There is no second implementation waiting to be written.

WHAT THIS MODULE IS ALLOWED TO DO. Exactly one thing: put the authenticated
book where the frozen rule already looks for it -- `row["pmus"]["bbo"]` --
and state the true age of that book in `row["pmus_snapshot_age_s"]`.

WHAT IT MUST NOT DO, and does not. It never filters an action, never
substitutes maker for taker, never imposes a price band or a pair-cost
ceiling, and never drops an action for being stale or bookless. It attaches
what was actually observed and lets the FROZEN rule refuse, so every refusal
carries the frozen rule's own reason and layer rather than a judgement made
out here. A missing book becomes an empty bbo, which the frozen rule reads as
NO_BOOK; an old book keeps its real age, which the frozen rule measures
against its own STALE_MAX_S.

DIRECTION OF TIME. The book attached to an action is the last authenticated
state at or BEFORE our receipt of that action. Never a later one: an action
must not be priced against a book that did not exist when it arrived.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_authenticated_observation as OBS
from src import l5_faithful_translation as FT
from src import l5_live_execution as EX

#: the stage names a caller may see on a record, in order
PATH = ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY", "ORDER")


def _epoch(v: Any) -> Optional[float]:
    return OBS._epoch(v)


def book_at(books: Sequence[Mapping[str, Any]],
            t: float) -> Optional[Dict[str, Any]]:
    """The last USABLE authenticated book at or before `t`.

    No max age here on purpose: the frozen rule owns the staleness decision,
    so this hands over whatever genuinely preceded the action and lets
    STALE_MAX_S be applied in one place.
    """
    prior = [b for b in books
             if b.get("status") == OBS.USABLE and _epoch(b.get("t")) is not None
             and _epoch(b["t"]) <= t]
    if not prior:
        return None
    b = max(prior, key=lambda r: _epoch(r["t"]))
    return {"bid": b.get("bid"), "ask": b.get("ask"),
            "bid_shares": b.get("bid_qty"), "ask_shares": b.get("ask_qty"),
            "t": _epoch(b["t"])}


def attach(row: Mapping[str, Any],
           books: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return the row with the AUTHENTICATED book in place of any other.

    The public book that the observer may have stamped on this row is
    discarded rather than merged: mixing the two would make it impossible to
    say which feed a decision was taken against.
    """
    out = dict(row)
    pmus = dict(out.get("pmus") or {})
    t = _epoch(out.get("receipt_utc"))
    b = book_at(books, t) if t is not None else None
    if b is None:
        pmus["bbo"] = {}
        out["pmus_snapshot_age_s"] = None
        out["book_source"] = "AUTHENTICATED_NONE_AT_OR_BEFORE_ACTION"
    else:
        pmus["bbo"] = {"bid": b["bid"], "ask": b["ask"],
                       "bid_shares": b["bid_shares"],
                       "ask_shares": b["ask_shares"]}
        out["pmus_snapshot_age_s"] = round(t - b["t"], 3)
        out["book_source"] = "AUTHENTICATED"
        out["book_t"] = b["t"]
    out["pmus"] = pmus
    return out


def run_window(rows: Sequence[Mapping[str, Any]],
               books: Sequence[Mapping[str, Any]],
               *, market: str, window: str,
               store: EX.InventoryStore, limits: EX.SafetyLimits,
               transport: Any, lam: D,
               interaction: Optional[Mapping[str, Any]] = None,
               now_fn=None,
               maker_treatment: bool = False,
               maker_only: bool = False,
               suppress_imbalance_increase: bool = False) -> Dict[str, Any]:
    """Drive one window's L5 actions through the real production path.

    `interaction` maps action_key -> observed interaction class, which the
    frozen rule consults only for an action that rests strictly inside the
    spread. Absent, that action is refused STRICT_NOT_EVALUABLE by the frozen
    rule -- an honest "we cannot prove it would have filled", not a claim
    that L5 did not act.
    """
    ex = EX.FaithfulExecutor(store, limits, transport=transport, lam=lam,
                             now_fn=now_fn,
                             maker_treatment=maker_treatment,
                             maker_only=maker_only,
                             suppress_imbalance_increase=(
                                 suppress_imbalance_increase))
    records: List[dict] = []
    for row in sorted(rows, key=lambda r: str(r.get("receipt_utc") or "")):
        wired = attach(row, books)
        rec = ex.handle(wired, dict(interaction or {}),
                        market=market, window=window,
                        open_markets=[market])
        rec["book_source"] = wired.get("book_source")
        rec["pmus_snapshot_age_s"] = wired.get("pmus_snapshot_age_s")
        records.append(rec)
    return {"market": market, "window": window,
            "actions": len(records), "records": records,
            "summary": summarize(records),
            "labels": ("authenticated book only; the frozen rule made every "
                       "refusal. DISARMED: a would-submit order is an intent "
                       "the transport declined to send, never a fill.")}


def summarize(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_layer: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    would_submit: List[dict] = []
    submitted_live = 0
    for r in records:
        order = r.get("ORDER") or {}
        if order.get("submitted") is True:
            submitted_live += 1
        elif order.get("reason") == "NOT_ARMED" and order.get("intent"):
            would_submit.append(order["intent"])
        layer = r.get("refused_by_layer")
        if layer:
            by_layer[layer] = by_layer.get(layer, 0) + 1
            why = str(r.get("refused_reason") or "").split(":")[0]
            reasons[why] = reasons.get(why, 0) + 1
    modes: Dict[str, int] = {}
    for i in would_submit:
        m = str(i.get("mode"))
        modes[m] = modes.get(m, 0) + 1
    return {"would_submit": len(would_submit),
            "would_submit_by_mode": modes,
            "refused_by_layer": by_layer,
            "refused_reason": reasons,
            "submitted_live": submitted_live,
            "note": ("submitted_live MUST be 0 while DISARMED; a non-zero "
                     "value means an order left this process")}
