"""THE ATTEMPT TAPE, WRITTEN FROM THE PRODUCTION DECISION PATH.

THE DEFECT THIS CLOSES. `recorder.record_attempt` is the row that carries
everything the mandate asks of an action -- the authenticated book state at
decision time, side, price, size, queue state, fill, complement, outcome,
pair economics, cancellations -- and `spec.py` names
`recorder:reconstruct_chain` as the official reconstructor, which reads
`attempts.jsonl` and nothing else.

Neither was ever called from production. A repository-wide search for
`record_attempt` outside tests returned the definition and one docstring,
so every live run wrote `events.jsonl` rows only, `attempts.jsonl` stayed
empty, and the estimation chain the stopping rule depends on would have
computed over ZERO attempts. It is the same defect class that superseded
fingerprint 7cc94e20 -- a function written, frozen into the spec's call
graph, covered by tests, and wired to nothing -- and it is why the 22:00z
probe's evidence had to be reconstructed from the returned result dict
instead of read off its own tape.

ONE ROW PER ATTEMPT, WRITTEN WHEN THE OUTCOME IS KNOWN.

`_append` is append-only, so an attempt cannot be written at submission and
updated at fill. `reconstruct_chain` counts `placements = len(attempts)`,
so two rows for one attempt would inflate the placement count and corrupt
every rate derived from it. Therefore exactly one row is emitted per
first-leg order id, at the end of the window, once the venue has told us
what became of it. The submission itself is already durable the moment it
happens -- `FIRST_LEG_SUBMITTED` goes to `events.jsonl` before the POST --
so a crash loses the analytic row, never the fact of the order.

NOTHING IS FABRICATED.

Queue depth ahead of our price and post-fill markouts are properties of a
book tape this probe does not retain per attempt. They are therefore
written as `{"observed": false, "why": ...}` rather than as numbers. A
zero would read as "nothing was ahead of us", which is a claim about the
market we did not make. `reconstruct_chain` does not read either field, so
refusing to invent them costs the estimation chain nothing.

REFUSALS DO NOT GO HERE, AND THAT IS DELIBERATE.

`record_attempt` enforces the lifecycle labels -- SUBMITTED, FILLED,
SETTLED, REALIZED and the replay/shadow labels -- and a refusal is not a
position in that chain. It also refuses any row whose book state is not
the pinned market streaming under PMUS's own authenticated feed, which is
exactly the state a book-unavailable refusal does not have. So refusals are
written to `events.jsonl` by `record_decision` below, carrying the same
market state, inventory before, bounds and reason, and the two files
together are the complete per-action trail.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

NOT_OBSERVED_QUEUE = {
    "observed": False,
    "why": ("this probe does not retain a per-attempt book tape, so the "
            "size resting at and ahead of our price was not recorded. A "
            "zero here would assert nothing was ahead of us."),
}

NOT_OBSERVED_MARKOUTS = {
    "observed": False,
    "why": ("post-fill markouts require the book tape after the fill, "
            "which this probe does not retain per attempt. No direction "
            "is asserted."),
}


def _fin(x: Any) -> Optional[D]:
    if x is None:
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return v if v.is_finite() else None


def inventory_snapshot(order_state: Optional[Mapping[str, Any]]
                       ) -> Dict[str, Any]:
    """Inventory as the VENUE states it, or an explicit unknown.

    Used for both the before and the after side of every decision, so a
    transition is always two readings of the same authority rather than a
    reading and a belief.
    """
    if not isinstance(order_state, Mapping) or not order_state.get("ok"):
        return {"established": False,
                "why": (order_state or {}).get("reason"),
                "NEVER_READ_AS_ZERO": True}
    resid = order_state.get("residual") or None
    return {
        "established": True,
        "n_outstanding": order_state.get("n_outstanding"),
        "outstanding_ids": list(order_state.get("outstanding_ids") or []),
        "net_filled_by_outcome_side":
            dict(order_state.get("net_filled_by_outcome_side") or {}),
        "filled_notional_usd": order_state.get("filled_notional_usd"),
        "open_notional_usd": order_state.get("open_notional_usd"),
        "aggregate_exposure_usd": order_state.get("aggregate_exposure_usd"),
        "residual": dict(resid) if resid else None,
        "venue_positions": dict(order_state.get("venue_positions") or {}),
        "source": "VENUE_AUTHORITATIVE",
    }


def record_decision(recorder: Any, *, window: Any, slot: int, event: str,
                    market: Any, reason: Any,
                    order_state: Optional[Mapping[str, Any]] = None,
                    snapshot: Optional[Mapping[str, Any]] = None,
                    envelope: Optional[Mapping[str, Any]] = None,
                    extra: Optional[Mapping[str, Any]] = None) -> None:
    """One event row per decision, refusals included.

    Carries the market state we DID observe, the inventory before the
    decision, the bounds consulted and the reason. Never raises: a
    recording failure must not change what the engine does.
    """
    if recorder is None:
        return
    snap = snapshot or {}
    detail: Dict[str, Any] = {
        "slug": market,
        "reason": reason,
        "inventory_before": inventory_snapshot(order_state),
        "market_state_observed": {
            "first_leg_side": snap.get("first_leg_side"),
            "first_leg_price": snap.get("first_leg_price"),
            "first_leg_best_bid": snap.get("first_leg_best_bid"),
            "first_leg_best_ask": snap.get("first_leg_best_ask"),
            "complement_best_bid": snap.get("complement_best_bid"),
            "complement_price": snap.get("complement_price"),
            "qty": snap.get("qty"),
            "book_available": snap.get("book_available"),
            "timing_authority": snap.get("timing_authority"),
        },
        "bounds_consulted": dict(envelope) if envelope else None,
    }
    if extra:
        detail.update(dict(extra))
    try:
        recorder.record_event(window=window if window is not None else -1,
                              slot=slot, event=event, detail=detail)
    except Exception:                                          # noqa: BLE001
        return


def _outcome_for(order_id: str, *, leg: Mapping[str, Any],
                 state: Optional[Mapping[str, Any]],
                 complement_id: Optional[str],
                 complement_state: Optional[Mapping[str, Any]],
                 residual: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """COMPLETE vs RESIDUAL, with the pair cost only when both legs filled.

    A pair cost is arithmetic over two CONFIRMED fills. If either side is
    unfilled there is no realized pair cost, and the field stays None
    rather than carrying a modelled number.
    """
    fl_price = _fin(leg.get("price"))
    fl_filled = _fin((state or {}).get("cum_quantity"))
    cp_price = _fin((complement_state or {}).get("own_side_price"))
    cp_filled = _fin((complement_state or {}).get("cum_quantity"))

    complete = bool(fl_filled and cp_filled and fl_filled > 0
                    and cp_filled >= fl_filled)
    pair_cost = None
    if complete and fl_price is not None and cp_price is not None:
        pair_cost = str(fl_price + cp_price)
    return {
        "state": "COMPLETE" if complete else (
            "RESIDUAL" if (fl_filled and fl_filled > 0) else "NO_FILL"),
        "realized_pair_cost_usd_per_share": pair_cost,
        "pair_cost_is_arithmetic_over_two_confirmed_fills": True,
        "residual_shares": (str(residual.get("filled_qty"))
                            if residual else None),
        "residual_duration_s": (residual.get("duration_s")
                                if residual else None),
        "complement_order_id": complement_id,
        "fees": {"disclosed": False,
                 "why": ("no venue fee statement has been read for this "
                         "attempt; a modelled fee is a gate input and is "
                         "never recorded as a fee")},
        "settlement": {"confirmed": False,
                       "why": "settlement is audited separately"},
        "REALIZED_CLAIM": False,
    }


def emit_attempt_rows(recorder: Any, *, lifecycle: Any,
                      order_state: Optional[Mapping[str, Any]],
                      market: Any, ws_state: Optional[Mapping[str, Any]],
                      complement_ids: Optional[Mapping[str, str]] = None,
                      ws_state_by_order_id: Optional[Mapping[str, Any]]
                      = None,
                      cancellations: Any = None,
                      label: str = "SUBMITTED",
                      windows_in_scope: Any = None) -> Dict[str, Any]:
    """Write ONE attempt row per first leg this run actually submitted.

    Driven by `lifecycle.legs`, which is the production bookkeeping, and
    by the venue's own per-id order state. Legs carried in from earlier
    windows are skipped: this run reports what it did.

    Returns what it wrote and what it skipped, so a caller can assert the
    tape exists rather than assume it.
    """
    out: Dict[str, Any] = {"written": [], "skipped": [], "errors": []}
    if recorder is None:
        out["skipped"].append("NO_RECORDER")
        return out
    legs = dict(getattr(lifecycle, "legs", {}) or {})
    residuals = dict(getattr(lifecycle, "residuals_opened", {}) or {})
    comp = dict(complement_ids or {})
    by_id: Dict[str, Mapping[str, Any]] = {}
    for o in ((order_state or {}).get("orders") or []):
        if isinstance(o, Mapping) and o.get("id") is not None:
            by_id[str(o["id"])] = o

    scope = None
    if windows_in_scope is not None:
        scope = {int(w) for w in windows_in_scope}

    for order_id, leg in sorted(legs.items()):
        try:
            window = int(leg.get("window", -1))
        except (TypeError, ValueError):
            window = -1
        if scope is not None and window not in scope:
            out["skipped"].append({"order_id": order_id,
                                   "why": "NOT_THIS_RUNS_WINDOW",
                                   "window": window})
            continue
        st = by_id.get(str(order_id))
        cid = comp.get(str(order_id))
        cst = by_id.get(str(cid)) if cid else None
        resid = residuals.get(str(order_id))
        fill = {
            "filled_qty": (st or {}).get("cum_quantity"),
            "leaves_quantity": (st or {}).get("leaves_quantity"),
            "state": (st or {}).get("state"),
            "venue_confirmed": st is not None,
            "partial": bool((st or {}).get("partially_filled")),
        }
        row_complement = None
        if cid:
            row_complement = {
                "complement_order_id": cid,
                "qty": leg.get("complemented"),
                "filled_qty": (cst or {}).get("cum_quantity"),
                "state": (cst or {}).get("state"),
                "venue_confirmed": cst is not None,
                "submitted": True,
            }
        try:
            written = recorder.record_attempt(
                window=window,
                slot=int(leg.get("slot", 0) or 0),
                market=market,
                ws_state=dict((ws_state_by_order_id or {}).get(
                    str(order_id)) or ws_state or {}),
                first_leg={"order_id": order_id,
                           "price": leg.get("price"),
                           "qty": (st or {}).get("quantity"),
                           "outcome_side": (st or {}).get("outcome_side"),
                           "own_side_price_at_venue":
                               (st or {}).get("own_side_price"),
                           "wire_price": (st or {}).get("wire_price")},
                queue=NOT_OBSERVED_QUEUE,
                timestamps={"observed": False,
                            "why": ("per-attempt submit/ack/fill times are "
                                    "not retained by this probe")},
                fill=fill,
                complement=row_complement,
                outcome=_outcome_for(order_id, leg=leg, state=st,
                                     complement_id=cid,
                                     complement_state=cst,
                                     residual=resid),
                cancellations=cancellations,
                markouts=NOT_OBSERVED_MARKOUTS,
                label=label,
                extra={"inventory_after": inventory_snapshot(order_state),
                       "written_from": "run_cohort production path"})
            out["written"].append({"order_id": order_id,
                                   "window": window,
                                   "row": written.get("kind")})
        except Exception as exc:                               # noqa: BLE001
            out["errors"].append({"order_id": order_id,
                                  "error": type(exc).__name__,
                                  "detail": str(exc)[:200]})
    out["n_written"] = len(out["written"])
    out["one_row_per_attempt"] = True
    out["nothing_fabricated"] = True
    return out
